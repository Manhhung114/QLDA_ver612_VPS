from __future__ import annotations

"""Delete action for Công việc / assigned work tasks.

Only Admin / project-site management can delete a task.  The database row is
hard-deleted so task comments, history and task-file links are removed through
ON DELETE CASCADE.  Physical task attachments are then moved to the configured
storage trash on a best-effort basis.

The Streamlit selectbox uses its own widget key.  Deletion happens after that
widget has already been instantiated, so this module uses a pending reset and
clears the widget state at the beginning of the next rerun.  This avoids the
Streamlit ``session_state cannot be modified after widget is instantiated``
error seen in other document flows.
"""

from typing import Any, Iterable


PATCH_MARKER = "V7.6 WORK TASK DELETE V1"


def _text(value: Any) -> str:
    return str(value or "").strip()


def delete_work_task(
    db,
    task_id: int,
    workspace_project_id: int,
    *,
    actor: Any,
    is_admin: bool = False,
) -> tuple[dict[str, Any], list[str]]:
    """Delete one task in the current workspace and return its attachment ids."""
    from qlda.runtime_core import work_tasks_v1 as wt

    user = wt._actor(actor)
    if not wt._is_manager(user, is_admin):
        raise PermissionError("Chỉ Admin/Ban điều hành được xóa công việc đã giao.")

    wt.ensure_schema(db)
    with db.connect() as connection:
        task = wt._require_task(connection, int(task_id), int(workspace_project_id))
        rows = connection.execute(
            f"SELECT file_id FROM {wt.FILES_TABLE} WHERE task_id=? ORDER BY id",
            (int(task_id),),
        ).fetchall()
        file_ids: list[str] = []
        for row in rows:
            try:
                value = _text(row["file_id"])
            except Exception:
                try:
                    value = _text(row[0])
                except Exception:
                    value = ""
            if value and value not in file_ids:
                file_ids.append(value)

        connection.execute(
            f"DELETE FROM {wt.TASKS_TABLE} WHERE id=? AND workspace_project_id=?",
            (int(task_id), int(workspace_project_id)),
        )

    return dict(task), file_ids


def _trash_task_files(gateway: Any, session_token: str, file_ids: list[str]) -> list[str]:
    errors: list[str] = []
    if not gateway or not session_token:
        return errors
    for file_id in file_ids:
        try:
            gateway.trash_file(session_token, file_id)
        except Exception as exc:
            errors.append(f"{file_id}: {_text(exc)[:180]}")
    return errors


def install_work_task_delete() -> None:
    """Add a confirmed delete button to the Công việc detail panel."""
    from qlda.runtime_core import work_tasks_v1 as wt

    if getattr(wt, "_qlda_work_task_delete_installed", False):
        return

    original_detail = wt._render_detail
    original_render = wt.render_work_tasks_v1

    def _render_detail_with_delete(
        st,
        db,
        task: dict[str, Any],
        pid: int,
        master_pid: int,
        identity: Any,
        *,
        can_update: bool,
        is_admin: bool,
        gateway: Any = None,
        session_token: str = "",
    ) -> None:
        original_detail(
            st,
            db,
            task,
            pid,
            master_pid,
            identity,
            can_update=can_update,
            is_admin=is_admin,
            gateway=gateway,
            session_token=session_token,
        )

        actor = wt._actor(identity)
        if not wt._is_manager(actor, is_admin):
            return

        task_id = int(task.get("id") or 0)
        if task_id <= 0:
            return
        task_code = _text(task.get("task_code")) or f"TASK-{task_id:05d}"
        title = _text(task.get("title"))
        confirm_key = f"work_v1_delete_confirm_{int(pid)}_{task_id}"

        st.divider()
        with st.expander("🗑️ Xóa công việc", expanded=False):
            st.warning(
                f"Xóa **{task_code} · {title}** sẽ xóa công việc, bình luận, lịch sử và liên kết file của task này. "
                "Thao tác không thể hoàn tác trong giao diện."
            )
            confirmed = st.checkbox(
                f"Tôi xác nhận xóa {task_code}",
                key=confirm_key,
            )
            if st.button(
                "🗑️ Xóa task giao việc",
                key=f"work_v1_delete_button_{int(pid)}_{task_id}",
                disabled=not bool(confirmed),
                use_container_width=True,
            ):
                try:
                    deleted, file_ids = delete_work_task(
                        db,
                        task_id,
                        int(pid),
                        actor=actor,
                        is_admin=bool(is_admin),
                    )
                    file_errors = _trash_task_files(gateway, session_token, file_ids)
                    st.session_state[f"work_v1_delete_reset_{int(pid)}"] = True
                    if file_errors:
                        message = (
                            f"Đã xóa {_text(deleted.get('task_code')) or task_code}. "
                            f"Có {len(file_errors)} file đính kèm chưa chuyển được vào thùng rác VPS."
                        )
                        st.session_state[f"work_v1_delete_notice_{int(pid)}"] = {
                            "ok": False,
                            "message": message,
                        }
                    else:
                        st.session_state[f"work_v1_delete_notice_{int(pid)}"] = {
                            "ok": True,
                            "message": f"Đã xóa {(_text(deleted.get('task_code')) or task_code)}.",
                        }
                    wt._rerun(st)
                except Exception as exc:
                    st.error(f"Không xóa được công việc: {exc}")

    def _render_with_delete(
        st,
        db,
        workspace_project_id: int,
        *,
        master_project_id: int,
        identity: Any,
        can_update: bool,
        is_admin: bool,
        users: Iterable[Any] = (),
        gateway: Any = None,
        session_token: str = "",
    ) -> None:
        pid = int(workspace_project_id)

        # Clear the selectbox state BEFORE the widget is instantiated on this run.
        if st.session_state.pop(f"work_v1_delete_reset_{pid}", False):
            st.session_state.pop(f"work_v1_open_{pid}", None)
            st.session_state.pop(f"work_v1_selected_{pid}", None)

        notice = st.session_state.pop(f"work_v1_delete_notice_{pid}", None)
        if isinstance(notice, dict) and _text(notice.get("message")):
            if bool(notice.get("ok")):
                st.success(_text(notice.get("message")))
            else:
                st.warning(_text(notice.get("message")))

        original_render(
            st,
            db,
            pid,
            master_project_id=int(master_project_id),
            identity=identity,
            can_update=can_update,
            is_admin=is_admin,
            users=users,
            gateway=gateway,
            session_token=session_token,
        )

    wt._render_detail = _render_detail_with_delete
    wt.render_work_tasks_v1 = _render_with_delete
    wt.delete_work_task = delete_work_task
    wt._qlda_work_task_delete_installed = True
    wt._qlda_work_task_delete_marker = PATCH_MARKER


__all__ = ["delete_work_task", "install_work_task_delete"]
