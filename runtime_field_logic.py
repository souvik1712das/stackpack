"""
runtime_field_logic.py
=======================
Core logic for scanning Kibana scripted fields and migrating them to
data-view runtime fields (Kibana Data Views API, e.g. 8.x / 9.x).

- Scan  : read-only — reads scripted fields from saved-object index-patterns
          and reports them (with space / data view / type / script) into an
          Excel workbook.
- Create: POST /api/data_views/data_view/{id}/runtime_field  (reversible)
- Delete: DELETE /api/data_views/data_view/{id}/scripted_field/{name}
          (destructive — only ever called explicitly, after a successful create)
- Test  : optional Painless syntax check via ES _scripts/painless/_execute

UI-agnostic — usable by the Streamlit app or standalone scripts.
"""

import io
import json
import re
import time
from datetime import datetime

import pandas as pd
import requests
from requests.auth import HTTPBasicAuth

# Scripted-field types (legacy) mapped to sensible runtime-field defaults.
SCRIPTED_TO_RUNTIME_TYPE = {
    "string": "keyword",
    "number": "double",
    "boolean": "boolean",
    "date": "date",
    "ip": "ip",
    "geo_point": "geo_point",
}


def make_session(username: str, password: str, verify_ssl: bool = True) -> requests.Session:
    session = requests.Session()
    session.auth = HTTPBasicAuth(username, password)
    session.headers.update({"kbn-xsrf": "true"})
    session.verify = verify_ssl
    return session


def safe_filename(name: str) -> str:
    return re.sub(r"[^\w\-.]", "_", name)


def _space_prefix(space_id: str) -> str:
    return "" if space_id == "default" else f"/s/{space_id}"


def get_all_spaces(session: requests.Session, base_url: str) -> list:
    base_url = base_url.rstrip("/")
    resp = session.get(f"{base_url}/api/spaces/space", timeout=30)
    resp.raise_for_status()
    spaces = resp.json()
    ids = {s["id"] for s in spaces}
    if "default" not in ids:
        spaces.insert(0, {"id": "default", "name": "Default", "description": ""})
    return spaces


# ── Parsing helpers ──────────────────────────────────────────────────────────

def _parse_scripted_fields(attributes: dict) -> list:
    """
    Extract scripted fields from a data-view saved object.

    Scripted fields are persisted in `attributes["fields"]` — a JSON-encoded
    array (or map) of field specs where each scripted entry has
    `"scripted": true`. A separate `attributes["scriptedFields"]` attribute
    exists on some legacy installs, so both are read and de-duplicated.
    """
    found = []
    # `scriptedFields` (legacy) contains scripted fields only, by definition.
    for key, require_flag in (("scriptedFields", False), ("fields", True)):
        raw = attributes.get(key)
        if not raw:
            continue
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except json.JSONDecodeError:
                continue
        if isinstance(raw, list):
            candidates = raw
        elif isinstance(raw, dict):
            candidates = raw.values()
        else:
            continue
        for f in candidates:
            if not isinstance(f, dict) or not f.get("name"):
                continue
            if require_flag and not f.get("scripted"):
                continue
            found.append(f)

    # A data view migrated from an old format can carry the same scripted field
    # in both attributes; field names are unique per data view, so de-dup.
    seen = set()
    unique = []
    for f in found:
        name = f["name"]
        if name in seen:
            continue
        seen.add(name)
        unique.append(f)
    return unique


def _parse_runtime_field_map(attributes: dict) -> dict:
    """attributes["runtimeFieldMap"] is stored as a JSON-encoded string."""
    raw = attributes.get("runtimeFieldMap")
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}
    return {}


# ── Scan (read-only) ─────────────────────────────────────────────────────────

def scan_scripted_fields(kibana_host: str, username: str, password: str,
                         verify_ssl: bool = True, request_delay: float = 0.15,
                         progress_callback=None) -> dict:
    """
    Scan every space for index-pattern/data-view saved objects and extract all
    scripted fields (with space / data view / type / script) plus whether a
    same-name runtime field already exists.

    Returns:
        {
            "records": [
                {space, data_view_id, data_view_title, field_name,
                 field_type, lang, script, has_runtime_field}
            ],
            "data_view_count": int,   # data views scanned (diagnostics)
            "errors": [...]
        }
    """
    session = make_session(username, password, verify_ssl)
    base_url = kibana_host.rstrip("/")

    if progress_callback:
        progress_callback(0, 1, "Discovering spaces...")

    spaces = get_all_spaces(session, base_url)
    total = len(spaces)

    records, errors = [], []
    data_view_count = 0

    # Data views are saved as "index-pattern" on most versions; newer installs
    # may expose them as "data-view", so fall back if none found.
    SO_TYPES = ("index-pattern", "data-view")

    for i, space in enumerate(spaces, 1):
        space_id = space["id"]
        if progress_callback:
            progress_callback(i, total, f"Scanning space '{space_id}'")

        prefix = _space_prefix(space_id)
        try:
            time.sleep(request_delay)
            objects = []
            for so_type in SO_TYPES:
                url = (f"{base_url}{prefix}/api/saved_objects/_find"
                       f"?type={so_type}&per_page=1000")
                resp = session.get(url, timeout=60)
                resp.raise_for_status()
                data = resp.json()
                objects = data.get("saved_objects", [])
                if objects:
                    break

            data_view_count += len(objects)

            for obj in objects:
                attrs = obj.get("attributes") or {}
                dv_id = obj.get("id", "")
                dv_title = attrs.get("title", attrs.get("name", dv_id))
                runtime_map = _parse_runtime_field_map(attrs)

                for sf in _parse_scripted_fields(attrs):
                    name = sf["name"]
                    records.append({
                        "space": space_id,
                        "data_view_id": dv_id,
                        "data_view_title": dv_title,
                        "field_name": name,
                        "field_type": sf.get("type", ""),
                        "lang": sf.get("lang", "painless"),
                        "script": sf.get("script", ""),
                        "has_runtime_field": name in runtime_map,
                    })
        except requests.HTTPError as exc:
            errors.append({
                "space": space_id,
                "error": f"HTTP {exc.response.status_code} - {exc.response.text[:150]}",
            })
        except Exception as exc:
            errors.append({"space": space_id, "error": str(exc)})

    return {"records": records, "data_view_count": data_view_count, "errors": errors}


# ── Report builder ───────────────────────────────────────────────────────────

def build_report_excel_bytes(records: list, status_map: dict = None) -> bytes:
    """
    Build the Excel inventory. status_map keys are "space|data_view_id|field_name"
    mapping to {"status": str, "target_name": str}.
    """
    status_map = status_map or {}

    rows = []
    for r in records:
        key = f"{r['space']}|{r['data_view_id']}|{r['field_name']}"
        meta = status_map.get(key, {})
        rows.append({
            "Space": r["space"],
            "Data View Title": r["data_view_title"],
            "Data View ID": r["data_view_id"],
            "Field Name": r["field_name"],
            "Target Field Name": meta.get("target_name", r["field_name"]),
            "Type": r["field_type"],
            "Lang": r["lang"],
            "Script": r["script"],
            "Runtime Exists": r.get("has_runtime_field", False),
            "Status": meta.get("status", "Pending"),
        })

    df = pd.DataFrame(rows)
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="Scripted Fields", index=False)
        ws = writer.sheets["Scripted Fields"]
        ws.freeze_panes = "A2"
        for col in ws.columns:
            max_len = max(len(str(cell.value or "")) for cell in col)
            ws.column_dimensions[col[0].column_letter].width = min(max_len + 4, 60)
    buffer.seek(0)
    return buffer.read()


# ── Write helpers ────────────────────────────────────────────────────────────

def create_runtime_field(session: requests.Session, base_url: str, space_id: str,
                         data_view_id: str, target_name: str, field_type: str,
                         script: str) -> dict:
    """Create a data-view runtime field. Returns {"ok": bool, "error": str|None}."""
    base_url = base_url.rstrip("/")
    prefix = _space_prefix(space_id)
    url = (f"{base_url}{prefix}/api/data_views/data_view/"
           f"{requests.utils.quote(data_view_id, safe='')}/runtime_field")
    body = {
        "name": target_name,
        "runtimeField": {"type": field_type, "script": {"source": script}},
    }
    try:
        resp = session.post(url, json=body, headers={"Content-Type": "application/json"}, timeout=30)
        if resp.status_code in (200, 201):
            return {"ok": True, "error": None}
        return {"ok": False, "error": f"HTTP {resp.status_code}: {resp.text[:150]}"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def delete_scripted_field(session: requests.Session, base_url: str, space_id: str,
                          data_view_id: str, field_name: str) -> dict:
    """Delete a scripted field. Returns {"ok": bool, "error": str|None}."""
    base_url = base_url.rstrip("/")
    prefix = _space_prefix(space_id)
    url = (f"{base_url}{prefix}/api/data_views/data_view/"
           f"{requests.utils.quote(data_view_id, safe='')}/scripted_field/"
           f"{requests.utils.quote(field_name, safe='')}")
    try:
        resp = session.delete(url, headers={"Content-Type": "application/json"}, timeout=30)
        if resp.status_code in (200, 201):
            return {"ok": True, "error": None}
        return {"ok": False, "error": f"HTTP {resp.status_code}: {resp.text[:150]}"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


# ── Orchestration ────────────────────────────────────────────────────────────

def run_migrate(kibana_host: str, username: str, password: str, items: list,
                delete_after: bool = False, verify_ssl: bool = True,
                request_delay: float = 0.2, progress_callback=None) -> dict:
    """
    items: list of dicts {space, data_view_id, field_name, target_name,
                          field_type, script}
    Creates a runtime field for every item. If delete_after is True, the
    original scripted field is deleted — but ONLY for items whose create
    succeeded (create-before-delete is enforced here).

    Returns {"created": [...], "deleted": [...], "failed": [...]}
    """
    session = make_session(username, password, verify_ssl)
    base_url = kibana_host.rstrip("/")

    created, deleted, failed = [], [], []
    total = len(items)
    current = 0

    for item in items:
        current += 1
        space_id = item["space"]
        dv_id = item["data_view_id"]
        field_name = item["field_name"]
        target_name = item.get("target_name", field_name)
        label = f"{space_id}/{dv_id}/{field_name}"

        if progress_callback:
            progress_callback(current, total, f"Creating runtime field: {label}")
        time.sleep(request_delay)

        create = create_runtime_field(session, base_url, space_id, dv_id,
                                      target_name, item["field_type"], item["script"])
        if not create["ok"]:
            failed.append({"space": space_id, "data_view_id": dv_id, "field_name": field_name,
                           "target_name": target_name, "action": "create", "reason": create["error"]})
            continue

        created.append({"space": space_id, "data_view_id": dv_id, "field_name": field_name,
                        "target_name": target_name})

        if delete_after:
            if progress_callback:
                progress_callback(current, total, f"Deleting scripted field: {label}")
            time.sleep(request_delay)
            delete = delete_scripted_field(session, base_url, space_id, dv_id, field_name)
            if delete["ok"]:
                deleted.append({"space": space_id, "data_view_id": dv_id, "field_name": field_name})
            else:
                failed.append({"space": space_id, "data_view_id": dv_id, "field_name": field_name,
                               "target_name": target_name, "action": "delete", "reason": delete["error"]})

    return {"created": created, "deleted": deleted, "failed": failed}


def run_delete_scripted_fields(kibana_host: str, username: str, password: str,
                               items: list, verify_ssl: bool = True,
                               request_delay: float = 0.2, progress_callback=None) -> dict:
    """
    items: list of dicts {space, data_view_id, field_name}
    Delete-only orchestration for scripted fields whose runtime field already
    exists (created this session or pre-existing on scan).

    Returns {"deleted": [...], "failed": [...]}
    """
    session = make_session(username, password, verify_ssl)
    base_url = kibana_host.rstrip("/")

    deleted, failed = [], []
    total = len(items)
    current = 0

    for item in items:
        current += 1
        space_id, dv_id, field_name = item["space"], item["data_view_id"], item["field_name"]
        label = f"{space_id}/{dv_id}/{field_name}"

        if progress_callback:
            progress_callback(current, total, f"Deleting scripted field: {label}")
        time.sleep(request_delay)

        delete = delete_scripted_field(session, base_url, space_id, dv_id, field_name)
        if delete["ok"]:
            deleted.append({"space": space_id, "data_view_id": dv_id, "field_name": field_name})
        else:
            failed.append({"space": space_id, "data_view_id": dv_id, "field_name": field_name,
                           "reason": delete["error"]})

    return {"deleted": deleted, "failed": failed}


# ── Optional script validation ───────────────────────────────────────────────

# ES metadata fields that cannot appear inside a `context_setup.document` body.
METADATA_FIELDS = {
    "_id", "_index", "_type", "_score", "_version", "_routing",
    "_seq_no", "_primary_term", "_shard", "_node", "_source", "_ignored",
}


def _is_metadata_only(doc) -> bool:
    return (isinstance(doc, dict) and bool(doc)
            and set(doc).issubset(METADATA_FIELDS))


def _resolve_sample_document(session, es_host: str, index_pattern: str,
                             sample_document) -> tuple:
    """
    Resolve the document + concrete index to test a script against.

    Returns (document_dict, concrete_index, error). On failure `error` is set
    and document/index are None.
    """
    es_host = es_host.rstrip("/")
    pattern_q = requests.utils.quote(index_pattern, safe="")

    def _search(body):
        resp = session.post(
            f"{es_host}/{pattern_q}/_search", json=body,
            headers={"Content-Type": "application/json"}, timeout=30)
        try:
            resp.raise_for_status()
        except requests.HTTPError:
            return None, f"HTTP {resp.status_code}: {resp.text[:200]}"
        hits = resp.json().get("hits", {}).get("hits", [])
        if not hits:
            return None, f"No documents found in `{index_pattern}`."
        return hits[0], None

    # 1) Raw hit wrapper (has _source) -> unwrap directly.
    if isinstance(sample_document, dict) and isinstance(sample_document.get("_source"), dict):
        return (sample_document["_source"], sample_document.get("_index"), None)

    # 2) Metadata-only (e.g. {"_id": "..."}) -> fetch that document.
    if _is_metadata_only(sample_document):
        doc_id = sample_document.get("_id")
        if doc_id:
            hit, err = _search({"size": 1, "query": {"ids": {"values": [doc_id]}}})
            if err:
                return None, None, err.replace(
                    "No documents found", f"Document `{doc_id}` not found")
            return (hit.get("_source") or {}), hit.get("_index"), None
        sample_document = None

    # 3) Plain non-empty fields dict -> use as-is (strip stray metadata keys).
    if isinstance(sample_document, dict) and sample_document:
        cleaned = {k: v for k, v in sample_document.items() if k not in METADATA_FIELDS}
        return cleaned, None, None

    # 4) None / empty / anything else -> fetch any document from the index.
    hit, err = _search({"size": 1})
    if err:
        return None, None, err
    return (hit.get("_source") or {}), hit.get("_index"), None


def test_script(es_host: str, username: str, password: str, index_pattern: str,
                script: str, verify_ssl: bool = True, sample_document: dict = None) -> dict:
    """
    Painless syntax check via POST /_scripts/painless/_execute.

    The `filter` context needs a concrete index (no wildcards) and a sample
    document whose fields the script reads. `sample_document` may be:
      - None/empty           -> a random document is fetched from the index
      - {"_id": "..."}       -> that specific document is fetched
      - a raw hit (with _source) -> unwrapped automatically
      - a plain fields dict  -> used as-is

    Returns {"ok": bool, "error": str|None} — a 200 means the script compiled;
    anything else returns the ES response for review.
    """
    session = requests.Session()
    if username and password:
        session.auth = HTTPBasicAuth(username, password)
    session.headers.update({"Content-Type": "application/json"})
    session.verify = verify_ssl

    document, concrete_index, error = _resolve_sample_document(
        session, es_host, index_pattern, sample_document)
    if error:
        return {"ok": False, "error": error}

    body = {
        "script": {"lang": "painless", "source": script},
        "context": "filter",
        "context_setup": {"index": concrete_index or index_pattern,
                          "document": document or {}},
    }
    try:
        resp = session.post(f"{es_host.rstrip('/')}/_scripts/painless/_execute",
                            json=body, timeout=30)
        if resp.status_code == 200:
            return {"ok": True, "error": None}
        return {"ok": False, "error": f"HTTP {resp.status_code}: {resp.text[:300]}"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
