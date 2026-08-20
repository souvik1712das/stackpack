"""
ml_logic.py
============
Core logic for fetching and restoring Elasticsearch Machine Learning (ML)
configurations.

Supports:
  - Anomaly Detection Jobs  (_ml/anomaly_detectors)
  - Datafeeds               (_ml/datafeeds)
  - Data Frame Analytics    (_ml/data_frame/analytics)
  - Calendars               (_ml/calendars)
  - Filters                 (_ml/filters)

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

# ── Runtime keys stripped before export so the JSON is "clean" for restore ──
_ANOMALY_STRIP_KEYS = {
    "job_version", "create_time", "finished_time", "model_snapshot_id",
    "model_snapshot_min_version", "established_model_memory", "state",
    "model_plot_config", "forecasts_stats", "node", "assignment_explanation",
    "open_time", "timing_stats", "datafeed_config",
}
_DATAFEED_STRIP_KEYS = {
    "state", "node", "assignment_explanation", "timing_stats",
}
_DFA_STRIP_KEYS = {
    "create_time", "version", "state", "data_counts", "model_memory_status",
    "model_size_stats", "assignment_explanation", "node", "progress",
}

# Dependency-safe restore order (Filters/Calendars before detectors, detectors before datafeeds)
ML_ASSET_TYPES = [
    "ml_filters",
    "ml_calendars",
    "ml_anomaly_detectors",
    "ml_datafeeds",
    "ml_data_frame_analytics",
]

ML_ASSET_LABELS = {
    "ml_filters": "ML Filters",
    "ml_calendars": "ML Calendars",
    "ml_anomaly_detectors": "Anomaly Detection Jobs",
    "ml_datafeeds": "Datafeeds",
    "ml_data_frame_analytics": "Data Frame Analytics Jobs",
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


def _strip_keys(d: dict, keys: set) -> dict:
    """Return a shallow copy of d with the specified keys removed."""
    return {k: v for k, v in d.items() if k not in keys}


# ── Fetch ─────────────────────────────────────────────────────────────────────

def run_fetch_ml_assets(
    es_host: str,
    username: str,
    password: str,
    export_types: list = None,
    verify_ssl: bool = True,
    request_delay: float = 0.05,
    progress_callback=None,
) -> dict:
    """
    Fetch ML assets from the cluster and return clean, restore-ready definitions.

    Returns:
        {
            "assets": { asset_type: { name: json_dict } },
            "errors": [...]
        }
    """
    session = make_session(username, password, verify_ssl)
    base_url = es_host.rstrip("/")
    export_types = export_types or ML_ASSET_TYPES

    results = {
        "assets": {t: {} for t in export_types},
        "errors": [],
    }

    total = len(export_types)
    for i, asset_type in enumerate(export_types, 1):
        if progress_callback:
            progress_callback(i, total, f"Fetching {ML_ASSET_LABELS.get(asset_type, asset_type)}...")

        time.sleep(request_delay)

        try:
            if asset_type == "ml_anomaly_detectors":
                resp = session.get(f"{base_url}/_ml/anomaly_detectors", timeout=30)
                if resp.status_code == 200:
                    for job in resp.json().get("jobs", []):
                        name = job.get("job_id", "unknown")
                        clean = _strip_keys(job, _ANOMALY_STRIP_KEYS)
                        # Remove job_id from body — it goes in the URL during PUT
                        clean.pop("job_id", None)
                        results["assets"][asset_type][name] = clean

            elif asset_type == "ml_datafeeds":
                resp = session.get(f"{base_url}/_ml/datafeeds", timeout=30)
                if resp.status_code == 200:
                    for feed in resp.json().get("datafeeds", []):
                        name = feed.get("datafeed_id", "unknown")
                        clean = _strip_keys(feed, _DATAFEED_STRIP_KEYS)
                        clean.pop("datafeed_id", None)
                        results["assets"][asset_type][name] = clean

            elif asset_type == "ml_data_frame_analytics":
                resp = session.get(f"{base_url}/_ml/data_frame/analytics", timeout=30)
                if resp.status_code == 200:
                    for job in resp.json().get("data_frame_analytics", []):
                        name = job.get("id", "unknown")
                        clean = _strip_keys(job, _DFA_STRIP_KEYS)
                        clean.pop("id", None)
                        results["assets"][asset_type][name] = clean

            elif asset_type == "ml_calendars":
                resp = session.get(f"{base_url}/_ml/calendars", timeout=30)
                if resp.status_code == 200:
                    for cal in resp.json().get("calendars", []):
                        name = cal.get("calendar_id", "unknown")
                        body = {k: v for k, v in cal.items() if k != "calendar_id"}
                        results["assets"][asset_type][name] = body

            elif asset_type == "ml_filters":
                resp = session.get(f"{base_url}/_ml/filters", timeout=30)
                if resp.status_code == 200:
                    for flt in resp.json().get("filters", []):
                        name = flt.get("filter_id", "unknown")
                        body = {k: v for k, v in flt.items() if k != "filter_id"}
                        results["assets"][asset_type][name] = body

        except requests.HTTPError as exc:
            results["errors"].append({
                "type": asset_type,
                "error": f"HTTP {exc.response.status_code} - {exc.response.text[:100]}",
            })
        except Exception as exc:
            results["errors"].append({"type": asset_type, "error": str(exc)})

    return results


# ── Restore ───────────────────────────────────────────────────────────────────

def run_restore_ml_assets(
    target_host: str,
    username: str,
    password: str,
    asset_files: dict,
    verify_ssl: bool = True,
    request_delay: float = 0.05,
    progress_callback=None,
) -> dict:
    """
    Restore ML assets in the correct dependency order.

    asset_files format: { asset_type: { name: json_dict } }
    """
    session = make_session(username, password, verify_ssl)
    base_url = target_host.rstrip("/")

    success, failed = [], []
    total = sum(len(items) for items in asset_files.values())
    current = 0

    for asset_type in ML_ASSET_TYPES:
        items = asset_files.get(asset_type, {})
        for name, body in items.items():
            current += 1
            label = ML_ASSET_LABELS.get(asset_type, asset_type)
            if progress_callback:
                progress_callback(current, total, f"Restoring {label}: {name}")

            time.sleep(request_delay)
            encoded_name = requests.utils.quote(name, safe="")

            try:
                if asset_type == "ml_anomaly_detectors":
                    url = f"{base_url}/_ml/anomaly_detectors/{encoded_name}"
                elif asset_type == "ml_datafeeds":
                    url = f"{base_url}/_ml/datafeeds/{encoded_name}"
                elif asset_type == "ml_data_frame_analytics":
                    url = f"{base_url}/_ml/data_frame/analytics/{encoded_name}"
                elif asset_type == "ml_calendars":
                    url = f"{base_url}/_ml/calendars/{encoded_name}"
                elif asset_type == "ml_filters":
                    url = f"{base_url}/_ml/filters/{encoded_name}"
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

    return {"success": success, "failed": failed}


# ── ZIP builder ───────────────────────────────────────────────────────────────

def build_ml_assets_zip(results: dict) -> io.BytesIO:
    """Builds a ZIP with individual JSON files per ML asset and an Excel summary."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    zip_buffer = io.BytesIO()
    summary_data = []

    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for asset_type, items in results["assets"].items():
            for name, body in items.items():
                filename = f"{asset_type}/{safe_filename(name)}.json"
                zf.writestr(filename, json.dumps(body, indent=2))
                summary_data.append({
                    "Asset Type": ML_ASSET_LABELS.get(asset_type, asset_type),
                    "Name": name,
                    "File Path": filename,
                })

        if summary_data:
            df = pd.DataFrame(summary_data)
            excel_buffer = io.BytesIO()
            with pd.ExcelWriter(excel_buffer, engine="openpyxl") as writer:
                df.to_excel(writer, sheet_name="Summary", index=False)
                ws = writer.sheets["Summary"]
                for col in ws.columns:
                    max_len = max(len(str(cell.value or "")) for cell in col)
                    ws.column_dimensions[col[0].column_letter].width = min(max_len + 4, 60)
            zf.writestr(f"ml_assets_summary_{timestamp}.xlsx", excel_buffer.getvalue())

    zip_buffer.seek(0)
    return zip_buffer
