from __future__ import annotations

from dataclasses import asdict
from typing import Any

PATCH_MARKER = "V7.7-V9 AUTONOMY OVERVIEW CONTROL CENTER V2 CONTRACTOR TENANT"


def _app_identity(ui_module) -> tuple[dict[str, Any], bool, bool]:
    try:
        glob = ui_module._find_app_globals() or {}
    except Exception:
        glob = {}
    identity = {}
    try:
        getter = glob.get("_cloud_identity")
        if callable(getter):
            identity = dict(getter() or {})
    except Exception:
        pass
    role = str(identity.get("role") or "read").lower()
    try:
        is_admin = bool(glob.get("_is_admin")()) if callable(glob.get("_is_admin")) else role == "admin"
    except Exception:
        is_admin = role == "admin"
    try:
        can_update = bool(glob.get("_can_update")()) if callable(glob.get("_can_update")) else role in {"update", "admin"}
    except Exception:
        can_update = role in {"update", "admin"}
    return identity, is_admin, can_update


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


def _health_badge(score: float) -> str:
    if score >= 90:
        return "Tốt"
    if score >= 75:
        return "Theo dõi"
    if score >= 55:
        return "Rủi ro"
    return "Nguy cấp"


def _contractor_tenants(db, project_id: int) -> tuple[int, list[dict[str, Any]]]:
    """Return master id and active contractor tenants for a project/workspace."""
    pid = int(project_id)
    try:
        with db.connect() as connection:
            current = connection.execute(
                """SELECT id,master_project_id,workspace_project_id,contractor_code,contractor_name,status
                FROM project_contractors WHERE workspace_project_id=? LIMIT 1""",
                (pid,),
            ).fetchone()
            current_row = _rowdict(current)
            master_id = int(current_row.get("master_project_id") or pid)
            rows = connection.execute(
                """SELECT id,master_project_id,workspace_project_id,contractor_code,contractor_name,status
                FROM project_contractors
                WHERE master_project_id=? AND status='Đang hoạt động'
                ORDER BY contractor_code,contractor_name,id""",
                (master_id,),
            ).fetchall()
        return master_id, [_rowdict(row) for row in rows]
    except Exception:
        return pid, []


def _select_ai_tenant(st, db, project_id: int, *, is_admin: bool) -> tuple[int, dict[str, Any]]:
    """Bind the Control Center to exactly one contractor workspace.

    The normal app sidebar may already hold ``contractor_workspace_<master>``.
    Reusing that value keeps Chat AI and AI Supervisor on the same contractor.
    Admin receives an explicit selector in the Control Center; non-admin users
    remain on the workspace already authorized by the application.
    """
    pid = int(project_id)
    master_id, rows = _contractor_tenants(db, pid)
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
    preferred = int(st.session_state.get(sidebar_key) or candidates[0])
    if preferred not in by_workspace:
        preferred = candidates[0]

    if not is_admin:
        return preferred, dict(by_workspace[preferred])

    state_key = f"autonomy_tenant_{master_id}"
    current = int(st.session_state.get(state_key) or preferred)
    if current not in by_workspace:
        current = preferred
    selected = st.selectbox(
        "AI nhà thầu đang hoạt động",
        candidates,
        index=candidates.index(current),
        format_func=lambda wid: (
            f"{by_workspace[wid].get('contractor_code','')} - "
            f"{by_workspace[wid].get('contractor_name','')}"
        ),
        key=f"autonomy_tenant_select_{master_id}",
        help="Mỗi nhà thầu có AI, snapshot, approval, event và Digital Twin độc lập.",
    )
    selected = int(selected)
    st.session_state[state_key] = selected
    # Keep the normal assistant tenant aligned with the Supervisor tenant.
    st.session_state[sidebar_key] = selected
    try:
        from qlda.runtime_core.contractor_access_control import _pin_ai_workspace_scope

        _pin_ai_workspace_scope(selected)
    except Exception:
        pass
    return selected, dict(by_workspace[selected])


def render_autonomy_control_center(st, db, project_id: int, *, ui_module=None) -> None:
    """Contractor-isolated V7.7→V9 AI control center."""
    from qlda.runtime_core.autonomy_runtime import (
        get_autonomy_platform,
        get_autonomy_repository,
        run_project_supervisor,
    )

    requested_project_id = int(project_id)
    identity, is_admin, can_update = _app_identity(ui_module) if ui_module is not None else ({}, False, False)
    actor = str(identity.get("email") or identity.get("name") or "QLDA User")
    role = str(identity.get("role") or ("admin" if is_admin else "update" if can_update else "read")).lower()

    tenant_id, contractor = _select_ai_tenant(
        st, db, requested_project_id, is_admin=is_admin
    )
    label = ""
    if contractor:
        label = " - ".join(
            x for x in (
                str(contractor.get("contractor_code") or "").strip(),
                str(contractor.get("contractor_name") or "").strip(),
            ) if x
        )

    platform = get_autonomy_platform(db)
    repository = get_autonomy_repository(db)
    latest = repository.latest_snapshot(project_id=tenant_id, snapshot_type="DAILY_SUPERVISOR")
    payload = latest.get("payload") if isinstance(latest, dict) else None

    title = "### 🧠 AI Project Supervisor"
    if label:
        title += f" · {label}"
    st.markdown(title)
    if contractor:
        st.caption(
            f"AI tenant: workspace #{tenant_id}. Dữ liệu, memory, event, approval và Digital Twin được cô lập theo nhà thầu."
        )

    if isinstance(payload, dict):
        health = float(payload.get("health_score") or 0)
        integrity = float((payload.get("integrity") or {}).get("score") or 0)
        findings = list(payload.get("findings") or [])
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Project Health", f"{health:.0f}/100")
        c2.metric("Data Integrity", f"{integrity:.0f}%")
        c3.metric("Cảnh báo", f"{len(findings)}")
        c4.metric("Trạng thái", _health_badge(health))
        if findings:
            rows = []
            for item in findings[:20]:
                rows.append({
                    "Mức": str(item.get("severity") or "").upper(),
                    "Vấn đề": item.get("title") or item.get("code") or "",
                    "Chi tiết": item.get("detail") or "",
                    "Đề xuất": item.get("recommended_action") or "",
                })
            st.dataframe(rows, hide_index=True, use_container_width=True)
    else:
        st.info("AI Supervisor của nhà thầu này chưa có snapshot. Worker sẽ tự chạy sau 06:20 hoặc Admin có thể chạy kiểm tra ngay.")

    if not is_admin:
        return

    with st.expander("⚙️ Điều phối AI tự động · Admin", expanded=False):
        a1, a2 = st.columns(2)
        if a1.button("🔎 Chạy AI Supervisor ngay", key=f"autonomy_supervisor_{tenant_id}", type="primary", use_container_width=True):
            try:
                result = run_project_supervisor(db, tenant_id, actor=actor or "Admin")
                st.success(
                    f"Đã kiểm tra {label or ('workspace #' + str(tenant_id))} · "
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
            placeholder="Ví dụ: Đánh giá nhà thầu này, kiểm tra sản lượng và lập các việc cần xử lý.",
            key=f"autonomy_objective_{tenant_id}",
        )
        p1, p2 = st.columns(2)
        if p1.button("🧩 Lập kế hoạch", disabled=not bool(objective.strip()), key=f"autonomy_plan_{tenant_id}", use_container_width=True):
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
            table = []
            for step in plan.steps:
                spec = platform.tools.get(step.tool_name).spec
                table.append({
                    "Bước": step.step_id,
                    "Tool": step.tool_name,
                    "Rủi ro": spec.risk.value,
                    "Chế độ": spec.mode.value,
                    "Lý do": step.reason,
                })
            st.dataframe(table, hide_index=True, use_container_width=True)
            dry_run = st.checkbox("Chạy thử (không thay đổi dữ liệu)", value=True, key=f"autonomy_dry_run_{tenant_id}")
            if p2.button("▶️ Thực thi kế hoạch", key=f"autonomy_execute_{tenant_id}", type="primary", use_container_width=True):
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
                    st.success("Đã chạy kế hoạch trong workspace nhà thầu hiện tại qua Service Layer/Audit Gate.")

        results = st.session_state.get(f"autonomy_results_{tenant_id}") or []
        if results:
            st.dataframe(results, hide_index=True, use_container_width=True)

        pending = repository.pending_approvals(project_id=tenant_id)
        if pending:
            st.markdown("#### Phê duyệt AI đang chờ")
            for item in pending[:20]:
                label_item = f"{item.get('tool_name','')} · {item.get('plan_id','')} / {item.get('step_id','')}"
                with st.expander(label_item, expanded=False):
                    st.write(f"Người yêu cầu: {item.get('requested_by','')}")
                    note = st.text_input("Ý kiến", key=f"autonomy_approval_note_{tenant_id}_{item.get('id')}")
                    b1, b2 = st.columns(2)
                    if b1.button("✅ Phê duyệt", key=f"autonomy_approve_{tenant_id}_{item.get('id')}", use_container_width=True):
                        repository.decide_approval(
                            project_id=tenant_id,
                            plan_id=str(item.get("plan_id") or ""),
                            step_id=str(item.get("step_id") or ""),
                            approved=True,
                            approved_by=actor,
                            note=note,
                        )
                        st.rerun()
                    if b2.button("❌ Từ chối", key=f"autonomy_reject_{tenant_id}_{item.get('id')}", use_container_width=True):
                        repository.decide_approval(
                            project_id=tenant_id,
                            plan_id=str(item.get("plan_id") or ""),
                            step_id=str(item.get("step_id") or ""),
                            approved=False,
                            approved_by=actor,
                            note=note,
                        )
                        st.rerun()

        st.markdown("#### V9.0 Digital Twin · What-if")
        twin = platform.digital_twin.get(tenant_id)
        if twin is None and isinstance(payload, dict):
            from qlda.autonomy.digital_twin import TwinState

            saved_twin = payload.get("twin") or {}
            twin = platform.digital_twin.update(
                TwinState(
                    project_id=tenant_id,
                    schedule_progress=float(saved_twin.get("schedule_progress") or 0),
                    production_progress=float(saved_twin.get("production_progress") or 0),
                    cost_progress=float(saved_twin.get("cost_progress") or 0),
                    cash_exposure=float(saved_twin.get("cash_exposure") or 0),
                    data_integrity_score=float(saved_twin.get("data_integrity_score") or 0),
                )
            )
        if twin is not None:
            s1, s2, s3 = st.columns(3)
            days = s1.number_input("Số ngày mô phỏng", min_value=1, max_value=180, value=14, step=1, key=f"twin_days_{tenant_id}")
            productivity = s2.number_input("Hệ số năng suất", min_value=0.5, max_value=2.0, value=1.0, step=0.05, key=f"twin_productivity_{tenant_id}")
            risk = s3.number_input("Rủi ro bổ sung", min_value=0.0, max_value=100.0, value=0.0, step=1.0, key=f"twin_risk_{tenant_id}")
            if st.button("🔮 Chạy mô phỏng", key=f"twin_simulate_{tenant_id}"):
                scenario = platform.digital_twin.simulate(
                    tenant_id,
                    {
                        "name": f"{label or 'Contractor'} what-if",
                        "days": days,
                        "productivity_multiplier": productivity,
                        "added_risk": risk,
                    },
                )
                st.json(asdict(scenario))
        else:
            st.info("Chạy AI Supervisor của nhà thầu này ít nhất một lần để tạo trạng thái Digital Twin.")


def install_autonomy_overview_patch() -> None:
    """Append the contractor-isolated AI control center to the V7 overview."""
    import qlda.runtime_core.ui_v7_compact as ui

    if getattr(ui, "_qlda_autonomy_overview_installed", False):
        return
    original = ui.render_overview_v7

    def render_overview_with_autonomy(st, db, pid, *args, **kwargs):
        result = original(st, db, pid, *args, **kwargs)
        try:
            render_autonomy_control_center(st, db, int(pid), ui_module=ui)
        except Exception as exc:
            identity, is_admin, _ = _app_identity(ui)
            if is_admin:
                st.warning(f"AI Automation Control Center chưa sẵn sàng: {exc}")
        return result

    ui.render_overview_v7 = render_overview_with_autonomy
    ui._qlda_autonomy_overview_installed = True
    ui._qlda_autonomy_overview_marker = PATCH_MARKER


__all__ = ["render_autonomy_control_center", "install_autonomy_overview_patch", "PATCH_MARKER"]
