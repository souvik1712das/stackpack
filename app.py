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
from datetime import datetime

import streamlit as st

import watcher_logic as wl
import kibana_logic as kl


# ── Page setup ──────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Stackpack — Elastic Stack Backup & Migration Tool",
    page_icon="🗄️",
    layout="wide",
)

st.title("🗄️ Stackpack — Elastic Stack Backup & Migration Tool")
st.caption(
    "Back up Elasticsearch Watcher scripts and Kibana saved objects before an "
    "upgrade or migration. Read-only by default — restore is opt-in and gated."
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

tab_watcher, tab_kibana, tab_about = st.tabs(
    ["🔧 Watcher Backup", "📊 Kibana Saved Objects", "📖 About / Setup"]
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

        st.subheader("2. Connect to target cluster")
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

        if uploaded_zip and target_host_w:
            try:
                with zipfile.ZipFile(uploaded_zip) as zf:
                    txt_names = [n for n in zf.namelist() if n.startswith("watcher_scripts/") and n.endswith(".txt")]
                    st.info(f"Found {len(txt_names)} watcher file(s) in the uploaded ZIP.")

                    with st.expander("Preview watcher IDs to be restored"):
                        st.write([n.split("/")[-1].replace(".txt", "") for n in txt_names[:50]])
                        if len(txt_names) > 50:
                            st.caption(f"... and {len(txt_names) - 50} more")

                    st.subheader("3. Confirm and restore")
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
                            for name in txt_names:
                                watcher_id = name.split("/")[-1].replace(".txt", "")
                                raw = zf.read(name).decode("utf-8")
                                json_lines = [ln for ln in raw.splitlines() if not ln.startswith("#")]
                                watcher_files[watcher_id] = "\n".join(json_lines).strip()

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

        st.subheader("2. Connect to target Kibana")
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

        if uploaded_zip_k and target_host_k:
            try:
                with zipfile.ZipFile(uploaded_zip_k) as zf:
                    ndjson_names = [n for n in zf.namelist() if n.startswith("spaces/") and n.endswith(".ndjson")]
                    all_space_ids = [n.split("/")[-1].replace(".ndjson", "") for n in ndjson_names]

                    st.info(f"Found {len(ndjson_names)} space file(s) in the uploaded ZIP.")

                    space_choice = st.multiselect(
                        "Select which space(s) to restore (default: all)",
                        options=all_space_ids, default=all_space_ids,
                        key="kibana_restore_space_select",
                    )

                    st.subheader("3. Confirm and restore")
                    confirm_phrase_k = st.text_input(
                        "Type RESTORE to confirm you want to write to the target Kibana above",
                        key="kibana_restore_confirm",
                    )

                    if st.button("🚀 Run restore", type="primary", key="kibana_restore_btn"):
                        if confirm_phrase_k != "RESTORE":
                            st.error("Confirmation phrase did not match. Type exactly: RESTORE")
                        elif target_host_k.rstrip("/") == kb_host.rstrip("/") if kb_host else False:
                            st.error("Target host matches the source host from the Fetch tab. Aborting for safety.")
                        elif not target_user_k or not target_pass_k:
                            st.error("Please fill in target username and password.")
                        elif not space_choice:
                            st.error("Select at least one space to restore.")
                        else:
                            space_files = {}
                            for sid in space_choice:
                                name = f"spaces/{sid}.ndjson"
                                space_files[sid] = zf.read(name)

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
                                with st.expander("Details per space"):
                                    st.json(result["details"])
                            except Exception as exc:
                                st.error(f"❌ Restore failed: {exc}")
            except zipfile.BadZipFile:
                st.error("That doesn't look like a valid ZIP file.")


# ════════════════════════════════════════════════════════════════════════════
#   TAB 3 — ABOUT / SETUP
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