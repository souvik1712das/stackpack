"""
core/cluster_logic.py
======================
Core logic for fetching and restoring Elasticsearch cluster-level configurations.
(Component Templates, Index Templates, ILM, Pipelines, Scripts, SLM, etc.)

UI-agnostic logic usable by the Streamlit app or standalone scripts.
"""

import json
import re
import time
from datetime import datetime
import io
import zipfile

import pandas as pd
import requests
from requests.auth import HTTPBasicAuth

# Order matters during restore (e.g., component templates before index templates)
ASSET_TYPES = [
    "stored_scripts",
    "ilm_policies",
    "slm_policies",
    "enrich_policies",
    "snapshot_repositories",
    "ingest_pipelines",
    "component_templates",
    "legacy_templates",
    "index_templates"
]

ASSET_LABELS = {
    "stored_scripts": "Stored Scripts",
    "ilm_policies": "ILM Policies",
    "slm_policies": "SLM Policies",
    "enrich_policies": "Enrich Policies",
    "snapshot_repositories": "Snapshot Repositories",
    "ingest_pipelines": "Ingest Pipelines",
    "component_templates": "Component Templates",
    "legacy_templates": "Legacy Index Templates",
    "index_templates": "Index Templates"
}

def make_session(username: str, password: str, verify_ssl: bool = True) -> requests.Session:
    session = requests.Session()
    if username and password:
        session.auth = HTTPBasicAuth(username, password)
    session.headers.update({"Content-Type": "application/json"})
    session.verify = verify_ssl
    return session

def safe_filename(name: str) -> str:
    return re.sub(r"[^\w\-.]", "_", name)

def run_fetch_cluster_assets(es_host: str, username: str, password: str, export_types: list = None,
                             verify_ssl: bool = True, request_delay: float = 0.05, progress_callback=None):
    """
    Fetch specified cluster assets and return a structure containing all definitions.
    Returns: {"assets": {asset_type: {name: json_dict}}, "errors": [...]}
    """
    session = make_session(username, password, verify_ssl)
    base_url = es_host.rstrip("/")
    export_types = export_types or ASSET_TYPES
    
    results = {
        "assets": {t: {} for t in export_types},
        "errors": []
    }
    
    total = len(export_types)
    for i, asset_type in enumerate(export_types, 1):
        if progress_callback:
            progress_callback(i, total, f"Fetching {ASSET_LABELS.get(asset_type, asset_type)}...")
            
        time.sleep(request_delay)
        
        try:
            if asset_type == "component_templates":
                resp = session.get(f"{base_url}/_component_template")
                if resp.status_code == 200:
                    for item in resp.json().get("component_templates", []):
                        name = item["name"]
                        if not name.startswith("."):
                            results["assets"][asset_type][name] = item["component_template"]
                            
            elif asset_type == "index_templates":
                resp = session.get(f"{base_url}/_index_template")
                if resp.status_code == 200:
                    for item in resp.json().get("index_templates", []):
                        name = item["name"]
                        if not name.startswith("."):
                            results["assets"][asset_type][name] = item["index_template"]
                            
            elif asset_type == "legacy_templates":
                resp = session.get(f"{base_url}/_template")
                if resp.status_code == 200:
                    for name, template in resp.json().items():
                        if not name.startswith("."):
                            results["assets"][asset_type][name] = template
                            
            elif asset_type == "ilm_policies":
                resp = session.get(f"{base_url}/_ilm/policy")
                if resp.status_code == 200:
                    for name, p_data in resp.json().items():
                        results["assets"][asset_type][name] = {"policy": p_data.get("policy", {})}
                        
            elif asset_type == "enrich_policies":
                resp = session.get(f"{base_url}/_enrich/policy")
                if resp.status_code == 200:
                    for item in resp.json().get("policies", []):
                        config = item.get("config", {})
                        # config looks like {"match": {"name": "p_name", "indices": "..."}}
                        # we need to remove 'name' from the inner block.
                        policy_name = "unknown"
                        inner_type = next(iter(config.keys())) if config else None
                        if inner_type and isinstance(config[inner_type], dict):
                            policy_name = config[inner_type].pop("name", "unknown")
                        if policy_name != "unknown":
                            results["assets"][asset_type][policy_name] = config
                            
            elif asset_type == "ingest_pipelines":
                resp = session.get(f"{base_url}/_ingest/pipeline")
                if resp.status_code == 200:
                    for name, pipeline in resp.json().items():
                        if not name.startswith("xpack"):
                            results["assets"][asset_type][name] = pipeline
                            
            elif asset_type == "stored_scripts":
                resp = session.get(f"{base_url}/_cluster/state/metadata?filter_path=metadata.stored_scripts")
                if resp.status_code == 200:
                    scripts = resp.json().get("metadata", {}).get("stored_scripts", {})
                    for name, script_data in scripts.items():
                        # Sometimes it is nested as {"script": {"lang": "...", "source": "..."}} 
                        # or just {"lang": "...", "source": "..."}. We normalise for PUT /_scripts/{id}.
                        if "script" in script_data:
                            results["assets"][asset_type][name] = script_data
                        else:
                            results["assets"][asset_type][name] = {"script": script_data}
                            
            elif asset_type == "snapshot_repositories":
                resp = session.get(f"{base_url}/_snapshot")
                if resp.status_code == 200:
                    for name, repo in resp.json().items():
                        results["assets"][asset_type][name] = repo
                        
            elif asset_type == "slm_policies":
                resp = session.get(f"{base_url}/_slm/policy")
                if resp.status_code == 200:
                    for name, p_data in resp.json().items():
                        if not name.startswith("."):
                            # The API returns {name: { "version": x, "policy": { ... } } }
                            # We just need the "policy" block for PUT. Wait, SLM API might return the body flat,
                            # actually SLM GET returns { "policy_id": { "version": 1, "modified_date_millis": 123, "policy": {...} } }
                            # The PUT takes the contents of "policy" flat or?
                            # PUT /_slm/policy/my-policy { "schedule": "...", "name": "...", "repository": "..." }
                            # So it's the contents inside "policy" block.
                            policy_body = p_data.get("policy", p_data)
                            results["assets"][asset_type][name] = policy_body
                            
        except requests.HTTPError as exc:
            results["errors"].append({"type": asset_type, "error": f"HTTP {exc.response.status_code} - {exc.response.text[:100]}"})
        except Exception as exc:
            results["errors"].append({"type": asset_type, "error": str(exc)})
            
    return results

def run_restore_cluster_assets(target_host: str, username: str, password: str, asset_files: dict,
                               verify_ssl: bool = True, request_delay: float = 0.05, progress_callback=None):
    """
    Restore cluster assets in proper dependency order.
    asset_files expected format: { asset_type: { name: json_dict } }
    """
    session = make_session(username, password, verify_ssl)
    base_url = target_host.rstrip("/")
    
    success, failed = [], []
    
    # Count total tasks
    total = sum(len(items) for items in asset_files.values())
    current = 0
    
    # Process in correct order
    for asset_type in ASSET_TYPES:
        items = asset_files.get(asset_type, {})
        for name, body in items.items():
            current += 1
            if progress_callback:
                progress_callback(current, total, f"Restoring {ASSET_LABELS.get(asset_type, asset_type)}: {name}")
                
            try:
                time.sleep(request_delay)
                encoded_name = requests.utils.quote(name, safe='')
                
                if asset_type == "component_templates":
                    url = f"{base_url}/_component_template/{encoded_name}"
                elif asset_type == "index_templates":
                    url = f"{base_url}/_index_template/{encoded_name}"
                elif asset_type == "legacy_templates":
                    url = f"{base_url}/_template/{encoded_name}"
                elif asset_type == "ilm_policies":
                    url = f"{base_url}/_ilm/policy/{encoded_name}"
                elif asset_type == "enrich_policies":
                    url = f"{base_url}/_enrich/policy/{encoded_name}"
                elif asset_type == "ingest_pipelines":
                    url = f"{base_url}/_ingest/pipeline/{encoded_name}"
                elif asset_type == "stored_scripts":
                    url = f"{base_url}/_scripts/{encoded_name}"
                elif asset_type == "snapshot_repositories":
                    url = f"{base_url}/_snapshot/{encoded_name}"
                elif asset_type == "slm_policies":
                    url = f"{base_url}/_slm/policy/{encoded_name}"
                else:
                    url = None
                    
                if url:
                    resp = session.put(url, json=body, timeout=30)
                    if resp.status_code in (200, 201):
                        success.append({"type": asset_type, "name": name, "status": "ok"})
                    else:
                        failed.append({"type": asset_type, "name": name, "reason": f"HTTP {resp.status_code}: {resp.text[:150]}"})
                else:
                    failed.append({"type": asset_type, "name": name, "reason": "Unknown asset type mapping."})
                    
            except Exception as exc:
                failed.append({"type": asset_type, "name": name, "reason": str(exc)})
                
    return {"success": success, "failed": failed}

def build_cluster_assets_zip(results: dict) -> io.BytesIO:
    """Builds a ZIP containing individual JSON files per asset and an Excel summary."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    zip_buffer = io.BytesIO()
    
    # Prepare summary data for Excel
    summary_data = []
    
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for asset_type, items in results["assets"].items():
            for name, body in items.items():
                folder = asset_type
                filename = f"{folder}/{safe_filename(name)}.json"
                zf.writestr(filename, json.dumps(body, indent=2))
                summary_data.append({
                    "Asset Type": ASSET_LABELS.get(asset_type, asset_type),
                    "Name": name,
                    "File Path": filename
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
            
            zf.writestr(f"cluster_assets_summary_{timestamp}.xlsx", excel_buffer.getvalue())
            
    zip_buffer.seek(0)
    return zip_buffer
