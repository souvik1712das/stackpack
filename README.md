# Elastic & Kibana Backup Tool

A small Streamlit app for backing up — and optionally restoring — **Elasticsearch
Watcher scripts** and **Kibana saved objects** (dashboards, visualizations,
index patterns, etc., per space, with deep references).

Originally built to support an **ELK 7.x → 9.x upgrade**, where documenting
and being able to restore 300+ watchers and dozens of Kibana spaces by hand
was not realistic. Should work against any reasonably compatible
Elastic/Kibana cluster.

## Features

- **Watcher backup**: fetches every watcher in the cluster, extracts embedded
  Painless scripts (condition / transform / action scripts) into readable
  columns, pulls recipient lists from email/Slack/PagerDuty/webhook/Jira
  actions, and outputs both an Excel summary and one restore-ready `.txt`
  file per watcher.
- **Kibana saved objects backup**: discovers all spaces, lets you pick which
  object types to export (or just use the sensible default selection),
  exports each space with `includeReferencesDeep` so dependent objects come
  along automatically, and outputs an Excel summary plus one `.ndjson` file
  per space.
- **Cluster Assets backup**: exports and restores cluster-level configurations
  including Component Templates, Index Templates, ILM & SLM Policies, Enrich
  Policies, Ingest Pipelines, Stored Scripts, and Snapshot Repositories into
  clean JSON structures and an Excel summary.
- **ML Assets backup**: exports and restores Machine Learning configurations —
  Anomaly Detection Jobs, Datafeeds, Data Frame Analytics Jobs, Calendars, and
  Filters — with runtime state stripped for clean restore.
- **Security backup**: exports native-realm users (with password hashes) and
  roles, with reserved-item detection and per-item Overwrite/Skip on restore.
- **Scripted → Runtime Fields migration**: inventories scripted fields in Kibana
  data views and migrates them to runtime fields (scripted fields are removed
  in 9.x), with an optional Painless syntax check.
- **Upgrade Assistant — bulk reindexing (8.19 → 9.x)**: discovers indices and
  data streams that still need reindexing for 9.x and reindexes them one at a
  time through Kibana's Upgrade Assistant — with live progress, Halt/Resume/
  Cancel/Hard-stop controls, targeted reindexing, post-reindex validation, and
  an Excel tracker.
- **Optional restore** for both, gated behind explicit confirmation and a
  same-host safety check so you can't accidentally write back into your
  source/production cluster.
- **Progress bars** for both fetch and restore, since 300+ watchers or many
  Kibana spaces can take a little while.
- Runs entirely **locally** — nothing is uploaded to a third-party server.
  Backups are built in memory and handed to you as a ZIP download.

## App Screenshot

<img width="943" height="472" alt="image" src="https://github.com/user-attachments/assets/4f2428e1-9b25-48d6-b20a-ce7d5d8c7ab7" />

## Quick start

```bash
git clone <this-repo-url>
cd <repo-folder>
pip install -r requirements.txt
streamlit run app.py
```

The app opens at `http://localhost:8501`.

## Usage

1. Open the **Watcher Backup**, **Kibana Saved Objects**, or **Cluster Assets Backup** tab.
2. Enter your cluster/Kibana URL, username, and password (see in-app
   placeholder examples for the expected format).
3. Click **Fetch** and watch the progress bar.
4. Download the resulting ZIP.

To restore into a **new** cluster (e.g. your upgraded 9.x environment):

1. Go to the **Restore** sub-tab.
2. Upload the ZIP you downloaded earlier.
3. Enter the **target** cluster's URL and credentials — this must be
   different from the source you used for fetching.
4. Type `RESTORE` to confirm, then run.

## What's inside a backup ZIP

**Watcher backup**
```
es_watchers_backup_<timestamp>.zip
├── es_watchers_<timestamp>.xlsx        # Summary + Full Detail sheets
└── watcher_scripts/
    ├── my_alert_watcher.txt            # restore-ready JSON, with header comments
    └── ...
```

**Kibana backup**
```
kibana_export_<timestamp>.zip
├── export_summary_<timestamp>.xlsx     # counts per space / object type
└── spaces/
    ├── default.ndjson                  # Kibana-import-ready NDJSON
    ├── analytics_team.ndjson
    └── ...
```

**Cluster Assets backup**
```
cluster_assets_backup_<timestamp>.zip
├── cluster_assets_summary_<timestamp>.xlsx
├── ilm_policies/
│   └── default_ilm_policy.json
├── ingest_pipelines/
│   └── filebeat_pipeline.json
└── index_templates/
    └── ...
```

## Required permissions

- Elasticsearch: a user with rights to read `.watches` and call the Watcher
  APIs (the `elastic` superuser works; for least-privilege, a role with
  `manage_watcher` cluster privilege).
- Kibana: a user with `all` privileges on the spaces you want to export/import
  (the `elastic` superuser works for this too).

## Notes on safety

- Fetch operations are **read-only** — no `PUT`/`POST` calls are made against
  the source cluster.
- Restore operations are explicit, opt-in, and require typing a confirmation
  phrase. They also refuse to run if the target host matches the source host
  entered earlier in the same session.
- Backup files (`.ndjson`, `.txt`) contain real configuration — URLs, index
  names, recipient lists, scripts. Treat them as sensitive and avoid
  committing them to a public repository.

## Project structure

```
.
├── app.py              # Streamlit UI
├── watcher_logic.py    # ES Watcher fetch/restore logic (UI-agnostic)
├── kibana_logic.py     # Kibana saved objects fetch/restore logic (UI-agnostic)
├── cluster_logic.py    # ES cluster assets fetch/restore logic (UI-agnostic)
├── ml_logic.py         # ES ML assets fetch/restore logic (UI-agnostic)
├── security_logic.py   # ES security users/roles fetch/restore logic (UI-agnostic)
├── runtime_field_logic.py  # Scripted → Runtime field migration logic (UI-agnostic)
├── reindex_logic.py    # Upgrade Assistant reindexing logic + background worker (UI-agnostic)
├── implementation_plan.md
├── requirements.txt
└── README.md
```

The logic codes have no Streamlit imports, so they can also be reused
directly from a plain Python script or notebook if you'd rather not run the UI.

## License

Use, modify, and share freely within your organization.

---

Crafted by Souvik Das

[![LinkedIn](https://img.shields.io/badge/LinkedIn-0077B5?style=for-the-badge&logo=linkedin&logoColor=white)](https://www.linkedin.com/in/souvik-das-6ba904a2/)
