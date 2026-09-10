from __future__ import annotations


PATCH_MARKER = "V6.22 ORIGINAL IMPORT UI V1"


def _replace_once(source: str, old: str, new: str, label: str) -> str:
    count = source.count(old)
    if count != 1:
        raise RuntimeError(f"{PATCH_MARKER}: expected one {label}, found {count}")
    return source.replace(old, new, 1)


def patch_original_import_storage(source: str) -> str:
    """Persist source files before saving parsed project data and expose originals."""
    if PATCH_MARKER in source:
        return source

    import_anchor = "from __future__ import annotations\n"
    if import_anchor not in source:
        raise RuntimeError(f"{PATCH_MARKER}: future import anchor missing")
    helper_import = '''from __future__ import annotations

# V6.22 ORIGINAL IMPORT UI V1
from original_import_storage_v622 import (
    archive_original_upload as _v622_archive_original_upload,
    render_original_link as _v622_render_original_link,
)
'''
    source = source.replace(import_anchor, helper_import, 1)

    # BOQ: download the exact saved workbook and archive bytes BEFORE parsed
    # BOQ/workbook data is persisted.
    boq_uploader = '''            _boq_file = st.file_uploader(
                "Chọn workbook BOQ mới (.xlsx / .xlsm)",
'''
    boq_uploader_new = '''            _v622_render_original_link(
                st, db, pid, _gateway_session_token(), "BOQ", "BOQ",
                "⬇️ Tải workbook BOQ gốc trên VPS",
            )
            _boq_file = st.file_uploader(
                "Chọn workbook BOQ mới (.xlsx / .xlsm)",
'''
    source = _replace_once(source, boq_uploader, boq_uploader_new, "BOQ original download")

    boq_save = '''                                _boq_stats = _v622_save_boq_summary_to_project(
                                    db, pid, _boq_result, replace_existing_excel=_boq_replace
                                )
                                _v622_save_saved_boq_workbook(db, pid, _boq_result)
'''
    boq_save_new = '''                                _v622_archive_original_upload(
                                    db, pid, _gateway_session_token(), "BOQ", "BOQ",
                                    _boq_file.name, _boq_file.getvalue(),
                                    str(getattr(_boq_file, "type", "") or ""),
                                )
                                _boq_stats = _v622_save_boq_summary_to_project(
                                    db, pid, _boq_result, replace_existing_excel=_boq_replace
                                )
                                _v622_save_saved_boq_workbook(db, pid, _boq_result)
'''
    source = _replace_once(source, boq_save, boq_save_new, "BOQ source archive")

    # Schedule MPP: keep the original .mpp before task rows are replaced.
    mpp_uploader = '''        mpp = st.file_uploader("Chọn file .mpp", type=["mpp"], key=f"mpp_{pid}")
'''
    mpp_uploader_new = '''        _v622_render_original_link(
            st, db, pid, _gateway_session_token(), "SCHEDULE_MPP", "SCHEDULE",
            "⬇️ Tải file MPP gốc trên VPS",
        )
        mpp = st.file_uploader("Chọn file .mpp", type=["mpp"], key=f"mpp_{pid}")
'''
    source = _replace_once(source, mpp_uploader, mpp_uploader_new, "MPP original download")

    mpp_save = '''                    info, tasks = read_mpp(temp_path, status_date=status_date)
                    db.sync_mpp_tasks(pid, tasks, mpp.name, info)
'''
    mpp_save_new = '''                    info, tasks = read_mpp(temp_path, status_date=status_date)
                    _v622_archive_original_upload(
                        db, pid, _gateway_session_token(), "SCHEDULE_MPP", "SCHEDULE",
                        mpp.name, mpp.getvalue(), str(getattr(mpp, "type", "") or ""),
                    )
                    db.sync_mpp_tasks(pid, tasks, mpp.name, info)
'''
    source = _replace_once(source, mpp_save, mpp_save_new, "MPP source archive")

    # Schedule Excel: archive only after required columns are validated and
    # before the first task row is inserted.
    task_excel_uploader = '''    excel_in = ex2.file_uploader("Nhập công việc từ Excel", type=["xlsx", "xls"], key=f"task_excel_{pid}")
'''
    task_excel_uploader_new = '''    _v622_render_original_link(
        ex2, db, pid, _gateway_session_token(), "SCHEDULE_EXCEL", "TASKS",
        "⬇️ File Excel tiến độ gốc trên VPS",
    )
    excel_in = ex2.file_uploader("Nhập công việc từ Excel", type=["xlsx", "xls"], key=f"task_excel_{pid}")
'''
    source = _replace_once(source, task_excel_uploader, task_excel_uploader_new, "schedule Excel original download")

    task_excel_save = '''            if not required.issubset(normalized):
                st.error("Excel cần tối thiểu các cột: Công việc/Name, Bắt đầu/Start, Kết thúc/Finish.")
            else:
                count = 0
'''
    task_excel_save_new = '''            if not required.issubset(normalized):
                st.error("Excel cần tối thiểu các cột: Công việc/Name, Bắt đầu/Start, Kết thúc/Finish.")
            else:
                _v622_archive_original_upload(
                    db, pid, _gateway_session_token(), "SCHEDULE_EXCEL", "TASKS",
                    excel_in.name, excel_in.getvalue(), str(getattr(excel_in, "type", "") or ""),
                )
                count = 0
'''
    source = _replace_once(source, task_excel_save, task_excel_save_new, "schedule Excel source archive")

    # IPC and VO are external renderers. Route them through wrappers that capture
    # the uploaded bytes and archive them before save_ipc_claim/save_vo executes.
    source = _replace_once(
        source,
        "from ipc_claim_v622 import render_ipc_claim_ui as _v622_render_ipc_claim_ui\n",
        "from original_import_storage_v622 import render_ipc_claim_ui_with_original as _v622_render_ipc_claim_ui\n",
        "IPC renderer wrapper",
    )
    source = _replace_once(
        source,
        "_v622_render_ipc_claim_ui(db, pid, can_update=bool(_can_update()))",
        "_v622_render_ipc_claim_ui(db, pid, can_update=bool(_can_update()), session_token=_gateway_session_token())",
        "IPC session token",
    )
    source = _replace_once(
        source,
        "from vo_independent_v622 import render_vo_ui as _v622_render_vo_ui\n",
        "from original_import_storage_v622 import render_vo_ui_with_original as _v622_render_vo_ui\n",
        "VO renderer wrapper",
    )
    source = _replace_once(
        source,
        "_v622_render_vo_ui(db, pid, can_update=bool(_can_update()))",
        "_v622_render_vo_ui(db, pid, can_update=bool(_can_update()), session_token=_gateway_session_token())",
        "VO session token",
    )

    compile(source, "streamlit_app_v622_original_imports.py", "exec")
    return source
