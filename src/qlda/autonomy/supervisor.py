from __future__ import annotations

from typing import Any, Iterable

from .models import HealthFinding, ProjectHealthReport, RiskLevel


def _severity(weight: float) -> RiskLevel:
    if weight >= 30:
        return RiskLevel.CRITICAL
    if weight >= 20:
        return RiskLevel.HIGH
    if weight >= 10:
        return RiskLevel.MEDIUM
    return RiskLevel.LOW


class ProjectSupervisor:
    """V8.1 deterministic project-health supervisor.

    AI can explain/prioritize the findings, but the base alarms are calculated by
    transparent rules so the system remains auditable and testable.
    """

    def evaluate(self, project_id: int, indicators: dict[str, Any]) -> ProjectHealthReport:
        findings: list[HealthFinding] = []
        penalty = 0.0

        def add(code: str, title: str, detail: str, weight: float, *, metric=None, threshold=None, action="") -> None:
            nonlocal penalty
            penalty += weight
            findings.append(
                HealthFinding(
                    code=code,
                    title=title,
                    detail=detail,
                    severity=_severity(weight),
                    metric=(float(metric) if metric is not None else None),
                    threshold=(float(threshold) if threshold is not None else None),
                    recommended_action=action,
                )
            )

        integrity = float(indicators.get("data_integrity_score", 100.0) or 0.0)
        if integrity < 100.0:
            add("DATA_INTEGRITY", "Dữ liệu chưa đối soát 100%", f"Data integrity score={integrity:.1f}%.", 30.0, metric=integrity, threshold=100.0, action="Admin kiểm tra nguồn và đồng bộ lại trước khi AI kết luận.")

        schedule_delay = float(indicators.get("schedule_delay_percent", 0.0) or 0.0)
        if schedule_delay > 5.0:
            add("SCHEDULE_DELAY", "Tiến độ chậm", f"Chênh lệch kế hoạch/thực tế {schedule_delay:.1f}%.", min(25.0, 8.0 + schedule_delay), metric=schedule_delay, threshold=5.0, action="Lập kế hoạch bù tiến độ và giao task cho bên liên quan.")

        production_delta = float(indicators.get("production_daily_delta", 0.0) or 0.0)
        if production_delta <= 0.1 and bool(indicators.get("production_expected_to_move", False)):
            add("PRODUCTION_STALLED", "Sản lượng gần như không tăng", f"Biến động 24h chỉ {production_delta:.2f}%.", 12.0, metric=production_delta, threshold=0.1, action="Kiểm tra nguồn lực, vật tư, mặt bằng và hồ sơ nghiệm thu.")

        ncr_overdue = int(indicators.get("ncr_overdue", 0) or 0)
        if ncr_overdue:
            add("NCR_OVERDUE", "NCR quá hạn", f"Có {ncr_overdue} NCR quá hạn.", min(25.0, 6.0 + ncr_overdue * 3.0), metric=ncr_overdue, threshold=0, action="Tạo task xử lý và escalates theo SLA.")

        rfi_overdue = int(indicators.get("rfi_overdue", 0) or 0)
        if rfi_overdue:
            add("RFI_OVERDUE", "RFI quá hạn", f"Có {rfi_overdue} RFI chưa phản hồi đúng hạn.", min(18.0, 4.0 + rfi_overdue * 2.0), metric=rfi_overdue, threshold=0, action="Nhắc người phụ trách và theo dõi phản hồi.")

        inspections_rejected = int(indicators.get("inspection_rejected", 0) or 0)
        if inspections_rejected:
            add("INSPECTION_REJECTED", "Nghiệm thu bị từ chối", f"Có {inspections_rejected} hồ sơ/đợt nghiệm thu bị từ chối.", min(20.0, 5.0 + inspections_rejected * 3.0), metric=inspections_rejected, threshold=0, action="Kiểm tra nguyên nhân và tạo corrective action.")

        contract_days = indicators.get("contract_days_remaining")
        if contract_days is not None and int(contract_days) <= 30:
            days = int(contract_days)
            weight = 20.0 if days <= 7 else 12.0 if days <= 18 else 6.0
            add("CONTRACT_EXPIRING", "Hợp đồng sắp hết hiệu lực", f"Còn {days} ngày hiệu lực.", weight, metric=days, threshold=30, action="Kiểm tra gia hạn/phụ lục và nghĩa vụ còn lại.")

        payment_overdue = float(indicators.get("payment_overdue_value", 0.0) or 0.0)
        if payment_overdue > 0:
            add("PAYMENT_OVERDUE", "Thanh toán quá hạn", f"Giá trị quá hạn {payment_overdue:,.0f}.", 10.0, metric=payment_overdue, threshold=0, action="Đối chiếu hồ sơ thanh toán và kỳ hạn hợp đồng.")

        score = max(0.0, 100.0 - min(100.0, penalty))
        findings.sort(key=lambda item: {RiskLevel.CRITICAL: 4, RiskLevel.HIGH: 3, RiskLevel.MEDIUM: 2, RiskLevel.LOW: 1}[item.severity], reverse=True)
        return ProjectHealthReport(int(project_id), round(score, 1), tuple(findings))

    def proposed_actions(self, report: ProjectHealthReport) -> list[dict[str, Any]]:
        actions: list[dict[str, Any]] = []
        for finding in report.findings:
            tool = "create_work_task"
            if finding.code == "DATA_INTEGRITY":
                tool = "check_data_integrity"
            elif finding.code in {"NCR_OVERDUE", "RFI_OVERDUE", "CONTRACT_EXPIRING", "PAYMENT_OVERDUE"}:
                tool = "send_notification"
            actions.append({
                "tool": tool,
                "finding": finding.code,
                "reason": finding.recommended_action or finding.detail,
                "severity": finding.severity.value,
            })
        return actions
