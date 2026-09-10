from __future__ import annotations


PATCH_MARKER = "V6.22 SCHEDULE MANAGEMENT V1"


_OLD_DELETE_BLOCK = '''    b1, b2 = st.columns([1, 3])
    _ui_note("TT % tự lưu khi nhấn Enter hoặc click ra khỏi ô.")
    selected_delete = b2.selectbox("Xóa task", [None] + [int(r["id"]) for r in rows], format_func=lambda x: "Chọn..." if x is None else f"#{x}", key=f"delete_task_select_{pid}")
    if st.button("Xóa công việc đã chọn", disabled=(selected_delete is None or not _is_admin()), key=f"delete_task_btn_{pid}"):
        db.delete_task(int(selected_delete))
        st.rerun()

'''

_ROWS_ANCHOR = '''    rows = db.tasks(pid, keyword, status_filter)
    if not rows:
'''

_MANAGEMENT_BLOCK = '''    rows = db.tasks(pid, keyword, status_filter)

    # V6.22: keep destructive schedule actions compact and scoped to the
    # contractor workspace currently selected in the sidebar. No contractor
    # selector is repeated here; pid is already the active workspace_project_id.
    _schedule_manage_rows = [dict(r) for r in db.tasks(pid, "", "Tất cả")]
    if _schedule_manage_rows and (_can_update() or _is_admin()):
        with st.expander("⚙️ Quản lý công việc", expanded=False):
            try:
                from contractor_workspace_v622 import list_contractors as _v622_list_contractors
                _schedule_contractor = next(
                    (
                        x for x in _v622_list_contractors(db, int(pid), active_only=False)
                        if int(x.get("workspace_project_id") or 0) == int(pid)
                    ),
                    {},
                )
            except Exception:
                _schedule_contractor = {}
            _schedule_owner = " - ".join(
                x for x in (
                    str(_schedule_contractor.get("contractor_code") or "").strip(),
                    str(_schedule_contractor.get("contractor_name") or "").strip(),
                ) if x
            ) or str(project["name"] or "Workspace hiện tại")
            st.caption(f"Nhà thầu hiện tại: {_schedule_owner} • {len(_schedule_manage_rows):,} công việc")

            _task_labels = {}
            _task_ids = []
            for _task_row in _schedule_manage_rows:
                _task_id = int(_task_row.get("id") or 0)
                if _task_id <= 0:
                    continue
                _task_ids.append(_task_id)
                _task_wbs = str(_task_row.get("wbs") or "").strip()
                _task_name = str(_task_row.get("name") or "").strip()
                _task_text = " | ".join(x for x in (f"#{_task_id}", _task_wbs, _task_name) if x)
                _task_labels[_task_id] = _task_text[:140]

            _selected_task_delete = st.selectbox(
                "Công việc cần xử lý",
                [None] + _task_ids,
                format_func=lambda x: "Chọn công việc..." if x is None else _task_labels.get(int(x), f"#{x}"),
                key=f"schedule_manage_task_{pid}",
                disabled=not _can_update(),
            )
            if st.button(
                "🗑 Xóa công việc",
                disabled=(_selected_task_delete is None or not _can_update()),
                key=f"schedule_delete_one_{pid}",
            ):
                db.delete_task(int(_selected_task_delete))
                st.success("Đã xóa công việc khỏi bảng tiến độ của nhà thầu hiện tại.")
                st.rerun()

            if _is_admin():
                st.divider()
                st.markdown("**ADMIN · Xóa bảng tiến độ**")
                _mpp_task_count = sum(
                    1 for _r in _schedule_manage_rows
                    if str(_r.get("source_type") or "").strip().lower() == "mpp"
                )
                with st.form(f"schedule_bulk_delete_form_{pid}"):
                    _schedule_delete_mode = st.radio(
                        "Phạm vi xóa",
                        ["Chỉ dữ liệu Microsoft Project (.mpp)", "Toàn bộ bảng tiến độ"],
                        key=f"schedule_bulk_delete_mode_{pid}",
                    )
                    _schedule_delete_count = (
                        _mpp_task_count
                        if _schedule_delete_mode.startswith("Chỉ dữ liệu")
                        else len(_schedule_manage_rows)
                    )
                    st.warning(
                        f"Sẽ xóa {_schedule_delete_count:,} công việc của {_schedule_owner}. "
                        "BOQ, Claim/IPC, VO, Hồ sơ, Bản vẽ và các nhà thầu khác không bị xóa."
                    )
                    _schedule_confirm = st.text_input(
                        'Nhập "XOA TIEN DO" để xác nhận',
                        key=f"schedule_bulk_delete_confirm_{pid}",
                    )
                    _schedule_bulk_submit = st.form_submit_button(
                        "🗑 Xóa vĩnh viễn",
                        disabled=(
                            _schedule_delete_count <= 0
                            or str(_schedule_confirm or "").strip().upper() != "XOA TIEN DO"
                        ),
                    )
                if _schedule_bulk_submit:
                    with db.connect() as _schedule_conn:
                        if _schedule_delete_mode.startswith("Chỉ dữ liệu"):
                            _schedule_conn.execute(
                                "DELETE FROM tasks WHERE project_id=? AND LOWER(TRIM(COALESCE(source_type,'')))='mpp'",
                                (int(pid),),
                            )
                        else:
                            _schedule_conn.execute(
                                "DELETE FROM tasks WHERE project_id=?",
                                (int(pid),),
                            )
                        _schedule_conn.execute(
                            "UPDATE projects SET source_mpp_path='', last_sync='' WHERE id=?",
                            (int(pid),),
                        )
                    st.success(
                        f"Đã xóa {_schedule_delete_count:,} công việc khỏi tiến độ của {_schedule_owner}."
                    )
                    st.rerun()

    if not rows:
'''


def patch_schedule_management(source: str) -> str:
    """Compact task deletion UI + Admin-only bulk delete for the active contractor."""
    if PATCH_MARKER in source:
        return source
    if source.count(_OLD_DELETE_BLOCK) != 1:
        raise RuntimeError(
            f"{PATCH_MARKER}: expected one legacy task-delete block, found {source.count(_OLD_DELETE_BLOCK)}"
        )
    if source.count(_ROWS_ANCHOR) != 1:
        raise RuntimeError(
            f"{PATCH_MARKER}: expected one schedule rows anchor, found {source.count(_ROWS_ANCHOR)}"
        )
    patched = source.replace(_OLD_DELETE_BLOCK, "", 1)
    patched = patched.replace(_ROWS_ANCHOR, _MANAGEMENT_BLOCK, 1)
    return f"# {PATCH_MARKER}\n" + patched
