"""
core/watcher_logic.py
======================
Core logic for fetching Elasticsearch Watcher scripts.
Refactored from the original CLI script into callable functions so the
Streamlit app (or any other UI/script) can drive it with progress callbacks.

No Streamlit imports here — this module is UI-agnostic by design, so it can
also be reused from a plain Python script or notebook.
"""

import json
import re
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests
from requests.auth import HTTPBasicAuth


# ── Session ────────────────────────────────────────────────────────────────

def make_session(username: str, password: str, verify_ssl: bool = True) -> requests.Session:
    session = requests.Session()
    session.auth = HTTPBasicAuth(username, password)
    session.headers.update({"Content-Type": "application/json"})
    session.verify = verify_ssl
    return session


# ── Script extraction helpers ─────────────────────────────────────────────

def _extract_script_source(script_block) -> str:
    """Normalise a script block (string, source/inline dict, or stored-id dict)."""
    if not script_block:
        return ""
    if isinstance(script_block, str):
        return script_block
    if isinstance(script_block, dict):
        if "source" in script_block:
            return script_block["source"]
        if "inline" in script_block:
            return script_block["inline"]
        if "id" in script_block:
            return f"[stored script id: {script_block['id']}]"
    return json.dumps(script_block, indent=2)


def extract_all_scripts(watch: dict) -> dict:
    """Walk the watch definition and pull out every embedded script, labelled by location."""
    scripts = {}

    condition = watch.get("condition", {})
    if "script" in condition:
        src = _extract_script_source(condition["script"].get("script", condition["script"]))
        if src:
            scripts["condition_script"] = src

    transform = watch.get("transform", {})
    if "script" in transform:
        src = _extract_script_source(transform["script"].get("script", transform["script"]))
        if src:
            scripts["transform_script"] = src

    for action_name, action_body in watch.get("actions", {}).items():
        if not isinstance(action_body, dict):
            continue
        if "script" in action_body:
            src = _extract_script_source(action_body["script"].get("script", action_body["script"]))
            if src:
                scripts[f"action_script.{action_name}"] = src
        act_transform = action_body.get("transform", {})
        if "script" in act_transform:
            src = _extract_script_source(act_transform["script"].get("script", act_transform["script"]))
            if src:
                scripts[f"action_transform_script.{action_name}"] = src

    input_block = watch.get("input", {})
    search_body = input_block.get("search", {}).get("request", {}).get("body", {})
    for field_name, field_def in search_body.get("script_fields", {}).items():
        src = _extract_script_source(field_def.get("script", {}))
        if src:
            scripts[f"input_script_field.{field_name}"] = src

    return scripts


def extract_recipients(actions: dict) -> str:
    """Pull recipient/destination info from email, slack, pagerduty, webhook, jira actions."""
    lines = []
    for action_name, action_body in actions.items():
        if not isinstance(action_body, dict):
            continue

        if "email" in action_body:
            email = action_body["email"]
            for field in ("to", "cc", "bcc"):
                val = email.get(field)
                if val:
                    recipients = val if isinstance(val, list) else [val]
                    lines.append(f"[email/{action_name}] {field}: {', '.join(recipients)}")

        if "slack" in action_body:
            slack = action_body["slack"]
            message = slack.get("message", slack)
            channel = message.get("to") or message.get("channel") or slack.get("to")
            if channel:
                channels = channel if isinstance(channel, list) else [channel]
                lines.append(f"[slack/{action_name}] channel: {', '.join(channels)}")

        if "pagerduty" in action_body:
            account = action_body["pagerduty"].get("account", "")
            lines.append(f"[pagerduty/{action_name}] account: {account}")

        if "webhook" in action_body:
            wh = action_body["webhook"]
            url = f"{wh.get('scheme','https')}://{wh.get('host','')}{wh.get('path','')}"
            lines.append(f"[webhook/{action_name}] url: {url}")

        if "jira" in action_body:
            jira = action_body["jira"]
            project = jira.get("issue", {}).get("project", {}).get("key", "")
            lines.append(f"[jira/{action_name}] project: {project}")

    return "\n".join(lines) if lines else ""


# ── Fetch helpers ────────────────────────────────────────────────────────

def get_all_watcher_ids(session: requests.Session, base_url: str,
                         scroll_ttl: str = "1m", page_size: int = 100,
                         request_delay: float = 0.05) -> list:
    """Scroll through .watches index and return every watcher ID."""
    watcher_ids = []
    base_url = base_url.rstrip("/")

    url = f"{base_url}/.watches/_search?scroll={scroll_ttl}"
    resp = session.post(url, json={"size": page_size, "_source": False})
    resp.raise_for_status()
    data = resp.json()

    scroll_id = data.get("_scroll_id")
    hits = data.get("hits", {}).get("hits", [])
    watcher_ids.extend(hit["_id"] for hit in hits)

    while hits:
        time.sleep(request_delay)
        resp = session.post(f"{base_url}/_search/scroll",
                             json={"scroll": scroll_ttl, "scroll_id": scroll_id})
        resp.raise_for_status()
        data = resp.json()
        scroll_id = data.get("_scroll_id")
        hits = data.get("hits", {}).get("hits", [])
        watcher_ids.extend(hit["_id"] for hit in hits)

    try:
        session.delete(f"{base_url}/_search/scroll", json={"scroll_id": scroll_id})
    except Exception:
        pass

    return watcher_ids


def get_watcher_detail(session: requests.Session, base_url: str, watcher_id: str):
    """Fetch a single watcher and return a fully flattened record dict, or None if 404."""
    base_url = base_url.rstrip("/")
    url = f"{base_url}/_watcher/watch/{watcher_id}"
    resp = session.get(url)

    if resp.status_code == 404:
        return None
    resp.raise_for_status()

    data = resp.json()
    watch = data.get("watch", {})

    schedule = watch.get("trigger", {}).get("schedule", {})
    schedule_type = next(iter(schedule), "unknown")
    schedule_val = schedule.get(schedule_type, "")
    if isinstance(schedule_val, (dict, list)):
        schedule_val = json.dumps(schedule_val)

    input_block = watch.get("input", {})
    input_type = next(iter(input_block), "unknown")
    if input_type == "search":
        input_query = json.dumps(input_block["search"].get("request", {}).get("body", {}), indent=2)
    elif input_type == "chain":
        input_query = json.dumps(input_block["chain"].get("inputs", []), indent=2)
    else:
        input_query = json.dumps(input_block.get(input_type, {}), indent=2)

    condition_block = watch.get("condition", {})
    condition_type = next(iter(condition_block), "unknown")
    condition_detail = json.dumps(condition_block.get(condition_type, {}), indent=2)

    actions = watch.get("actions", {})
    action_names = list(actions.keys())
    action_types = list({next(iter(v)) for v in actions.values() if isinstance(v, dict)})
    actions_raw = json.dumps(actions, indent=2)

    all_scripts = extract_all_scripts(watch)
    condition_script = all_scripts.get("condition_script", "")
    transform_script = all_scripts.get("transform_script", "")
    other_scripts = {k: v for k, v in all_scripts.items() if k not in ("condition_script", "transform_script")}
    other_scripts_str = "\n\n".join(f"# {k}\n{v}" for k, v in other_scripts.items())

    recipients = extract_recipients(actions)
    transform_block = watch.get("transform", {})
    transform_raw = json.dumps(transform_block, indent=2) if transform_block else ""
    full_watch_json = json.dumps(watch, indent=2)

    return {
        "watcher_id": watcher_id,
        "status": data.get("status", {}).get("state", {}).get("active", "unknown"),
        "schedule_type": schedule_type,
        "schedule_value": schedule_val,
        "input_type": input_type,
        "condition_type": condition_type,
        "action_names": ", ".join(action_names),
        "action_types": ", ".join(action_types),
        "recipients": recipients,
        "input_query": input_query,
        "condition_detail": condition_detail,
        "condition_script": condition_script,
        "transform_raw": transform_raw,
        "transform_script": transform_script,
        "action_scripts": other_scripts_str,
        "actions_raw": actions_raw,
        "metadata": json.dumps(watch.get("metadata", {}), indent=2),
        "full_watch_json": full_watch_json,
    }


# ── Output helpers ──────────────────────────────────────────────────────

def safe_filename(name: str) -> str:
    return re.sub(r"[^\w\-.]", "_", name)


def build_txt_content(record: dict) -> str:
    """Build the .txt file content (header comments + restore-ready JSON body)."""
    return (
        f"# Watcher ID : {record['watcher_id']}\n"
        f"# Status     : {record['status']}\n"
        f"# Schedule   : {record['schedule_type']} = {record['schedule_value']}\n"
        f"# Exported   : {datetime.now().isoformat()}\n"
        f"#\n"
        f"# To restore : PUT /_watcher/watch/{record['watcher_id']}\n"
        f"# Body       : (the JSON below)\n"
        f"# ---------------------------------------------------------------\n\n"
        f"{record['full_watch_json']}\n"
    )


def build_excel_bytes(records: list) -> bytes:
    """Build the Summary + Full Detail Excel workbook and return raw bytes."""
    import io
    df = pd.DataFrame(records)

    summary_cols = ["watcher_id", "status", "schedule_type", "schedule_value",
                     "input_type", "condition_type", "action_names", "action_types", "recipients"]
    full_cols = ["watcher_id", "status", "schedule_type", "schedule_value",
                 "input_type", "input_query", "condition_type", "condition_detail",
                 "condition_script", "transform_raw", "transform_script",
                 "action_names", "action_types", "action_scripts", "actions_raw",
                 "recipients", "metadata", "full_watch_json"]

    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        df[summary_cols].to_excel(writer, sheet_name="Summary", index=False)
        df[full_cols].to_excel(writer, sheet_name="Full Detail", index=False)

        ws = writer.sheets["Summary"]
        for col in ws.columns:
            max_len = max(len(str(cell.value or "")) for cell in col)
            ws.column_dimensions[col[0].column_letter].width = min(max_len + 4, 60)

        for sheet_name in ("Summary", "Full Detail"):
            writer.sheets[sheet_name].freeze_panes = "A2"

    buffer.seek(0)
    return buffer.read()


# ── Orchestration (called by Streamlit app) ────────────────────────────────

def run_fetch(es_host: str, username: str, password: str, verify_ssl: bool = True,
              request_delay: float = 0.05, progress_callback=None):
    """
    Full fetch workflow. progress_callback(current, total, message) is called
    after each watcher so the caller (Streamlit) can update a progress bar.

    Returns dict: {
        "records": [...], "errors": [...], "total_found": int
    }
    """
    session = make_session(username, password, verify_ssl)
    base_url = es_host.rstrip("/")

    if progress_callback:
        progress_callback(0, 1, "Discovering watcher IDs...")

    watcher_ids = get_all_watcher_ids(session, base_url, request_delay=request_delay)
    total = len(watcher_ids)

    records, errors = [], []

    for i, wid in enumerate(watcher_ids, 1):
        try:
            time.sleep(request_delay)
            record = get_watcher_detail(session, base_url, wid)
            if record:
                records.append(record)
            else:
                errors.append({"id": wid, "reason": "not found (404)"})
        except requests.HTTPError as exc:
            errors.append({"id": wid, "reason": f"HTTP {exc.response.status_code}"})
        except Exception as exc:
            errors.append({"id": wid, "reason": str(exc)})

        if progress_callback:
            progress_callback(i, total, f"Fetched {wid}")

    return {"records": records, "errors": errors, "total_found": total}


def run_restore(target_host: str, username: str, password: str, watcher_files: dict,
                 verify_ssl: bool = True, request_delay: float = 0.05, progress_callback=None):
    """
    Restore watchers into target_host.
    watcher_files: dict of {watcher_id: full_watch_json_string}
    Returns dict: {"success": [...], "failed": [...]}
    """
    session = make_session(username, password, verify_ssl)
    base_url = target_host.rstrip("/")

    success, failed = [], []
    total = len(watcher_files)

    for i, (watcher_id, watch_json_str) in enumerate(watcher_files.items(), 1):
        try:
            watch_dict = json.loads(watch_json_str)
            url = f"{base_url}/_watcher/watch/{requests.utils.quote(watcher_id, safe='')}"
            resp = session.put(url, json=watch_dict, timeout=30)
            if resp.status_code in (200, 201):
                action = "created" if resp.json().get("created") else "updated"
                success.append({"id": watcher_id, "action": action})
            else:
                failed.append({"id": watcher_id, "reason": f"HTTP {resp.status_code}: {resp.text[:150]}"})
        except json.JSONDecodeError as exc:
            failed.append({"id": watcher_id, "reason": f"Invalid JSON: {exc}"})
        except Exception as exc:
            failed.append({"id": watcher_id, "reason": str(exc)})

        time.sleep(request_delay)
        if progress_callback:
            progress_callback(i, total, f"Restored {watcher_id}")

    return {"success": success, "failed": failed}


def check_cluster_health(host: str, username: str, password: str, verify_ssl: bool = True) -> dict:
    """Quick connectivity + health check. Returns the parsed /_cluster/health response."""
    session = make_session(username, password, verify_ssl)
    resp = session.get(f"{host.rstrip('/')}/_cluster/health", timeout=10)
    resp.raise_for_status()
    return resp.json()
