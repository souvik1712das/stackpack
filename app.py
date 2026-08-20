"""
================================================================================
  Elastic & Kibana Backup Tool — Streamlit App
================================================================================
  A self-contained UI for backing up (and optionally restoring):
    - Elasticsearch Watcher scripts
    - Kibana saved objects (per space, with deep references)

  Built for ELK 7.x → 9.x upgrade workflows, but works on any compatible
  cluster you point it at.

  Run locally:
      pip install -r requirements.txt
      streamlit run app.py

================================================================================
"""

import io
import json
import zipfile
from datetime import datetime, timezone

import streamlit as st

import watcher_logic as wl
import kibana_logic as kl
import cluster_logic as cl
import ml_logic as ml
import security_logic as sl
import runtime_field_logic as rfl
import reindex_logic as ril


# ── Page setup ──────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Stackpack — Elastic Stack Backup & Migration Tool",
    page_icon="🗄️",
    layout="wide",
)

st.title("🗄️ Stackpack — Elastic Stack Backup & Migration Tool")
st.caption(
    "Back up Elasticsearch Watcher scripts, Kibana saved objects, cluster & ML "
    "assets, and security users/roles — and migrate scripted fields to runtime "
    "fields. Read-only by default — restore/write actions are opt-in and gated."
)

with st.expander("ℹ️ How this works / safety notes", expanded=False):
    st.markdown(
        """
        **Fetch (backup) is always read-only.** It only calls `GET`/`_search`-type
        APIs against the source cluster — nothing is modified.

        **Restore is opt-in and isolated** behind its own tab. Before anything is
        written, the app will:
        - Refuse to restore if the target URL is identical to the source URL
        - Show you a preview of what will be written
        - Require you to type a confirmation phrase before proceeding

        **Nothing is stored on a server.** This app runs in your own browser/local
        session. Files are generated in memory and offered to you as downloads —
        they are not saved anywhere else unless you choose to save them.

        **Credentials are not persisted.** They live only in the current browser
        session's memory for as long as the page is open.
        """
    )

tab_watcher, tab_kibana, tab_cluster, tab_ml, tab_security, tab_runtime, tab_reindex, tab_about = st.tabs(
    ["🔧 Watcher Backup", "📊 Kibana Saved Objects", "⚙️ Cluster Assets Backup", "🧠 ML Assets Backup", "🔐 Security", "🔄 Scripted → Runtime Fields", "♻️ Upgrade Assistant (8.19 - 9.x, Bulk Reindexing)", "📖 About / Setup"]
)


# ════════════════════════════════════════════════════════════════════════════
#   TAB 1 — WATCHER BACKUP
# ════════════════════════════════════════════════════════════════════════════

with tab_watcher:
    st.header("Elasticsearch Watcher Backup")

    sub_fetch, sub_restore = st.tabs(["⬇️ Fetch (backup)", "⬆️ Restore (advanced)"])

    # ── FETCH ──────────────────────────────────────────────────────────────
    with sub_fetch:
        st.subheader("1. Connect to source cluster")

        col1, col2 = st.columns([2, 1])
        with col1:
            es_host = st.text_input(
                "Elasticsearch URL",
                placeholder="https://your-cluster.example.com/elasticsearch",
                key="watcher_fetch_host",
            )
        with col2:
            verify_ssl_w = st.checkbox("Verify SSL certificate", value=True, key="w_verify_ssl")

        col3, col4 = st.columns(2)
        with col3:
            es_user = st.text_input("Username", value="elastic", key="watcher_fetch_user")
        with col4:
            es_pass = st.text_input("Password", type="password", key="watcher_fetch_pass")

        st.caption(
            "· Username → `elastic` · Password → your cluster's password (never logged or stored)."
        )

        st.subheader("2. Run the backup")
        run_clicked = st.button("🚀 Fetch all watchers", type="primary", key="watcher_fetch_btn")

        if run_clicked:
            if not es_host or not es_user or not es_pass:
                st.error("Please fill in the URL, username, and password.")
            else:
                progress_bar = st.progress(0, text="Starting...")
                status_text = st.empty()

                def progress_cb(current, total, message):
                    pct = int((current / total) * 100) if total else 0
                    progress_bar.progress(min(pct, 100), text=f"{message}  ({current}/{total})")

                try:
                    with st.spinner("Connecting and discovering watchers..."):
                        result = wl.run_fetch(
                            es_host=es_host, username=es_user, password=es_pass,
                            verify_ssl=verify_ssl_w, progress_callback=progress_cb,
                        )

                    records = result["records"]
                    errors = result["errors"]

                    progress_bar.progress(100, text="Done")

                    if not records:
                        st.warning("No watchers were fetched. Check the connection details and that watchers exist.")
                    else:
                        st.success(f"✅ Fetched {len(records)} watcher(s) successfully. "
                                   f"{len(errors)} error(s).")

                        if errors:
                            with st.expander(f"⚠️ {len(errors)} error(s) — click to view"):
                                st.json(errors)

                        # Build downloadable ZIP: excel + one txt per watcher
                        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                        excel_bytes = wl.build_excel_bytes(records)

                        zip_buffer = io.BytesIO()
                        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
                            zf.writestr(f"es_watchers_{timestamp}.xlsx", excel_bytes)
                            for rec in records:
                                fname = f"watcher_scripts/{wl.safe_filename(rec['watcher_id'])}.txt"
                                zf.writestr(fname, wl.build_txt_content(rec))
                        zip_buffer.seek(0)

                        st.markdown("### 📦 Your backup is ready")
                        st.markdown(
                            f"""
                            The ZIP file below contains:
                            - **`es_watchers_{timestamp}.xlsx`** — Summary + Full Detail sheets
                              (schedules, conditions, scripts, recipients, raw JSON)
                            - **`watcher_scripts/`** folder — one `.txt` file per watcher,
                              containing the restore-ready JSON body
                              (use these with the Restore tab, or `PUT /_watcher/watch/{{id}}`)
                            """
                        )

                        st.download_button(
                            label="⬇️ Download backup ZIP",
                            data=zip_buffer,
                            file_name=f"es_watchers_backup_{timestamp}.zip",
                            mime="application/zip",
                            type="primary",
                        )

                        # Quick on-screen preview table
                        import pandas as pd
                        preview_df = pd.DataFrame(records)[
                            ["watcher_id", "status", "schedule_type", "action_types", "recipients"]
                        ]
                        st.markdown("### Preview")
                        st.dataframe(preview_df, use_container_width=True)

                except Exception as exc:
                    st.error(f"❌ Fetch failed: {exc}")

    # ── RESTORE ────────────────────────────────────────────────────────────
    with sub_restore:
        st.warning(
            "⚠️ **This writes to a live cluster.** Use this only to restore "
            "watchers into a **new/target** cluster — never point this at your "
            "original production source by mistake."
        )

        st.subheader("1. Upload watcher backup ZIP")
        uploaded_zip = st.file_uploader(
            "Upload the ZIP file produced by the Fetch tab",
            type=["zip"],
            key="watcher_restore_zip",
        )

        if uploaded_zip:
            try:
                with zipfile.ZipFile(uploaded_zip) as zf:
                    txt_names = [n for n in zf.namelist() if n.startswith("watcher_scripts/") and n.endswith(".txt")]
                    st.info(f"Found {len(txt_names)} watcher file(s) in the uploaded ZIP.")
                    
                    restored_watchers = st.session_state.get("restored_watchers", set())
                    candidate_watchers = [
                        ("✅ " if wid in restored_watchers else "") + wid
                        for wid in [n.split("/")[-1].replace(".txt", "") for n in txt_names]
                    ]
                    # Keep a plain mapping to extract the real wid from the labelled option
                    w_label_to_id = {label: label.lstrip("✅ ") for label in candidate_watchers}

                    st.subheader("2. Select watchers to restore")
                    selected_w_items = st.multiselect(
                        "1. Select specific watchers to restore (leave empty to restore ALL watchers)",
                        options=candidate_watchers,
                        default=[],
                        key="wr_item_select"
                    )
                    
                    final_w_candidates = []
                    for n in txt_names:
                        wid = n.split("/")[-1].replace(".txt", "")
                        label = ("✅ " if wid in restored_watchers else "") + wid
                        if selected_w_items and label not in selected_w_items:
                            continue
                        final_w_candidates.append((wid, n))
                        
                    if final_w_candidates:
                        st.subheader("3. View and Edit Assets (Optional)")
                        if "edit_w_buffers" not in st.session_state:
                            st.session_state["edit_w_buffers"] = {}
                        if "last_w_zip_name" not in st.session_state or st.session_state["last_w_zip_name"] != uploaded_zip.name:
                            st.session_state["edit_w_buffers"] = {}
                            st.session_state["last_w_zip_name"] = uploaded_zip.name
                            
                        w_options = [c[0] for c in final_w_candidates]
                        selected_w_edit = st.selectbox("Select a watcher to view/edit", options=w_options, key="wr_edit_select")
                        
                        if selected_w_edit:
                            idx = w_options.index(selected_w_edit)
                            wid, w_path = final_w_candidates[idx]
                            
                            buffer_key = f"watcher/{wid}"
                            name_buffer_key = f"wname/{wid}"
                            
                            if buffer_key not in st.session_state["edit_w_buffers"]:
                                raw = zf.read(w_path).decode("utf-8")
                                json_lines = [ln for ln in raw.splitlines() if not ln.startswith("#")]
                                clean_json = "\n".join(json_lines).strip()
                                st.session_state["edit_w_buffers"][buffer_key] = clean_json
                                st.session_state["edit_w_buffers"][name_buffer_key] = wid
                                
                            current_val = st.session_state["edit_w_buffers"][buffer_key]
                            current_name = st.session_state["edit_w_buffers"].get(name_buffer_key, wid)
                            
                            new_name = st.text_input(f"Target Name for {wid}", value=current_name, key=f"text_{name_buffer_key}")
                            if new_name != current_name:
                                st.session_state["edit_w_buffers"][name_buffer_key] = new_name
                                
                            new_val = st.text_area(f"JSON Definition: {wid}", value=current_val, height=300, key=f"text_{buffer_key}")
                            if new_val != current_val:
                                st.session_state["edit_w_buffers"][buffer_key] = new_val

                    st.subheader("4. Connect to target cluster")
                    col1, col2 = st.columns([2, 1])
                    with col1:
                        target_host_w = st.text_input(
                            "Target Elasticsearch URL",
                            placeholder="https://new-cluster.example.com:9200",
                            help="Example: https://new-cluster.internal:9200 — must be "
                                 "DIFFERENT from your source cluster URL.",
                            key="watcher_restore_host",
                        )
                    with col2:
                        verify_ssl_wr = st.checkbox("Verify SSL certificate", value=True, key="wr_verify_ssl")

                    col3, col4 = st.columns(2)
                    with col3:
                        target_user_w = st.text_input("Username", value="elastic", key="watcher_restore_user")
                    with col4:
                        target_pass_w = st.text_input("Password", type="password", key="watcher_restore_pass")

                    if target_host_w:
                        st.subheader("5. Confirm and restore")
                    confirm_phrase = st.text_input(
                        "Type RESTORE to confirm you want to write to the target cluster above",
                        key="watcher_restore_confirm",
                    )

                    if st.button("🚀 Run restore", type="primary", key="watcher_restore_btn"):
                        if confirm_phrase != "RESTORE":
                            st.error("Confirmation phrase did not match. Type exactly: RESTORE")
                        elif target_host_w.rstrip("/") == es_host.rstrip("/") if es_host else False:
                            st.error("Target host matches the source host from the Fetch tab. Aborting for safety.")
                        elif not target_user_w or not target_pass_w:
                            st.error("Please fill in target username and password.")
                        else:
                            watcher_files = {}
                            for wid, w_path in final_w_candidates:
                                buffer_key = f"watcher/{wid}"
                                name_buffer_key = f"wname/{wid}"
                                
                                target_wid = st.session_state.get("edit_w_buffers", {}).get(name_buffer_key, wid)
                                
                                if buffer_key in st.session_state.get("edit_w_buffers", {}):
                                    body_str = st.session_state["edit_w_buffers"][buffer_key]
                                else:
                                    raw = zf.read(w_path).decode("utf-8")
                                    json_lines = [ln for ln in raw.splitlines() if not ln.startswith("#")]
                                    body_str = "\n".join(json_lines).strip()
                                    
                                watcher_files[target_wid] = body_str

                            progress_bar_r = st.progress(0, text="Starting restore...")

                            def restore_progress_cb(current, total, message):
                                pct = int((current / total) * 100) if total else 0
                                progress_bar_r.progress(min(pct, 100), text=f"{message} ({current}/{total})")

                            try:
                                result = wl.run_restore(
                                    target_host=target_host_w, username=target_user_w,
                                    password=target_pass_w, watcher_files=watcher_files,
                                    verify_ssl=verify_ssl_wr, progress_callback=restore_progress_cb,
                                )
                                progress_bar_r.progress(100, text="Done")
                                st.success(f"✅ Restored {len(result['success'])} watcher(s). "
                                           f"{len(result['failed'])} failed.")
                                # Track restored IDs for the session indicator
                                if "restored_watchers" not in st.session_state:
                                    st.session_state["restored_watchers"] = set()
                                for r in result["success"]:
                                    st.session_state["restored_watchers"].add(r.get("id", "") if isinstance(r, dict) else r)
                                if result["failed"]:
                                    with st.expander("⚠️ Failed watchers — click to view"):
                                        st.json(result["failed"])
                            except Exception as exc:
                                st.error(f"❌ Restore failed: {exc}")
            except zipfile.BadZipFile:
                st.error("That doesn't look like a valid ZIP file.")


# ════════════════════════════════════════════════════════════════════════════
#   TAB 2 — KIBANA SAVED OBJECTS
# ════════════════════════════════════════════════════════════════════════════

with tab_kibana:
    st.header("Kibana Saved Objects Backup")

    sub_kfetch, sub_krestore = st.tabs(["⬇️ Fetch (backup)", "⬆️ Restore (advanced)"])

    # ── FETCH ──────────────────────────────────────────────────────────────
    with sub_kfetch:
        st.subheader("1. Connect to source Kibana")

        col1, col2 = st.columns([2, 1])
        with col1:
            kb_host = st.text_input(
                "Kibana URL",
                placeholder="https://your-kibana.example.com/",
                key="kibana_fetch_host",
            )
        with col2:
            verify_ssl_k = st.checkbox("Verify SSL certificate", value=True, key="k_verify_ssl")

        col3, col4 = st.columns(2)
        with col3:
            kb_user = st.text_input("Username", value="elastic", key="kibana_fetch_user")
        with col4:
            kb_pass = st.text_input("Password", type="password", key="kibana_fetch_pass")

        st.caption(
            "Username → `elastic` · Password → your cluster's password."
        )

        st.subheader("2. Choose object types to export")
        st.caption(
            "Leave the default selection unless you have a specific reason to "
            "narrow it. Some types may not be exportable on your Kibana version — "
            "the app automatically skips those and tells you which ones."
        )

        select_all = st.checkbox("Select all types", value=True, key="kibana_select_all_types")
        if select_all:
            selected_types = st.multiselect(
                "Object types", options=kl.ALL_OBJECT_TYPES,
                default=kl.DEFAULT_EXPORT_TYPES, key="kibana_type_select_all",
            )
        else:
            selected_types = st.multiselect(
                "Object types", options=kl.ALL_OBJECT_TYPES,
                default=["dashboard", "visualization", "lens", "search", "index-pattern"],
                key="kibana_type_select_custom",
            )

        st.subheader("3. Run the backup")
        run_k_clicked = st.button("🚀 Fetch all spaces", type="primary", key="kibana_fetch_btn")

        if run_k_clicked:
            if not kb_host or not kb_user or not kb_pass:
                st.error("Please fill in the URL, username, and password.")
            elif not selected_types:
                st.error("Select at least one object type to export.")
            else:
                progress_bar_k = st.progress(0, text="Starting...")

                def k_progress_cb(current, total, message):
                    pct = int((current / total) * 100) if total else 0
                    progress_bar_k.progress(min(pct, 100), text=f"{message} ({current}/{total})")

                try:
                    with st.spinner("Connecting and discovering spaces..."):
                        result = kl.run_fetch(
                            kibana_host=kb_host, username=kb_user, password=kb_pass,
                            export_types=selected_types, verify_ssl=verify_ssl_k,
                            progress_callback=k_progress_cb,
                        )

                    space_results = result["space_results"]
                    skipped_types = result["skipped_types"]
                    progress_bar_k.progress(100, text="Done")

                    exported = [r for r in space_results if r["status"] == "ok"]
                    empty = [r for r in space_results if r["status"] == "empty"]
                    errored = [r for r in space_results if r["status"].startswith("error")]

                    st.success(
                        f"✅ {len(exported)} space(s) exported · {len(empty)} empty · "
                        f"{len(errored)} error(s)"
                    )

                    if skipped_types:
                        st.info(
                            f"ℹ️ These object types were not exportable on this Kibana "
                            f"version and were automatically skipped: {sorted(skipped_types)}"
                        )

                    if errored:
                        with st.expander(f"⚠️ {len(errored)} space(s) with errors"):
                            st.json([{"space": r["space_id"], "status": r["status"]} for r in errored])

                    if exported:
                        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                        excel_bytes = kl.build_summary_excel_bytes(space_results)

                        zip_buffer_k = io.BytesIO()
                        with zipfile.ZipFile(zip_buffer_k, "w", zipfile.ZIP_DEFLATED) as zf:
                            zf.writestr(f"export_summary_{timestamp}.xlsx", excel_bytes)
                            for r in exported:
                                zf.writestr(f"spaces/{r['export_file']}", r["ndjson_bytes"])
                        zip_buffer_k.seek(0)

                        st.markdown("### 📦 Your backup is ready")
                        st.markdown(
                            f"""
                            The ZIP file below contains:
                            - **`export_summary_{timestamp}.xlsx`** — one row per space with
                              object counts per type (Summary + Type Totals sheets)
                            - **`spaces/`** folder — one `.ndjson` file per space, ready to
                              re-import into Kibana via the Restore tab or the Kibana UI's
                              Saved Objects → Import screen
                            """
                        )

                        st.download_button(
                            label="⬇️ Download backup ZIP",
                            data=zip_buffer_k,
                            file_name=f"kibana_export_{timestamp}.zip",
                            mime="application/zip",
                            type="primary",
                        )

                        import pandas as pd
                        preview_df_k = pd.DataFrame([
                            {"space_id": r["space_id"], "space_name": r["space_name"],
                             "total_objects": r["total"], "status": r["status"]}
                            for r in space_results
                        ])
                        st.markdown("### Preview")
                        st.dataframe(preview_df_k, use_container_width=True)

                except Exception as exc:
                    st.error(f"❌ Fetch failed: {exc}")

    # ── RESTORE ────────────────────────────────────────────────────────────
    with sub_krestore:
        st.warning(
            "⚠️ **This writes to a live Kibana instance.** Use this only to "
            "restore objects into a **new/target** Kibana — never point this at "
            "your original production source by mistake."
        )

        st.subheader("1. Upload Kibana backup ZIP")
        uploaded_zip_k = st.file_uploader(
            "Upload the ZIP file produced by the Fetch tab",
            type=["zip"],
            key="kibana_restore_zip",
        )

        if uploaded_zip_k:
            try:
                with zipfile.ZipFile(uploaded_zip_k) as zf:
                    ndjson_names = [n for n in zf.namelist() if n.startswith("spaces/") and n.endswith(".ndjson")]
                    restored_kibana = st.session_state.get("restored_kibana", set())
                    all_space_ids = [n.split("/")[-1].replace(".ndjson", "") for n in ndjson_names]

                    st.info(f"Found {len(ndjson_names)} space file(s) in the uploaded ZIP.")

                    st.subheader("2. Select spaces to restore")
                    space_options_labeled = [
                        ("✅ " if sid in restored_kibana else "") + sid
                        for sid in all_space_ids
                    ]
                    space_choice = st.multiselect(
                        "1. Select which space(s) to restore (leave empty to restore ALL spaces)",
                        options=space_options_labeled, default=[],
                        key="kibana_restore_space_select",
                    )
                    # Strip ✅ prefix to get raw space IDs
                    space_choice_ids = [s.lstrip("✅ ") for s in space_choice]
                    
                    final_k_candidates = []
                    for n in ndjson_names:
                        sid = n.split("/")[-1].replace(".ndjson", "")
                        if space_choice_ids and sid not in space_choice_ids:
                            continue
                        final_k_candidates.append((sid, n))
                        
                    if final_k_candidates:
                        st.subheader("3. View and Edit Assets (Optional)")
                        if "edit_k_buffers" not in st.session_state:
                            st.session_state["edit_k_buffers"] = {}
                        if "last_k_zip_name" not in st.session_state or st.session_state["last_k_zip_name"] != uploaded_zip_k.name:
                            st.session_state["edit_k_buffers"] = {}
                            st.session_state["last_k_zip_name"] = uploaded_zip_k.name
                            
                        k_options = [c[0] for c in final_k_candidates]
                        selected_k_edit = st.selectbox("Select a space to view/edit", options=k_options, key="kr_edit_select")
                        
                        if selected_k_edit:
                            idx = k_options.index(selected_k_edit)
                            sid, k_path = final_k_candidates[idx]
                            
                            buffer_key = f"space/{sid}"
                            name_buffer_key = f"kname/{sid}"
                            
                            if buffer_key not in st.session_state["edit_k_buffers"]:
                                raw = zf.read(k_path).decode("utf-8")
                                st.session_state["edit_k_buffers"][buffer_key] = raw
                                st.session_state["edit_k_buffers"][name_buffer_key] = sid
                                
                            current_val = st.session_state["edit_k_buffers"][buffer_key]
                            current_name = st.session_state["edit_k_buffers"].get(name_buffer_key, sid)
                            
                            new_name = st.text_input(f"Target Space ID for {sid}", value=current_name, key=f"text_{name_buffer_key}")
                            if new_name != current_name:
                                st.session_state["edit_k_buffers"][name_buffer_key] = new_name
                                
                            st.caption("Note: NDJSON files must have exactly one valid JSON object per line.")
                            new_val = st.text_area(f"NDJSON Definition: {sid}", value=current_val, height=300, key=f"text_{buffer_key}")
                            if new_val != current_val:
                                st.session_state["edit_k_buffers"][buffer_key] = new_val

                    st.subheader("4. Connect to target Kibana")
                    col1, col2 = st.columns([2, 1])
                    with col1:
                        target_host_k = st.text_input(
                            "Target Kibana URL",
                            placeholder="https://new-kibana.example.com:5601",
                            help="Example: https://new-kibana.internal:5601 — must be "
                                 "DIFFERENT from your source Kibana URL.",
                            key="kibana_restore_host",
                        )
                    with col2:
                        verify_ssl_kr = st.checkbox("Verify SSL certificate", value=True, key="kr_verify_ssl")
            
                    col3, col4 = st.columns(2)
                    with col3:
                        target_user_k = st.text_input("Username", value="elastic", key="kibana_restore_user")
                    with col4:
                        target_pass_k = st.text_input("Password", type="password", key="kibana_restore_pass")
            
                    overwrite_k = st.checkbox(
                        "Overwrite existing objects with the same ID on target",
                        value=True, key="kibana_restore_overwrite",
                    )
            
                    if target_host_k:
                        st.subheader("5. Confirm and restore")
                    confirm_phrase_k = st.text_input(
                        "Type RESTORE to confirm you want to write to the target Kibana above",
                        key="kibana_restore_confirm",
                    )

                    if st.button("🚀 Run restore", type="primary", key="kibana_restore_btn"):
                        if confirm_phrase_k != "RESTORE":
                            st.error("Confirmation phrase did not match. Type exactly: RESTORE")
                        elif target_host_k.rstrip("/") == kb_host.rstrip("/") if 'kb_host' in locals() and kb_host else False:
                            st.error("Target host matches the source host from the Fetch tab. Aborting for safety.")
                        elif not target_user_k or not target_pass_k:
                            st.error("Please fill in target username and password.")
                        else:
                            space_files = {}
                            for sid, k_path in final_k_candidates:
                                buffer_key = f"space/{sid}"
                                name_buffer_key = f"kname/{sid}"
                                
                                target_sid = st.session_state.get("edit_k_buffers", {}).get(name_buffer_key, sid)
                                
                                if buffer_key in st.session_state.get("edit_k_buffers", {}):
                                    body_bytes = st.session_state["edit_k_buffers"][buffer_key].encode("utf-8")
                                else:
                                    body_bytes = zf.read(k_path)
                                    
                                space_files[target_sid] = body_bytes
                                
                            if not space_files:
                                st.warning("No spaces selected to restore.")
                                st.stop()

                            progress_bar_kr = st.progress(0, text="Starting restore...")

                            def k_restore_progress_cb(current, total, message):
                                pct = int((current / total) * 100) if total else 0
                                progress_bar_kr.progress(min(pct, 100), text=f"{message} ({current}/{total})")

                            try:
                                result = kl.run_restore(
                                    target_host=target_host_k, username=target_user_k,
                                    password=target_pass_k, space_files=space_files,
                                    overwrite=overwrite_k, verify_ssl=verify_ssl_kr,
                                    progress_callback=k_restore_progress_cb,
                                )
                                progress_bar_kr.progress(100, text="Done")
                                st.success(f"✅ Restored {len(result['success'])} space(s). "
                                           f"{len(result['failed'])} failed.")
                                # Track restored space IDs for the session indicator
                                if "restored_kibana" not in st.session_state:
                                    st.session_state["restored_kibana"] = set()
                                for r in result["success"]:
                                    st.session_state["restored_kibana"].add(r if isinstance(r, str) else r.get("space_id", r))
                                with st.expander("Details per space"):
                                    st.json(result["details"])
                            except Exception as exc:
                                st.error(f"❌ Restore failed: {exc}")
            except zipfile.BadZipFile:
                st.error("That doesn't look like a valid ZIP file.")


# ════════════════════════════════════════════════════════════════════════════
#   TAB 3 — CLUSTER ASSETS BACKUP
# ════════════════════════════════════════════════════════════════════════════

with tab_cluster:
    st.header("Elasticsearch Cluster Assets Backup")

    sub_cfetch, sub_crestore = st.tabs(["⬇️ Fetch (backup)", "⬆️ Restore (advanced)"])

    # ── FETCH ──────────────────────────────────────────────────────────────
    with sub_cfetch:
        st.subheader("1. Connect to source cluster")

        col1, col2 = st.columns([2, 1])
        with col1:
            es_host_c = st.text_input(
                "Elasticsearch URL",
                placeholder="https://your-cluster.example.com/elasticsearch",
                key="cluster_fetch_host",
            )
        with col2:
            verify_ssl_c = st.checkbox("Verify SSL certificate", value=True, key="c_verify_ssl")

        col3, col4 = st.columns(2)
        with col3:
            es_user_c = st.text_input("Username", value="elastic", key="cluster_fetch_user")
        with col4:
            es_pass_c = st.text_input("Password", type="password", key="cluster_fetch_pass")

        st.subheader("2. Choose assets to export")
        select_all_c = st.checkbox("Select all asset types", value=True, key="cluster_select_all")
        if select_all_c:
            selected_assets = st.multiselect(
                "Asset types", options=cl.ASSET_TYPES,
                default=cl.ASSET_TYPES, key="cluster_asset_select_all",
                format_func=lambda x: cl.ASSET_LABELS.get(x, x)
            )
        else:
            selected_assets = st.multiselect(
                "Asset types", options=cl.ASSET_TYPES,
                default=["index_templates", "ilm_policies", "component_templates"],
                key="cluster_asset_select_custom",
                format_func=lambda x: cl.ASSET_LABELS.get(x, x)
            )

        st.subheader("3. Run the backup")
        run_c_clicked = st.button("🚀 Fetch cluster assets", type="primary", key="cluster_fetch_btn")

        if run_c_clicked:
            if not es_host_c or not es_user_c or not es_pass_c:
                st.error("Please fill in the URL, username, and password.")
            elif not selected_assets:
                st.error("Select at least one asset type to export.")
            else:
                progress_bar_c = st.progress(0, text="Starting...")

                def c_progress_cb(current, total, message):
                    pct = int((current / total) * 100) if total else 0
                    progress_bar_c.progress(min(pct, 100), text=f"{message} ({current}/{total})")

                try:
                    with st.spinner("Connecting and fetching cluster assets..."):
                        results = cl.run_fetch_cluster_assets(
                            es_host=es_host_c, username=es_user_c, password=es_pass_c,
                            export_types=selected_assets, verify_ssl=verify_ssl_c,
                            progress_callback=c_progress_cb
                        )

                    progress_bar_c.progress(100, text="Done")
                    
                    total_assets_found = sum(len(items) for items in results["assets"].values())
                    st.success(f"✅ Fetched {total_assets_found} total asset(s). {len(results['errors'])} error(s).")
                    
                    if results["errors"]:
                        with st.expander(f"⚠️ {len(results['errors'])} error(s) — click to view"):
                            st.json(results["errors"])

                    if total_assets_found > 0:
                        zip_buffer_c = cl.build_cluster_assets_zip(results)
                        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                        
                        st.markdown("### 📦 Your backup is ready")
                        st.download_button(
                            label="⬇️ Download cluster assets ZIP",
                            data=zip_buffer_c,
                            file_name=f"cluster_assets_backup_{timestamp}.zip",
                            mime="application/zip",
                            type="primary",
                        )
                        
                except Exception as exc:
                    st.error(f"❌ Fetch failed: {exc}")

    # ── RESTORE ────────────────────────────────────────────────────────────
    with sub_crestore:
        st.warning(
            "⚠️ **This writes to a live cluster.** Use this only to restore "
            "assets into a **new/target** cluster."
        )

        st.subheader("1. Upload cluster assets ZIP")
        uploaded_zip_c = st.file_uploader(
            "Upload the ZIP file produced by the Fetch tab",
            type=["zip"],
            key="cluster_restore_zip",
        )

        if uploaded_zip_c:
            try:
                import json
                with zipfile.ZipFile(uploaded_zip_c) as zf:
                    json_names = [n for n in zf.namelist() if n.endswith(".json")]
                    
                    available_types = set()
                    available_items_by_type = {}
                    
                    for name in json_names:
                        parts = name.split("/")
                        if len(parts) == 2:
                            atype, fname = parts[0], parts[1]
                            asset_name = fname.replace(".json", "")
                            available_types.add(atype)
                            available_items_by_type.setdefault(atype, []).append(asset_name)

                    restored_cluster = st.session_state.get("restored_cluster", set())

                    st.info(f"Found {len(json_names)} asset file(s) across {len(available_types)} categories in the uploaded ZIP.")
                    
                    st.subheader("2. Select assets to restore")
                    selected_c_types = st.multiselect(
                        "1. Select asset categories to restore",
                        options=sorted(list(available_types)),
                        default=sorted(list(available_types)),
                        format_func=lambda x: cl.ASSET_LABELS.get(x, x),
                        key="cr_type_select"
                    )
                    
                    selected_c_items = []
                    if selected_c_types:
                        candidate_items = []
                        for t in selected_c_types:
                            for item in available_items_by_type[t]:
                                item_key = f"{t}/{item}"
                                prefix = "✅ " if item_key in restored_cluster else ""
                                candidate_items.append(f"{prefix}[{cl.ASSET_LABELS.get(t, t)}] {item}")
                                
                        selected_c_items = st.multiselect(
                            "2. Select specific items to restore (leave empty to restore ALL items in the selected categories)",
                            options=candidate_items,
                            default=[],
                            key="cr_item_select"
                        )
                    
                    # Determine final candidates
                    final_candidates = []
                    for name in json_names:
                        parts = name.split("/")
                        if len(parts) != 2: continue
                        atype, fname = parts[0], parts[1]
                        asset_name = fname.replace(".json", "")
                        
                        if atype not in selected_c_types:
                            continue
                            
                        item_key = f"{atype}/{asset_name}"
                        prefix = "✅ " if item_key in restored_cluster else ""
                        item_label = f"{prefix}[{cl.ASSET_LABELS.get(atype, atype)}] {asset_name}"
                        if selected_c_items and item_label not in selected_c_items:
                            continue
                            
                        final_candidates.append((atype, asset_name, item_label, name))
                    
                    if final_candidates:
                        st.subheader("3. View and Edit Assets (Optional)")
                        
                        if "edit_buffers" not in st.session_state:
                            st.session_state["edit_buffers"] = {}
                        if "last_zip_name" not in st.session_state or st.session_state["last_zip_name"] != uploaded_zip_c.name:
                            st.session_state["edit_buffers"] = {}
                            st.session_state["last_zip_name"] = uploaded_zip_c.name
                            
                        edit_options = [c[2] for c in final_candidates]
                        selected_edit = st.selectbox("Select an asset to view/edit", options=edit_options, key="cr_edit_select")
                        
                        if selected_edit:
                            idx = edit_options.index(selected_edit)
                            c_atype, c_aname, c_label, c_path = final_candidates[idx]
                            buffer_key = f"{c_atype}/{c_aname}"
                            name_buffer_key = f"name_{c_atype}/{c_aname}"
                            
                            if buffer_key not in st.session_state["edit_buffers"]:
                                orig_dict = json.loads(zf.read(c_path).decode("utf-8"))
                                st.session_state["edit_buffers"][buffer_key] = json.dumps(orig_dict, indent=2)
                                st.session_state["edit_buffers"][name_buffer_key] = c_aname
                                
                            current_val = st.session_state["edit_buffers"][buffer_key]
                            current_name = st.session_state["edit_buffers"].get(name_buffer_key, c_aname)
                            
                            new_name = st.text_input(f"Target Name for {c_label}", value=current_name, key=f"text_{name_buffer_key}")
                            if new_name != current_name:
                                st.session_state["edit_buffers"][name_buffer_key] = new_name
                            
                            new_val = st.text_area(f"JSON Definition: {c_label}", value=current_val, height=300, key=f"text_{buffer_key}")
                            if new_val != current_val:
                                st.session_state["edit_buffers"][buffer_key] = new_val

                    st.subheader("4. Connect to target cluster")
                    col1, col2 = st.columns([2, 1])
                    with col1:
                        target_host_c = st.text_input(
                            "Target Elasticsearch URL",
                            placeholder="https://new-cluster.example.com:9200",
                            key="cluster_restore_host",
                        )
                    with col2:
                        verify_ssl_cr = st.checkbox("Verify SSL certificate", value=True, key="cr_verify_ssl")
            
                    col3, col4 = st.columns(2)
                    with col3:
                        target_user_c = st.text_input("Username", value="elastic", key="cluster_restore_user")
                    with col4:
                        target_pass_c = st.text_input("Password", type="password", key="cluster_restore_pass")

                    if target_host_c and selected_c_types:
                        st.subheader("5. Confirm and restore")
                        confirm_phrase_c = st.text_input(
                            "Type RESTORE to confirm you want to write to the target cluster above",
                            key="cluster_restore_confirm",
                        )
                        
                        if st.button("🚀 Run restore", type="primary", key="cluster_restore_btn"):
                            if confirm_phrase_c != "RESTORE":
                                st.error("Confirmation phrase did not match. Type exactly: RESTORE")
                            elif target_host_c.rstrip("/") == es_host_c.rstrip("/") if 'es_host_c' in locals() and es_host_c else False:
                                st.error("Target host matches the source host from the Fetch tab. Aborting for safety.")
                            elif not target_user_c or not target_pass_c:
                                st.error("Please fill in target username and password.")
                            else:
                                asset_files = {}
                                items_to_restore = 0
                                for name in json_names:
                                    parts = name.split("/")
                                    if len(parts) != 2: continue
                                    atype, fname = parts[0], parts[1]
                                    asset_name = fname.replace(".json", "")
                                    
                                    if atype not in selected_c_types:
                                        continue
                                        
                                    item_label = f"[{cl.ASSET_LABELS.get(atype, atype)}] {asset_name}"
                                    if selected_c_items and item_label not in selected_c_items:
                                        continue
                                        
                                    buffer_key = f"{atype}/{asset_name}"
                                    name_buffer_key = f"name_{atype}/{asset_name}"
                                    
                                    target_asset_name = st.session_state.get("edit_buffers", {}).get(name_buffer_key, asset_name)
                                    
                                    if buffer_key in st.session_state.get("edit_buffers", {}):
                                        try:
                                            body = json.loads(st.session_state["edit_buffers"][buffer_key])
                                        except Exception as exc:
                                            st.error(f"Invalid JSON in edited asset {item_label}: {exc}")
                                            st.stop()
                                    else:
                                        body = json.loads(zf.read(name).decode("utf-8"))
                                        
                                    if atype not in asset_files:
                                        asset_files[atype] = {}
                                    asset_files[atype][target_asset_name] = body
                                    items_to_restore += 1
                                    
                                if items_to_restore == 0:
                                    st.warning("No items matched your selection criteria.")
                                else:
                                    progress_bar_cr = st.progress(0, text="Starting restore...")

                                    def cr_progress_cb(current, total, message):
                                        pct = int((current / total) * 100) if total else 0
                                        progress_bar_cr.progress(min(pct, 100), text=f"{message} ({current}/{total})")
                                        
                                    try:
                                        result = cl.run_restore_cluster_assets(
                                            target_host=target_host_c, username=target_user_c,
                                            password=target_pass_c, asset_files=asset_files,
                                            verify_ssl=verify_ssl_cr, progress_callback=cr_progress_cb
                                        )
                                        progress_bar_cr.progress(100, text="Done")
                                        st.success(f"✅ Restored {len(result['success'])} asset(s). {len(result['failed'])} failed.")
                                        # Track restored cluster assets for the session indicator
                                        if "restored_cluster" not in st.session_state:
                                            st.session_state["restored_cluster"] = set()
                                        for r in result["success"]:
                                            st.session_state["restored_cluster"].add(f"{r.get('type', '')}/{r.get('name', '')}")
                                        if result["failed"]:
                                            with st.expander("⚠️ Failed assets — click to view"):
                                                st.json(result["failed"])
                                    except Exception as exc:
                                        st.error(f"❌ Restore failed: {exc}")
            except zipfile.BadZipFile:
                st.error("That doesn't look like a valid ZIP file.")


# ════════════════════════════════════════════════════════════════════════════
#   TAB 4 — ML ASSETS BACKUP
# ════════════════════════════════════════════════════════════════════════════

with tab_ml:
    st.header("Elasticsearch ML Assets Backup")

    sub_mlfetch, sub_mlrestore = st.tabs(["⬇️ Fetch (backup)", "⬆️ Restore (advanced)"])

    # ── FETCH ──────────────────────────────────────────────────────────────
    with sub_mlfetch:
        st.subheader("1. Connect to source cluster")
        col1, col2 = st.columns([2, 1])
        with col1:
            es_host_ml = st.text_input(
                "Elasticsearch URL",
                placeholder="https://your-cluster.example.com:9200",
                key="ml_fetch_host",
            )
        with col2:
            verify_ssl_ml = st.checkbox("Verify SSL certificate", value=True, key="ml_verify_ssl")

        col3, col4 = st.columns(2)
        with col3:
            es_user_ml = st.text_input("Username", value="elastic", key="ml_fetch_user")
        with col4:
            es_pass_ml = st.text_input("Password", type="password", key="ml_fetch_pass")

        st.subheader("2. Choose ML asset types to export")
        selected_ml_types = st.multiselect(
            "Asset types",
            options=ml.ML_ASSET_TYPES,
            default=ml.ML_ASSET_TYPES,
            format_func=lambda x: ml.ML_ASSET_LABELS.get(x, x),
            key="ml_type_select",
        )

        st.subheader("3. Run the backup")
        if st.button("🚀 Fetch ML assets", type="primary", key="ml_fetch_btn"):
            if not es_host_ml or not es_user_ml or not es_pass_ml:
                st.error("Please fill in the URL, username, and password.")
            elif not selected_ml_types:
                st.error("Select at least one ML asset type to export.")
            else:
                progress_bar_ml = st.progress(0, text="Starting...")

                def ml_progress_cb(current, total, message):
                    pct = int((current / total) * 100) if total else 0
                    progress_bar_ml.progress(min(pct, 100), text=f"{message} ({current}/{total})")

                try:
                    result_ml = ml.run_fetch_ml_assets(
                        es_host=es_host_ml, username=es_user_ml, password=es_pass_ml,
                        export_types=selected_ml_types, verify_ssl=verify_ssl_ml,
                        progress_callback=ml_progress_cb,
                    )
                    progress_bar_ml.progress(100, text="Done")

                    total_assets = sum(len(v) for v in result_ml["assets"].values())
                    st.success(f"✅ Fetched {total_assets} ML asset(s).")

                    if result_ml["errors"]:
                        with st.expander(f"⚠️ {len(result_ml['errors'])} error(s) — click to view"):
                            st.json(result_ml["errors"])

                    if total_assets > 0:
                        timestamp_ml = datetime.now().strftime("%Y%m%d_%H%M%S")
                        zip_buffer_ml = ml.build_ml_assets_zip(result_ml)

                        st.markdown("### 📦 Your backup is ready")
                        import pandas as pd
                        preview_data_ml = [
                            {"Asset Type": ml.ML_ASSET_LABELS.get(at, at), "Name": name}
                            for at, items in result_ml["assets"].items()
                            for name in items
                        ]
                        if preview_data_ml:
                            st.dataframe(pd.DataFrame(preview_data_ml), use_container_width=True)

                        st.download_button(
                            label="⬇️ Download ML assets ZIP",
                            data=zip_buffer_ml,
                            file_name=f"ml_assets_backup_{timestamp_ml}.zip",
                            mime="application/zip",
                            type="primary",
                            key="ml_download_btn",
                        )

                except Exception as exc:
                    st.error(f"❌ Fetch failed: {exc}")

    # ── RESTORE ────────────────────────────────────────────────────────────
    with sub_mlrestore:
        st.warning(
            "⚠️ **This writes to a live cluster.** Use this only to restore "
            "ML assets into a **new/target** cluster."
        )

        st.subheader("1. Upload ML assets ZIP")
        uploaded_zip_ml = st.file_uploader(
            "Upload the ZIP file produced by the Fetch tab",
            type=["zip"],
            key="ml_restore_zip",
        )

        if uploaded_zip_ml:
            try:
                with zipfile.ZipFile(uploaded_zip_ml) as zf_ml:
                    ml_json_names = [n for n in zf_ml.namelist() if n.endswith(".json")]

                    available_ml_types = set()
                    available_ml_items_by_type = {}
                    for n in ml_json_names:
                        parts = n.split("/")
                        if len(parts) == 2:
                            atype, fname = parts
                            aname = fname.replace(".json", "")
                            available_ml_types.add(atype)
                            available_ml_items_by_type.setdefault(atype, []).append(aname)

                    restored_ml = st.session_state.get("restored_ml", set())

                    st.info(f"Found {len(ml_json_names)} asset file(s) across {len(available_ml_types)} categories.")

                    st.subheader("2. Select assets to restore")
                    selected_ml_r_types = st.multiselect(
                        "1. Select asset categories to restore",
                        options=sorted(list(available_ml_types)),
                        default=sorted(list(available_ml_types)),
                        format_func=lambda x: ml.ML_ASSET_LABELS.get(x, x),
                        key="mlr_type_select",
                    )

                    selected_ml_r_items = []
                    if selected_ml_r_types:
                        candidate_ml_items = []
                        for t in selected_ml_r_types:
                            for item in available_ml_items_by_type.get(t, []):
                                item_key = f"{t}/{item}"
                                prefix = "✅ " if item_key in restored_ml else ""
                                candidate_ml_items.append(f"{prefix}[{ml.ML_ASSET_LABELS.get(t, t)}] {item}")
                        selected_ml_r_items = st.multiselect(
                            "2. Select specific items to restore (leave empty to restore ALL in selected categories)",
                            options=candidate_ml_items,
                            default=[],
                            key="mlr_item_select",
                        )

                    final_ml_candidates = []
                    for n in ml_json_names:
                        parts = n.split("/")
                        if len(parts) != 2: continue
                        atype, fname = parts
                        aname = fname.replace(".json", "")
                        if atype not in selected_ml_r_types: continue
                        item_key = f"{atype}/{aname}"
                        prefix = "✅ " if item_key in restored_ml else ""
                        item_label = f"{prefix}[{ml.ML_ASSET_LABELS.get(atype, atype)}] {aname}"
                        if selected_ml_r_items and item_label not in selected_ml_r_items: continue
                        final_ml_candidates.append((atype, aname, item_label, n))

                    if final_ml_candidates:
                        st.subheader("3. View and Edit Assets (Optional)")
                        if "edit_ml_buffers" not in st.session_state:
                            st.session_state["edit_ml_buffers"] = {}
                        if st.session_state.get("last_ml_zip_name") != uploaded_zip_ml.name:
                            st.session_state["edit_ml_buffers"] = {}
                            st.session_state["last_ml_zip_name"] = uploaded_zip_ml.name

                        ml_edit_options = [c[2] for c in final_ml_candidates]
                        selected_ml_edit = st.selectbox(
                            "Select an asset to view/edit", options=ml_edit_options, key="mlr_edit_select"
                        )

                        if selected_ml_edit:
                            idx = ml_edit_options.index(selected_ml_edit)
                            c_atype, c_aname, c_label, c_path = final_ml_candidates[idx]
                            buffer_key = f"ml/{c_atype}/{c_aname}"
                            name_buffer_key = f"mlname/{c_atype}/{c_aname}"

                            if buffer_key not in st.session_state["edit_ml_buffers"]:
                                orig = json.loads(zf_ml.read(c_path).decode("utf-8"))
                                st.session_state["edit_ml_buffers"][buffer_key] = json.dumps(orig, indent=2)
                                st.session_state["edit_ml_buffers"][name_buffer_key] = c_aname

                            current_ml_val = st.session_state["edit_ml_buffers"][buffer_key]
                            current_ml_name = st.session_state["edit_ml_buffers"].get(name_buffer_key, c_aname)

                            new_ml_name = st.text_input(
                                f"Target Name for {c_label}", value=current_ml_name, key=f"text_{name_buffer_key}"
                            )
                            if new_ml_name != current_ml_name:
                                st.session_state["edit_ml_buffers"][name_buffer_key] = new_ml_name

                            new_ml_val = st.text_area(
                                f"JSON Definition: {c_label}", value=current_ml_val, height=300, key=f"text_{buffer_key}"
                            )
                            if new_ml_val != current_ml_val:
                                st.session_state["edit_ml_buffers"][buffer_key] = new_ml_val

                    st.subheader("4. Connect to target cluster")
                    col1, col2 = st.columns([2, 1])
                    with col1:
                        target_host_ml = st.text_input(
                            "Target Elasticsearch URL",
                            placeholder="https://new-cluster.example.com:9200",
                            key="ml_restore_host",
                        )
                    with col2:
                        verify_ssl_mlr = st.checkbox("Verify SSL certificate", value=True, key="mlr_verify_ssl")

                    col3, col4 = st.columns(2)
                    with col3:
                        target_user_ml = st.text_input("Username", value="elastic", key="ml_restore_user")
                    with col4:
                        target_pass_ml = st.text_input("Password", type="password", key="ml_restore_pass")

                    if target_host_ml and selected_ml_r_types:
                        st.subheader("5. Confirm and restore")
                        confirm_phrase_ml = st.text_input(
                            "Type RESTORE to confirm you want to write to the target cluster above",
                            key="ml_restore_confirm",
                        )

                        if st.button("🚀 Run restore", type="primary", key="ml_restore_btn"):
                            if confirm_phrase_ml != "RESTORE":
                                st.error("Confirmation phrase did not match. Type exactly: RESTORE")
                            elif target_host_ml.rstrip("/") == es_host_ml.rstrip("/") if 'es_host_ml' in locals() and es_host_ml else False:
                                st.error("Target host matches the source host from the Fetch tab. Aborting for safety.")
                            elif not target_user_ml or not target_pass_ml:
                                st.error("Please fill in target username and password.")
                            else:
                                ml_asset_files = {}
                                items_to_restore_ml = 0
                                for atype, aname, item_label, n in final_ml_candidates:
                                    buffer_key = f"ml/{atype}/{aname}"
                                    name_buffer_key = f"mlname/{atype}/{aname}"
                                    target_aname = st.session_state.get("edit_ml_buffers", {}).get(name_buffer_key, aname)

                                    if buffer_key in st.session_state.get("edit_ml_buffers", {}):
                                        try:
                                            body = json.loads(st.session_state["edit_ml_buffers"][buffer_key])
                                        except Exception as exc:
                                            st.error(f"Invalid JSON in edited asset {item_label}: {exc}")
                                            st.stop()
                                    else:
                                        body = json.loads(zf_ml.read(n).decode("utf-8"))

                                    ml_asset_files.setdefault(atype, {})[target_aname] = body
                                    items_to_restore_ml += 1

                                if items_to_restore_ml == 0:
                                    st.warning("No items matched your selection criteria.")
                                else:
                                    progress_bar_mlr = st.progress(0, text="Starting restore...")

                                    def mlr_progress_cb(current, total, message):
                                        pct = int((current / total) * 100) if total else 0
                                        progress_bar_mlr.progress(min(pct, 100), text=f"{message} ({current}/{total})")

                                    try:
                                        result_mlr = ml.run_restore_ml_assets(
                                            target_host=target_host_ml, username=target_user_ml,
                                            password=target_pass_ml, asset_files=ml_asset_files,
                                            verify_ssl=verify_ssl_mlr, progress_callback=mlr_progress_cb,
                                        )
                                        progress_bar_mlr.progress(100, text="Done")
                                        st.success(f"✅ Restored {len(result_mlr['success'])} asset(s). {len(result_mlr['failed'])} failed.")
                                        # Track restored ML assets for the session indicator
                                        if "restored_ml" not in st.session_state:
                                            st.session_state["restored_ml"] = set()
                                        for r in result_mlr["success"]:
                                            st.session_state["restored_ml"].add(f"{r.get('type', '')}/{r.get('name', '')}")
                                        if result_mlr["failed"]:
                                            with st.expander("⚠️ Failed assets — click to view"):
                                                st.json(result_mlr["failed"])
                                    except Exception as exc:
                                        st.error(f"❌ Restore failed: {exc}")

            except zipfile.BadZipFile:
                st.error("That doesn't look like a valid ZIP file.")


# ════════════════════════════════════════════════════════════════════════════
#   TAB 5 — SECURITY ASSETS (USERS & ROLES)
# ════════════════════════════════════════════════════════════════════════════

with tab_security:
    st.header("Elasticsearch Security Assets Backup (Users & Roles)")

    sub_sfetch, sub_srestore = st.tabs(["⬇️ Fetch (backup)", "⬆️ Restore (advanced)"])

    # ── FETCH ──────────────────────────────────────────────────────────────
    with sub_sfetch:
        st.subheader("1. Connect to source cluster")
        col1, col2 = st.columns([2, 1])
        with col1:
            es_host_s = st.text_input(
                "Elasticsearch URL",
                placeholder="https://your-cluster.example.com:9200",
                key="security_fetch_host",
            )
        with col2:
            verify_ssl_s = st.checkbox("Verify SSL certificate", value=True, key="s_verify_ssl")

        col3, col4 = st.columns(2)
        with col3:
            es_user_s = st.text_input("Username", value="elastic", key="security_fetch_user")
        with col4:
            es_pass_s = st.text_input("Password", type="password", key="security_fetch_pass")

        st.subheader("2. Choose what to export")
        include_reserved_s = st.checkbox(
            "Include reserved users/roles (built-ins)",
            value=False,
            key="security_include_reserved",
            help="Reserved = built-in users (`type: reserved-user`) and roles "
                 "(`metadata._reserved: true`). Off by default — only custom "
                 "users/roles are backed up.",
        )
        selected_s_types = st.multiselect(
            "Asset types",
            options=sl.SECURITY_ASSET_TYPES,
            default=sl.SECURITY_ASSET_TYPES,
            format_func=lambda x: sl.SECURITY_ASSET_LABELS.get(x, x),
            key="security_type_select",
        )
        with st.expander("Advanced"):
            security_index = st.text_input(
                "Security index name",
                value=sl.SECURITY_INDEX,
                key="security_index_input",
                help="The internal index holding native user docs incl. password hashes. "
                     "Defaults to .security-7.",
            )

        st.subheader("3. Run the backup")
        if st.button("🚀 Fetch security assets", type="primary", key="security_fetch_btn"):
            if not es_host_s or not es_user_s or not es_pass_s:
                st.error("Please fill in the URL, username, and password.")
            elif not selected_s_types:
                st.error("Select at least one asset type to export.")
            else:
                progress_bar_s = st.progress(0, text="Starting...")

                def s_progress_cb(current, total, message):
                    pct = int((current / total) * 100) if total else 0
                    progress_bar_s.progress(min(pct, 100), text=f"{message} ({current}/{total})")

                try:
                    result_s = sl.run_fetch_security_assets(
                        es_host=es_host_s, username=es_user_s, password=es_pass_s,
                        export_types=selected_s_types, include_reserved=include_reserved_s,
                        security_index=security_index, verify_ssl=verify_ssl_s,
                        progress_callback=s_progress_cb,
                    )
                    progress_bar_s.progress(100, text="Done")

                    total_assets_s = sum(len(v) for v in result_s["assets"].values())
                    st.success(f"✅ Fetched {total_assets_s} security asset(s). "
                               f"{len(result_s['errors'])} error(s).")

                    if result_s["errors"]:
                        with st.expander(f"⚠️ {len(result_s['errors'])} error(s) — click to view"):
                            st.json(result_s["errors"])
                        if any(e["type"] == "users" for e in result_s["errors"]):
                            st.warning(
                                "Users could not be read from the security index. The "
                                "user needs `manage_security` (or direct read access to "
                                "the security index) to export password hashes."
                            )

                    if total_assets_s > 0:
                        timestamp_s = datetime.now().strftime("%Y%m%d_%H%M%S")
                        zip_buffer_s = sl.build_security_assets_zip(result_s)

                        st.markdown("### 📦 Your backup is ready")

                        import pandas as pd
                        preview_data_s = [
                            {
                                "Asset Type": sl.SECURITY_ASSET_LABELS.get(at, at),
                                "Name": name,
                                "Reserved": bool((result_s.get("meta", {}).get(at, {}).get(name) or {}).get("reserved")),
                            }
                            for at, items in result_s["assets"].items()
                            for name in items
                        ]
                        if preview_data_s:
                            st.dataframe(pd.DataFrame(preview_data_s), use_container_width=True)

                        st.download_button(
                            label="⬇️ Download security assets ZIP",
                            data=zip_buffer_s,
                            file_name=f"security_assets_backup_{timestamp_s}.zip",
                            mime="application/zip",
                            type="primary",
                            key="security_download_btn",
                        )

                except Exception as exc:
                    st.error(f"❌ Fetch failed: {exc}")

    # ── RESTORE ────────────────────────────────────────────────────────────
    with sub_srestore:
        st.warning(
            "⚠️ **This writes to a live cluster.** Use this only to restore "
            "security assets into a **new/target** cluster."
        )

        st.subheader("1. Upload security assets ZIP")
        uploaded_zip_s = st.file_uploader(
            "Upload the ZIP file produced by the Fetch tab",
            type=["zip"],
            key="security_restore_zip",
        )

        if uploaded_zip_s:
            try:
                with zipfile.ZipFile(uploaded_zip_s) as zf_s:
                    s_json_names = [n for n in zf_s.namelist() if n.endswith(".json") and n != "security_meta.json"]

                    sec_meta = {}
                    if "security_meta.json" in zf_s.namelist():
                        try:
                            sec_meta = json.loads(zf_s.read("security_meta.json").decode("utf-8"))
                        except Exception:
                            sec_meta = {}

                    available_s_types = set()
                    available_s_items_by_type = {}
                    for n in s_json_names:
                        parts = n.split("/")
                        if len(parts) == 2:
                            atype, fname = parts
                            aname = fname.replace(".json", "")
                            available_s_types.add(atype)
                            available_s_items_by_type.setdefault(atype, []).append(aname)

                    restored_sec = st.session_state.get("restored_security", set())

                    st.info(f"Found {len(s_json_names)} asset file(s) across "
                            f"{len(available_s_types)} categories in the uploaded ZIP.")

                    st.subheader("2. Select assets to restore")
                    include_reserved_sr = st.checkbox(
                        "Include reserved users/roles (built-ins)",
                        value=False,
                        key="security_restore_include_reserved",
                    )
                    selected_sr_types = st.multiselect(
                        "1. Select asset categories to restore",
                        options=sorted(list(available_s_types)),
                        default=sorted(list(available_s_types)),
                        format_func=lambda x: sl.SECURITY_ASSET_LABELS.get(x, x),
                        key="sr_type_select",
                    )

                    selected_sr_items = []
                    if selected_sr_types:
                        candidate_sr_items = []
                        for t in selected_sr_types:
                            for item in available_s_items_by_type.get(t, []):
                                reserved = bool((sec_meta.get(t, {}).get(item) or {}).get("reserved"))
                                if reserved and not include_reserved_sr:
                                    continue
                                item_key = f"{t}/{item}"
                                prefix = "✅ " if item_key in restored_sec else ""
                                candidate_sr_items.append(f"{prefix}[{sl.SECURITY_ASSET_LABELS.get(t, t)}] {item}")
                        selected_sr_items = st.multiselect(
                            "2. Select specific items to restore (leave empty to restore ALL in the selected categories)",
                            options=candidate_sr_items,
                            default=[],
                            key="sr_item_select",
                        )

                    final_sr_candidates = []
                    for n in s_json_names:
                        parts = n.split("/")
                        if len(parts) != 2: continue
                        atype, fname = parts
                        aname = fname.replace(".json", "")
                        if atype not in selected_sr_types: continue
                        reserved = bool((sec_meta.get(atype, {}).get(aname) or {}).get("reserved"))
                        if reserved and not include_reserved_sr: continue
                        item_key = f"{atype}/{aname}"
                        prefix = "✅ " if item_key in restored_sec else ""
                        item_label = f"{prefix}[{sl.SECURITY_ASSET_LABELS.get(atype, atype)}] {aname}"
                        if selected_sr_items and item_label not in selected_sr_items: continue
                        final_sr_candidates.append((atype, aname, item_label, n))

                    if final_sr_candidates:
                        st.subheader("3. View and Edit Assets (Optional)")
                        if "edit_s_buffers" not in st.session_state:
                            st.session_state["edit_s_buffers"] = {}
                        if st.session_state.get("last_s_zip_name") != uploaded_zip_s.name:
                            st.session_state["edit_s_buffers"] = {}
                            st.session_state["last_s_zip_name"] = uploaded_zip_s.name

                        s_edit_options = [c[2] for c in final_sr_candidates]
                        selected_s_edit = st.selectbox(
                            "Select an asset to view/edit", options=s_edit_options, key="sr_edit_select"
                        )

                        if selected_s_edit:
                            idx = s_edit_options.index(selected_s_edit)
                            c_atype, c_aname, c_label, c_path = final_sr_candidates[idx]
                            buffer_key = f"sec/{c_atype}/{c_aname}"
                            name_buffer_key = f"secname/{c_atype}/{c_aname}"

                            if buffer_key not in st.session_state["edit_s_buffers"]:
                                orig = json.loads(zf_s.read(c_path).decode("utf-8"))
                                st.session_state["edit_s_buffers"][buffer_key] = json.dumps(orig, indent=2)
                                st.session_state["edit_s_buffers"][name_buffer_key] = c_aname

                            current_s_val = st.session_state["edit_s_buffers"][buffer_key]
                            current_s_name = st.session_state["edit_s_buffers"].get(name_buffer_key, c_aname)

                            new_s_name = st.text_input(
                                f"Target Name for {c_label}", value=current_s_name, key=f"text_{name_buffer_key}"
                            )
                            if new_s_name != current_s_name:
                                st.session_state["edit_s_buffers"][name_buffer_key] = new_s_name

                            new_s_val = st.text_area(
                                f"JSON Definition: {c_label}", value=current_s_val, height=300, key=f"text_{buffer_key}"
                            )
                            if new_s_val != current_s_val:
                                st.session_state["edit_s_buffers"][buffer_key] = new_s_val

                    st.subheader("4. Connect to target cluster")
                    col1, col2 = st.columns([2, 1])
                    with col1:
                        target_host_s = st.text_input(
                            "Target Elasticsearch URL",
                            placeholder="https://new-cluster.example.com:9200",
                            key="security_restore_host",
                        )
                    with col2:
                        verify_ssl_sr = st.checkbox("Verify SSL certificate", value=True, key="sr_verify_ssl")

                    col3, col4 = st.columns(2)
                    with col3:
                        target_user_s = st.text_input("Username", value="elastic", key="security_restore_user")
                    with col4:
                        target_pass_s = st.text_input("Password", type="password", key="security_restore_pass")

                    # ── Check existing items on the target ─────────────────
                    if final_sr_candidates and target_host_s:
                        st.subheader("5. Check existing items on target")
                        st.caption(
                            "Compares the selected items against the target cluster so you "
                            "can decide, per item, whether to overwrite or skip anything "
                            "that already exists."
                        )

                        if st.session_state.get("last_s_target_host") != target_host_s:
                            st.session_state["sec_existing"] = None
                            st.session_state["last_s_target_host"] = target_host_s

                        if st.button("🔍 Check existing roles/users on target", key="sr_check_existing_btn"):
                            if not target_user_s or not target_pass_s:
                                st.error("Please fill in target username and password.")
                            else:
                                try:
                                    with st.spinner("Querying target cluster..."):
                                        st.session_state["sec_existing"] = sl.check_security_existing(
                                            target_host=target_host_s, username=target_user_s,
                                            password=target_pass_s, verify_ssl=verify_ssl_sr,
                                        )
                                except Exception as exc:
                                    st.error(f"❌ Failed to check target cluster: {exc}")
                                    st.session_state["sec_existing"] = None

                        existing_data = st.session_state.get("sec_existing")
                        if existing_data:
                            existing_roles = existing_data.get("roles", set())
                            existing_users = existing_data.get("users", set())

                            target_names = {}
                            for atype, aname, label, _ in final_sr_candidates:
                                name_buffer_key = f"secname/{atype}/{aname}"
                                target_names[label] = st.session_state.get("edit_s_buffers", {}).get(name_buffer_key, aname)

                            conflict_items = []
                            for atype, aname, label, _ in final_sr_candidates:
                                tname = target_names[label]
                                if (atype == "roles" and tname in existing_roles) or \
                                   (atype == "users" and tname in existing_users):
                                    conflict_items.append((atype, aname, label, tname))

                            new_count = len(final_sr_candidates) - len(conflict_items)
                            st.markdown(
                                f"**{len(final_sr_candidates)} item(s) selected · "
                                f"{new_count} new · {len(conflict_items)} already exist on target.**"
                            )

                            overwrite_actions_s = {"roles": {}, "users": {}}
                            if conflict_items:
                                default_action = st.radio(
                                    "Default action for existing items",
                                    options=["Skip", "Overwrite"],
                                    index=0,
                                    key="sr_default_action",
                                    help="Skip = leave the existing item untouched. "
                                         "Overwrite = replace it with the backed-up definition.",
                                )
                                for i, (atype, aname, label, tname) in enumerate(conflict_items):
                                    chosen = st.radio(
                                        f"{label} → **{tname}** (already exists on target)",
                                        options=["Skip", "Overwrite"],
                                        index=0 if default_action == "Skip" else 1,
                                        key=f"sr_action_{atype}_{i}",
                                        horizontal=True,
                                    )
                                    overwrite_actions_s[atype][aname] = (chosen == "Overwrite")
                            else:
                                st.success("None of the selected items exist on the target — all will be created.")
                            st.session_state["sec_overwrite_actions"] = overwrite_actions_s

                    if target_host_s and final_sr_candidates:
                        st.subheader("6. Confirm and restore")
                    confirm_phrase_s = st.text_input(
                        "Type RESTORE to confirm you want to write to the target cluster above",
                        key="security_restore_confirm",
                    )

                    if st.button("🚀 Run restore", type="primary", key="security_restore_btn"):
                        if confirm_phrase_s != "RESTORE":
                            st.error("Confirmation phrase did not match. Type exactly: RESTORE")
                        elif target_host_s.rstrip("/") == es_host_s.rstrip("/") if 'es_host_s' in locals() and es_host_s else False:
                            st.error("Target host matches the source host from the Fetch tab. Aborting for safety.")
                        elif not target_user_s or not target_pass_s:
                            st.error("Please fill in target username and password.")
                        elif st.session_state.get("sec_existing") is None:
                            st.error("Please click '🔍 Check existing roles/users on target' first.")
                        else:
                            existing_data = st.session_state["sec_existing"]
                            s_asset_files = {}
                            items_to_restore_s = 0
                            for atype, aname, item_label, n in final_sr_candidates:
                                buffer_key = f"sec/{atype}/{aname}"
                                name_buffer_key = f"secname/{atype}/{aname}"
                                target_aname = st.session_state.get("edit_s_buffers", {}).get(name_buffer_key, aname)

                                if buffer_key in st.session_state.get("edit_s_buffers", {}):
                                    try:
                                        body = json.loads(st.session_state["edit_s_buffers"][buffer_key])
                                    except Exception as exc:
                                        st.error(f"Invalid JSON in edited asset {item_label}: {exc}")
                                        st.stop()
                                else:
                                    body = json.loads(zf_s.read(n).decode("utf-8"))

                                s_asset_files.setdefault(atype, {})[target_aname] = body
                                items_to_restore_s += 1

                            if items_to_restore_s == 0:
                                st.warning("No items matched your selection criteria.")
                            else:
                                overwrite_actions_s = st.session_state.get("sec_overwrite_actions", {"roles": {}, "users": {}})
                                progress_bar_sr = st.progress(0, text="Starting restore...")

                                def sr_progress_cb(current, total, message):
                                    pct = int((current / total) * 100) if total else 0
                                    progress_bar_sr.progress(min(pct, 100), text=f"{message} ({current}/{total})")

                                try:
                                    result_sr = sl.run_restore_security_assets(
                                        target_host=target_host_s, username=target_user_s,
                                        password=target_pass_s, asset_files=s_asset_files,
                                        existing=existing_data,
                                        overwrite_actions=overwrite_actions_s,
                                        verify_ssl=verify_ssl_sr, progress_callback=sr_progress_cb,
                                    )
                                    progress_bar_sr.progress(100, text="Done")
                                    st.success(
                                        f"✅ Restored {len(result_sr['success'])} asset(s). "
                                        f"{len(result_sr['skipped'])} skipped. "
                                        f"{len(result_sr['failed'])} failed."
                                    )
                                    if "restored_security" not in st.session_state:
                                        st.session_state["restored_security"] = set()
                                    for r in result_sr["success"]:
                                        st.session_state["restored_security"].add(f"{r.get('type', '')}/{r.get('name', '')}")
                                    if result_sr["skipped"]:
                                        with st.expander("⏭️ Skipped assets (already existed on target)"):
                                            st.json(result_sr["skipped"])
                                    if result_sr["failed"]:
                                        with st.expander("⚠️ Failed assets — click to view"):
                                            st.json(result_sr["failed"])
                                except Exception as exc:
                                    st.error(f"❌ Restore failed: {exc}")

            except zipfile.BadZipFile:
                st.error("That doesn't look like a valid ZIP file.")


# ════════════════════════════════════════════════════════════════════════════
#   TAB 6 — SCRIPTED → RUNTIME FIELDS MIGRATION
# ════════════════════════════════════════════════════════════════════════════

def _runtime_key(space, data_view_id, field_name):
    return f"{space}|{data_view_id}|{field_name}"


def _runtime_status_map(records=None):
    records = records if records is not None else st.session_state.get("runtime_scan_records", [])
    created = st.session_state.get("runtime_created", set())
    migrated = st.session_state.get("runtime_migrated", set())
    failed = st.session_state.get("runtime_failed", set())
    edit_buffers = st.session_state.get("edit_r_buffers", {})

    status_map = {}
    for r in records:
        key = _runtime_key(r["space"], r["data_view_id"], r["field_name"])
        target_name = edit_buffers.get(f"rname/{key}", r["field_name"])
        if key in migrated:
            status = "Migrated"
        elif key in created:
            status = "Runtime Created"
        elif key in failed:
            status = "Error"
        elif r.get("has_runtime_field"):
            status = "Runtime Exists"
        else:
            status = "Pending"
        status_map[key] = {"status": status, "target_name": target_name}
    return status_map


with tab_runtime:
    st.header("Scripted Fields → Runtime Fields Migration")
    st.caption(
        "Migrate Kibana **scripted fields** to **data-view runtime fields** on the "
        "destination cluster (scripted fields are removed in Elastic 9.x). "
        "Scanning is read-only; creating/deleting fields is opt-in and gated."
    )

    sub_rscan, sub_rmigrate = st.tabs(["📋 Scan & Report (read-only)", "🚀 Migrate (writes to cluster)"])

    # ── SCAN & REPORT ─────────────────────────────────────────────────────
    with sub_rscan:
        st.subheader("1. Connect to destination Kibana")
        col1, col2 = st.columns([2, 1])
        with col1:
            kb_host_r = st.text_input(
                "Kibana URL",
                placeholder="https://your-kibana.example.com:5601",
                key="runtime_fetch_host",
            )
        with col2:
            verify_ssl_r = st.checkbox("Verify SSL certificate", value=True, key="r_verify_ssl")

        col3, col4 = st.columns(2)
        with col3:
            kb_user_r = st.text_input("Username", value="elastic", key="runtime_fetch_user")
        with col4:
            kb_pass_r = st.text_input("Password", type="password", key="runtime_fetch_pass")

        if st.button("🔍 Scan scripted fields", type="primary", key="runtime_fetch_btn"):
            if not kb_host_r or not kb_user_r or not kb_pass_r:
                st.error("Please fill in the URL, username, and password.")
            else:
                progress_bar_r = st.progress(0, text="Starting...")

                def r_progress_cb(current, total, message):
                    pct = int((current / total) * 100) if total else 0
                    progress_bar_r.progress(min(pct, 100), text=f"{message} ({current}/{total})")

                try:
                    with st.spinner("Scanning spaces and data views..."):
                        scan_result = rfl.scan_scripted_fields(
                            kibana_host=kb_host_r, username=kb_user_r, password=kb_pass_r,
                            verify_ssl=verify_ssl_r, progress_callback=r_progress_cb,
                        )
                    progress_bar_r.progress(100, text="Done")

                    records = scan_result["records"]
                    errors = scan_result["errors"]
                    st.session_state["runtime_scan_records"] = records
                    st.session_state["runtime_scan_errors"] = errors

                    st.success(f"✅ Found {len(records)} scripted field(s) across "
                               f"{scan_result['data_view_count']} data view(s). {len(errors)} error(s).")

                    if errors:
                        with st.expander(f"⚠️ {len(errors)} error(s) — click to view"):
                            st.json(errors)

                    if records:
                        status_map = _runtime_status_map(records)
                        excel_bytes = rfl.build_report_excel_bytes(records, status_map)
                        timestamp_r = datetime.now().strftime("%Y%m%d_%H%M%S")

                        st.markdown("### 📄 Your report is ready")
                        st.download_button(
                            label="⬇️ Download scripted fields report (Excel)",
                            data=excel_bytes,
                            file_name=f"scripted_fields_report_{timestamp_r}.xlsx",
                            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                            type="primary",
                            key="runtime_report_download",
                        )

                        import pandas as pd
                        preview_r = pd.DataFrame([
                            {k: r[k] for k in ("space", "data_view_title", "data_view_id",
                                               "field_name", "field_type", "has_runtime_field")}
                            for r in records
                        ])
                        st.markdown("### Preview")
                        st.dataframe(preview_r, use_container_width=True)

                except Exception as exc:
                    st.error(f"❌ Scan failed: {exc}")

    # ── MIGRATE ───────────────────────────────────────────────────────────
    with sub_rmigrate:
        st.warning(
            "⚠️ **This writes to the connected Kibana instance.** Use this only on your "
            "**destination** cluster (e.g. 8.19). Creating runtime fields is reversible; "
            "deleting scripted fields is not."
        )

        records = st.session_state.get("runtime_scan_records", [])
        migrate_host = st.session_state.get("runtime_fetch_host", "")
        migrate_user = st.session_state.get("runtime_fetch_user", "elastic")
        migrate_pass = st.session_state.get("runtime_fetch_pass", "")
        migrate_verify = st.session_state.get("r_verify_ssl", True)

        if not records:
            st.info("No scan results yet. Run the **📋 Scan & Report** tab first to connect and scan.")
            if st.button("🔄 Rescan now", key="runtime_rescan_empty_btn"):
                if not migrate_host:
                    st.error("Fill in the Kibana connection on the 📋 Scan & Report tab first.")
                else:
                    with st.spinner("Scanning..."):
                        scan_result = rfl.scan_scripted_fields(
                            kibana_host=migrate_host, username=migrate_user, password=migrate_pass,
                            verify_ssl=migrate_verify,
                        )
                    st.session_state["runtime_scan_records"] = scan_result["records"]
                    st.session_state["runtime_scan_errors"] = scan_result["errors"]
                    st.success(f"Rescan complete: {len(scan_result['records'])} scripted field(s) across "
                               f"{scan_result['data_view_count']} data view(s).")
        else:
            st.subheader("1. Optional rescan")
            if st.button("🔄 Rescan now", key="runtime_rescan_btn"):
                if not migrate_host:
                    st.error("Fill in the Kibana connection on the 📋 Scan & Report tab first.")
                else:
                    with st.spinner("Scanning..."):
                        scan_result = rfl.scan_scripted_fields(
                            kibana_host=migrate_host, username=migrate_user, password=migrate_pass,
                            verify_ssl=migrate_verify,
                        )
                    st.session_state["runtime_scan_records"] = scan_result["records"]
                    st.session_state["runtime_scan_errors"] = scan_result["errors"]
                    st.success(f"Rescan complete: {len(scan_result['records'])} scripted field(s) across "
                               f"{scan_result['data_view_count']} data view(s).")

            st.info(f"Working from the last scan: **{len(records)} scripted field(s)** across "
                    f"{len({r['space'] for r in records})} space(s).")

            if migrate_host:
                st.caption(f"Writes will go to: `{migrate_host}`")

            st.subheader("2. Filter")
            sel_spaces = st.multiselect(
                "Spaces", options=sorted({r["space"] for r in records}),
                default=sorted({r["space"] for r in records}), key="rm_space_filter",
            )
            filtered_by_space = [r for r in records if r["space"] in sel_spaces]

            dvs = sorted({(r["data_view_id"], r["data_view_title"]) for r in filtered_by_space})
            dv_labels = [f"{t} ({i})" for i, t in dvs]
            dv_to_pair = {f"{t} ({i})": (i, t) for i, t in dvs}
            sel_dvs = st.multiselect("Data views", options=dv_labels, default=dv_labels, key="rm_dv_filter")
            selected_dv_pairs = {dv_to_pair[l] for l in sel_dvs}
            filtered_by_dv = [r for r in filtered_by_space
                              if (r["data_view_id"], r["data_view_title"]) in selected_dv_pairs]

            hide_migrated = st.checkbox("Hide already migrated", value=True, key="rm_hide_migrated")
            status_filter = st.multiselect(
                "Status",
                options=["Pending", "Runtime Created", "Runtime Exists", "Migrated", "Error"],
                default=["Pending", "Runtime Created", "Runtime Exists"],
                key="rm_status_filter",
            )

            status_map = _runtime_status_map()
            candidates = []
            for r in filtered_by_dv:
                key = _runtime_key(r["space"], r["data_view_id"], r["field_name"])
                status = status_map.get(key, {}).get("status", "Pending")
                if status not in status_filter:
                    continue
                if hide_migrated and status == "Migrated":
                    continue
                prefix = "✅ " if status == "Migrated" else ""
                label = f"{prefix}[{r['space']} / {r['data_view_title']}] {r['field_name']} — {status}"
                candidates.append((r, label, key))

            st.subheader("3. Select fields to migrate")
            candidate_labels = [c[1] for c in candidates]
            selected_labels = st.multiselect(
                "Select scripted fields (leave empty to select none)",
                options=candidate_labels, default=[], key="rm_item_select",
            )
            label_to_candidate = {c[1]: c for c in candidates}
            selected_items = [(label_to_candidate[l][0], label_to_candidate[l][2]) for l in selected_labels]

            if selected_items:
                st.subheader("4. View / Edit (optional)")
                if "edit_r_buffers" not in st.session_state:
                    st.session_state["edit_r_buffers"] = {}

                edit_labels = [
                    f"{c[0]['space']} / {c[0]['data_view_title']} / {c[0]['field_name']}"
                    for c in selected_items
                ]
                edit_label_map = {lbl: c for lbl, c in zip(edit_labels, selected_items)}
                selected_edit = st.selectbox(
                    "Select a scripted field to view/edit", options=edit_labels, key="rm_edit_select"
                )

                if selected_edit:
                    record, key = edit_label_map[selected_edit]
                    name_buf_key = f"rname/{key}"
                    type_buf_key = f"rtype/{key}"
                    script_buf_key = f"rscript/{key}"

                    if name_buf_key not in st.session_state["edit_r_buffers"]:
                        st.session_state["edit_r_buffers"][name_buf_key] = record["field_name"]
                        st.session_state["edit_r_buffers"][type_buf_key] = (
                            rfl.SCRIPTED_TO_RUNTIME_TYPE.get(record["field_type"], record["field_type"] or "keyword")
                        )
                        st.session_state["edit_r_buffers"][script_buf_key] = record["script"]

                    new_name = st.text_input(
                        f"Target runtime field name (default = {record['field_name']})",
                        value=st.session_state["edit_r_buffers"][name_buf_key],
                        key=f"text_{name_buf_key}",
                        help="Keeping the same name keeps existing dashboards/visualizations working.",
                    )
                    if new_name != record["field_name"]:
                        st.warning("⚠️ Renaming the runtime field may break references in saved objects.")
                    st.session_state["edit_r_buffers"][name_buf_key] = new_name

                    type_options = ["keyword", "long", "double", "date", "ip", "boolean", "geo_point"]
                    cur_type = st.session_state["edit_r_buffers"][type_buf_key]
                    type_idx = type_options.index(cur_type) if cur_type in type_options else 0
                    new_type = st.selectbox(
                        "Runtime field type", options=type_options, index=type_idx, key=f"sel_{type_buf_key}"
                    )
                    st.session_state["edit_r_buffers"][type_buf_key] = new_type

                    new_script = st.text_area(
                        "Painless script",
                        value=st.session_state["edit_r_buffers"][script_buf_key],
                        height=200,
                        key=f"text_{script_buf_key}",
                    )
                    st.session_state["edit_r_buffers"][script_buf_key] = new_script

                st.markdown("---")
                with st.expander("🧪 Optional: test script (syntax check)"):
                    st.caption(
                        "Validates the Painless script against Elasticsearch before creating "
                        "the runtime field (uses `/_scripts/painless/_execute` in the `filter` "
                        "context). Leave the sample document empty to auto-fetch a real "
                        "document from the index."
                    )
                    col1, col2 = st.columns([2, 1])
                    with col1:
                        es_host_t = st.text_input(
                            "Elasticsearch URL for testing",
                            placeholder="https://your-es.example.com:9200",
                            key="runtime_test_es_host",
                        )
                    with col2:
                        verify_ssl_rt = st.checkbox("Verify SSL", value=True, key="rt_verify_ssl")
                    col3, col4 = st.columns(2)
                    with col3:
                        es_user_t = st.text_input("ES Username", value="elastic", key="runtime_test_es_user")
                    with col4:
                        es_pass_t = st.text_input("ES Password", type="password", key="runtime_test_es_pass")

                    test_index_t = st.text_input(
                        "Index to test against (concrete, no wildcards)",
                        value=record["data_view_title"],
                        key="runtime_test_index",
                        help="The filter context needs a concrete index (data-view titles like "
                             "`logs-*` are not accepted). Change it if the data view is a wildcard pattern.",
                    )
                    sample_doc_t = st.text_area(
                        "Sample document (JSON, optional)",
                        value="{}",
                        key="runtime_test_sample_doc",
                        height=100,
                        help="Leave empty to auto-fetch any document from the index, or paste "
                             "{\"_id\": \"...\"} to test against that specific document, or paste "
                             "a full document body (raw hits with _index/_id/_source are "
                             "unwrapped automatically). Fields the script reads must be present.",
                    )

                    if st.button("🧪 Test selected script", key="runtime_test_btn"):
                        if not selected_edit:
                            st.warning("Select a scripted field in the editor first.")
                        elif not es_host_t:
                            st.error("Enter the Elasticsearch URL for testing.")
                        else:
                            try:
                                sample_doc = json.loads(sample_doc_t) if sample_doc_t.strip() else {}
                                if not isinstance(sample_doc, dict):
                                    raise ValueError("must be a JSON object")
                            except ValueError:
                                st.error("Sample document must be valid JSON (e.g. {\"field\": \"value\"}).")
                            else:
                                record_sel, key_sel = edit_label_map[selected_edit]
                                script_to_test = st.session_state["edit_r_buffers"].get(
                                    f"rscript/{key_sel}", record_sel["script"]
                                )
                                with st.spinner("Testing script..."):
                                    test_res = rfl.test_script(
                                        es_host_t, es_user_t, es_pass_t,
                                        test_index_t, script_to_test, verify_ssl_rt,
                                        sample_document=sample_doc,
                                    )
                                if test_res["ok"]:
                                    st.success(f"✅ Script compiled successfully against `{test_index_t}`.")
                                else:
                                    st.error(f"❌ Script test failed: {test_res.get('error', 'unknown error')}")

                st.subheader("5. Migrate actions")
                st.caption(
                    "**Step 1** creates runtime fields (reversible). **Step 2** deletes the "
                    "original scripted fields — only for fields whose runtime field was "
                    "created successfully, and only if you explicitly opt in."
                )

                confirm_phrase_r = st.text_input(
                    "Type MIGRATE to confirm write actions below",
                    key="runtime_confirm",
                )

                c1, c2 = st.columns(2)
                with c1:
                    if st.button("🚀 1. Create runtime fields", type="primary", key="runtime_create_btn"):
                        if confirm_phrase_r != "MIGRATE":
                            st.error("Confirmation phrase did not match. Type exactly: MIGRATE")
                        elif not migrate_host:
                            st.error("No Kibana connection found. Run the 📋 Scan & Report tab first.")
                        else:
                            items = []
                            for record, key in selected_items:
                                items.append({
                                    "space": record["space"],
                                    "data_view_id": record["data_view_id"],
                                    "field_name": record["field_name"],
                                    "target_name": st.session_state.get("edit_r_buffers", {}).get(
                                        f"rname/{key}", record["field_name"]),
                                    "field_type": st.session_state.get("edit_r_buffers", {}).get(
                                        f"rtype/{key}", record["field_type"] or "keyword"),
                                    "script": st.session_state.get("edit_r_buffers", {}).get(
                                        f"rscript/{key}", record["script"]),
                                })
                            progress_bar_rm = st.progress(0, text="Starting...")

                            def rm_progress_cb(current, total, message):
                                pct = int((current / total) * 100) if total else 0
                                progress_bar_rm.progress(min(pct, 100), text=f"{message} ({current}/{total})")

                            try:
                                with st.spinner("Creating runtime fields..."):
                                    res_rm = rfl.run_migrate(
                                        kibana_host=migrate_host, username=migrate_user,
                                        password=migrate_pass, items=items, delete_after=False,
                                        verify_ssl=migrate_verify, progress_callback=rm_progress_cb,
                                    )
                                progress_bar_rm.progress(100, text="Done")
                                if "runtime_created" not in st.session_state:
                                    st.session_state["runtime_created"] = set()
                                if "runtime_failed" not in st.session_state:
                                    st.session_state["runtime_failed"] = set()
                                for c_ in res_rm["created"]:
                                    st.session_state["runtime_created"].add(
                                        _runtime_key(c_["space"], c_["data_view_id"], c_["field_name"]))
                                for f_ in res_rm["failed"]:
                                    st.session_state["runtime_failed"].add(
                                        _runtime_key(f_["space"], f_["data_view_id"], f_["field_name"]))
                                st.success(f"✅ Created {len(res_rm['created'])} runtime field(s). "
                                           f"{len(res_rm['failed'])} failed.")
                                if res_rm["failed"]:
                                    with st.expander("⚠️ Failed creates — click to view"):
                                        st.json(res_rm["failed"])
                            except Exception as exc:
                                st.error(f"❌ Create failed: {exc}")

                with c2:
                    delete_confirmed = st.checkbox(
                        "I confirm: delete the original scripted fields for fields whose runtime "
                        "field was created successfully (irreversible)",
                        value=False,
                        key="runtime_delete_confirm",
                    )
                    if st.button("🗑️ 2. Delete scripted fields", type="secondary", key="runtime_delete_btn"):
                        if confirm_phrase_r != "MIGRATE":
                            st.error("Confirmation phrase did not match. Type exactly: MIGRATE")
                        elif not delete_confirmed:
                            st.error("Tick the confirmation checkbox to allow deletion.")
                        elif not migrate_host:
                            st.error("No Kibana connection found. Run the 📋 Scan & Report tab first.")
                        else:
                            delete_candidates = []
                            for record, key in selected_items:
                                if key in st.session_state.get("runtime_created", set()) or record.get("has_runtime_field"):
                                    delete_candidates.append({
                                        "space": record["space"],
                                        "data_view_id": record["data_view_id"],
                                        "field_name": record["field_name"],
                                    })
                            if not delete_candidates:
                                st.warning(
                                    "No selected field has a successfully created runtime field yet. "
                                    "Create the runtime fields first (Step 1)."
                                )
                            else:
                                progress_bar_rmd = st.progress(0, text="Starting...")

                                def rmd_progress_cb(current, total, message):
                                    pct = int((current / total) * 100) if total else 0
                                    progress_bar_rmd.progress(min(pct, 100), text=f"{message} ({current}/{total})")

                                try:
                                    with st.spinner("Deleting scripted fields..."):
                                        res_rmd = rfl.run_delete_scripted_fields(
                                            kibana_host=migrate_host, username=migrate_user,
                                            password=migrate_pass, items=delete_candidates,
                                            verify_ssl=migrate_verify, progress_callback=rmd_progress_cb,
                                        )
                                    progress_bar_rmd.progress(100, text="Done")
                                    if "runtime_migrated" not in st.session_state:
                                        st.session_state["runtime_migrated"] = set()
                                    if "runtime_failed" not in st.session_state:
                                        st.session_state["runtime_failed"] = set()
                                    for d_ in res_rmd["deleted"]:
                                        st.session_state["runtime_migrated"].add(
                                            _runtime_key(d_["space"], d_["data_view_id"], d_["field_name"]))
                                    for f_ in res_rmd["failed"]:
                                        st.session_state["runtime_failed"].add(
                                            _runtime_key(f_["space"], f_["data_view_id"], f_["field_name"]))
                                    st.success(f"✅ Deleted {len(res_rmd['deleted'])} scripted field(s). "
                                               f"{len(res_rmd['failed'])} failed.")
                                    if res_rmd["failed"]:
                                        with st.expander("⚠️ Failed deletes — click to view"):
                                            st.json(res_rmd["failed"])
                                except Exception as exc:
                                    st.error(f"❌ Delete failed: {exc}")

                st.subheader("6. Updated report")
                if st.button("🔄 Regenerate Excel report", key="runtime_report_regenerate"):
                    status_map = _runtime_status_map()
                    st.session_state["runtime_report_bytes"] = rfl.build_report_excel_bytes(records, status_map)
                    st.session_state["runtime_report_ts"] = datetime.now().strftime("%Y%m%d_%H%M%S")
                if "runtime_report_bytes" in st.session_state:
                    st.download_button(
                        label="⬇️ Download updated report (Excel)",
                        data=st.session_state["runtime_report_bytes"],
                        file_name=f"scripted_fields_report_{st.session_state['runtime_report_ts']}.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        key="runtime_report_download2",
                    )


# ════════════════════════════════════════════════════════════════════════════
#   TAB 7 — UPGRADE ASSISTANT (8.19 → 9.x, BULK REINDEXING)
# ════════════════════════════════════════════════════════════════════════════

with tab_reindex:
    st.header("Upgrade Assistant — Index & Data-Stream Reindexing (8.19 → 9.x)")
    st.caption(
        "Find indices that still need reindexing before the 9.x upgrade and reindex "
        "them **one at a time** through Kibana's Upgrade Assistant (each index follows "
        "the read-only → reindex → alias-swap → cleanup flow). Discovery is read-only; "
        "reindexing is opt-in and gated."
    )

    sub_ridisc, sub_riwork, sub_riro = st.tabs(
        ["📋 Discover (read-only)", "🚀 Bulk Reindex (writes to cluster)",
         "🔒 Data Streams Read-Only (writes to cluster)"]
    )

    # ── DISCOVER ─────────────────────────────────────────────────────────────
    with sub_ridisc:
        st.subheader("1. Connect")
        c1, c2 = st.columns(2)
        with c1:
            ri_kb_url = st.text_input(
                "Kibana URL (Upgrade Assistant API)",
                placeholder="https://your-kibana.example.com:5601",
                help="Used for the Upgrade Assistant reindex APIs — usually port 5601.",
                key="ri_kb_url",
            )
        with c2:
            ri_es_url = st.text_input(
                "Elasticsearch URL (read-only checks)",
                placeholder="https://your-es.example.com:9200",
                help="Used for index / version / deprecation reads — usually port 9200. "
                     "This is the Elasticsearch endpoint, NOT the Kibana URL.",
                key="ri_es_url",
            )

        if ri_es_url and (":5601" in ri_es_url or "/api/" in ri_es_url):
            st.warning(
                "⚠️ This Elasticsearch URL looks like a Kibana URL. The ES endpoint serves "
                "the index APIs (usually `https://…:9200`). If both point at the same host, "
                "enter the ES port here."
            )

        c3, c4, c5 = st.columns(3)
        with c3:
            ri_user = st.text_input("Username", value="elastic", key="ri_user")
        with c4:
            ri_pass = st.text_input("Password", type="password", key="ri_pass")
        with c5:
            ri_verify = st.checkbox("Verify SSL certificate", value=True, key="ri_verify_ssl")

        c6, c7 = st.columns(2)
        with c6:
            ri_target = st.text_input(
                "Upgrade target version", value="9.0.0", key="ri_target_version",
            )
        with c7:
            ri_include_sys = st.checkbox(
                "Include system indices (.kibana, .watches, ...)", value=False,
                key="ri_include_sys",
            )

        if st.button("🔍 Discover indices & data streams", type="primary", key="ri_discover_btn"):
            if not (ri_kb_url and ri_es_url and ri_user and ri_pass):
                st.error("Please fill in the Kibana URL, Elasticsearch URL, username, and password.")
            else:
                try:
                    with st.spinner("Connecting and discovering..."):
                        session = ril.make_session(ri_user, ri_pass, ri_verify)
                        es_ok, es_hint = ril.is_es_endpoint(session, ri_es_url)
                        if not es_ok:
                            st.error(f"❌ Elasticsearch URL check failed: {es_hint}")
                        else:
                            result = ril.discover_all(
                                session, ri_kb_url, ri_es_url,
                                include_system=ri_include_sys, target_version=ri_target,
                            )
                            st.session_state["ri_ua_status"] = result["ua_status"]
                            st.session_state["ri_deprecations"] = result["deprecations"]
                            st.session_state["ri_indices"] = result["indices"]
                            st.session_state["ri_streams"] = result["streams"]
                            st.session_state["ri_discover_warnings"] = result["warnings"]
                except Exception as exc:
                    st.error(f"❌ Discovery failed: {exc}")

        ua_status = st.session_state.get("ri_ua_status")
        indices = st.session_state.get("ri_indices", [])
        streams = st.session_state.get("ri_streams", {})
        warnings = st.session_state.get("ri_discover_warnings", [])

        if ua_status is None and not indices and not streams:
            st.info("Run **Discover** above to connect and scan the cluster.")
        else:
            for w in warnings:
                st.warning(f"⚠️ {w}")

            need_idx = [i for i in indices if i.get("needs_reindex")]
            need_streams = [s for s in streams.values() if s.get("needs_reindex")]

            c_ready = st.columns(4)
            with c_ready[0]:
                st.metric("Ready for 9.x",
                          "✅ Yes" if (ua_status and ua_status.get("ready")) else "❌ No")
            with c_ready[1]:
                st.metric("Indices needing reindex", len(need_idx))
            with c_ready[2]:
                st.metric("Data streams needing reindex", len(need_streams))
            with c_ready[3]:
                st.metric("Indices total", len(indices))

            if ua_status and ua_status.get("counts"):
                st.caption("Upgrade Assistant deprecation counts: " + ", ".join(
                    f"{k}: {v}" for k, v in ua_status["counts"].items()
                ))

            import pandas as pd
            if indices:
                st.markdown("### Indices")
                st.dataframe(pd.DataFrame([
                    {"Index": i["name"], "Stream": i.get("stream") or "",
                     "Version created": i.get("version_created"),
                     "Needs reindex": "✅" if i.get("needs_reindex") else "—",
                     "State": i.get("state"), "Docs": i.get("docs_count"),
                     "Store": i.get("store_size"),
                     "System": "✅" if i.get("is_system") else ""}
                    for i in indices
                ]), use_container_width=True)
            if streams:
                st.markdown("### Data streams")
                st.dataframe(pd.DataFrame([
                    {"Data stream": s["name"],
                     "Backing indices": len(s.get("backing_indices", [])),
                     "Old backing": len(s.get("old_backing", [])),
                     "Write index": s.get("write_index") or "",
                     "Generation": s.get("generation"),
                     "ILM policy": s.get("ilm_policy") or "",
                     "Needs reindex": "✅" if s.get("needs_reindex") else "—"}
                    for s in streams.values()
                ]), use_container_width=True)

            if need_idx or need_streams:
                st.success(
                    f"Found {len(need_idx)} index(es) and {len(need_streams)} data stream(s) "
                    "that still need reindexing. Go to the 🚀 **Bulk Reindex** tab to start."
                )
            else:
                st.success("✅ No indices or data streams need reindexing for 9.x.")

    # ── BULK REINDEX ─────────────────────────────────────────────────────────
    with sub_riwork:
        st.warning(
            "⚠️ **This writes to the connected clusters.** Reindexing creates a "
            "`reindexed-v8-…` index, moves documents, swaps the alias, and deletes the "
            "original index. Use this only after taking a **final snapshot** and pausing "
            "ingestion (see the ELK upgrade runbook). Each index is processed **one at a "
            "time**; on failure the worker halts and flags the corresponding event."
        )

        indices = st.session_state.get("ri_indices", [])
        streams = st.session_state.get("ri_streams", {})
        ri_es_url = st.session_state.get("ri_es_url", "")
        ri_kb_url = st.session_state.get("ri_kb_url", "")
        ri_user = st.session_state.get("ri_user", "elastic")
        ri_pass = st.session_state.get("ri_pass", "")
        ri_verify = st.session_state.get("ri_verify_ssl", True)

        if not indices and not streams:
            st.info("No discovery results yet. Run the **📋 Discover** tab first to connect and scan.")
        elif not ri_kb_url:
            st.info("Complete discovery on the 📋 Discover tab first.")
        else:
            # Worker persists across reruns in session state, keyed by connection.
            # Normalize exactly like ReindexWorker.connection_key so a trailing "/"
            # (e.g. a Kibana URL pasted from the browser) does NOT recreate the
            # worker — and wipe its queue — on every 3s refresh.
            conn_key = f"{ri_kb_url.strip().rstrip('/')}|{ri_es_url.strip().rstrip('/')}"
            worker = st.session_state.get("reindex_worker")
            if worker is None or getattr(worker, "connection_key", None) != conn_key:
                worker = ril.ReindexWorker(
                    kibana_url=ri_kb_url, es_url=ri_es_url,
                    username=ri_user, password=ri_pass, verify_ssl=ri_verify,
                )
                st.session_state["reindex_worker"] = worker

            st.subheader("1. Gate")
            ri_confirm = st.text_input("Type REINDEX to confirm write actions below", key="ri_confirm")
            ri_ack = st.checkbox(
                "I confirm ingestion is paused and a final snapshot exists for this cluster.",
                value=False, key="ri_ack",
            )
            gate_ok = (ri_confirm == "REINDEX") and ri_ack
            if not gate_ok:
                st.caption(
                    "Write actions stay disabled until you type **REINDEX** and tick the "
                    "snapshot confirmation above."
                )

            st.subheader("2. Select what to reindex")
            can = [i for i in indices if i.get("needs_reindex") and not i.get("is_closed")]
            stream_backing = {b for s in streams.values() for b in s.get("backing_indices", [])}
            can_sel = [i for i in can if i["name"] not in stream_backing]
            can_names = [i["name"] for i in can_sel]
            can_by = {i["name"]: i for i in indices}
            stream_names = [s["name"] for s in streams.values() if s.get("needs_reindex")]
            _tasks_now = worker.state["tasks"]
            _tagmap = {"Completed": "✅ completed", "Running": "▶ running",
                       "In progress": "▶ running", "Rolling Over": "🔄 rolling over",
                       "Queued": "⏳ queued", "Failed": "❌ failed",
                       "Cancelled": "⏹ cancelled", "Stopped": "⏹ stopped"}

            def _idx_tag(name):
                rec = _tasks_now.get(name)
                t = _tagmap.get(rec.get("status")) if rec else None
                return f"  — {t}" if t else ""

            def _idx_label(name):
                i = can_by.get(name, {})
                return (f"{name}  (v{i.get('version_created')}, "
                        f"{i.get('docs_count', 0)} docs){_idx_tag(name)}")

            def _stream_label(name):
                s = streams.get(name, {})
                return (f"Data stream: {name}  "
                        f"({len(s.get('old_backing', []))} old backing){_idx_tag(name)}")

            sel_idx = st.multiselect(
                "Select indices to reindex", options=can_names,
                format_func=_idx_label, key="ri_sel_idx",
            )
            sel_streams = st.multiselect(
                "Select data streams to reindex (upgraded in-place by Elasticsearch's "
                "migration reindex)",
                options=stream_names, format_func=_stream_label, key="ri_sel_streams",
            )
            if stream_backing:
                st.caption(
                    f"{len(stream_backing)} data-stream backing index(es) need reindexing and are "
                    "handled by selecting their **data stream** above (ES reindexes each old "
                    "backing index in place — they are excluded from the standalone index list "
                    "to avoid double reindexing)."
                )
            ri_free = st.text_input("…or type an extra index name (not in the list)", key="ri_free_idx")

            # Queueing is local-only (no cluster writes), so it needs no gate.
            idx_meta = {i["name"]: i for i in indices}
            cq = st.columns(2)
            with cq[0]:
                if st.button("⏫ Queue selected (front)", key="ri_queue_btn"):
                    added = 0
                    for n in sel_idx:
                        if worker.enqueue(n, kind="index", front=True,
                                          meta=idx_meta.get(n)):
                            added += 1
                    for n in sel_streams:
                        if worker.enqueue_stream(n, streams.get(n) or {}):
                            added += 1
                    if ri_free.strip():
                        if worker.enqueue(ri_free.strip(), kind="index", front=True,
                                          meta=idx_meta.get(ri_free.strip())):
                            added += 1
                    st.success(f"Queued {added} item(s) to the front of the queue.")
            with cq[1]:
                if st.button("⏫ Queue ALL needing reindex", key="ri_queue_all_btn"):
                    added = 0
                    for i in can_sel:
                        if worker.enqueue(i["name"], kind="index", front=False,
                                          meta=idx_meta.get(i["name"])):
                            added += 1
                    for sname, srec in streams.items():
                        if srec.get("needs_reindex"):
                            if worker.enqueue_stream(sname, srec):
                                added += 1
                    st.success(f"Queued {added} item(s) (standalone indices + data streams).")

            all_items = [("index", i["name"]) for i in can_sel] + [
                ("stream", s["name"]) for s in streams.values() if s.get("needs_reindex")]
            sel_stream_recs = {n: streams[n] for n in sel_streams if n in streams}
            sel_items = [("index", n) for n in sel_idx] + [
                ("stream", n) for n in sel_stream_recs]
            rq = st.columns(2)
            with rq[0]:
                if st.button("🔄 Reset queue → ALL (needing reindex)", key="ri_reset_all_btn"):
                    n = worker.reset_queue(all_items, stream_records=streams, index_meta=idx_meta)
                    st.success(f"Queue reset to ALL ({n} item(s) queued).")
            with rq[1]:
                if st.button("🔄 Reset queue → selected only", key="ri_reset_sel_btn"):
                    n = worker.reset_queue(sel_items, stream_records=sel_stream_recs,
                                           index_meta=idx_meta)
                    st.success(f"Queue reset to selection ({n} item(s) queued).")

            _qstate = worker.state
            if _qstate["queue"]:
                _qk = {"index": "📇", "stream": "💧"}
                _qlist = ", ".join(
                    f"{_qk.get(q.get('kind', 'index'), '•')} `{q['name']}`"
                    for q in _qstate["queue"]
                )
                st.caption(f"**Queue (next → last):** {_qlist}")
            else:
                st.caption(
                    "Queue is empty. Select items above and press **Queue selected** or "
                    "**Queue ALL**, then **▶ Start / Resume**."
                )

            st.subheader("3. Run controls")
            st.caption(
                "**Start / Resume** processes the queue one item at a time. **Pause after "
                "each** lets you validate mid-upgrade. **Halt** stops after the current task. "
                "**Cancel** stops the in-flight reindex on the cluster. **Hard stop** cancels "
                "the current task and takes no further items."
            )
            ctrl = st.columns(4)
            with ctrl[0]:
                if st.button("▶ Start / Resume", type="primary", key="ri_start_btn", disabled=not gate_ok):
                    _sstate = worker.state
                    if not _sstate["queue"] and not _sstate["current"]:
                        st.warning(
                            "Queue is empty — nothing to process. Select items above and "
                            "press **Queue selected** or **Queue ALL** first."
                        )
                    else:
                        if not worker.is_alive:
                            worker.start()
                        worker.resume()
                        st.success("Worker started / resumed.")
            with ctrl[1]:
                if st.button("⏸ Halt (after current task)", key="ri_halt_btn", disabled=not gate_ok):
                    worker.halt()
            with ctrl[2]:
                if st.button("🛑 Cancel current task", key="ri_cancel_btn", disabled=not gate_ok):
                    worker.cancel_current()
            with ctrl[3]:
                if st.button("⏹ Hard stop", key="ri_stop_btn", disabled=not gate_ok):
                    worker.hard_stop()

            worker.pause_after_each = st.checkbox(
                "Pause after each index (validate in the middle before continuing)",
                value=False, key="ri_pause_each",
            )

            st.subheader("4. Live status")
            ri_live = st.checkbox(
                "Refresh automatically every 3 s (watch progress)", value=True, key="ri_live"
            )

            state = worker.state
            sm = state["summary"]
            if not worker.is_alive:
                status_chip = "⚪ Idle"
            elif state["stopped"]:
                status_chip = "⏹ Stopped"
            elif state["halted"] and state["running"]:
                status_chip = "⏸ Paused"
            elif state["running"]:
                status_chip = "▶ Running"
            else:
                status_chip = "⚪ Idle"

            mcols = st.columns(6)
            with mcols[0]:
                st.metric("Worker", status_chip)
            with mcols[1]:
                st.metric("Queued", sm["queued"])
            with mcols[2]:
                st.metric("Running", sm["running"])
            with mcols[3]:
                st.metric("Completed", sm["completed"])
            with mcols[4]:
                st.metric("Failed", sm["failed"])
            with mcols[5]:
                st.metric("Cancelled / Stopped", sm["cancelled"])

            _failed_tasks = [
                t for t in state["tasks"].values()
                if t.get("status") in ("Failed", "Cancelled", "Stopped")
            ]
            if _failed_tasks:
                _f = max(_failed_tasks, key=lambda t: t.get("completed_at") or "")
                _ferr = (_f.get("error") or "").strip()
                st.error(
                    f"❌ **{_f.get('status')}: `{_f.get('name')}`**"
                    f"{(' — ' + _ferr) if _ferr else ''}\n\n"
                    "The worker **paused** after this. Check the **Kibana URL** (and that the "
                    "Upgrade Assistant can reindex this item), then press **▶ Start / Resume** "
                    "to continue with the remaining items."
                )

            if state["stopped"]:
                st.info("⏹ Worker was **hard-stopped** — no new tasks will start. "
                        "Press **▶ Start / Resume** to take further items again.")
            elif state["halted"] and state["running"]:
                st.info("⏸ Worker is **paused** (after the current task). "
                        "Use **▶ Start / Resume** to continue.")
            elif not state["running"]:
                st.caption("Worker not running. Use **▶ Start / Resume** to begin processing the queue.")

            try:
                if state["tasks"]:
                    import pandas as pd
                    _badge = {
                        "Completed": "✅ Completed",
                        "Running": "▶ Running",
                        "Rolling Over": "🔄 Rolling over",
                        "In progress": "▶ In progress",
                        "Queued": "⏳ Queued",
                        "Failed": "❌ Failed",
                        "Cancelled": "⏹ Cancelled",
                        "Stopped": "⏹ Stopped",
                    }
                    _rank = {"Running": 0, "Rolling Over": 0, "In progress": 0,
                             "Queued": 1, "Completed": 2, "Failed": 3,
                             "Cancelled": 4, "Stopped": 4}
                    cur = state.get("current")
                    rows = sorted(
                        state["tasks"].values(),
                        key=lambda t: (0 if t.get("name") == cur else 1,
                                       _rank.get(t.get("status"), 5),
                                       t.get("name") or ""),
                    )
                    st.markdown("### All tasks")
                    _td = lambda t, k: (t.get("task_details") or {}).get(k)
                    st.dataframe(pd.DataFrame([
                        {"Index": t["name"],
                         "Type": "Stream" if t.get("kind") == "stream" else "Index",
                         "Stream": t.get("stream") or "",
                         "Status": _badge.get(t["status"], t["status"]),
                         "New index": t.get("new_index") or ril.expected_new_index(t["name"]),
                         "Alias": t.get("alias") or t["name"],
                         "Version created": t.get("version_created") or "",
                         "Docs": t.get("docs") or "",
                         "Progress %": t.get("progress") if t.get("progress") is not None else "",
                         "Step": t.get("step_name") or "",
                         "Task ID": t.get("task_id") or "",
                         "ES docs": (str(_td(t, "docs_created")) if _td(t, "docs_created") is not None else "")
                                    + ("/" + str(_td(t, "docs_total")) if _td(t, "docs_total") else ""),
                         "Started": t.get("started_at") or "",
                         "Completed": t.get("completed_at") or "",
                         "Error": (t.get("error") or "")[:200]}
                        for t in rows
                    ]), use_container_width=True, height=360)
                else:
                    st.caption(
                        "No items queued yet — select items above and press **Queue selected** "
                        "or **Queue ALL**, then **▶ Start / Resume**."
                    )
            except Exception as exc:
                st.error(f"⚠️ Could not render the tasks table: {exc}")

            try:
                st.markdown("### Audit — API request / response")
                if state["api_log"]:
                    import pandas as pd
                    st.dataframe(pd.DataFrame([
                        {"Time": e.get("ts"), "Method": e.get("method"),
                         "URL": e.get("url"), "Status": e.get("status"),
                         "Response": (e.get("resp_body") or "")[:200]}
                        for e in reversed(state["api_log"])
                    ]), use_container_width=True, height=360)
                else:
                    st.caption("No API calls yet — start the worker to begin reindexing.")
            except Exception as exc:
                st.error(f"⚠️ Could not render the audit table: {exc}")

            st.subheader("5. Validate & download")
            done = [t["name"] for t in state["tasks"].values() if t["status"] == "Completed"]
            vcols = st.columns(2)
            with vcols[0]:
                val_idx = st.selectbox("Validate a completed index", options=done or [""], key="ri_val_idx")
                if st.button("✅ Validate", key="ri_val_btn", disabled=not done or not val_idx):
                    try:
                        session = ril.make_session(ri_user, ri_pass, ri_verify)
                        target = val_idx
                        _vrec = state["tasks"].get(val_idx)
                        if (_vrec and _vrec.get("stream") and _vrec.get("new_index")):
                            target = _vrec["new_index"]
                        res = ril.validate_index(session, ri_es_url, target)
                    except Exception as exc:
                        st.error(f"❌ Validation failed: {exc}")
                    else:
                        if res.get("ok"):
                            st.success(
                                f"✅ `{val_idx}` is backed by an 8.x index "
                                f"(version.created = {res.get('version_created')}, "
                                f"{res.get('docs_count')} docs)."
                            )
                        else:
                            st.error(
                                f"❌ `{val_idx}` does not look reindexed yet "
                                f"(version.created = {res.get('version_created')})."
                            )
            with vcols[1]:
                if st.button("📦 Build tracker Excel", key="ri_build_btn"):
                    st.session_state["ri_tracker_bytes"] = ril.build_tracker_excel(state)
                    st.session_state["ri_tracker_ts"] = datetime.now().strftime("%Y%m%d_%H%M%S")
                if "ri_tracker_bytes" in st.session_state:
                    st.download_button(
                        label="⬇️ Download tracker (Excel)",
                        data=st.session_state["ri_tracker_bytes"],
                        file_name=f"reindexing_tracker_{st.session_state['ri_tracker_ts']}.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        key="ri_tracker_download",
                    )

            if ri_live:
                import time as _time
                _time.sleep(3)
                st.rerun()

    # ── DATA STREAMS READ-ONLY ───────────────────────────────────────────────
    with sub_riro:
        st.warning(
            "⚠️ **This writes to the connected clusters.** Marking a data stream "
            "read-only sets `index.blocks.write=true` on **every backing index** "
            "(including the write index), so no new data can be written to it. "
            "The operation is applied via Elasticsearch directly (the Upgrade "
            "Assistant refuses to block a data stream's write index, and it is "
            "also used for regular indices when the UA rejects the request). "
            "Only select streams you no longer ingest to. "
            "Take a **final snapshot** first — this is the alternative to reindexing "
            "for historical data that does not need to change."
        )

        ro_kb_url = st.session_state.get("ri_kb_url", "")
        ro_es_url = st.session_state.get("ri_es_url", "")
        ro_user = st.session_state.get("ri_user", "elastic")
        ro_pass = st.session_state.get("ri_pass", "")
        ro_verify = st.session_state.get("ri_verify_ssl", True)

        if not ro_es_url:
            st.info("No discovery results yet. Run the **📋 Discover** tab first to connect and scan.")
        elif not ro_kb_url:
            st.info("Complete discovery on the **📋 Discover** tab first — the Kibana URL is needed for the Upgrade Assistant call.")
        else:
            ro_include_sys = st.checkbox(
                "Include Fleet system data streams (fleet-*, .fleet-*)",
                value=False, key="ri_ro_include_sys",
            )
            if st.button("🔍 Load data streams (read-only)", key="ri_ro_load_btn"):
                try:
                    with st.spinner("Reading data-stream stats..."):
                        session = ril.make_session(ro_user, ro_pass, ro_verify)
                        result = ril.discover_stream_readonly(
                            session, ro_es_url, include_system=ro_include_sys,
                            deprecations=st.session_state.get("ri_deprecations"),
                        )
                        st.session_state["ri_ro_records"] = result["records"]
                        st.session_state["ri_ro_excluded"] = result["excluded_system"]
                        st.session_state["ri_ro_warnings"] = result["warnings"]
                        st.session_state.pop("ri_ro_status", None)
                except Exception as exc:
                    st.error(f"❌ Could not load data streams: {exc}")

            ro_records = st.session_state.get("ri_ro_records", [])
            ro_excluded = st.session_state.get("ri_ro_excluded", [])
            ro_warnings = st.session_state.get("ri_ro_warnings", [])

            for w in ro_warnings:
                st.warning(f"⚠️ {w}")
            if ro_excluded:
                st.caption(
                    f"ℹ️ {len(ro_excluded)} Fleet system stream(s) hidden "
                    "(`fleet-*`/`.fleet-*`). Tick **Include Fleet system data "
                    "streams** above and reload to see them: "
                    + ", ".join(sorted(ro_excluded)[:8])
                    + (" …" if len(ro_excluded) > 8 else "")
                )

            if not ro_records:
                st.info(
                    "Press **🔍 Load data streams** to inventory them with their "
                    "**last-updated** timestamp (`maximum_timestamp` from "
                    "`_data_stream/*/_stats`)."
                )
            else:
                st.success(f"Loaded **{len(ro_records)}** data stream(s).")
                import pandas as pd

                ro_empty = [r for r in ro_records if r.get("is_empty")]
                if ro_empty:
                    st.info(
                        f"ℹ️ **{len(ro_empty)}** data stream(s) have **no data** "
                        "(`maximum_timestamp = 0`) — there is nothing to freeze, "
                        "no need to make them read-only. They are tagged "
                        "**\"max_ts = 0\"** below."
                    )
                ro_status = st.session_state.get("ri_ro_status", {})

                def _fmt_ts(v):
                    if v in (None, "", "0") or v == 0:
                        return "—"
                    if isinstance(v, (int, float)) and not isinstance(v, bool):
                        try:
                            return datetime.fromtimestamp(v / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
                        except (ValueError, OverflowError, OSError):
                            return str(v)
                    return str(v)

                st.markdown("### Data streams (last updated)")
                st.dataframe(pd.DataFrame([
                    {"Data stream": r["name"],
                     "Backing indices": r.get("backing_count"),
                     "Write index": r.get("write_index") or "",
                     "Last updated": _fmt_ts(r.get("maximum_timestamp")),
                     "Docs": r.get("docs_count") or "",
                     "Store": r.get("store_size") or "",
                     "Data": "⚠️ no data" if r.get("is_empty") else "",
                     "Read-only": "✅" if ro_status.get(r["name"]) else "",
                     "Old backing": len(r.get("old_backing", [])),
                     "Needs reindex": "✅" if r.get("needs_reindex") else "—"}
                    for r in ro_records
                ]), use_container_width=True)

                if st.button("✅ Check current read-only status", key="ri_ro_check_btn"):
                    try:
                        with st.spinner("Checking index.blocks.write on each backing index..."):
                            session = ril.make_session(ro_user, ro_pass, ro_verify)
                            status = {}
                            for r in ro_records:
                                backing = r.get("backing_indices") or []
                                checks = ril.check_indices_blocks_write(session, ro_es_url, backing)
                                status[r["name"]] = bool(backing) and all(
                                    checks.get(b) for b in backing
                                )
                            st.session_state["ri_ro_status"] = status
                    except Exception as exc:
                        st.error(f"❌ Status check failed: {exc}")

                def _ro_label(name):
                    r = next((x for x in ro_records if x["name"] == name), {})
                    tag = "  ✅ read-only" if ro_status.get(name) else ""
                    nodata = "  ⚠️ max_ts = 0" if r.get("is_empty") else ""
                    return (f"{name}  (last updated: {_fmt_ts(r.get('maximum_timestamp'))}, "
                            f"{r.get('backing_count', 0)} backing){nodata}{tag}")

                sel_ro = st.multiselect(
                    "Select data streams to make read-only",
                    options=[r["name"] for r in ro_records],
                    format_func=_ro_label, key="ri_ro_sel",
                )
                if ro_status:
                    already = [n for n in sel_ro if ro_status.get(n)]
                    if already:
                        st.caption(
                            f"ℹ️ {len(already)} selected stream(s) are already read-only — "
                            "re-applying is harmless."
                        )

                st.subheader("Gate")
                ro_confirm = st.text_input(
                    "Type READONLY to confirm write actions below", key="ri_ro_confirm",
                )
                ro_ack = st.checkbox(
                    "I confirm ingestion to these streams is stopped and a final snapshot exists.",
                    value=False, key="ri_ro_ack",
                )
                ro_gate = (ro_confirm == "READONLY") and ro_ack
                if not ro_gate:
                    st.caption(
                        "Apply stays disabled until you type **READONLY** and tick the "
                        "confirmation above."
                    )

                if st.button(
                    "🔒 Make selected streams read-only", type="primary",
                    key="ri_ro_apply_btn", disabled=not ro_gate or not sel_ro,
                ):
                    try:
                        with st.spinner("Applying read-only and verifying..."):
                            session = ril.make_session(ro_user, ro_pass, ro_verify)
                            selected = [r for r in ro_records if r["name"] in sel_ro]
                            res = ril.apply_streams_read_only(
                                session, ro_kb_url, ro_es_url, selected,
                            )
                            st.session_state["ri_ro_results"] = res
                            st.session_state["ri_ro_ts"] = datetime.now().strftime("%Y%m%d_%H%M%S")
                            st.session_state["ri_ro_status"] = {
                                r["name"]: bool(r.get("read_only")) for r in res
                            }
                    except Exception as exc:
                        st.error(f"❌ Apply failed: {exc}")

                ro_results = st.session_state.get("ri_ro_results", [])
                if ro_results:
                    st.markdown("### Results")
                    ro_ok = [r for r in ro_results if r.get("read_only")]
                    ro_err = [r for r in ro_results if not r.get("read_only")]
                    c_ro = st.columns(3)
                    with c_ro[0]:
                        st.metric("Streams selected", len(ro_results))
                    with c_ro[1]:
                        st.metric("Fully read-only", len(ro_ok))
                    with c_ro[2]:
                        st.metric("Not verified", len(ro_err))
                    for r in ro_err:
                        _msg = r.get("error") or "write-block not detected on all backing indices"
                        st.error(f"❌ `{r['name']}` — {_msg}")
                    _method_label = {
                        "upgrade_assistant": "Upgrade Assistant",
                        "es_settings": "ES fallback",
                        "already": "already read-only",
                        "failed": "failed",
                    }
                    st.dataframe(pd.DataFrame([
                        {"Data stream": r["name"],
                         "Backing index": ir["index"],
                         "Write index": "✅" if ir.get("is_write") else "",
                         "HTTP": ir.get("http_status"),
                         "Method": _method_label.get(ir.get("method"), ir.get("method") or ""),
                         "Operation": "✅" if ir.get("ok") else "❌",
                         "Blocks.write": "✅" if ir.get("blocks_write") else "❌"}
                        for r in ro_results for ir in r.get("index_results", [])
                    ]), use_container_width=True)
                    for r in ro_results:
                        for ir in r.get("index_results", []):
                            if ir.get("note"):
                                st.caption(f"ℹ️ `{ir['index']}` — {ir['note']}")

                    if st.button("📦 Build report Excel", key="ri_ro_build_btn"):
                        st.session_state["ri_ro_excel"] = ril.build_readonly_report_excel(ro_results)
                    if "ri_ro_excel" in st.session_state:
                        st.download_button(
                            label="⬇️ Download read-only report (Excel)",
                            data=st.session_state["ri_ro_excel"],
                            file_name=f"data_streams_readonly_{st.session_state.get('ri_ro_ts', 'report')}.xlsx",
                            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                            key="ri_ro_download",
                        )


# ════════════════════════════════════════════════════════════════════════════
#   TAB 8 — ABOUT / SETUP
# ════════════════════════════════════════════════════════════════════════════

with tab_about:
    st.header("About this tool")
    st.markdown(
        """
        This app was built to help document and back up Elasticsearch Watcher
        scripts and Kibana saved objects ahead of an **ELK 7.x → 9.x upgrade**,
        but it works against any reasonably compatible Elastic/Kibana cluster
        you point it at.

        ### What gets backed up

        **Watcher tab**
        - Every watcher script in the cluster (`.watches` index), including
          schedules, conditions, transforms, actions, and any embedded Painless
          scripts
        - Recipients for email / Slack / PagerDuty / webhook / Jira actions,
          where applicable
        - Output: one Excel file (Summary + Full Detail) and one `.txt` file
          per watcher containing the restore-ready JSON body

        **Kibana tab**
        - All saved objects per space (dashboards, visualizations, Lens, Maps,
          searches, index patterns, Canvas, tags, and more), exported with
          `includeReferencesDeep` so dependent objects are automatically
          included
        - Output: one Excel summary (counts per space/type) and one `.ndjson`
          file per space, ready to re-import via Kibana's Saved Objects API or
          UI

        **Cluster Assets tab**
        - Cluster-level configurations including Component Templates, Index
          Templates, ILM/SLM Policies, Enrich Policies, Ingest Pipelines,
          Stored Scripts, and Snapshot Repositories.
        - Output: one Excel summary file and individual `.json` configuration
          files organized by asset type in a single ZIP archive, ready for
          target cluster restoration.

        **ML Assets tab**
        - Machine Learning configurations including Anomaly Detection Jobs,
          Datafeeds, Data Frame Analytics Jobs, Calendars, and Filters.
        - Runtime state is automatically stripped so exported JSON is
          clean and ready to restore on a fresh cluster.
        - Output: one Excel summary file and individual `.json` configuration
          files organized by asset type in a single ZIP archive.

        **Security tab**
        - Native-realm users and roles, including password hashes read
          directly from the `.security-7` index (no API round-trip), so users
          can be recreated verbatim on the target cluster.
        - Reserved (built-in) users/roles are detected dynamically via the
          `type: reserved-user` field and `metadata._reserved: true` flag.
        - Restore first checks which roles/users already exist on the target
          and lets you choose, per item, whether to **overwrite** or **skip**
          anything already present.
        - Output: one Excel summary file and individual `.json` files under
          `roles/` and `users/` folders, plus a `security_meta.json` reserved
          flag map, in a single ZIP archive.

        **Scripted → Runtime Fields tab**
        - Scans the destination Kibana for **scripted fields** (space, data
          view, field name, type, script) and produces an Excel inventory.
        - Lets you edit the type and Painless script per field (most scripted
          fields need manual tweaks to behave correctly as runtime fields).
        - Creates **data-view runtime fields** (reversible) and, only after a
          successful create and explicit opt-in, deletes the original scripted
          field (irreversible).
        - Optional Painless syntax check via `/_script/painless/_execute`
          before writing.
        - Tracks migration status (Pending / Runtime Created / Migrated /
          Runtime Exists / Error) and can regenerate the Excel report so it
          reflects exactly what has been migrated.

        **Upgrade Assistant tab**
        - Discovers (read-only) which indices and data streams still need
          reindexing for the **9.x** upgrade, using Kibana's Upgrade Assistant
          API plus each index's `version.created` (< 8.0.0 = needs reindex).
        - Reindexes them **one at a time** through the Upgrade Assistant —
          each index follows the full read-only → reindexed-v8-… → alias swap
          → cleanup flow, with the current step and progress shown live.
        - Data streams are upgraded **in place** by Elasticsearch's migration
          reindex API (background task): an old write index is rolled over,
          each old backing index is reindexed, swapped into the stream as
          `.migrated-…` and the original deleted.
        - Controls to **Halt** (pause after the current task, so you can
          validate mid-upgrade), **Resume**, **Cancel** the in-flight task, and
          **Hard stop**; on failure the worker halts and flags the
          corresponding reindexing event.
        - Lets you reindex a **specific** index/data stream, validates a
          completed index, and produces an Excel tracker (Indices / Data
          Streams / Events).
        - **Data Streams Read-Only** sub-tab: inventories data streams with
          their **last-updated** timestamp (`maximum_timestamp` from
          `_data_stream/*/_stats`), lets you select which streams to freeze,
          and marks them read-only through the Upgrade Assistant
          (`update_index` with `blockWrite` + `unfreeze`). It then verifies
          `index.blocks.write` on **every backing index** and produces an Excel
          report (Data Streams / Backing Indices). A good alternative to
          reindexing when historical data does not need to change.

        ### Where files go
        Nothing is written to a server. Each backup is built in memory and
        offered to you as a single ZIP download via your browser. Save it
        wherever you'd normally keep migration artifacts (e.g. a private repo,
        secure shared drive, or local disk) — just remember the `.ndjson` and
        watcher `.txt` files contain your actual configuration and may be
        sensitive (URLs, index names, recipient lists), so don't commit them
        to a public repo.

        ### Restore safety
        Both Restore tabs:
        - Refuse to run if the target URL matches the source URL you typed in
          the Fetch tab during this session
        - Require you to type the literal word `RESTORE` before anything is
          written
        - Show a preview of what will be restored before you confirm
        - Report per-item success/failure rather than failing silently

        That said, **restore is inherently a write operation** — always test
        against a staging/new cluster first, and keep your backup ZIP safe
        regardless.

        """
    )
    st.divider()
    st.caption("Crafted by Souvik Das")
    st.caption("[![LinkedIn](https://img.shields.io/badge/LinkedIn-0077B5?style=for-the-badge&logo=linkedin&logoColor=white)](https://www.linkedin.com/in/souvik-das-6ba904a2/)")