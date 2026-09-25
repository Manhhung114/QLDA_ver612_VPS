from __future__ import annotations

from dataclasses import asdict
from html import escape
from typing import Any

import qlda.presentation.streamlit.autonomy_overview as overview


PATCH_MARKER = "AI SUPERVISOR COMPACT PAGE V1"


def _select_tenant(st, db, project_id: int, *, is_admin: bool) -> tuple[int, dict[str, Any]]:
    """Resolve one contractor workspace and hide the Admin switcher by default."""
    pid = int(project_id)
    master_id, rows = overview._contractor_tenants(db, pid)
    if not rows:
        return pid, {}

    by_workspace = {
        int(row.get("workspace_project_id") or 0): row
        for row in rows
        if int(row.get("workspace_project_id") or 0) > 0
    }
    if pid in by_workspace:
        return pid, dict(by_workspace[pid])

    candidates = list(by_workspace)
    if not candidates:
        return pid, {}

    sidebar_key = f"contractor_workspace_{master_id}"
    state_key = f"autonomy_tenant_{master_id}"
    selected = int(
        st.session_state.get(state_key)
        or st.session_state.get(sidebar_key)
        or candidates[0]
    )
    if selected not in by_workspace:
        selected = candidates[0]

    if is_admin and len(candidates) > 1:
        with st.expander("🏗️ Đổi nhà thầu", expanded=False):
            selected = int(
                st.selectbox(
                    "Nhà thầu",
                    candidates,
                    index=candidates.index(selected),
                    format_func=lambda wid: (
                        f"{by_workspace[wid].get('contractor_code','')} - "
                        f"{by_workspace[wid].get('contractor_name','')}"
                    ),
                    key=f"autonomy_tenant_select_{master_id}",
                )
            )

    st.session_state[state_key] = selected
    st.session_state[sidebar_key] = selected
    try:
        from qlda.runtime_core.contractor_access_control import _pin_ai_workspace_scope

        _pin_ai_workspace_scope(selected)
    except Exception:
        pass
    return selected, dict(by_workspace[selected])


def _tenant_label(contractor: dict[str, Any]) -> tuple[str, str]:
    code = str(contractor.get("contractor_code") or "").strip()
    name = str(contractor.get("contractor_name") or "").strip()
    label = " - ".join(value for value in (code, name) if value)
    return code, label


def _render_header(st, tenant_id: int, contractor: dict[str, Any]) -> None:
    code, label = _tenant_label(contractor)
    badge = escape(code or f"Workspace #{tenant_id}")
    subtitle = escape(label or f"Workspace #{tenant_id}")
    st.markdown(
        f"""
<div class="qlda-ai-page-head">
  <div class="qlda-ai-page-title">🧠 AI Supervisor</div>
  <div class="qlda-ai-tenant-badge">{badge}</div>
</div>
<div class="qlda-ai-page-subtitle">{subtitle}</div>
        """,
        unsafe_allow_html=True,
    )


def _render_kpis(st, payload: dict[str, Any] | None) -> tuple[float, float, list[dict[str, Any]]]:
    c1, c2, c3, c4 = st.columns(4)
    if not isinstance(payload, dict):
        c1.metric("Project Health", "—")
        c2.metric("Data Integrity", "—")
        c3.metric("Cảnh báo", "—")
        c4.metric("Trạng thái", "Chưa có dữ liệu")
        return 0.0, 0.0, []

    health = float(payload.get("health_score") or 0)
    integrity = float((payload.get("integrity") or {}).get("score") or 0)
    findings = list(payload.get("findings") or [])
    c1.metric("Project Health", f"{health:.0f}/100")
    c2.metric("Data Integrity", f"{integrity:.0f}%")
    c3.metric("Cảnh báo", f"{len(findings)}")
    c4.metric("Trạng thái", overview._health_badge(health))
    return health, integrity, findings


def _render_summary_tab(st, tenant_id: int, payload: dict[str, Any] | None, legacy_snapshot: bool) -> None:
    if isinstance(payload, dict):
        health = float(payload.get("health_score") or 0)
        integrity = float((payload.get("integrity") or {}).get("score") or 0)
        findings = list(payload.get("findings") or [])
        badge = overview._health_badge(health)
        st.markdown(
            f"**Trạng thái:** {badge} &nbsp;&nbsp;·&nbsp;&nbsp; "
            f"**Health:** {health:.0f}/100 &nbsp;&nbsp;·&nbsp;&nbsp; "
            f"**Integrity:** {integrity:.0f}% &nbsp;&nbsp;·&nbsp;&nbsp; "
            f"**Cảnh báo:** {len(findings)}"
        )
        if not findings:
            st.success("Không có cảnh báo đang mở trong snapshot AI Supervisor hiện tại.")
        return

    if legacy_snapshot:
        st.info("Snapshot cũ đã bị vô hiệu. AI Supervisor sẽ tạo snapshot mới theo mô hình dữ liệu hiện tại.")
    else:
        st.info("Nhà thầu này chưa có snapshot AI Supervisor. Admin có thể chạy kiểm tra trong tab Điều phối.")


def _render_findings_tab(st, tenant_id: int, findings: list[dict[str, Any]]) -> None:
    if not findings:
        st.success("Không có cảnh báo cần xử lý.")
        return

    rows = []
    for item in findings[:50]:
        rows.append(
            {
                "Mức": str(item.get("severity") or "").upper(),
                "Vấn đề": item.get("title") or item.get("code") or "",
                "Hành động": item.get("recommended_action") or "",
            }
        )
    st.dataframe(rows, hide_index=True, use_container_width=True)

    choices = list(range(min(len(findings), 50)))
    selected = st.selectbox(
        "Xem chi tiết cảnh báo",
        choices,
        format_func=lambda idx: (
            f"{str(findings[idx].get('severity') or '').upper()} · "
            f"{findings[idx].get('title') or findings[idx].get('code') or 'Cảnh báo'}"
        ),
        key=f"supervisor_finding_detail_{tenant_id}",
    )
    detail = findings[int(selected)]
    st.info(str(detail.get("detail") or "Không có mô tả chi tiết."))


def _render_coordination_tab(
    st,
    db,
    tenant_id: int,
    *,
    actor: str,
    role: str,
    identity: dict[str, Any],
    is_admin: bool,
) -> None:
    from qlda.autonomy.runtime import get_autonomy_platform, get_autonomy_repository, run_project_supervisor

    if not is_admin:
        st.info("Khu vực Điều phối dành cho Admin. Người dùng hiện tại vẫn xem được báo cáo và cảnh báo AI.")
        return

    platform = get_autonomy_platform(db)
    repository = get_autonomy_repository(db)

    a1, a2 = st.columns(2)
    if a1.button(
        "🔎 Chạy AI Supervisor",
        key=f"autonomy_supervisor_{tenant_id}",
        type="primary",
        use_container_width=True,
    ):
        try:
            result = run_project_supervisor(db, tenant_id, actor=actor or "Admin")
            st.success(
                f"Health {float(result.get('health_score') or 0):.0f}/100 · "
                f"Integrity {float((result.get('integrity') or {}).get('score') or 0):.0f}%"
            )
            st.rerun()
        except Exception as exc:
            st.error(f"AI Supervisor chưa chạy được: {exc}")

    latest_integrity = platform.tools.execute(
        "check_data_integrity",
        project_id=tenant_id,
        actor=actor,
        role="admin",
    )
    a2.metric("AI_DATA_VALID", "TRUE" if latest_integrity.get("valid") else "FALSE")

    objective = st.text_area(
        "Mục tiêu cho AI Orchestrator",
        placeholder="Ví dụ: đánh giá tiến độ và lập các việc cần xử lý.",
        key=f"autonomy_objective_{tenant_id}",
        height=88,
    )
    p1, p2 = st.columns(2)
    if p1.button(
        "🧩 Lập kế hoạch",
        disabled=not bool(objective.strip()),
        key=f"autonomy_plan_{tenant_id}",
        use_container_width=True,
    ):
        st.session_state[f"autonomy_plan_obj_{tenant_id}"] = objective.strip()
        st.rerun()

    planned_objective = str(st.session_state.get(f"autonomy_plan_obj_{tenant_id}") or "")
    if planned_objective:
        plan = platform.orchestrator.create_plan(
            tenant_id,
            planned_objective,
            context={
                "default_assignee_email": actor,
                "default_assignee_name": str(identity.get("name") or actor),
                "requester_role": role,
            },
        )
        plan_rows = []
        for step in plan.steps:
            spec = platform.tools.get(step.tool_name).spec
            plan_rows.append(
                {
                    "Bước": step.step_id,
                    "Tool": step.tool_name,
                    "Rủi ro": spec.risk.value,
                    "Chế độ": spec.mode.value,
                    "Lý do": step.reason,
                }
            )
        st.dataframe(plan_rows, hide_index=True, use_container_width=True)
        dry_run = st.checkbox(
            "Chạy thử (không thay đổi dữ liệu)",
            value=True,
            key=f"autonomy_dry_run_{tenant_id}",
        )
        if p2.button(
            "▶️ Thực thi kế hoạch",
            key=f"autonomy_execute_{tenant_id}",
            type="primary",
            use_container_width=True,
        ):
            approved_steps = repository.approved_steps(project_id=tenant_id, plan_id=plan.plan_id)
            results = platform.orchestrator.execute_plan(
                plan,
                actor=actor,
                role=role,
                approvals=approved_steps,
                dry_run=bool(dry_run),
                data_valid=bool(latest_integrity.get("valid", False)),
            )
            pending = []
            for result in results:
                if result.status == "PENDING_APPROVAL":
                    repository.request_approval(
                        project_id=tenant_id,
                        plan_id=plan.plan_id,
                        step_id=result.step_id,
                        tool_name=result.tool_name,
                        requested_by=actor,
                    )
                    pending.append(result.step_id)
            st.session_state[f"autonomy_results_{tenant_id}"] = [asdict(x) for x in results]
            if pending:
                st.warning("Có bước đang chờ phê duyệt: " + ", ".join(pending))
            else:
                st.success("Đã chạy kế hoạch qua Service Layer/Audit Gate.")

    results = st.session_state.get(f"autonomy_results_{tenant_id}") or []
    if results:
        st.dataframe(results, hide_index=True, use_container_width=True)

    pending = repository.pending_approvals(project_id=tenant_id)
    if pending:
        st.markdown("#### Phê duyệt đang chờ")
        for item in pending[:20]:
            label_item = f"{item.get('tool_name','')} · {item.get('plan_id','')} / {item.get('step_id','')}"
            with st.expander(label_item, expanded=False):
                st.write(f"Người yêu cầu: {item.get('requested_by','')}")
                note = st.text_input(
                    "Ý kiến",
                    key=f"autonomy_approval_note_{tenant_id}_{item.get('id')}",
                )
                b1, b2 = st.columns(2)
                if b1.button(
                    "✅ Phê duyệt",
                    key=f"autonomy_approve_{tenant_id}_{item.get('id')}",
                    use_container_width=True,
                ):
                    repository.decide_approval(
                        project_id=tenant_id,
                        plan_id=str(item.get("plan_id") or ""),
                        step_id=str(item.get("step_id") or ""),
                        approved=True,
                        approved_by=actor,
                        note=note,
                    )
                    st.rerun()
                if b2.button(
                    "❌ Từ chối",
                    key=f"autonomy_reject_{tenant_id}_{item.get('id')}",
                    use_container_width=True,
                ):
                    repository.decide_approval(
                        project_id=tenant_id,
                        plan_id=str(item.get("plan_id") or ""),
                        step_id=str(item.get("step_id") or ""),
                        approved=False,
                        approved_by=actor,
                        note=note,
                    )
                    st.rerun()


def _render_automation_tab(st, db, tenant_id: int, *, actor: str, is_admin: bool) -> None:
    if not is_admin:
        st.info("Khu vực Tự động hóa dành cho Admin.")
        return
    renderer = getattr(overview, "_qlda_advanced_automation_renderer", None)
    if not callable(renderer):
        try:
            from qlda.presentation.streamlit.advanced_automation import _render_advanced_ui

            renderer = _render_advanced_ui
        except Exception:
            renderer = None
    if not callable(renderer):
        st.info("Các công cụ tự động hóa chưa sẵn sàng.")
        return
    renderer(st, db, tenant_id, actor=actor)


def render_ai_supervisor_page(st, db, project_id: int, *, ui_module=None) -> None:
    """Compact contractor-isolated AI Supervisor page used by sidebar navigation."""
    identity, is_admin, can_update = (
        overview._app_identity(ui_module) if ui_module is not None else ({}, False, False)
    )
    role = str(identity.get("role") or ("admin" if is_admin else "update" if can_update else "read")).lower()
    actor = str(identity.get("email") or identity.get("name") or "QLDA User")

    tenant_id, contractor = _select_tenant(st, db, int(project_id), is_admin=is_admin)
    payload, legacy_snapshot = overview._latest_supervisor_payload(db, tenant_id)
    _render_header(st, tenant_id, contractor)
    _health, _integrity, findings = _render_kpis(st, payload)

    summary_tab, findings_tab, coordination_tab, automation_tab = st.tabs(
        ["Tổng quan AI", "Cảnh báo", "Điều phối", "Tự động hóa"]
    )
    with summary_tab:
        _render_summary_tab(st, tenant_id, payload, legacy_snapshot)
    with findings_tab:
        _render_findings_tab(st, tenant_id, findings)
    with coordination_tab:
        _render_coordination_tab(
            st,
            db,
            tenant_id,
            actor=actor,
            role=role,
            identity=identity,
            is_admin=is_admin,
        )
    with automation_tab:
        _render_automation_tab(st, db, tenant_id, actor=actor, is_admin=is_admin)


__all__ = ["PATCH_MARKER", "render_ai_supervisor_page"]
