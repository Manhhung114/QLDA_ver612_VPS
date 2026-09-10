from __future__ import annotations

PATCH_MARKER = "V6.22 ORIGINAL FILE ARCHIVE UI V1"


def _replace_once(source: str, old: str, new: str, label: str) -> str:
    count = source.count(old)
    if count != 1:
        raise RuntimeError(f"{PATCH_MARKER}: expected one {label}, found {count}")
    return source.replace(old, new, 1)


def patch_original_file_archive(source: str) -> str:
    """Persist every import-style upload before parsing/analysis on local VPS.

    Document/drawing/diary attachment flows already use the storage gateway and
    therefore already persist exact file bytes. This patch covers the remaining
    direct Streamlit uploaders that previously parsed in memory: BOQ, MPP, IPC
    and ad-hoc AI files.
    """
    if PATCH_MARKER in source:
        return source

    imports = '''# V6.22 ORIGINAL FILE ARCHIVE UI V1\nfrom original_file_archive_v622 import (\n    archive_original_upload as _v622_archive_original_upload,\n    install_ipc_original_archive as _v622_install_ipc_original_archive,\n)\n'''
    source = imports + source

    boq_old = '''            if _boq_file is not None:\n                try:\n                    _boq_result = _v622_parse_boq_cached(_boq_file.getvalue(), _boq_file.name)\n                    _boq_source = "upload"\n'''
    boq_new = '''            if _boq_file is not None:\n                try:\n                    _boq_raw = _boq_file.getvalue()\n                    _v622_archive_original_upload(\n                        db, pid, _gateway_session_token(), subtype="BOQ", record_code="BOQ",\n                        name=_boq_file.name, content=_boq_raw, upload_purpose="boq_original",\n                    )\n                    _boq_result = _v622_parse_boq_cached(_boq_raw, _boq_file.name)\n                    _boq_source = "upload"\n'''
    source = _replace_once(source, boq_old, boq_new, "BOQ upload archive")

    mpp_old = '''        if st.button("Đọc và đồng bộ MPP", type="primary", disabled=(mpp is None or not _can_update()), key=f"syncmpp_{pid}"):\n            suffix = Path(mpp.name).suffix or ".mpp"\n            temp_path = None\n            try:\n                with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:\n                    tmp.write(mpp.getvalue())\n'''
    mpp_new = '''        if st.button("Đọc và đồng bộ MPP", type="primary", disabled=(mpp is None or not _can_update()), key=f"syncmpp_{pid}"):\n            suffix = Path(mpp.name).suffix or ".mpp"\n            temp_path = None\n            try:\n                _mpp_raw = mpp.getvalue()\n                _v622_archive_original_upload(\n                    db, pid, _gateway_session_token(), subtype="MPP", record_code="MPP",\n                    name=mpp.name, content=_mpp_raw, upload_purpose="schedule_original",\n                )\n                with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:\n                    tmp.write(_mpp_raw)\n'''
    source = _replace_once(source, mpp_old, mpp_new, "MPP upload archive")

    ai_old = '''        if f2.button("Phân tích file tải lên", disabled=upload is None, width="stretch", key=f"ai_uploaded_file_{pid}"):\n            try:\n                with st.spinner(f"AI đang đọc {upload.name}..."):\n                    st.session_state[f"ai_file_result_{pid}"] = ai.summarize_file(pid, upload.name, upload.getvalue(), instruction, date.today())\n'''
    ai_new = '''        if f2.button("Phân tích file tải lên", disabled=upload is None, width="stretch", key=f"ai_uploaded_file_{pid}"):\n            try:\n                _ai_raw = upload.getvalue()\n                _v622_archive_original_upload(\n                    db, pid, _gateway_session_token(), subtype="AI", record_code="AI-UPLOAD",\n                    name=upload.name, content=_ai_raw, upload_purpose="ai_original",\n                )\n                with st.spinner(f"AI đang đọc {upload.name}..."):\n                    st.session_state[f"ai_file_result_{pid}"] = ai.summarize_file(pid, upload.name, _ai_raw, instruction, date.today())\n'''
    source = _replace_once(source, ai_old, ai_new, "AI upload archive")

    ipc_helper_old = '''from v622_ipc_claim_patch import install_ipc_claim_due_date as _v622_install_ipc_claim_due_date\n_v622_install_ipc_claim_due_date()\nfrom ipc_claim_v622 import render_ipc_claim_ui as _v622_render_ipc_claim_ui\n'''
    ipc_helper_new = '''from v622_ipc_claim_patch import install_ipc_claim_due_date as _v622_install_ipc_claim_due_date\n_v622_install_ipc_claim_due_date()\n_v622_install_ipc_original_archive()\nfrom ipc_claim_v622 import render_ipc_claim_ui as _v622_render_ipc_claim_ui\n'''
    source = _replace_once(source, ipc_helper_old, ipc_helper_new, "IPC original archive installer")

    ipc_call_old = '_v622_render_ipc_claim_ui(db, pid, can_update=bool(_can_update()))'
    ipc_call_new = '_v622_render_ipc_claim_ui(db, pid, can_update=bool(_can_update()), session_token=_gateway_session_token())'
    source = _replace_once(source, ipc_call_old, ipc_call_new, "IPC session token")

    compile(source, "streamlit_app_v622_original_file_archive.py", "exec")
    return source
