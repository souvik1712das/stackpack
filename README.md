# Elastic & Kibana Backup & Migration Tool (Stackpack v4)

A comprehensive Streamlit application for backing up, restoring, and migrating **Elasticsearch Watchers**, **Kibana Saved Objects**, **Cluster Assets**, **ML Configurations**, **Security Users & Roles**, **Scripted to Runtime Fields**, and **Upgrade Assistant Reindexing (8.19 → 9.x)**.

Originally built to support an **ELK 7.x → 9.x upgrade**, where documenting and restoring 300+ watchers, dozens of Kibana spaces, templates, ILM policies, ML jobs, and native users by hand was not realistic. Compatible with Elastic/Kibana 7.x, 8.x, and 9.x clusters.

## Features

- **Watcher Backup & Restore**: Fetches watchers, extracts embedded Painless scripts into readable columns, pulls recipient lists (email/Slack/Webhook/Jira), and outputs an Excel summary plus restore-ready `.txt` files.
- **Kibana Saved Objects Backup & Restore**: Discovers spaces, exports selected object types with `includeReferencesDeep` so dependent objects follow automatically, and outputs an Excel summary plus `.ndjson` files per space.
- **Cluster Assets Backup & Restore**: Exports/restores Component Templates, Index Templates, ILM & SLM Policies, Enrich Policies, Ingest Pipelines, Stored Scripts, and Snapshot Repositories into clean JSON structures and an Excel summary.
- **ML Assets Backup & Restore**: Exports/restores Anomaly Detection Jobs, Datafeeds, Data Frame Analytics Jobs, Calendars, and Filters — with runtime state stripped for clean restore.
- **Security Backup & Restore**: Exports native-realm users (with password hashes) and roles, with reserved-item detection and per-item Overwrite/Skip controls on restore.
- **Scripted → Runtime Fields Migration**: Inventories scripted fields in Kibana data views and migrates them to runtime fields (as scripted fields are deprecated/removed in 9.x), with Painless syntax validation.
- **Upgrade Assistant — Bulk Reindexing (8.19 → 9.x)**: Discovers indices and data streams requiring reindexing for 9.x and reindexes them via Kibana's Upgrade Assistant API — with live progress tracking, Halt/Resume/Cancel controls, targeted reindexing, and post-reindex validation.
- **Safety First**: Fetch operations are strictly read-only. Restore operations require explicit text confirmation and enforce a host-mismatch check to prevent accidental overwrites into source/production clusters.

## App Screenshot

<img width="943" height="472" alt="Stackpack UI" src="https://github.com/user-attachments/assets/4f2428e1-9b25-48d6-b20a-ce7d5d8c7ab7" />

---

## Prerequisites

Before installing Stackpack, ensure your system has the following installed:

### 🪟 Windows Prerequisites
- **Python 3.10+**: Ensure Python is added to PATH during installation ([python.org](https://www.python.org/downloads/)).
- **Git**: Installed for cloning the repository ([git-scm.com](https://git-scm.com/)).
- **Network connectivity**: Access to target Elasticsearch & Kibana HTTP(S) ports (e.g. `9200`, `5601`).

### 🐧 Linux Prerequisites
- **Python 3.10+** & `pip` & `venv`:
  ```bash
  # Ubuntu / Debian
  sudo apt update && sudo apt install -y python3 python3-pip python3-venv python3-full git

  # RHEL / CentOS / Rocky Linux
  sudo dnf install -y python3 python3-pip git
  ```
- **Port 8501 Firewall access** (if accessing remotely from your PC/laptop):
  ```bash
  # UFW (Ubuntu/Debian)
  sudo ufw allow 8501/tcp

  # Firewalld (RHEL/CentOS)
  sudo firewall-cmd --add-port=8501/tcp --permanent
  sudo firewall-cmd --reload
  ```

---

## Quick Start

### 🐧 Linux Setup & Access URL

#### Option 1: Automated Script (Recommended)
```bash
git clone https://github.com/souvik1712das/stackpack.git
cd stackpack
chmod +x deploy_linux.sh
./deploy_linux.sh
source .venv/bin/activate
streamlit run app.py --server.port 8501 --server.address 0.0.0.0
```

#### Option 2: Continuous Background Daemon (`systemd`)
```bash
sudo mv stackpack /opt/stackpack
cd /opt/stackpack
sudo cp stackpack.service /etc/systemd/system/stackpack.service
sudo systemctl daemon-reload
sudo systemctl enable --now stackpack
```

#### 🌐 How to access Stackpack on Linux:
- **From the Linux server itself** (local browser): `http://localhost:8501`
- **From another PC/laptop on the network**: `http://<your-linux-server-ip>:8501`  
  *(Example: `http://192.168.1.50:8501` or `http://stackpack.internal:8501`)*

---

### 🪟 Windows Setup & Access URL

```powershell
# 1. Clone repository
git clone https://github.com/souvik1712das/stackpack.git
cd stackpack

# 2. Create and activate virtual environment
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# 3. Install requirements & run
pip install -r requirements.txt
streamlit run app.py
```

#### 🌐 How to access Stackpack on Windows:
- Open browser at `http://localhost:8501`

---

## Usage

1. **Select Feature Tab**: Choose **Watcher Backup**, **Kibana Saved Objects**, **Cluster Assets**, **ML Assets**, **Security**, **Runtime Fields**, or **Upgrade Reindex**.
2. **Source Connection**: Enter source cluster / Kibana URL and credentials (`URL`, `Username`, `Password`).
3. **Fetch & Download**: Click **Fetch** to run the discovery. Once complete, click **Download Backup ZIP** to save the generated archive locally.
4. **Restore into Target Cluster**:
   - Go to the **Restore** sub-tab within the respective feature.
   - Upload your previously generated Backup ZIP.
   - Enter **Target** cluster credentials (must differ from the source host for safety).
   - Type `RESTORE` to confirm, then execute.

---

## What's Inside a Backup ZIP

### 1. Watcher Backup
```
es_watchers_backup_<timestamp>.zip
├── es_watchers_<timestamp>.xlsx        # Summary + Full Detail sheets
└── watcher_scripts/
    ├── alert_high_cpu.txt              # Restore-ready JSON payload per watcher
    └── ...
```

### 2. Kibana Saved Objects Backup
```
kibana_export_<timestamp>.zip
├── export_summary_<timestamp>.xlsx     # Object counts per space & type
└── spaces/
    ├── default.ndjson                  # Kibana-importable NDJSON per space
    ├── security_analytics.ndjson
    └── ...
```

### 3. Cluster Assets Backup
```
cluster_assets_backup_<timestamp>.zip
├── cluster_assets_summary_<timestamp>.xlsx
├── component_templates/
│   └── logs_settings.json
├── index_templates/
│   └── custom_logs_template.json
├── ilm_policies/
│   └── hot_warm_policy.json
├── slm_policies/
│   └── daily_snapshots.json
├── ingest_pipelines/
│   └── main_parser_pipeline.json
└── snapshot_repositories/
    └── s3_backup_repo.json
```

### 4. ML Assets Backup
```
ml_assets_backup_<timestamp>.zip
├── ml_assets_summary_<timestamp>.xlsx
├── anomaly_detectors/
│   └── response_time_detector.json
├── datafeeds/
│   └── datafeed_response_time.json
├── calendars/
│   └── maintenance_windows.json
└── filters/
    └── known_ips_filter.json
```

### 5. Security Backup
```
security_backup_<timestamp>.zip
├── security_summary_<timestamp>.xlsx   # Native users & roles summary
├── users/
│   └── analyst_user.json              # Native user payload with hash
└── roles/
    └── security_reader_role.json       # Native role definition payload
```

---

## Required Permissions

- **Elasticsearch**: User with cluster read permissions and appropriate API privileges (`manage_watcher`, `manage_security`, `cluster:admin/ilm/get`, etc.). The `elastic` superuser works for full scope.
- **Kibana**: User with `all` privileges across targeted Kibana spaces.

---

## Safety Controls

- Fetch operations are **100% read-only**.
- Restore operations require explicit confirmation (`RESTORE`) and enforce host distinction so production clusters are protected against accidental overwrites.
- No sensitive credentials or backup payloads leave your system — execution occurs strictly locally in memory.

---

## Project Structure

```
.
├── app.py                  # Streamlit UI application entry point
├── watcher_logic.py        # ES Watcher fetch, parse, and restore module
├── kibana_logic.py         # Kibana saved objects space export/restore module
├── cluster_logic.py        # ES templates, ILM/SLM, pipelines, repos module
├── ml_logic.py             # ES ML jobs, datafeeds, calendars, filters module
├── security_logic.py       # Native realm users and roles backup/restore module
├── runtime_field_logic.py  # Scripted fields to Runtime fields migration module
├── reindex_logic.py        # Upgrade Assistant bulk reindex worker engine
├── deploy_linux.sh         # Linux automated environment deployment script
├── stackpack.service       # Systemd service unit template for Linux background running
├── requirements.txt        # Python dependency specifications
└── README.md               # Project documentation
```

---

## License

Use, modify, and share freely within your organization.

---

Crafted by Souvik Das  
[![LinkedIn](https://img.shields.io/badge/LinkedIn-0077B5?style=for-the-badge&logo=linkedin&logoColor=white)](https://www.linkedin.com/in/souvik-das-6ba904a2/)
