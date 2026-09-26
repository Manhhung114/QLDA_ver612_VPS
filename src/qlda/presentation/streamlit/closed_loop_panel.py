from __future__ import annotations

from typing import Any

import qlda.presentation.streamlit.autonomy_overview as overview
from qlda.autonomy.closed_loop_runtime import (
    add_closed_loop_feedback,
    get_closed_loop_engine,
    get_closed_loop_repository,
    latest_closed_loop,
    run_closed_loop_cycle,
)


PATCH_MARKER = "CLOSED LOOP ENGINEERING PANEL V3 INPUTS APPROVAL LEARN"
_STAGE_LABELS = {
    "SENSE": "Sense",
    "ANALYZE": "Analyze",
    "RECOMMEND": "Recommend",
    "APPROVE": "Approve",
    "ACT": "Act",
    "VERIFY": "Verify",
    "LEARN": "Learn",
    "CLOSED": "Closed",
}
_INPUT_LABELS = {
    "title": "Tiêu đề",
    "subject": "Chủ đề",
    "task_id": "ID công việc",
    "actual_progress": "Tiến độ thực tế (%)",
    "item_name": "Tên hạng mục",
    "old_qty": "Khối lượng cũ",
    "new_qty": "Khối lượng mới",
    "planned_quantity": "Khối lượng kế hoạch",
    "observed_quantity": "Khối lượng quan sát",
    "unit_price": "Đơn giá",
    "description": "Mô tả",
    "due_date": "Hạn xử lý",
    "due_at": "Hạn xử lý",
    "discipline": "Bộ môn",
    "contractor": "Nhà thầu",
    "assignee": "Người/đơn vị xử lý",
    "assignee_email": "Email người xử lý",
}


def _resolve_tenant(st, db, project_id: int) -> int:
    pid = int(project_id)
    master_id, rows = overview._contractor_tenants(db, pid)
    by_workspace = {
        int(row.get("workspace_project_id") or 0): row
        for row in rows
        if int(row.get("workspace_project_id") or 0) > 0
    }
    if pid in by_workspace or not by_workspace:
        return pid
    selected = int(
        st.session_state.get(f"autonomy_tenant_{master_id}")
        or st.session_state.get(f"contractor_workspace_{master_id}")
        or next(iter(by_workspace))
    )
    return selected if selected in by_workspace else next(iter(by_workspace))


def _stage_line(loop: dict[str, Any]) -> str:
    stage = str(loop.get("current_stage") or "SENSE").upper()
    order = ["SENSE", "ANALYZE", "RECOMMEND", "APPROVE", "ACT", "VERIFY", "LEARN"]
    if stage == "CLOSED":
        return " → ".join(f"✅ {_STAGE_LABELS[item]}" for item in order) + " → ✅ Closed"
    try:
        current = order.index(stage)
    except ValueError:
        current = 0
    parts = []
    for index, item in enumerate(order):
        if index < current:
            icon = "✅"
        elif index == current:
            icon = "🔵"
        else:
            icon = "⚪"
        parts.append(f"{icon} {_STAGE_LABELS[item]}")
    return " → ".join(parts)


def _recommendation_rows(loop: dict[str, Any], learning: dict[str, Any]) -> list[dict[str, Any]]:
    by_tool = dict(learning.get("by_tool") or {})
    rows: list[dict[str, Any]] = []
    for rec in list(loop.get("recommendations") or []):
        tool = str(rec.get("tool") or "")
        tool_learning = dict(by_tool.get(tool) or {})
        rate = tool_learning.get("effectiveness_rate")
        rows.append({
            "Finding": rec.get("finding") or "",
            "Tool": tool,
            "Risk": str(rec.get("risk") or "").upper(),
            "Mode": rec.get("mode") or "",
            "Trạng thái": rec.get("status") or "",
            "Cần duyệt": "Có" if rec.get("requires_approval") else "Không",
            "Cần nhập": ", ".join(str(x) for x in list(rec.get("required_inputs") or [])),
            "Hiệu quả lịch sử": (f"{float(rate):.0f}%" if rate is not None else "—"),
        })
    return rows


def _render_learning(st, learning: dict[str, Any]) -> None:
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Loop", int(learning.get("loop_count") or 0))
    c2.metric("Loop đóng", int(learning.get("closed_loops") or 0))
    c3.metric("Feedback", int(learning.get("feedback_total") or 0))
    rate = learning.get("effectiveness_rate")
    c4.metric("Hiệu quả", f"{float(rate):.0f}%" if rate is not None else "—")
    outcomes = dict(learning.get("verification_outcomes") or {})
    if outcomes:
        st.markdown(
            "**Verify:** "
            + " · ".join(
                f"{label} {int(outcomes.get(key) or 0)}"
                for key, label in (
                    ("RESOLVED", "Resolved"),
                    ("IMPROVED", "Improved"),
                    ("STABLE", "Stable"),
                    ("DEGRADED", "Degraded"),
                )
            )
        )
    safety_note = str(learning.get("safety_note") or "")
    if safety_note:
        st.info(safety_note)


def _input_widget(st, *, name: str, field_schema: dict[str, Any], current: Any, key: str):
    label = _INPUT_LABELS.get(name, name)
    raw_type = field_schema.get("type")
    types = [str(item) for item in raw_type] if isinstance(raw_type, list) else [str(raw_type or "string")]
    enum = field_schema.get("enum")
    if isinstance(enum, list) and enum:
        options = list(enum)
        index = options.index(current) if current in options else 0
        return st.selectbox(label, options, index=index, key=key)
    if "boolean" in types:
        return st.checkbox(label, value=bool(current) if current is not None else False, key=key)

    help_bits = []
    for schema_key, prefix in (
        ("minimum", "min="),
        ("exclusiveMinimum", ">"),
        ("maximum", "max="),
        ("exclusiveMaximum", "<"),
        ("maxLength", "max ký tự="),
    ):
        if field_schema.get(schema_key) is not None:
            help_bits.append(f"{prefix}{field_schema.get(schema_key)}")
    help_text = " · ".join(help_bits) or None
    value = "" if current is None else str(current)
    return st.text_input(label, value=value, key=key, help=help_text)


def _render_required_inputs(st, engine, loop: dict[str, Any], tenant_id: int, actor: str, role: str) -> None:
    needs_input = [
        dict(rec)
        for rec in list(loop.get("recommendations") or [])
        if str(rec.get("status") or "") == "NEEDS_INPUT" or list(rec.get("required_inputs") or [])
    ]
    if not needs_input:
        return

    st.markdown("#### Recommend · Bổ sung dữ liệu bắt buộc")
    st.warning("Một số hành động chưa đủ tham số. Closed Loop sẽ không Act các hành động này cho tới khi dữ liệu hợp lệ.")
    for rec in needs_input:
        tool_name = str(rec.get("tool") or "")
        step_id = str(rec.get("step_id") or "")
        required = [str(x) for x in list(rec.get("required_inputs") or [])]
        arguments = dict(rec.get("arguments") or {})
        try:
            schema = engine.recommendation_schema(tool_name)
        except Exception as exc:
            st.error(f"Không đọc được schema {tool_name}: {exc}")
            continue
        properties = dict(schema.get("properties") or {})
        with st.expander(f"{tool_name} · cần: {', '.join(required) or 'kiểm tra lại dữ liệu'}", expanded=True):
            st.write(str(rec.get("reason") or ""))
            values: dict[str, Any] = {}
            for field_name in required:
                values[field_name] = _input_widget(
                    st,
                    name=field_name,
                    field_schema=dict(properties.get(field_name) or {}),
                    current=arguments.get(field_name),
                    key=f"closed_loop_input_{tenant_id}_{step_id}_{field_name}",
                )
            if st.button(
                "💾 Lưu tham số",
                key=f"closed_loop_input_save_{tenant_id}_{step_id}",
                use_container_width=True,
            ):
                try:
                    updated = engine.set_recommendation_inputs(
                        project_id=int(tenant_id),
                        loop_id=str(loop.get("loop_id") or ""),
                        step_id=step_id,
                        inputs=values,
                        actor=str(actor),
                        role=str(role),
                    )
                    target = next(
                        (
                            item for item in list(updated.get("recommendations") or [])
                            if str(item.get("step_id") or "") == step_id
                        ),
                        {},
                    )
                    remaining = list(target.get("required_inputs") or [])
                    if remaining:
                        st.warning("Vẫn thiếu: " + ", ".join(str(x) for x in remaining))
                    elif target.get("requires_approval"):
                        st.success("Đã đủ dữ liệu. Hành động sẽ chuyển qua Approval Gate khi bấm Act.")
                    else:
                        st.success("Đã đủ dữ liệu. Hành động sẵn sàng cho Act.")
                    st.rerun()
                except Exception as exc:
                    st.error(f"Tham số chưa hợp lệ: {exc}")


def _render_loop_approvals(st, auto_repo, engine, tenant_id: int, loop_id: str, actor: str) -> None:
    pending = [
        dict(item)
        for item in auto_repo.pending_approvals(project_id=int(tenant_id))
        if str(item.get("plan_id") or "") == str(loop_id)
    ]
    if not pending:
        return

    st.markdown("#### Approve · Chờ phê duyệt")
    st.warning(f"Có {len(pending)} hành động Closed Loop đang chờ Admin quyết định.")
    for item in pending[:20]:
        tool_name = str(item.get("tool_name") or "")
        step_id = str(item.get("step_id") or "")
        with st.expander(f"{tool_name} · {step_id}", expanded=False):
            st.write(f"Người yêu cầu: {item.get('requested_by','')}")
            note = st.text_input(
                "Ý kiến phê duyệt",
                key=f"closed_loop_approval_note_{tenant_id}_{item.get('id')}",
            )
            a1, a2 = st.columns(2)
            if a1.button(
                "✅ Phê duyệt",
                key=f"closed_loop_approve_{tenant_id}_{item.get('id')}",
                use_container_width=True,
            ):
                auto_repo.decide_approval(
                    project_id=int(tenant_id),
                    plan_id=str(loop_id),
                    step_id=step_id,
                    approved=True,
                    approved_by=str(actor),
                    note=note,
                )
                st.success("Đã phê duyệt. Bấm Act để thực thi qua Service Layer/Audit Gate.")
                st.rerun()
            if a2.button(
                "❌ Từ chối",
                key=f"closed_loop_reject_{tenant_id}_{item.get('id')}",
                use_container_width=True,
            ):
                auto_repo.decide_approval(
                    project_id=int(tenant_id),
                    plan_id=str(loop_id),
                    step_id=step_id,
                    approved=False,
                    approved_by=str(actor),
                    note=note,
                )
                engine.reject_recommendation(
                    project_id=int(tenant_id),
                    loop_id=str(loop_id),
                    step_id=step_id,
                    actor=str(actor),
                    note=note,
                )
                st.warning("Đã từ chối hành động; recommendation đã được khóa ở trạng thái REJECTED.")
                st.rerun()


def render_closed_loop_panel(st, db, project_id: int, *, ui_module=None) -> None:
    """Render contractor-isolated Closed Loop Engineering under AI Supervisor."""
    from qlda.autonomy.runtime import get_autonomy_repository

    identity, is_admin, can_update = (
        overview._app_identity(ui_module) if ui_module is not None else ({}, False, False)
    )
    role = str(identity.get("role") or ("admin" if is_admin else "update" if can_update else "read")).lower()
    actor = str(identity.get("email") or identity.get("name") or "QLDA User")
    tenant_id = _resolve_tenant(st, db, int(project_id))

    st.divider()
    st.markdown("### 🔁 Closed Loop Engineering")
    st.caption("Sense → Analyze → Recommend → Approve → Act → Verify → Learn. Mọi hành động vẫn đi qua Schema, RBAC, Approval, Data Integrity và Audit Gate.")

    repository = get_closed_loop_repository(db)
    engine = get_closed_loop_engine(db)
    auto_repo = get_autonomy_repository(db)
    loop = latest_closed_loop(db, tenant_id)
    learning = engine.learning_summary(project_id=tenant_id)

    if not loop:
        st.info("Chưa có chu trình Closed Loop cho workspace này. Chạy chu trình để tạo baseline đầu tiên.")
    else:
        st.markdown(_stage_line(loop))
        verification = dict(loop.get("verification") or {})
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Health hiện tại", f"{float(loop.get('latest_health') or 0):.0f}/100")
        delta = float(verification.get("health_delta") or 0)
        c2.metric("Δ Health", f"{delta:+.1f}")
        c3.metric("Chu kỳ", int(loop.get("cycle_count") or 1))
        c4.metric("Verify", str(verification.get("outcome") or "BASELINE"))

        resolved = list(verification.get("resolved_findings") or [])
        new = list(verification.get("new_findings") or [])
        persistent = list(verification.get("persistent_findings") or [])
        if resolved:
            st.success("Đã xử lý/xác minh hết: " + ", ".join(resolved))
        if new:
            st.warning("Cảnh báo mới: " + ", ".join(new))
        if persistent:
            st.info("Còn tồn tại: " + ", ".join(persistent))

        rec_rows = _recommendation_rows(loop, learning)
        if rec_rows:
            st.markdown("#### Recommend")
            st.dataframe(rec_rows, hide_index=True, use_container_width=True)

        actions = list(loop.get("actions") or [])
        if actions:
            with st.expander("Lịch sử hành động của loop", expanded=False):
                st.dataframe(actions[-50:], hide_index=True, use_container_width=True)

    _render_learning(st, learning)

    if not is_admin:
        st.info("Closed Loop đang ở chế độ chỉ xem. Chỉ Admin được nhập dữ liệu, chạy chu trình, phê duyệt hoặc thực thi hành động.")
        return

    current_loop = latest_closed_loop(db, tenant_id)
    if current_loop:
        _render_required_inputs(st, engine, current_loop, tenant_id, actor, role)

    st.markdown("#### Sense / Analyze / Act / Verify")
    dry_run = st.checkbox(
        "Dry-run trước khi thực thi (không thay đổi dữ liệu)",
        value=True,
        key=f"closed_loop_dry_run_{tenant_id}",
    )
    c_run, c_act = st.columns(2)
    if c_run.button(
        "🔄 Sense + Analyze + Verify",
        key=f"closed_loop_cycle_{tenant_id}",
        type="primary",
        use_container_width=True,
    ):
        try:
            outcome = run_closed_loop_cycle(
                db,
                tenant_id,
                actor=actor,
                role=role,
                execute=False,
            )
            st.session_state[f"closed_loop_last_{tenant_id}"] = outcome
            st.success("Đã cập nhật một chu kỳ từ dữ liệu Supervisor hiện tại.")
            st.rerun()
        except Exception as exc:
            st.error(f"Không chạy được Closed Loop: {exc}")

    current_loop = latest_closed_loop(db, tenant_id)
    disabled = not bool(current_loop) or str(current_loop.get("status") or "") == "CLOSED"
    if c_act.button(
        "▶️ Act qua Service Layer",
        key=f"closed_loop_act_{tenant_id}",
        disabled=disabled,
        use_container_width=True,
    ):
        try:
            updated = engine.execute_ready(
                project_id=tenant_id,
                loop_id=str(current_loop.get("loop_id") or ""),
                actor=actor,
                role=role,
                dry_run=bool(dry_run),
            )
            needs_input = [
                str(x.get("tool") or "")
                for x in list(updated.get("recommendations") or [])
                if str(x.get("status") or "") == "NEEDS_INPUT"
            ]
            pending = [
                str(x.get("tool") or "")
                for x in list(updated.get("recommendations") or [])
                if str(x.get("status") or "") == "PENDING_APPROVAL"
            ]
            blocked = [
                str(x.get("tool") or "")
                for x in list(updated.get("recommendations") or [])
                if str(x.get("status") or "") == "BLOCKED_DATA_INTEGRITY"
            ]
            if blocked:
                st.error("Data Integrity chưa hợp lệ; đã chặn action ghi dữ liệu: " + ", ".join(blocked))
            elif needs_input:
                st.warning("Chưa Act các tool thiếu tham số: " + ", ".join(needs_input))
            elif pending:
                st.warning("Đã tạo yêu cầu phê duyệt: " + ", ".join(pending))
            elif dry_run:
                st.success("Dry-run hoàn tất. Không có dữ liệu nghiệp vụ nào bị thay đổi.")
            else:
                st.success("Hành động an toàn đã chạy qua Schema/RBAC/ToolRegistry/Audit Gate. Chạy Verify ở chu kỳ kế tiếp.")
            st.rerun()
        except Exception as exc:
            st.error(f"Không thực thi được Closed Loop: {exc}")

    current_loop = latest_closed_loop(db, tenant_id)
    if current_loop:
        _render_loop_approvals(
            st,
            auto_repo,
            engine,
            tenant_id,
            str(current_loop.get("loop_id") or ""),
            actor,
        )

        st.markdown("#### Human Feedback → Learn")
        feedback_options = {
            "Có hiệu quả": "EFFECTIVE",
            "Trung tính / chưa rõ": "NEUTRAL",
            "Không hiệu quả": "INEFFECTIVE",
        }
        label = st.selectbox(
            "Đánh giá kết quả",
            list(feedback_options),
            key=f"closed_loop_feedback_rating_{tenant_id}",
        )
        note = st.text_area(
            "Ghi chú feedback",
            key=f"closed_loop_feedback_note_{tenant_id}",
            height=70,
        )
        tool_choices = [""] + sorted({
            str(x.get("tool") or "")
            for x in list(current_loop.get("recommendations") or [])
            if str(x.get("tool") or "")
        })
        tool_name = st.selectbox(
            "Áp dụng cho tool (tùy chọn)",
            tool_choices,
            format_func=lambda value: value or "Toàn bộ loop",
            key=f"closed_loop_feedback_tool_{tenant_id}",
        )
        if st.button("💾 Lưu feedback", key=f"closed_loop_feedback_save_{tenant_id}"):
            try:
                add_closed_loop_feedback(
                    db,
                    tenant_id,
                    str(current_loop.get("loop_id") or ""),
                    actor=actor,
                    rating=feedback_options[label],
                    note=note,
                    tool_name=tool_name,
                )
                st.success("Đã lưu feedback vào Learn. Risk/RBAC/Approval không bị AI tự thay đổi.")
                st.rerun()
            except Exception as exc:
                st.error(f"Không lưu được feedback: {exc}")

    with st.expander("Lịch sử Closed Loop", expanded=False):
        history = repository.list_loops(project_id=tenant_id, limit=30)
        if history:
            rows = [
                {
                    "Loop": x.get("loop_id") or "",
                    "Status": x.get("status") or "",
                    "Stage": x.get("current_stage") or "",
                    "Cycles": int(x.get("cycle_count") or 0),
                    "Health": float(x.get("latest_health") or 0),
                    "Verify": str((x.get("verification") or {}).get("outcome") or ""),
                    "Updated": x.get("updated_at") or "",
                }
                for x in history
            ]
            st.dataframe(rows, hide_index=True, use_container_width=True)
        else:
            st.write("Chưa có lịch sử.")


__all__ = ["PATCH_MARKER", "render_closed_loop_panel"]
