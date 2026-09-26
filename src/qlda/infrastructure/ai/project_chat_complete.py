from __future__ import annotations

"""Complete native project chat: compact summaries plus detailed live domain rows."""

import time
from datetime import date
from typing import Any, Sequence

from qlda.infrastructure.ai.aggregate_context import build_authoritative_aggregate_context
from qlda.infrastructure.ai.live_domain_context import build_live_domain_context
from qlda.infrastructure.ai.project_chat import build_live_project_context
from qlda.infrastructure.ai.provider_gateway import AIProviderError, NativeProviderGateway
from qlda.infrastructure.ai.telemetry import content_hash, record_ai_event
from qlda.infrastructure.postgres import connect


def ask_project_chat(
    project_id: int,
    question: str,
    *,
    provider: str,
    history: Sequence[dict[str, Any]] | None = None,
    status_date: date | None = None,
    use_web: bool | None = None,
    workspace_scope: int | None = None,
    allow_project_wide: bool = False,
) -> str:
    """Answer from the same authorized workspace using summary + actual business rows."""
    started = time.perf_counter()
    base_context, scope = build_live_project_context(
        int(project_id),
        str(question or ""),
        workspace_scope=workspace_scope,
        allow_project_wide=allow_project_wide,
        max_chars=26000,
    )
    workspace_ids = [int(x) for x in list(scope.get("workspace_ids") or []) if int(x or 0) > 0]
    with connect() as connection:
        aggregate_context = build_authoritative_aggregate_context(
            connection,
            workspace_ids,
            str(question or ""),
            detail_limit=120,
            max_chars=18000,
        )
        detail_context = build_live_domain_context(
            connection,
            workspace_ids,
            str(question or ""),
            max_chars=22000,
        )

    effective_project = int(scope.get("master_project_id") or project_id) if scope.get("project_wide") else int(project_id)
    effective_tenant = effective_project if scope.get("project_wide") else int(workspace_scope or project_id)
    report_date = status_date.isoformat() if isinstance(status_date, date) else str(status_date or "")
    grounded = (
        f"{str(question or '').strip()}\n\n"
        f"NGÀY BÁO CÁO CHÍNH XÁC: {report_date or 'không chỉ định'}. Không được tự đổi năm/ngày.\n"
        "Dữ liệu LIVE dưới đây có độ ưu tiên cao hơn lịch sử hội thoại và mọi snapshot cũ.\n\n"
        f"{base_context}\n\n"
        f"{aggregate_context}\n\n"
        f"{detail_context}\n\n"
        "QUY TẮC TRẢ LỜI: chỉ kết luận từ dữ liệu LIVE. "
        "Nếu có [AGGREGATE-AUTHORITY], mọi số [EXACT-DOMAIN], [EXACT-DOC-TYPE] và [EXACT-DOC-SUMMARY] là tổng chính xác từ SQL và có quyền ưu tiên cao nhất. "
        "Tuyệt đối không dùng số lượng dòng chi tiết [DOC], [EXACT-DOC], top-k, kết quả tìm kiếm hoặc RAG để suy ra tổng số bản ghi. "
        "Nếu [EXACT-DOC-DETAIL-LIMIT] xuất hiện thì vẫn phải báo tổng theo [EXACT-DOC-SUMMARY], không được báo theo số dòng chi tiết đã đưa vào prompt. "
        "[LIVE-SUMMARY] và [DOMAIN-COVERAGE] là số đếm/index; các nhãn [DOC], [DRAWING], [BOQ], [PAYMENT], "
        "[IPC], [VO], [MATERIAL], [PROCUREMENT], [INVENTORY], [WORK-TASK], [CONTRACT], [APPROVAL], "
        "[DATA-HUB-ROW], [PRODUCTION-ROW] là bằng chứng chi tiết. "
        "Nếu số đếm lớn hơn 0 và có dòng chi tiết phù hợp thì tuyệt đối không nói 'chỉ có số tổng hợp', 'không có nội dung' hoặc 'không có dữ liệu'. "
        "Với yêu cầu 'gần đây nhất/mới nhất', ưu tiên dòng phù hợp đầu tiên vì các nhóm LIVE đã được sắp xếp mới nhất trước. "
        "Nếu DATA HUB bằng 0 nhưng có [PRODUCTION-ROW] thì phải dùng dữ liệu sản lượng live đó. "
        "Nếu có [SCOPE-RECOVERY], phải nói rõ dữ liệu được tìm thấy sau khi mở rộng phạm vi quản lý hợp lệ; không được nói workspace ban đầu có dữ liệu. "
        "Khi nêu số liệu/nội dung, giữ nhãn nguồn tương ứng để người dùng kiểm tra được."
    )
    source_refs = [f"workspace:{wid}" for wid in workspace_ids]
    if aggregate_context:
        source_refs.append("postgres:authoritative-aggregate")
    source_refs.append("postgres:live-domain-details")
    try:
        result = NativeProviderGateway.run(
            str(provider or "openai").lower(),
            "ask_project",
            effective_tenant,
            effective_project,
            grounded,
            history=history,
            status_date=status_date,
            use_web=use_web,
        )
        record_ai_event({
            "workspace_project_id": effective_tenant,
            "event_type": "AI_PROJECT_CHAT_COMPLETE_LIVE",
            "provider": provider,
            "input": question,
            "input_hash": content_hash(question),
            "context": scope,
            "source_refs": source_refs,
            "latency_ms": int((time.perf_counter() - started) * 1000),
            "success": True,
        })
        return result
    except AIProviderError:
        raise
    except Exception as exc:
        record_ai_event({
            "workspace_project_id": effective_tenant,
            "event_type": "AI_PROJECT_CHAT_COMPLETE_LIVE",
            "provider": provider,
            "input": question,
            "context": scope,
            "source_refs": source_refs,
            "latency_ms": int((time.perf_counter() - started) * 1000),
            "success": False,
            "error_code": exc.__class__.__name__,
        })
        raise


__all__ = ["ask_project_chat"]
