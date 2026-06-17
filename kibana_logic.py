"""
core/kibana_logic.py
======================
Core logic for fetching and restoring Kibana saved objects (per space, with
deep references). Refactored from the original CLI script into callable
functions so the Streamlit app can drive it with progress callbacks.

No Streamlit imports here — UI-agnostic by design.
"""

import json
import re
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests
from requests.auth import HTTPBasicAuth


# Full candidate list of saved-object types. Not all are exportable on every
# Kibana version/install — export_space() self-heals against rejected types.
ALL_OBJECT_TYPES = [
    "dashboard",
    "visualization",
    "lens",
    "map",
    "search",
    "index-pattern",
    "canvas-workpad",
    "canvas-element",
    "url",
    "tag",
    "config",
    "query",
    "graph-workspace",
    "infrastructure-ui-source",
    "metrics-explorer-view",
    "uptime-dynamic-settings",
    "apm-indices",
    "osquery-saved-query",
    "osquery-pack",
    "cases-configure",
    "connector",
]

# Pre-known as non-exportable on most 7.13.x installs — excluded by default,
# but the self-healing retry in export_space() would catch this anyway even
# if a user re-adds it via the type-selection UI.
KNOWN_NON_EXPORTABLE_TYPES = {"uptime-dynamic-settings"}

DEFAULT_EXPORT_TYPES = [t for t in ALL_OBJECT_TYPES if t not in KNOWN_NON_EXPORTABLE_TYPES]


# ── Session ──────────────────────────────────────────────────────────────

def make_session(username: str, password: str, verify_ssl: bool = True) -> requests.Session:
    session = requests.Session()
    session.auth = HTTPBasicAuth(username, password)
    session.headers.update({"kbn-xsrf": "true"})
    session.verify = verify_ssl
    return session


# ── Space discovery ─────────────────────────────────────────────────────

def get_all_spaces(session: requests.Session, base_url: str) -> list:
    base_url = base_url.rstrip("/")
    resp = session.get(f"{base_url}/api/spaces/space", timeout=30)
    resp.raise_for_status()
    spaces = resp.json()
    ids = {s["id"] for s in spaces}
    if "default" not in ids:
        spaces.insert(0, {"id": "default", "name": "Default", "description": ""})
    return spaces


def check_kibana_status(host: str, username: str, password: str, verify_ssl: bool = True) -> dict:
    """Quick connectivity check. Returns parsed /api/status response."""
    session = make_session(username, password, verify_ssl)
    resp = session.get(f"{host.rstrip('/')}/api/status", timeout=15)
    resp.raise_for_status()
    return resp.json()


# ── Export (with self-healing against non-exportable types) ───────────────

def _parse_non_exportable_types(error_text: str, known_types: list) -> list:
    found = []
    match = re.search(r"non-exportable type\(s\):\s*([\w\-,\s]+)", error_text)
    if match:
        candidates = [t.strip() for t in match.group(1).split(",")]
        found = [t for t in candidates if t in known_types]
    if found:
        return found
    return [t for t in known_types if t in error_text]


def _count_types(ndjson_lines: list) -> dict:
    counts = {}
    for line in ndjson_lines:
        try:
            obj = json.loads(line)
            t = obj.get("type")
            if t:
                counts[t] = counts.get(t, 0) + 1
        except json.JSONDecodeError:
            pass
    return counts


def export_space(session: requests.Session, base_url: str, space_id: str,
                  export_types: list, request_timeout: int = 60):
    """
    Export all saved objects from a single space.
    Self-heals: if Kibana rejects one or more types as non-exportable, strips
    them and retries automatically.

    Returns (ndjson_bytes, stats_dict) where stats_dict has:
      total, per_type {type: count}, skipped_types [list]
    """
    base_url = base_url.rstrip("/")
    types_to_try = list(export_types)
    skipped_types = []
    prefix = "" if space_id == "default" else f"/s/{space_id}"
    url = f"{base_url}{prefix}/api/saved_objects/_export"

    max_retries = max(len(types_to_try), 1)
    raw = b""

    for _ in range(max_retries):
        payload = {"type": types_to_try, "includeReferencesDeep": True, "excludeExportDetails": False}
        resp = session.post(url, json=payload, headers={"Content-Type": "application/json"},
                             timeout=request_timeout, stream=True)

        if resp.status_code == 400:
            bad_types = _parse_non_exportable_types(resp.text, types_to_try)
            if bad_types:
                skipped_types.extend(bad_types)
                types_to_try = [t for t in types_to_try if t not in bad_types]
                if not types_to_try:
                    return b"", {"total": 0, "per_type": {}, "skipped_types": skipped_types}
                continue
            try:
                err_body = resp.json()
            except Exception:
                err_body = {}
            if "exportedCount" in str(err_body):
                return b"", {"total": 0, "per_type": {}, "skipped_types": skipped_types}
            resp.raise_for_status()

        resp.raise_for_status()
        raw = resp.content
        break

    stats = {"total": 0, "per_type": {}, "skipped_types": skipped_types}
    lines = [ln for ln in raw.decode("utf-8").splitlines() if ln.strip()] if raw else []

    if lines:
        try:
            last = json.loads(lines[-1])
            if last.get("exportedCount") is not None:
                stats["total"] = last.get("exportedCount", 0)
                stats["per_type"] = last.get("exportDetails", {})
            else:
                stats["total"] = len(lines)
                stats["per_type"] = _count_types(lines)
        except (json.JSONDecodeError, KeyError):
            stats["total"] = len(lines)
            stats["per_type"] = _count_types(lines)

    return raw, stats


# ── Filename helper ─────────────────────────────────────────────────────

def safe_filename(name: str) -> str:
    return re.sub(r"[^\w\-.]", "_", name)


# ── Excel summary ───────────────────────────────────────────────────────

def build_summary_excel_bytes(space_results: list) -> bytes:
    import io
    all_types = sorted({t for r in space_results for t in r["per_type"].keys()})

    rows = []
    for r in space_results:
        row = {
            "space_id": r["space_id"],
            "space_name": r["space_name"],
            "description": r["description"],
            "total_objects": r["total"],
            "export_file": r["export_file"],
            "status": r["status"],
        }
        for t in all_types:
            row[t] = r["per_type"].get(t, 0)
        rows.append(row)

    df = pd.DataFrame(rows)
    type_totals = {t: df[t].sum() for t in all_types if t in df.columns}
    df_totals = pd.DataFrame(
        [{"object_type": k, "total_across_all_spaces": v}
         for k, v in sorted(type_totals.items(), key=lambda x: -x[1])]
    )

    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="Summary", index=False)
        df_totals.to_excel(writer, sheet_name="Type Totals", index=False)
        for sheet_name in ("Summary", "Type Totals"):
            ws = writer.sheets[sheet_name]
            ws.freeze_panes = "A2"
            for col in ws.columns:
                max_len = max(len(str(cell.value or "")) for cell in col)
                ws.column_dimensions[col[0].column_letter].width = min(max_len + 4, 50)
    buffer.seek(0)
    return buffer.read()


# ── Orchestration (called by Streamlit app) ────────────────────────────────

def run_fetch(kibana_host: str, username: str, password: str, export_types: list = None,
              verify_ssl: bool = True, request_delay: float = 0.2, progress_callback=None):
    """
    Full fetch workflow across all spaces.
    progress_callback(current, total, message) called after each space.

    Returns dict: {
        "space_results": [...],   # one dict per space (includes ndjson bytes under "ndjson_bytes")
        "skipped_types": set(),
        "kibana_info": {...}
    }
    """
    session = make_session(username, password, verify_ssl)
    base_url = kibana_host.rstrip("/")
    export_types = export_types or DEFAULT_EXPORT_TYPES

    kibana_info = check_kibana_status(kibana_host, username, password, verify_ssl)

    if progress_callback:
        progress_callback(0, 1, "Discovering spaces...")

    spaces = get_all_spaces(session, base_url)
    total = len(spaces)

    space_results = []
    all_skipped_types = set()

    for i, space in enumerate(spaces, 1):
        space_id = space["id"]
        space_name = space.get("name", space_id)
        desc = space.get("description", "")

        result = {
            "space_id": space_id, "space_name": space_name, "description": desc,
            "total": 0, "per_type": {}, "export_file": "", "status": "pending",
            "ndjson_bytes": b"",
        }

        try:
            time.sleep(request_delay)
            ndjson_bytes, stats = export_space(session, base_url, space_id, export_types)

            if stats.get("skipped_types"):
                all_skipped_types.update(stats["skipped_types"])

            if stats["total"] == 0:
                result["status"] = "empty"
            else:
                result.update({
                    "total": stats["total"],
                    "per_type": stats["per_type"],
                    "export_file": f"{safe_filename(space_id)}.ndjson",
                    "status": "ok",
                    "ndjson_bytes": ndjson_bytes,
                })
        except requests.HTTPError as exc:
            result["status"] = f"error: HTTP {exc.response.status_code}"
        except Exception as exc:
            result["status"] = f"error: {exc}"

        space_results.append(result)
        if progress_callback:
            progress_callback(i, total, f"Exported space '{space_id}'")

    return {"space_results": space_results, "skipped_types": all_skipped_types, "kibana_info": kibana_info}


def create_space(session: requests.Session, base_url: str, space_id: str) -> None:
    url = f"{base_url.rstrip('/')}/api/spaces/space"
    body = {"id": space_id, "name": space_id.replace("_", " ").replace("-", " ").title()}
    resp = session.post(url, json=body, headers={"Content-Type": "application/json"})
    if resp.status_code in (200, 409):
        return
    resp.raise_for_status()


def run_restore(target_host: str, username: str, password: str, space_files: dict,
                 overwrite: bool = True, verify_ssl: bool = True,
                 request_delay: float = 0.2, progress_callback=None):
    """
    Import .ndjson content into target Kibana, space by space.
    space_files: dict of {space_id: ndjson_bytes}
    Returns dict: {"success": [...], "failed": [...], "details": [...]}
    """
    session = make_session(username, password, verify_ssl)
    base_url = target_host.rstrip("/")

    success, failed, details = [], [], []
    total = len(space_files)

    for i, (space_id, ndjson_bytes) in enumerate(space_files.items(), 1):
        prefix = "" if space_id == "default" else f"/s/{space_id}"
        url = f"{base_url}{prefix}/api/saved_objects/_import?overwrite={str(overwrite).lower()}"

        try:
            resp = session.post(
                url,
                files={"file": (f"{space_id}.ndjson", ndjson_bytes, "application/ndjson")},
                headers={"kbn-xsrf": "true"},
                timeout=60,
            )

            if resp.status_code == 404:
                create_space(session, base_url, space_id)
                time.sleep(1)
                resp = session.post(
                    url,
                    files={"file": (f"{space_id}.ndjson", ndjson_bytes, "application/ndjson")},
                    headers={"kbn-xsrf": "true"},
                    timeout=60,
                )

            resp.raise_for_status()
            result = resp.json()
            imported = result.get("successCount", 0)
            errors_in = result.get("errors", [])

            if result.get("success"):
                success.append(space_id)
            else:
                success.append(space_id)  # Partial success still counts as attempted
            details.append({
                "space_id": space_id, "imported": imported,
                "error_count": len(errors_in), "errors": errors_in[:10],
            })

        except requests.HTTPError as exc:
            failed.append(space_id)
            details.append({"space_id": space_id, "imported": 0,
                             "error_count": -1, "errors": [str(exc)]})
        except Exception as exc:
            failed.append(space_id)
            details.append({"space_id": space_id, "imported": 0,
                             "error_count": -1, "errors": [str(exc)]})

        time.sleep(request_delay)
        if progress_callback:
            progress_callback(i, total, f"Restored space '{space_id}'")

    return {"success": success, "failed": failed, "details": details}
