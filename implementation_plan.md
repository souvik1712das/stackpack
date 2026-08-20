# Security Tab — Users & Roles Backup/Restore

## Overview

The **🔐 Security** tab (implemented) exports and restores Elasticsearch native
realm **users** and **roles** — including **password hashes** — so they can be
recreated verbatim in a target cluster.

The feature follows the same UX patterns as the existing tabs:

- Fetch sub-tab (read-only backup)
- Restore sub-tab (opt-in, write-gated)
- Multi-select with "select all" behaviour
- Filter reserved vs. custom items
- Per-item edit capability
- Migration flag (✅ already restored indicator)
- Download as ZIP + Excel summary
- Progress bar + error reporting

---

## Key Design Decisions (confirmed during implementation)

### 1. Users are read ONLY from the `.security-7` index (no API fallback)

- `GET .security-7/_search` (scroll paginated) filtered to
  `{"terms": {"type": ["user", "reserved-user"]}}`.
- The raw `password` field of each doc is reused **verbatim** as
  `password_hash` on restore (manually verified round-trip:
  `PUT /_security/user/{username}` with `{"password_hash": "$2a$10$..."}`).
- **No fallback** to `GET /_security/user`. If the index read fails (e.g.
  insufficient privileges), the fetch errors out with a clear hint to grant
  `manage_security` or direct read access to the security index.

### 2. Roles are fetched via `GET /_security/role`

- `transient_metadata` is stripped; `metadata._reserved` is stripped from the
  body (read-only flag) but used for reserved tagging.
- Restore via `PUT /_security/role/{name}`.

### 3. Reserved detection is DYNAMIC (no hardcoded name lists)

- **Users:** index doc field `type == "reserved-user"` → reserved.
- **Roles:** role definition `metadata._reserved is True` → reserved.
- The reserved flag is written to `security_meta.json` in the ZIP so the
  restore tab can re-apply the filter after upload.

### 4. Restore checks for existing items and asks Overwrite vs Skip

- A **"🔍 Check existing roles/users on target"** button queries
  `GET /_security/role` + `GET /_security/user` on the target and stores the
  name sets.
- For every item that already exists, the user picks **Overwrite** or **Skip**
  per item (global default radio, default = **Skip** for safety).
- Items absent on the target are always created; skipped items are recorded
  separately (not counted as failures). Result reports success / skipped /
  failed.
- Roles are restored before users (users reference role names).

---

## Files

### [NEW] security_logic.py

UI-agnostic logic module following the same structure as `cluster_logic.py` /
`ml_logic.py`.

**Constants**
```python
SECURITY_INDEX = ".security-7"          # overridable via UI (Advanced)
SECURITY_ASSET_TYPES = ["roles", "users"]   # roles first (dependency order)
SECURITY_ASSET_LABELS = {"roles": "Roles", "users": "Users"}
```

**Functions**
- `make_session(...)` — Basic-auth session (same pattern as other modules)
- `is_reserved_user(doc)` — `doc["type"] == "reserved-user"`
- `is_reserved_role(role_def)` — `metadata["_reserved"] is True`
- `run_fetch_security_assets(es_host, username, password, export_types,
  include_reserved, security_index, verify_ssl, progress_callback)`
  → `{"assets": {...}, "meta": {...}, "errors": [...]}`
  - users: scroll `.security-7`, `password` → `password_hash`, strip runtime
    keys, keep `username`/`roles`/`full_name`/`email`/`enabled` (+ custom
    `metadata` only when non-empty)
  - roles: `GET /_security/role`, cleaned as above
  - reserved items filtered unless `include_reserved=True`
- `check_security_existing(target_host, username, password, verify_ssl)`
  → `{"roles": set(), "users": set()}` for the target cluster
- `run_restore_security_assets(target_host, username, password, asset_files,
  existing, overwrite_actions, verify_ssl, progress_callback)`
  → `{"success": [...], "skipped": [...], "failed": [...]}`
  - `existing`: target-side name sets (from `check_security_existing`)
  - `overwrite_actions`: `{asset_type: {name: bool}}` — consulted only for
    items already present; default for a present item = **skip**
  - roles via `PUT /_security/role/{name}`, users via
    `PUT /_security/user/{name}` (with `password_hash`)
- `build_security_assets_zip(results)` → `io.BytesIO`
  - `roles/*.json`, `users/*.json`, `security_meta.json` (reserved flag map),
    `security_assets_summary_<timestamp>.xlsx`

**User restore body (matches manually-verified payload)**
```json
{
  "password_hash": "<raw password field from .security-7, verbatim>",
  "roles": ["aws-dev-logs-default-viewer"],
  "full_name": "Amit Rana",
  "email": "amit.rana@onafriq.com",
  "enabled": true
}
```

### [MODIFY] app.py

- Import `security_logic as sl`
- Tabs: `🔐 Security` added between `🧠 ML Assets Backup` and `📖 About / Setup`
- **Fetch sub-tab:** connection inputs → "Include reserved users/roles" checkbox
  (default OFF) → asset-type multiselect → Advanced expander with security
  index name → Fetch button + progress → success/error reporting (with a
  dedicated warning when the security index read fails) → preview table
  (Asset Type / Name / Reserved) → download ZIP
- **Restore sub-tab:** upload ZIP → detect `roles/`/`users/` categories +
  `security_meta.json` → reserved filter checkbox → category + item multiselect
  (✅ restored indicator) → per-item view/edit (JSON text area) → target
  connection → **check-existing step** with Overwrite/Skip radios → `RESTORE`
  gate + same-host check → run → success/skipped/failed reporting +
  `restored_security` session tracking
- About tab: Security section added

---

## Restore Flow Detail

```
1. Upload ZIP → detect categories + reserved flags
2. Select categories and items (reserved filter applies)
3. (Optional) Edit per-item JSON / rename target
4. Enter target cluster connection details
5. Click "🔍 Check existing roles/users on target"
     → GET /_security/role and /_security/user on TARGET
     → conflicts listed with per-item Overwrite / Skip radios
       (global default radio, default = Skip)
6. Type RESTORE and run
     - absent on target            → create
     - present + Overwrite         → PUT (with password_hash)
     - present + Skip              → recorded as skipped
```

---

## Verification Plan

### Automated Tests
- None (matches existing project pattern — no unit tests).

### Manual Verification
1. `streamlit run app.py` — confirm the **🔐 Security** tab appears.
2. Fetch against a source cluster:
   - Reserved users/roles hidden when filter is OFF; shown when ON.
   - Users carry `password_hash` from the `.security-7` index.
   - ZIP downloads with `roles/`, `users/`, `security_meta.json`, and the Excel
     summary.
3. Restore to a second cluster:
   - Confirm roles are created before users.
   - Confirm check-existing lists conflicts; default action is Skip; per-item
     Overwrite works (users restored with their original password hash).
   - Confirm skipped items are reported separately and ✅ indicators update.
4. Confirm per-item edit capability works.
5. Confirm removing `manage_security`/index read access makes the users fetch
   error out with the permission hint (no silent fallback).

---

# Scripted → Runtime Fields Tab — Data View Migration

## Overview

The **🔄 Scripted → Runtime Fields** tab (implemented) inventories **scripted
fields** in Kibana data views and migrates them to **runtime fields**. Scripted
fields were deprecated in Kibana 7.13 and removed in Kibana 8.16, so this is a
required pre-upgrade step for the ELK 7.x → 9.x path.

The feature follows the same UX patterns as the existing tabs:

- Fetch/scan sub-tab (read-only inventory)
- Per-item edit capability
- Migration status tracking (Pending / Runtime Created / Migrated /
  Runtime Exists / Error)
- Excel report download + ZIP with per-space JSON
- Write actions are opt-in and gated

---

## Key Design Decisions (confirmed during implementation)

### 1. Scan reads data views via the Kibana Saved Objects API

- For each space (`GET /api/spaces/space`), each data view is read with
  `GET /api/saved_objects/_find` filtered to `type=index-pattern` (falling
  back to `type=data-view` when a space returns no objects), `perPage=1000`.
- **Scripted fields are read from the saved-object `attributes.fields`**
  (JSON-encoded array/map of field specs where each scripted entry carries
  `"scripted": true` — confirmed against Kibana source
  `getAsSavedObjectBody()`). A legacy `attributes.scriptedFields` attribute is
  also supported, with name-based de-duplication across both.
- `attributes.runtimeFieldMap` (JSON-encoded map) is parsed to mark
  `has_runtime_field` per scripted field.
- Excel inventory columns: Space, Data View, Data View ID, Field, Type,
  Script, Migration Status.

### 2. Edit + syntax test before writing

- Per item: the Painless `script` and the runtime `type` are editable
  (most scripted fields need manual tweaks to work as runtime fields).
- Optional syntax check runs `POST /_scripts/painless/_execute` (note the
  plural `_scripts`) in the `filter` context — which requires a **concrete
  index** (no wildcards) and a **sample document** whose fields the script
  reads. The sample document is resolved automatically:
    - empty sample doc → any document is fetched via `_search` (`size: 1`)
    - `{"_id": "..."}` → that document is fetched via an `ids` query
    - raw hits (with `_index`/`_id`/`_source`) → unwrapped to `_source`
    - plain fields dict → used as-is (stray metadata keys stripped)
  When a document is fetched, the hit's concrete `_index` is used for
  `context_setup.index` (so wildcard data-view titles still work).

### 3. Create-before-delete (reversible, then irreversible)

- **Step A (reversible):** `POST /api/saved_objects/data_view/{id}/runtime_field`
  creates the runtime field on the data view. Deletion of the runtime field is
  a single documented API call, so this step is safe.
- **Step B (irreversible):** deleting the scripted field rewrites the data view
  `fields` via `PUT /api/saved_objects/index-pattern/{id}` with `overwrite=true`.
  It is gated behind an explicit checkbox and only enabled for items whose
  runtime field was created successfully in this session.

### 4. Migration status is tracked in session state

- `Pending` — discovered, not yet processed.
- `Runtime Created` — runtime field exists, scripted field still present.
- `Migrated` — runtime field exists AND scripted field deleted.
- `Runtime Exists` — runtime field already present on the data view
  (skips re-creation).
- `Error` — last action failed; error message shown.
- The report can be **regenerated** so the Excel download reflects what has
  actually been migrated, not the original scan.

---

## Files

### [NEW] runtime_field_logic.py

UI-agnostic logic module following the same structure as the other `*_logic.py`
modules.

**Constants**
```python
FIELD_MIGRATION_STATUS = {
    "pending": "Pending",
    "runtime_created": "Runtime Created",
    "migrated": "Migrated",
    "runtime_exists": "Runtime Exists",
    "error": "Error",
}
FIELD_TYPES = ["string", "number", "boolean", "date", "ip", "long", "double"]
```

**Functions**
- `make_session(...)` — Basic-auth session (same pattern as other modules)
- `_parse_scripted_fields(attributes)` / `_parse_runtime_field_map(attributes)`
  → decode the JSON-encoded `scriptedFields` / `runtimeFieldMap` attributes
- `scan_scripted_fields(host, username, password, verify_ssl, request_delay,
  progress_callback)` → `{"records": [...], "errors": [...]}`
  - records: `{space, data_view_id, data_view_title, field_name, field_type,
    lang, script, has_runtime_field}`
- `build_report_excel_bytes(records, status_map)` → Excel report (BytesIO)
  - `status_map` keys: `"space|data_view_id|field_name"` →
    `{"status": str, "target_name": str}`
- `create_runtime_field(session, base_url, space_id, data_view_id, target_name,
  field_type, script)` → `{"ok": bool, "error": str|None}` via
  `POST /api/data_views/data_view/{id}/runtime_field`
- `delete_scripted_field(session, base_url, space_id, data_view_id, field_name)`
  → `{"ok": bool, "error": str|None}` via
  `DELETE /api/data_views/data_view/{id}/scripted_field/{name}`
- `run_migrate(host, username, password, items, delete_after, verify_ssl, ...)`
  → `{"created": [...], "deleted": [...], "failed": [...]}` — create-before-
  delete enforced (delete only runs for items whose create succeeded)
- `run_delete_scripted_fields(host, username, password, items, ...)`
  → `{"deleted": [...], "failed": [...]}` — delete-only pass for fields whose
  runtime field already exists
- `test_script(es_host, username, password, index_pattern, script, verify_ssl,
  sample_document=None)`
  → `{"ok": bool, "error": str|None}` via `POST /_scripts/painless/_execute`
  (filter context; sample document auto-resolved via `_resolve_sample_document`,
  see "Key Design Decisions" above)
- `_resolve_sample_document(session, es_host, index_pattern, sample_document)`
  → `(document_dict, concrete_index, error)` — decides fetch-vs-use per input
- `safe_filename(name)` / `_space_prefix(space_id)` / `get_all_spaces(...)`
  — helpers; space prefix is `""` for `default`, `/s/{space}` otherwise

### [MODIFY] app.py

- Import `runtime_field_logic as rtfl`
- Tabs: `🔄 Scripted → Runtime Fields` added between `🔐 Security` and
  `📖 About / Setup`
- **Scan sub-tab:** connection inputs → spaces multiselect (with select-all
  default) → Scan button + progress → items table with Status, Script,
  Type, Space, Data View → edit buttons → test / create / delete actions →
  report download (regenerate supported)
- **About tab:** Scripted → Runtime Fields section added

---

## Migration Flow Detail

```
1. Enter destination cluster connection details
2. Scan spaces → scripted field inventory (read-only)
3. Select items to migrate (scripted only; runtime fields shown as Reference)
4. (Optional) Edit type / Painless script; optionally syntax-test
5. Create runtime field (reversible) → status = Runtime Created
6. Regenerate report to confirm progress
7. Check "I understand this is irreversible" + click Delete scripted field
   (enabled only when runtime field created) → status = Migrated
```

---

## Verification Plan

### Automated Tests
- None (matches existing project pattern — no unit tests).

### Manual Verification
1. `streamlit run app.py` — confirm the **🔄 Scripted → Runtime Fields** tab
   appears and no other tab is broken.
2. Scan against a cluster with scripted fields:
   - Inventory lists scripted fields with space/data view/type/script.
   - Excel report downloads.
3. Edit a script and run the Painless syntax test:
   - Valid scripts report success; broken scripts report the Elasticsearch
     error message.
4. Create a runtime field → status moves to **Runtime Created**; the field
   appears in the data view under runtime fields (verified via Kibana UI).
5. Delete the scripted field (checkbox + gate) → status moves to **Migrated**;
   runtime field still present and the scripted field is gone.
6. Confirm delete is blocked until the runtime field exists and the checkbox
   is checked.

---

# Upgrade Assistant Tab — Index & Data-Stream Reindexing (8.19 → 9.x)

## Overview

The **♻️ Upgrade Assistant (8.19 - 9.x, Bulk Reindexing)** tab (implemented)
discovers which Elasticsearch indices and data streams still need reindexing
for the 9.x upgrade and reindexes them **one at a time** through Kibana's
Upgrade Assistant (UA) API.

Each index is reindexed by the UA through the full flow:

1. Reindex event created (UA)
2. Set index to read-only (UA step 20)
3. Create `reindexed-v8-<index>` + copy settings (UA step 30)
4. Reindex documents (UA step 40/50)
5. Alias swap + delete original index (UA step 60)
6. Re-point ILM / data-stream aliases (UA step 70)

Data streams are handled by rolling over an old write index (if created before
8.0) and reindexing each old backing index one at a time.

The feature follows the same UX patterns as the existing tabs:

- Discover sub-tab (read-only inventory)
- Reindex sub-tab (opt-in, write-gated)
- Live status table + metrics + auto-refresh
- Halt / Resume / Cancel / Hard-stop controls
- Targeted reindexing of a specific index or data stream
- Post-reindex validation + Excel tracker download
- Events audit log

---

## Key Design Decisions (confirmed during implementation)

### 1. "Needs reindex" is derived from `index.version.created < 8000000`

- One bulk read `GET /_all/_settings?flat_settings=true&filter_path=*.settings.index.version.created`
  gives every index's creation version (e.g. `7130199` = 7.13.1).
- Discovery also reads `GET _cat/indices` (state, docs, store, data-stream
  membership) and `GET /_data_stream` for streams.
- Indices created at/after 8.0.0 (`>= 8000000`) are already ready for 9.x.
- System indices (`.kibana*`, `.watches`, …) are hidden unless explicitly
  included; closed indices are listed but never auto-queued.

### 2. Reindexing goes through the Kibana Upgrade Assistant API

- `POST /api/upgrade_assistant/reindex/{index}` starts an event.
- `GET /api/upgrade_assistant/reindex/{index}` returns the `reindexOp`
  (`status`, `lastCompletedStep`, `reindexTaskPercComplete`, `errorMessage`,
  `newIndexName`).
- `DELETE /api/upgrade_assistant/reindex/{index}` cancels an in-flight event
  (used by Cancel / Hard stop).
- UA readiness: `GET /api/upgrade_assistant/status?targetVersion=9.0.0`.
- `lastCompletedStep` is mapped to the 7 human-readable steps above.

### 3. Data streams: rollover first, then reindex old backing indices

- The write index is the last backing index in the `GET /_data_stream` payload.
- If the write index was created before 8.0, `POST /{stream}/_rollover` creates
  a new (8.x) write index before reindexing the old backing indices.
- Each old backing index is reindexed one at a time, and the stream is marked
  **Completed** only when all of them succeed.

### 4. Background worker with halt / resume / cancel semantics

- A `ReindexWorker` thread processes the queue one task at a time and never
  touches `streamlit`; the UI reads `worker.state` on every rerun.
- **Halt** — finishes the in-flight reindex event, then stops picking up new
  tasks (so the user can validate mid-upgrade).
- **Resume** — clears the halt.
- **Cancel current** — `DELETE`s the in-flight UA event.
- **Hard stop** — cancels the in-flight event and refuses further tasks.
- **Pause after each index** — optional toggle for validating between items.
- On a terminal failure (status 2/3/4 = Failed/Paused/Cancelled) the row is
  flagged, the raw `reindexOp` is recorded in the events audit, and the worker
  **halts** (flag-and-stop).

### 5. Worker lives in `st.session_state`, keyed by connection

- Recreated only when the Kibana/ES connection changes; survives reruns while
  the app session is open.
- Live updates use Streamlit's built-in rerun loop (`time.sleep` + `st.rerun`),
  no extra dependency.

---

## Files

### [NEW] reindex_logic.py

UI-agnostic logic module (never imports streamlit).

**Constants**
```python
ELASTICSEARCH_VERSION_8 = 8000000
TARGET_UPGRADE_VERSION = "9.0.0"
STEP_CODES = {0: ..., 10: ..., 20: ..., 30: ..., 40: ..., 50: ..., 60: ..., 70: ...}
EVENT_STATUS = {0: "In progress", 1: "Completed", 2: "Failed", 3: "Paused", 4: "Cancelled"}
TERMINAL_FAILED = (2, 3, 4)
```

**Functions**
- `make_session(...)` — Basic-auth session (same pattern as other modules)
- `get_upgrade_status(session, kibana_url, target_version)` → readiness +
  deprecation counts
- `discover_indices(session, es_url, include_system)` → per-index record
  (name, state, docs, store, stream, version_created, needs_reindex, closed)
- `discover_data_streams(session, es_url)` → per-stream record
  (backing_indices, old_backing, write_index, needs_reindex, generation, …)
- `start_reindex` / `get_reindex_status` / `cancel_reindex` → UA REST calls
- `rollover_stream(session, es_url, stream_name)` → `POST /{stream}/_rollover`
- `validate_index(session, es_url, index_name)` → post-reindex check that the
  name now resolves to an index with `version.created >= 8000000`
- `ReindexWorker` class — queue + background thread + controls + `state`
- `build_tracker_excel(state)` → Excel workbook (Indices / Data Streams /
  Events sheets)

### [MODIFY] app.py

- Import `reindex_logic as ril`
- Tabs: `♻️ Upgrade Assistant (8.19 - 9.x, Bulk Reindexing)` added between
  `🔄 Scripted → Runtime Fields` and `📖 About / Setup`
- **Discover sub-tab:** Kibana URL + ES URL + credentials + verify SSL +
  target version + include-system → Discover button → UA readiness metrics +
  indices table + data streams table (read-only)
- **Bulk Reindex sub-tab:** `REINDEX` confirmation phrase + snapshot/ingestion
  ack gate → Start/Resume · Halt · Cancel · Hard-stop buttons → pause-after-each
  toggle → multiselect (indices + data streams) + free-text index → queue
  selected / queue all → live status metrics + tasks table + events expander →
  validate a completed index → tracker Excel download → live auto-refresh
- **About tab:** Upgrade Assistant section added

---

## Reindex Flow Detail

```
1. Discover (read-only) → indices + data streams that need reindexing
2. Gate: type REINDEX + confirm snapshot taken / ingestion paused
3. Queue (selected / all / specific) → press Start/Resume
4. Worker processes one index at a time via the Upgrade Assistant:
     read-only → create reindexed-v8-… → reindex → alias swap → cleanup
   For streams: rollover write index first, then each old backing index
5. Live status updates every rerun (metrics + per-index step/progress)
6. Optional Halt → validate in the middle → Resume
7. On failure: row flagged, reindexing event recorded, worker halts
8. Validate completed indices → download Excel tracker
```

---

## Verification Plan

### Automated Tests
- None (matches existing project pattern — no unit tests).

### Manual Verification
1. `streamlit run app.py` — confirm the **♻️ Upgrade Assistant** tab appears
   and no other tab is broken.
2. Discover against the 8.19 cluster:
   - Indices created before 8.0 appear with `needs_reindex = ✅`.
   - Data streams list old backing indices and the write index.
   - UA readiness matches the Kibana Upgrade Assistant UI.
3. Queue all + Start:
   - Each index follows the read-only → reindexed-v8-… → alias-swap flow.
   - Status/step/progress update live; the tracker Excel reflects it.
4. Halt after the current task → validate in Kibana → Resume.
5. Cancel / Hard stop during an in-flight reindex → event is cancelled and
   flagged.
6. Reindex a specific index / data stream via the targeted controls.
7. Force a failure (e.g. an index the UA cannot reindex) → row flagged,
   event recorded, worker halts.

---

# Upgrade Assistant — 🔒 Data Streams Read-Only sub-tab

## Overview

A third sub-tab inside the **♻️ Upgrade Assistant** tab. The Upgrade Assistant
suggests, for each old data stream, either **reindex** it or **set it
read-only** when historical data does not need to change ("You can reindex
post-upgrade if updates are needed"). This sub-tab automates the read-only path:

1. Inventory data streams with their **last-updated** timestamp
   (`maximum_timestamp` from `GET _data_stream/*/_stats?human=true`).
2. Select which streams to freeze (only ones no longer being written to).
3. Mark them read-only via the Upgrade Assistant:

   ```
   POST kbn:api/upgrade_assistant/update_index/index_1,index_2,index_3...
   {"operations": ["blockWrite", "unfreeze"]}
   ```

   (Comma-separated backing-index names are accepted by this endpoint; batches
   are kept ≤ 50 names per call. `unfreeze` also thaws legacy frozen backing
   indices, which must be unfrozen before 9.x.)
4. Verify the write-block took effect:

   ```
   GET /<index>/_settings?filter_path=*.settings.index.blocks.write
   ```

   — per **backing index** (write index included).
5. Download an Excel report (Data Streams summary + Backing Indices detail).

## Key Design Decisions

### 1. Freeze the whole stream (all backing indices)

Marking a data stream read-only blocks writes on **every** backing index,
including the current write index. This is intentional — a read-only stream
must accept no new data. Only streams whose ingestion has stopped should be
selected. The Upgrade Assistant `update_index` endpoint operates on index names,
so the data-stream's backing indices are resolved first and sent as CSV.

### 2. `maximum_timestamp` drives the "last updated" column

`GET _data_stream/*/_stats?human=true` returns `maximum_timestamp` (last time
data was written), `docs_count`, `store_size`, and the backing-index list per
stream. Merged with the existing `discover_data_streams()` result (old_backing /
needs_reindex) in one call: `discover_stream_readonly()`.

### 3. Verification is independent of the update_index response

`update_index` returns an opaque body, so success is *not* judged from it.
After the call, `GET /{index}/_settings?filter_path=*.settings.index.blocks.write`
is checked on every backing index; a stream counts as **Fully read-only** only
when all backing indices report `blocks.write = "true"`. A "✅ Check current
read-only status" button re-runs this check without applying anything.

### 4. Only Fleet-internal streams hidden by default

APM / Synthetics / `metrics-*` / `logs-*` streams carry real application data
the operator legitimately needs to freeze, so only Fleet-internal prefixes
(`fleet-`, `.fleet-`) are dropped by default. Hidden streams are reported to the
UI (`excluded_system`) so the operator can tick the "Include Fleet system data
streams" checkbox and reload. The discovery itself is resilient: if
`_data_stream` or `_data_stream/*/_stats` fails (older 7.x, permissions), the
load still returns whatever succeeded and surfaces the failure as a warning —
never a silent no-op.

## Files

### [MODIFY] reindex_logic.py

New UI-agnostic functions (never import streamlit):

- `get_data_stream_stats(session, es_url)` → `GET _data_stream/*/_stats?human=true`
  → `{stream: {backing_indices, write_index, generation, store_size,
  store_size_bytes, maximum_timestamp, docs_count, backing_count}}`
- `discover_stream_readonly(session, es_url, include_system=False,
  deprecations=None, version_map=None)` → `{"records": [...], "excluded_system": [...],
  "warnings": [...]}` (backing / write index / old_backing / needs_reindex /
  maximum_timestamp / docs / store / backing_count / **is_empty**), Fleet-internal
  streams filtered + reported; never raises — failures become warnings.
  `is_empty` is True when `maximum_timestamp` is `0`/`"0"` or `docs_count == 0`
  (empty streams need no read-only block).
- `mark_indices_read_only(session, kibana_url, es_url, index_names, operations=None)`
  → **adaptive**: per index, reads current settings and only sends `blockWrite`
  when `index.blocks.write` is not already true and `unfreeze` only when frozen
  (7.x UA rejects `unfreeze` with 400). Groups by operations, POSTs
  `update_index/{csv}` body `{"operations":[...]}` in ≤ 50-name batches, and on
  any rejection **falls back to direct ES**: `POST {idx}/_unfreeze` (if needed)
  + `PUT {idx}/_settings {"index.blocks.write": true}`.
  → per-index `{index, http_status, ok, body, error, operations, method}`
  where `method` = `upgrade_assistant` | `es_settings` | `already`
- `check_indices_blocks_write(session, es_url, index_names)` → GET
  `/{csv}/_settings?filter_path=*.settings.index.blocks.write` → `{index: bool}`
- `apply_streams_read_only(session, kibana_url, es_url, stream_records)` → marks
  + verifies every backing index, returns enriched records (`read_only`,
  `applied`, `http_statuses`, `error`, `index_results`)
- `build_readonly_report_excel(results)` → workbook with **Data Streams** and
  **Backing Indices** sheets (openpyxl, same styling as `build_tracker_excel`)

New constants: `SYSTEM_STREAMS_PREFIXES = ("fleet-", ".fleet-")`,
`READ_ONLY_OPERATIONS = ("blockWrite", "unfreeze")`, `_UPDATE_INDEX_BATCH = 50`.

### [MODIFY] app.py

- Sub-tabs in `tab_reindex` → third tab `🔒 Data Streams Read-Only (writes to cluster)`
- **Load** button → inventory table (backing count / write index / last updated /
  docs / store / **no-data flag** / old backing / needs reindex); shows a
  "Loaded N data stream(s)" success, a warning for any failed source, a caption
  listing hidden Fleet-internal streams, and an info banner listing streams with
  `maximum_timestamp = 0` (nothing to freeze)
- **Check current read-only status** button (read-only, tags already-frozen streams)
- Multiselect of streams (empty streams tagged **"no data"** in the label) →
  **Gate** (type `READONLY` + ack) → **Make selected streams read-only** →
  results (per-stream success/error + per-backing-index table with a Method
  column: Upgrade Assistant / ES fallback / already read-only) → **Build report
  Excel** / **Download**
- About tab: Data Streams Read-Only bullet under the Upgrade Assistant section

## Verification Plan

### Manual Verification
1. `streamlit run app.py` — confirm the third sub-tab appears and the other
   two reindex sub-tabs still work.
2. Discover-tab connection, then load data streams: last-updated / docs / store
   columns populate from `_data_stream/_stats`.
3. Check current status → already read-only streams are tagged in the
   multiselect.
4. Select a stream, type `READONLY` + ack → Apply:
   - UA `update_index` call runs with the stream's backing indices.
   - Every backing index reports `blocks.write = "true"` afterwards.
   - A write attempt to that stream now fails with `cluster_block_exception`.
5. Excel report downloads with Data Streams + Backing Indices sheets.

---
