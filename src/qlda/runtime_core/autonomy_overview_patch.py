from __future__ import annotations

from dataclasses import asdict
from typing import Any

PATCH_MARKER = "V7.7-V9 AUTONOMY OVERVIEW CONTROL CENTER V1"


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


def _health_badge(score: float) -> str:
    if score >= 90:
        return "Tốt"
    if score >= 75:
        return "Theo dõi"
    if score >= 55:
        return "Rủi ro"
    return "Nguy cấp"


def render_autonomy_control_center(st, db, project_id: int, *, ui_module=None) -> None:
    """Compact Overview panel for the V7.7→V9 automation stack.

    Read users see only the latest verified health state. Admin owns execution,
    approvals and what-if controls; protected business actions remain approval-gated.
    """
    from qlda.runtime_core.autonomy_runtime import (
        get_autonomy_platform,
        get_autonomy_repository,
        run_project_supervisor,
    )

    project_id = int(project_id)
    identity, is_admin, can_update = _app_identity(ui_module) if ui_module is not None else ({}, False, False)
    actor = str(identity.get("email") or identity.get("name") or "QLDA User")
    role = str(identity.get("role") or ("admin" if is_admin else "update" if can_update else "read")).lower()

    platform = get_autonomy_platform(db)
    repository = get_autonomy_repository(db)
    latest = repository.latest_snapshot(project_id=project_id, snapshot_type="DAILY_SUPERVISOR")
    payload = latest.get("payload") if isinstance(latest, dict) else None

    st.markdown("### 🧠 AI Project Supervisor")
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
        st.info("AI Supervisor chưa có snapshot. Worker sẽ tự chạy sau 06:20 hoặc Admin có thể chạy kiểm tra ngay.")

    if not is_admin:
        return

    with st.expander("⚙️ Điều phối AI tự động · Admin", expanded=False):
        a1, a2 = st.columns(2)
        if a1.button("🔎 Chạy AI Supervisor ngay", key=f"autonomy_supervisor_{project_id}", type="primary", use_container_width=True):
            try:
                result = run_project_supervisor(db, project_id, actor=actor or "Admin")
                st.success(
                    f"Đã kiểm tra dự án · Health {float(result.get('health_score') or 0):.0f}/100 · "
                    f"Integrity {float((result.get('integrity') or {}).get('score') or 0):.0f}%"
                )
                st.rerun()
            except Exception as exc:
                st.error(f"AI Supervisor chưa chạy được: {exc}")

        latest_integrity = platform.tools.execute(
            "check_data_integrity",
            project_id=project_id,
            actor=actor,
            role="admin",
        )
        a2.metric("AI_DATA_VALID", "TRUE" if latest_integrity.get("valid") else "FALSE")

        objective = st.text_area(
            "Mục tiêu cho AI Orchestrator",
            placeholder="Ví dụ: Đánh giá tình hình dự án, kiểm tra sản lượng và lập các việc cần xử lý.",
            key=f"autonomy_objective_{project_id}",
        )
        p1, p2 = st.columns(2)
        if p1.button("🧩 Lập kế hoạch", disabled=not bool(objective.strip()), key=f"autonomy_plan_{project_id}", use_container_width=True):
            st.session_state[f"autonomy_plan_obj_{project_id}"] = objective.strip()
            st.rerun()

        planned_objective = str(st.session_state.get(f"autonomy_plan_obj_{project_id}") or "")
        if planned_objective:
            plan = platform.orchestrator.create_plan(project_id, planned_objective)
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
            dry_run = st.checkbox("Chạy thử (không thay đổi dữ liệu)", value=True, key=f"autonomy_dry_run_{project_id}")
            if p2.button("▶️ Thực thi kế hoạch", key=f"autonomy_execute_{project_id}", type="primary", use_container_width=True):
                results = platform.orchestrator.execute_plan(
                    plan,
                    actor=actor,
                    role=role,
                    approvals=set(),
                    dry_run=bool(dry_run),
                    data_valid=bool(latest_integrity.get("valid", False)),
                )
                pending = []
                for result in results:
                    if result.status == "PENDING_APPROVAL":
                        repository.request_approval(
                            project_id=project_id,
                            plan_id=plan.plan_id,
                            step_id=result.step_id,
                            tool_name=result.tool_name,
                            requested_by=actor,
                        )
                        pending.append(result.step_id)
                st.session_state[f"autonomy_results_{project_id}"] = [asdict(x) for x in results]
                if pending:
                    st.warning("Có bước đang chờ phê duyệt: " + ", ".join(pending))
                else:
                    st.success("Đã chạy kế hoạch qua Service Layer/Audit Gate.")

        results = st.session_state.get(f"autonomy_results_{project_id}") or []
        if results:
            st.dataframe(results, hide_index=True, use_container_width=True)

        pending = repository.pending_approvals(project_id=project_id)
        if pending:
            st.markdown("#### Phê duyệt AI đang chờ")
            for item in pending[:20]:
                label = f"{item.get('tool_name','')} · {item.get('plan_id','')} / {item.get('step_id','')}"
                with st.expander(label, expanded=False):
                    st.write(f"Người yêu cầu: {item.get('requested_by','')}")
                    note = st.text_input("Ý kiến", key=f"autonomy_approval_note_{item.get('id')}")
                    b1, b2 = st.columns(2)
                    if b1.button("✅ Phê duyệt", key=f"autonomy_approve_{item.get('id')}", use_container_width=True):
                        repository.decide_approval(
                            project_id=project_id,
                            plan_id=str(item.get("plan_id") or ""),
                            step_id=str(item.get("step_id") or ""),
                            approved=True,
                            approved_by=actor,
                            note=note,
                        )
                        st.rerun()
                    if b2.button("❌ Từ chối", key=f"autonomy_reject_{item.get('id')}", use_container_width=True):
                        repository.decide_approval(
                            project_id=project_id,
                            plan_id=str(item.get("plan_id") or ""),
                            step_id=str(item.get("step_id") or ""),
                            approved=False,
                            approved_by=actor,
                            note=note,
                        )
                        st.rerun()

        st.markdown("#### V9.0 Digital Twin · What-if")
        twin = platform.digital_twin.get(project_id)
        if twin is None and isinstance(payload, dict):
            # Supervisor state is persisted; rebuild the in-memory twin on demand.
            from qlda.autonomy.digital_twin import TwinState
            saved_twin = payload.get("twin") or {}
            twin = platform.digital_twin.update(
                TwinState(
                    project_id=project_id,
                    schedule_progress=float(saved_twin.get("schedule_progress") or 0),
                    production_progress=float(saved_twin.get("production_progress") or 0),
                    cost_progress=float(saved_twin.get("cost_progress") or 0),
                    cash_exposure=float(saved_twin.get("cash_exposure") or 0),
                    data_integrity_score=float(saved_twin.get("data_integrity_score") or 0),
                )
            )
        if twin is not None:
            s1, s2, s3 = st.columns(3)
            days = s1.number_input("Số ngày mô phỏng", min_value=1, max_value=180, value=14, step=1, key=f"twin_days_{project_id}")
            productivity = s2.number_input("Hệ số năng suất", min_value=0.5, max_value=2.0, value=1.0, step=0.05, key=f"twin_productivity_{project_id}")
            risk = s3.number_input("Rủi ro bổ sung", min_value=0.0, max_value=100.0, value=0.0, step=1.0, key=f"twin_risk_{project_id}")
            if st.button("🔮 Chạy mô phỏng", key=f"twin_simulate_{project_id}"):
                scenario = platform.digital_twin.simulate(
                    project_id,
                    {
                        "name": "Admin what-if",
                        "days": days,
                        "productivity_multiplier": productivity,
                        "added_risk": risk,
                    },
                )
                st.json(asdict(scenario))
        else:
            st.info("Chạy AI Supervisor ít nhất một lần để tạo trạng thái Digital Twin.")


def install_autonomy_overview_patch() -> None:
    """Append the control center to the existing V7 overview without editing app.py."""
    import qlda.runtime_core.ui_v7_compact as ui

    if getattr(ui, "_qlda_autonomy_overview_installed", False):
        return
    original = ui.render_overview_v7

    def render_overview_with_autonomy(st, db, pid, *args, **kwargs):
        result = original(st, db, pid, *args, **kwargs)
        try:
            render_autonomy_control_center(st, db, int(pid), ui_module=ui)
        except Exception as exc:
            # Overview must remain available even when optional AI automation has
            # a configuration problem. Admin can inspect the runtime error here.
            identity, is_admin, _ = _app_identity(ui)
            if is_admin:
                st.warning(f"AI Automation Control Center chưa sẵn sàng: {exc}")
        return result

    ui.render_overview_v7 = render_overview_with_autonomy
    ui._qlda_autonomy_overview_installed = True
    ui._qlda_autonomy_overview_marker = PATCH_MARKER


__all__ = ["render_autonomy_control_center", "install_autonomy_overview_patch", "PATCH_MARKER"]
