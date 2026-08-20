"""
security_logic.py
==================
Core logic for fetching and restoring Elasticsearch native-realm users and
roles, including password hashes read directly from the .security-7 index.

Supported asset types:
  - roles   (GET /_security/role, cleaned)
  - users   (direct read of the security index, password hash preserved)

Reserved detection is dynamic (no hardcoded name lists):
  - users: the index doc's "type" field == "reserved-user"
  - roles: the role definition's metadata["_reserved"] is true

UI-agnostic — usable by the Streamlit app or standalone scripts.
"""

import io
import json
import re
import time
import zipfile
from datetime import datetime

import pandas as pd
import requests
from requests.auth import HTTPBasicAuth

SECURITY_INDEX = ".security-7"

# Roles must be restored before users (users reference role names).
SECURITY_ASSET_TYPES = ["roles", "users"]

SECURITY_ASSET_LABELS = {
    "roles": "Roles",
    "users": "Users",
}


# ── Helpers ──────────────────────────────────────────────────────────────────

def make_session(username: str, password: str, verify_ssl: bool = True) -> requests.Session:
    session = requests.Session()
    if username and password:
        session.auth = HTTPBasicAuth(username, password)
    session.headers.update({"Content-Type": "application/json"})
    session.verify = verify_ssl
    return session


def safe_filename(name: str) -> str:
    return re.sub(r"[^\w\-.]", "_", name)


def is_reserved_user(user_doc: dict) -> bool:
    """Reserved users are flagged via the index doc's `type` field."""
    return user_doc.get("type") == "reserved-user"


def is_reserved_role(role_def: dict) -> bool:
    """Reserved (built-in) roles are flagged via metadata._reserved."""
    return bool((role_def.get("metadata") or {}).get("_reserved"))


# ── Fetch helpers ────────────────────────────────────────────────────────────

def _scroll_security_users(session: requests.Session, base_url: str, security_index: str,
                           request_delay: float, scroll_ttl: str = "1m",
                           page_size: int = 200) -> list:
    """
    Scroll every user document out of the security index.
    Only `type: user` and `type: reserved-user` docs are returned, so the
    reserved flag can be read straight off each doc's `type` field.
    """
    base_url = base_url.rstrip("/")
    url = f"{base_url}/{security_index}/_search?scroll={scroll_ttl}"
    body = {"size": page_size, "query": {"terms": {"type": ["user", "reserved-user"]}}}

    docs = []
    resp = session.post(url, json=body, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    scroll_id = data.get("_scroll_id")
    hits = data.get("hits", {}).get("hits", [])
    docs.extend(h["_source"] for h in hits)

    while hits:
        time.sleep(request_delay)
        resp = session.post(f"{base_url}/_search/scroll",
                            json={"scroll": scroll_ttl, "scroll_id": scroll_id}, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        scroll_id = data.get("_scroll_id")
        hits = data.get("hits", {}).get("hits", [])
        docs.extend(h["_source"] for h in hits)

    try:
        session.delete(f"{base_url}/_search/scroll", json={"scroll_id": scroll_id})
    except Exception:
        pass

    return docs


def _normalize_user_doc(doc: dict) -> dict:
    """
    Turn a raw .security-7 user doc into a restore-ready body for
    PUT /_security/user/{username}. The raw `password` value is reused
    verbatim as `password_hash` (matches manually-tested restore payloads).
    """
    body = {}
    if doc.get("password"):
        body["password_hash"] = doc["password"]

    for field in ("roles", "full_name", "email", "enabled"):
        if doc.get(field) is not None:
            body[field] = doc[field]

    metadata = doc.get("metadata") or {}
    custom_metadata = {k: v for k, v in metadata.items() if k != "_reserved"}
    if custom_metadata:
        body["metadata"] = custom_metadata

    return body


def _fetch_roles(session: requests.Session, base_url: str) -> dict:
    """
    Fetch all roles via GET /_security/role and return
    {name: {"reserved": bool, "body": clean_body}}.
    """
    base_url = base_url.rstrip("/")
    resp = session.get(f"{base_url}/_security/role", timeout=30)
    resp.raise_for_status()

    roles = {}
    for name, role_def in resp.json().items():
        reserved = is_reserved_role(role_def)
        body = {k: v for k, v in role_def.items() if k != "transient_metadata"}

        metadata = body.get("metadata") or {}
        if "_reserved" in metadata:
            metadata = {k: v for k, v in metadata.items() if k != "_reserved"}
        if metadata:
            body["metadata"] = metadata
        else:
            body.pop("metadata", None)

        roles[name] = {"reserved": reserved, "body": body}

    return roles


# ── Fetch orchestration ──────────────────────────────────────────────────────

def run_fetch_security_assets(es_host: str, username: str, password: str,
                              export_types: list = None, include_reserved: bool = False,
                              security_index: str = SECURITY_INDEX, verify_ssl: bool = True,
                              request_delay: float = 0.05, progress_callback=None) -> dict:
    """
    Fetch users (from the security index, with password hashes) and roles.

    Returns:
        {
            "assets": {asset_type: {name: clean_body}},
            "meta":   {asset_type: {name: {"reserved": bool}}},
            "errors": [...]
        }
    """
    session = make_session(username, password, verify_ssl)
    base_url = es_host.rstrip("/")
    export_types = export_types or SECURITY_ASSET_TYPES
    security_index = (security_index or SECURITY_INDEX).strip() or SECURITY_INDEX

    results = {
        "assets": {t: {} for t in export_types},
        "meta": {t: {} for t in export_types},
        "errors": [],
    }

    total = len(export_types)
    for i, asset_type in enumerate(export_types, 1):
        label = SECURITY_ASSET_LABELS.get(asset_type, asset_type)
        if progress_callback:
            progress_callback(i, total, f"Fetching {label}...")

        time.sleep(request_delay)

        try:
            if asset_type == "users":
                docs = _scroll_security_users(session, base_url, security_index, request_delay)
                for doc in docs:
                    name = doc.get("username")
                    if not name:
                        continue
                    reserved = is_reserved_user(doc)
                    if reserved and not include_reserved:
                        continue
                    results["assets"]["users"][name] = _normalize_user_doc(doc)
                    results["meta"]["users"][name] = {"reserved": reserved}

            elif asset_type == "roles":
                role_map = _fetch_roles(session, base_url)
                for name, info in role_map.items():
                    if info["reserved"] and not include_reserved:
                        continue
                    results["assets"]["roles"][name] = info["body"]
                    results["meta"]["roles"][name] = {"reserved": info["reserved"]}

        except requests.HTTPError as exc:
            hint = ""
            if asset_type == "users" and exc.response.status_code in (403, 404):
                hint = (f" The user needs `manage_security` (or direct read access to "
                        f"`{security_index}`) to export password hashes.")
            results["errors"].append({
                "type": asset_type,
                "error": f"HTTP {exc.response.status_code} - {exc.response.text[:200]}{hint}",
            })
        except Exception as exc:
            results["errors"].append({"type": asset_type, "error": str(exc)})

    return results


# ── Existing-item check (for restore) ────────────────────────────────────────

def check_security_existing(target_host: str, username: str, password: str,
                            verify_ssl: bool = True) -> dict:
    """Return {"roles": set_of_names, "users": set_of_names} present on the target."""
    session = make_session(username, password, verify_ssl)
    base_url = target_host.rstrip("/")

    existing = {"roles": set(), "users": set()}

    resp = session.get(f"{base_url}/_security/role", timeout=30)
    resp.raise_for_status()
    existing["roles"] = set(resp.json().keys())

    resp = session.get(f"{base_url}/_security/user", timeout=30)
    resp.raise_for_status()
    existing["users"] = set(resp.json().keys())

    return existing


# ── Restore orchestration ────────────────────────────────────────────────────

def run_restore_security_assets(target_host: str, username: str, password: str,
                                asset_files: dict, existing: dict = None,
                                overwrite_actions: dict = None, verify_ssl: bool = True,
                                request_delay: float = 0.05, progress_callback=None) -> dict:
    """
    Restore roles first, then users.

    asset_files:        {asset_type: {name: clean_body}}
    existing:           {"roles": set(...), "users": set(...)} from check_security_existing()
                        (None => nothing is assumed to exist on the target)
    overwrite_actions:  {asset_type: {name: bool}} — only consulted for items already
                        present on the target. Default for a present item = skip.

    Returns:
        {"success": [...], "skipped": [...], "failed": [...]}
    """
    session = make_session(username, password, verify_ssl)
    base_url = target_host.rstrip("/")
    existing = existing or {"roles": set(), "users": set()}
    overwrite_actions = overwrite_actions or {}

    success, skipped, failed = [], [], []

    total = sum(len(items) for items in asset_files.values())
    current = 0

    for asset_type in SECURITY_ASSET_TYPES:
        items = asset_files.get(asset_type, {})
        for name, body in items.items():
            current += 1
            label = SECURITY_ASSET_LABELS.get(asset_type, asset_type)

            exists = name in existing.get(asset_type, set())
            should_overwrite = bool(overwrite_actions.get(asset_type, {}).get(name, False))

            if progress_callback:
                action = "Restoring" if (not exists or should_overwrite) else "Skipping"
                progress_callback(current, total, f"{action} {label}: {name}")

            if exists and not should_overwrite:
                skipped.append({"type": asset_type, "name": name,
                                "reason": "already exists on target (skipped)"})
                continue

            time.sleep(request_delay)
            encoded_name = requests.utils.quote(name, safe="")

            try:
                if asset_type == "roles":
                    url = f"{base_url}/_security/role/{encoded_name}"
                elif asset_type == "users":
                    url = f"{base_url}/_security/user/{encoded_name}"
                else:
                    failed.append({"type": asset_type, "name": name, "reason": "Unknown asset type."})
                    continue

                resp = session.put(url, json=body, timeout=30)
                if resp.status_code in (200, 201):
                    success.append({"type": asset_type, "name": name, "status": "ok"})
                else:
                    failed.append({
                        "type": asset_type, "name": name,
                        "reason": f"HTTP {resp.status_code}: {resp.text[:150]}",
                    })
            except Exception as exc:
                failed.append({"type": asset_type, "name": name, "reason": str(exc)})

    return {"success": success, "skipped": skipped, "failed": failed}


# ── ZIP builder ──────────────────────────────────────────────────────────────

def build_security_assets_zip(results: dict) -> io.BytesIO:
    """Build a ZIP with per-asset JSON files, a reserved-flag meta file, and an Excel summary."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    zip_buffer = io.BytesIO()
    summary_data = []
    meta = results.get("meta", {})

    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for asset_type, items in results["assets"].items():
            for name, body in items.items():
                filename = f"{asset_type}/{safe_filename(name)}.json"
                zf.writestr(filename, json.dumps(body, indent=2))
                reserved = bool((meta.get(asset_type, {}).get(name) or {}).get("reserved"))
                summary_data.append({
                    "Asset Type": SECURITY_ASSET_LABELS.get(asset_type, asset_type),
                    "Name": name,
                    "Reserved": reserved,
                    "File Path": filename,
                })

        if meta and any(meta.values()):
            zf.writestr("security_meta.json", json.dumps(meta, indent=2))

        if summary_data:
            df = pd.DataFrame(summary_data)
            excel_buffer = io.BytesIO()
            with pd.ExcelWriter(excel_buffer, engine="openpyxl") as writer:
                df.to_excel(writer, sheet_name="Summary", index=False)
                ws = writer.sheets["Summary"]
                for col in ws.columns:
                    max_len = max(len(str(cell.value or "")) for cell in col)
                    ws.column_dimensions[col[0].column_letter].width = min(max_len + 4, 60)
            zf.writestr(f"security_assets_summary_{timestamp}.xlsx", excel_buffer.getvalue())

    zip_buffer.seek(0)
    return zip_buffer
