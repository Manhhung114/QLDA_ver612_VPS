from __future__ import annotations

from datetime import datetime
from typing import Any


PATCH_MARKER = "V9.1-V9.6 ADVANCED AUTOMATION ADMIN UI V1"


def _rowdict(row: Any) -> dict[str, Any]:
    if row is None:
        return {}
    if isinstance(row, dict):
        return dict(row)
    try:
        return {str(k): row[k] for k in row.keys()}
    except Exception:
        try:
            return dict(row)
        except Exception:
            return {}


def _active_tenant(ui_module, st, db, project_id: int) -> tuple[int, str]:
    pid = int(project_id)
    master_id, rows = ui_module._contractor_tenants(db, pid)
    by_workspace = {
        int(row.get("workspace_project_id") or 0): row
        for row in rows
        if int(row.get("workspace_project_id") or 0) > 0
    }
    if pid in by_workspace:
        selected = pid
    elif by_workspace:
        candidates = list(by_workspace)
        selected = int(
            st.session_state.get(f"autonomy_tenant_{master_id}")
            or st.session_state.get(f"contractor_workspace_{master_id}")
            or candidates[0]
        )
        if selected not in by_workspace:
            selected = candidates[0]
    else:
        selected = pid
    row = by_workspace.get(selected, {})
    label = " - ".join(
        value for value in (
            str(row.get("contractor_code") or "").strip(),
            str(row.get("contractor_name") or "").strip(),
        ) if value
    )
    return int(selected), label


def _execute_refresh(platform, tool: str, tenant_id: int, actor: str, arguments: dict[str, Any] | None = None):
    args = dict(arguments or {})
    # ToolRegistry idempotency protects repeated write requests. A read/analysis
    # refresh from an explicit Admin button must see the newest DB state, so use a
    # harmless request token accepted by advanced handlers through **kwargs.
    args["refresh_token"] = datetime.now().isoformat(timespec="microseconds")
    return platform.tools.execute(
        tool,
        project_id=int(tenant_id),
        actor=actor,
        role="admin",
        arguments=args,
    )


def _site_images(db, tenant_id: int) -> list[dict[str, Any]]:
    with db.connect() as connection:
        try:
            rows = connection.execute(
                """SELECT a.id,a.file_name,a.mime_type,d.code,d.subject
                FROM document_attachments a JOIN documents d ON d.id=a.document_id
                WHERE d.project_id=? ORDER BY a.id DESC LIMIT 300""",
                (int(tenant_id),),
            ).fetchall()
        except Exception:
            return []
    images = []
    for raw in rows:
        row = _rowdict(raw)
        mime = str(row.get("mime_type") or "").lower()
        name = str(row.get("file_name") or "").lower()
        if mime.startswith("image/") or name.endswith((".jpg", ".jpeg", ".png", ".webp")):
            images.append(row)
    return images


def _render_advanced_ui(st, db, tenant_id: int, *, actor: str) -> None:
    from qlda.autonomy.runtime import get_autonomy_platform

    platform = get_autonomy_platform(db)
    state_prefix = f"advanced_ai_{tenant_id}"

    with st.expander("🚀 AI Automation V9.1–V9.6 · Admin", expanded=False):
        st.caption(
            "Tất cả chức năng chạy trong đúng AI tenant nhà thầu. VO và Site Vision chỉ tạo đề xuất/draft; "
            "không tự phê duyệt IPC/VO/hồ sơ và không tự ghi % tiến độ."
        )

        st.markdown("#### Kiểm tra tự động")
        c1, c2, c3 = st.columns(3)
        if c1.button("📜 Audit hợp đồng", key=f"{state_prefix}_contract", use_container_width=True):
            try:
                st.session_state[f"{state_prefix}_contract_result"] = _execute_refresh(
                    platform, "audit_contract_obligations", tenant_id, actor
                )
            except Exception as exc:
                st.error(f"Contract Audit lỗi: {exc}")
        if c2.button("🧾 Đối soát IPC ↔ BOQ", key=f"{state_prefix}_ipc", use_container_width=True):
            try:
                st.session_state[f"{state_prefix}_ipc_result"] = _execute_refresh(
                    platform, "reconcile_ipc_boq", tenant_id, actor
                )
            except Exception as exc:
                st.error(f"IPC ↔ BOQ lỗi: {exc}")
        if c3.button("📈 Dự báo rủi ro 60 ngày", key=f"{state_prefix}_forecast", use_container_width=True):
            try:
                st.session_state[f"{state_prefix}_forecast_result"] = _execute_refresh(
                    platform,
                    "forecast_project_risk",
                    tenant_id,
                    actor,
                    {"horizon_days": 60},
                )
            except Exception as exc:
                st.error(f"Forecast lỗi: {exc}")

        contract = st.session_state.get(f"{state_prefix}_contract_result")
        if isinstance(contract, dict):
            m1, m2, m3 = st.columns(3)
            m1.metric("Nghĩa vụ đang mở", int(contract.get("obligation_count") or 0))
            m2.metric("Quá hạn", int(contract.get("overdue_count") or 0))
            m3.metric("Đến hạn ≤30 ngày", int(contract.get("due_30d_count") or 0))
            ledger = list(contract.get("ledger") or [])
            if ledger:
                st.dataframe(ledger, hide_index=True, use_container_width=True)

        reconciliation = st.session_state.get(f"{state_prefix}_ipc_result")
        if isinstance(reconciliation, dict):
            if not reconciliation.get("available", True):
                st.info(f"Chưa thể đối soát IPC ↔ BOQ: {reconciliation.get('reason','chưa có dữ liệu')}")
            else:
                m1, m2, m3 = st.columns(3)
                m1.metric("IPC", reconciliation.get("claim_code") or "-")
                m2.metric("Cờ sai lệch", int(reconciliation.get("flag_count") or 0))
                m3.metric("High/Critical", int(reconciliation.get("high_flag_count") or 0))
                flags = list(reconciliation.get("flags") or [])
                if flags:
                    st.dataframe(flags, hide_index=True, use_container_width=True)

        forecast = st.session_state.get(f"{state_prefix}_forecast_result")
        if isinstance(forecast, dict):
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Risk", f"{float(forecast.get('risk_score') or 0):.0f}/100")
            m2.metric("Confidence", f"{float(forecast.get('confidence') or 0):.0%}")
            m3.metric("Trễ dự báo lớn nhất", f"{int(forecast.get('max_predicted_delay_days') or 0)} ngày")
            m4.metric("Finish dự báo", forecast.get("predicted_finish") or "-")
            st.caption(
                "Nhu cầu tiền 30/60 ngày chỉ tính IPC hiện hữu có due-date thật; không gọi là nợ quá hạn và không làm giảm Project Health."
            )
            tasks = list(forecast.get("tasks") or [])
            if tasks:
                st.dataframe(tasks[:50], hide_index=True, use_container_width=True)

        st.markdown("#### V9.4 · Draft VO từ thay đổi đã định lượng")
        with db.connect() as connection:
            try:
                raw_docs = connection.execute(
                    """SELECT id,doc_type,code,subject,cost_impact,time_impact_days,status
                    FROM documents WHERE project_id=? AND UPPER(doc_type) IN ('RFI','RFA')
                    ORDER BY id DESC LIMIT 200""",
                    (int(tenant_id),),
                ).fetchall()
            except Exception:
                raw_docs = []
        docs = [_rowdict(row) for row in raw_docs]
        doc_ids = [0] + [int(x.get("id") or 0) for x in docs if int(x.get("id") or 0) > 0]
        doc_map = {int(x.get("id") or 0): x for x in docs}
        selected_doc = st.selectbox(
            "RFI/RFA nguồn (không bắt buộc)",
            doc_ids,
            format_func=lambda value: "Không chọn" if int(value) == 0 else f"{doc_map[int(value)].get('code','')} · {doc_map[int(value)].get('subject','')}",
            key=f"{state_prefix}_vo_doc",
        )
        v1, v2 = st.columns(2)
        item_name = v1.text_input("Hạng mục thay đổi", key=f"{state_prefix}_vo_item")
        unit = v2.text_input("Đơn vị", key=f"{state_prefix}_vo_unit")
        v3, v4, v5 = st.columns(3)
        old_qty = v3.number_input("KL cũ", value=0.0, key=f"{state_prefix}_vo_old")
        new_qty = v4.number_input("KL mới", value=0.0, key=f"{state_prefix}_vo_new")
        manual_rate = v5.number_input("Đơn giá (0 = dò BOQ)", value=0.0, min_value=0.0, key=f"{state_prefix}_vo_rate")
        if st.button("📝 Tạo Draft VO", key=f"{state_prefix}_vo_create", use_container_width=True):
            try:
                result = platform.tools.execute(
                    "draft_vo_from_change",
                    project_id=tenant_id,
                    actor=actor,
                    role="admin",
                    arguments={
                        "source_type": "RFI/RFA" if int(selected_doc) else "CHANGE",
                        "source_document_id": int(selected_doc),
                        "item_name": item_name,
                        "unit": unit,
                        "old_qty": float(old_qty),
                        "new_qty": float(new_qty),
                        "unit_price": float(manual_rate),
                    },
                )
                st.session_state[f"{state_prefix}_vo_result"] = result
                st.success(f"Đã tạo {result.get('draft_code')} ở trạng thái DRAFT; chưa ghi vào VO chính thức.")
            except Exception as exc:
                st.error(f"Không tạo được Draft VO: {exc}")
        vo_result = st.session_state.get(f"{state_prefix}_vo_result")
        if isinstance(vo_result, dict):
            st.json(vo_result)

        st.markdown("#### V9.6 · Site Vision → đề xuất tiến độ")
        images = _site_images(db, tenant_id)
        tasks = [_rowdict(row) for row in db.tasks(tenant_id)]
        task_ids = [int(x.get("id") or 0) for x in tasks if int(x.get("id") or 0) > 0]
        task_map = {int(x.get("id") or 0): x for x in tasks}
        if not images:
            st.info("Chưa có attachment hình ảnh trong hồ sơ của nhà thầu này. Hãy đính kèm ảnh công trường vào hồ sơ trước.")
        elif not task_ids:
            st.info("Chưa có task tiến độ để ánh xạ quan sát Site Vision.")
        else:
            image_map = {int(x.get("id") or 0): x for x in images}
            selected_image = st.selectbox(
                "Ảnh hiện trường",
                list(image_map),
                format_func=lambda value: f"{image_map[int(value)].get('file_name','')} · {image_map[int(value)].get('code','')}",
                key=f"{state_prefix}_vision_image",
            )
            selected_task = st.selectbox(
                "Task/WBS",
                task_ids,
                format_func=lambda value: f"{task_map[int(value)].get('wbs','')} · {task_map[int(value)].get('name','')}",
                key=f"{state_prefix}_vision_task",
            )
            s1, s2 = st.columns(2)
            object_label = s1.text_input(
                "Đối tượng cần đếm",
                value=str(task_map[int(selected_task)].get("name") or ""),
                key=f"{state_prefix}_vision_label",
            )
            planned_qty = s2.number_input(
                "Số lượng kế hoạch",
                min_value=0.0,
                value=0.0,
                key=f"{state_prefix}_vision_planned",
            )
            if st.button(
                "👁️ AI đọc ảnh và đề xuất tiến độ",
                key=f"{state_prefix}_vision_run",
                disabled=float(planned_qty) <= 0,
                use_container_width=True,
            ):
                try:
                    result = _execute_refresh(
                        platform,
                        "analyze_site_progress",
                        tenant_id,
                        actor,
                        {
                            "task_id": int(selected_task),
                            "object_label": object_label,
                            "planned_quantity": float(planned_qty),
                            "attachment_id": int(selected_image),
                        },
                    )
                    st.session_state[f"{state_prefix}_vision_result"] = result
                    st.success(
                        f"AI đề xuất {float(result.get('proposed_progress') or 0):.1f}% · "
                        "chưa cập nhật tiến độ; cần dùng workflow update_schedule_progress/Approval Gate."
                    )
                except Exception as exc:
                    st.error(f"Site Vision chưa xử lý được ảnh: {exc}")
            vision_result = st.session_state.get(f"{state_prefix}_vision_result")
            if isinstance(vision_result, dict):
                st.json(vision_result)

        st.markdown("#### V9.1 · Task Routing")
        with db.connect() as connection:
            try:
                route_rows = connection.execute(
                    """SELECT finding_code,discipline,assignee_email,assignee_name,priority,sla_hours,title,status,updated_at
                    FROM qlda_ai_task_routes WHERE project_id=? ORDER BY id DESC LIMIT 100""",
                    (int(tenant_id),),
                ).fetchall()
            except Exception:
                route_rows = []
        routes = [_rowdict(row) for row in route_rows]
        if routes:
            st.dataframe(routes, hide_index=True, use_container_width=True)
        else:
            st.caption("Task Router sẽ tự tạo route proposal sau lần AI Supervisor kế tiếp.")


def install_advanced_automation_ui() -> None:
    """Append the V9.1→V9.6 Admin panel after the existing Supervisor UI."""
    import qlda.presentation.streamlit.autonomy_overview as overview

    if getattr(overview, "_qlda_advanced_automation_ui_installed", False):
        return
    original = overview.render_autonomy_control_center

    def render_with_advanced(st, db, project_id: int, *, ui_module=None) -> None:
        original(st, db, int(project_id), ui_module=ui_module)
        identity, is_admin, _can_update = overview._app_identity(ui_module) if ui_module is not None else ({}, False, False)
        if not is_admin:
            return
        tenant_id, label = _active_tenant(overview, st, db, int(project_id))
        actor = str(identity.get("email") or identity.get("name") or "Admin")
        if label:
            st.caption(f"Automation scope: {label} · workspace #{tenant_id}")
        _render_advanced_ui(st, db, tenant_id, actor=actor)

    overview.render_autonomy_control_center = render_with_advanced
    overview._qlda_advanced_automation_ui_installed = True
    overview._qlda_advanced_automation_ui_marker = PATCH_MARKER


__all__ = ["PATCH_MARKER", "install_advanced_automation_ui"]