from __future__ import annotations

from typing import Any

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
    """Deterministic, auditable project-health supervisor.

    LLMs may explain and prioritize findings, but base alarms are computed by
    transparent rules from one contractor workspace. Payment-overdue remains
    intentionally excluded: legacy payment tables may contain cumulative values
    that are not trustworthy overdue balances. V9.5 may show verified future cash
    needs from real due dates, but those amounts never become PAYMENT_OVERDUE health
    penalties here.
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

        production_source_count = int(indicators.get("production_source_count", 0) or 0)
        production_stale_hours = indicators.get("production_stale_hours")
        if (
            production_source_count > 0
            and production_stale_hours is not None
            and float(production_stale_hours) > 30.0
        ):
            hours = float(production_stale_hours)
            add(
                "PRODUCTION_DATA_STALE",
                "Nguồn sản lượng chưa cập nhật",
                f"Nguồn sản lượng gần nhất đã {hours:.1f} giờ chưa đồng bộ.",
                10.0,
                metric=hours,
                threshold=30.0,
                action="Đồng bộ lại nguồn Google Sheet của nhà thầu và kiểm tra lỗi nguồn.",
            )

        production_ready = bool(indicators.get("production_monitor_ready", False))
        production_expected = bool(indicators.get("production_expected_to_move", False))
        changed_24h = indicators.get("production_changed_24h")
        if production_ready and production_expected and changed_24h is False:
            add(
                "PRODUCTION_STALLED",
                "Sản lượng không thay đổi trong 24 giờ",
                "Nguồn sản lượng đã đồng bộ nhưng checksum dữ liệu không thay đổi trong 24 giờ, trong khi tiến độ vẫn cần tăng.",
                12.0,
                metric=0.0,
                threshold=1.0,
                action="Kiểm tra nguồn lực, vật tư, mặt bằng, sản lượng thực tế và hồ sơ nghiệm thu.",
            )
        elif "production_daily_delta" in indicators and production_expected:
            # Compatibility for explicit verified numeric production feeds. The
            # automatic collector itself never fabricates a percentage delta.
            production_delta = float(indicators.get("production_daily_delta", 0.0) or 0.0)
            if production_delta <= 0.1:
                add("PRODUCTION_STALLED", "Sản lượng gần như không tăng", f"Biến động 24h chỉ {production_delta:.2f}%.", 12.0, metric=production_delta, threshold=0.1, action="Kiểm tra nguồn lực, vật tư, mặt bằng và hồ sơ nghiệm thu.")

        ncr_overdue = int(indicators.get("ncr_overdue", 0) or 0)
        if ncr_overdue:
            add("NCR_OVERDUE", "NCR quá hạn", f"Có {ncr_overdue} NCR quá hạn theo ngày đến hạn trong hồ sơ.", min(25.0, 6.0 + ncr_overdue * 3.0), metric=ncr_overdue, threshold=0, action="Tạo task xử lý và escalation theo SLA.")

        rfi_overdue = int(indicators.get("rfi_overdue", 0) or 0)
        if rfi_overdue:
            add("RFI_OVERDUE", "RFI quá hạn", f"Có {rfi_overdue} RFI chưa phản hồi đúng hạn theo ngày đến hạn trong hồ sơ.", min(18.0, 4.0 + rfi_overdue * 2.0), metric=rfi_overdue, threshold=0, action="Nhắc người phụ trách và theo dõi phản hồi.")

        inspections_rejected = int(indicators.get("inspection_rejected", 0) or 0)
        if inspections_rejected:
            add("INSPECTION_REJECTED", "Nghiệm thu bị từ chối", f"Có {inspections_rejected} hồ sơ/đợt nghiệm thu đang ở trạng thái từ chối/chưa đạt.", min(20.0, 5.0 + inspections_rejected * 3.0), metric=inspections_rejected, threshold=0, action="Kiểm tra nguyên nhân và tạo corrective action.")

        contract_days = indicators.get("contract_days_remaining")
        if contract_days is not None and int(contract_days) <= 30:
            days = int(contract_days)
            weight = 20.0 if days <= 7 else 12.0 if days <= 18 else 6.0
            add("CONTRACT_EXPIRING", "Hợp đồng sắp hết hiệu lực", f"Còn {days} ngày hiệu lực.", weight, metric=days, threshold=30, action="Kiểm tra gia hạn/phụ lục và nghĩa vụ còn lại.")

        # V9.2: obligations come only from structured due dates/evidence recorded
        # inside the current contractor workspace. Keep the penalty modest when a
        # separate CONTRACT_EXPIRING alarm already represents the same time risk.
        obligation_overdue = int(indicators.get("contract_obligations_overdue", 0) or 0)
        if obligation_overdue:
            add(
                "CONTRACT_OBLIGATION_OVERDUE",
                "Nghĩa vụ hợp đồng quá hạn",
                f"Có {obligation_overdue} nghĩa vụ/mốc hợp đồng có ngày đến hạn thực tế đã quá hạn.",
                min(22.0, 8.0 + obligation_overdue * 3.0),
                metric=obligation_overdue,
                threshold=0,
                action="Mở Contract Obligation Ledger, xác minh bằng chứng và giao người phụ trách xử lý ngay.",
            )
        else:
            obligation_due = int(indicators.get("contract_obligations_due_30d", 0) or 0)
            if obligation_due:
                add(
                    "CONTRACT_OBLIGATION_DUE",
                    "Nghĩa vụ hợp đồng sắp đến hạn",
                    f"Có {obligation_due} nghĩa vụ/mốc có due-date trong 30 ngày tới.",
                    min(9.0, 3.0 + obligation_due),
                    metric=obligation_due,
                    threshold=0,
                    action="Theo dõi Contract Obligation Ledger và nhắc trước hạn theo SLA.",
                )

        # V9.3: only deterministic reconciliation flags generated from saved IPC
        # rows and saved BOQ rows participate in Health. The LLM does no arithmetic.
        ipc_flags = int(indicators.get("ipc_reconciliation_high_flags", 0) or 0)
        if ipc_flags:
            add(
                "IPC_BOQ_MISMATCH",
                "IPC có sai lệch so với BOQ",
                f"Đối soát IPC ↔ BOQ phát hiện {ipc_flags} cờ mức high/critical cần QS kiểm tra.",
                min(20.0, 7.0 + ipc_flags * 2.0),
                metric=ipc_flags,
                threshold=0,
                action="Mở báo cáo reconciliation, kiểm tra khối lượng/đơn vị/đơn giá/lũy kế trước khi phê duyệt IPC.",
            )

        # V9.5 is an early warning, not a claim of future certainty. Only forecasts
        # with minimum confidence affect Health and the penalty is capped to avoid
        # double-counting the current SCHEDULE_DELAY rule.
        forecast_risk = float(indicators.get("forecast_risk_score", 0.0) or 0.0)
        forecast_conf = float(indicators.get("forecast_confidence", 0.0) or 0.0)
        forecast_delay = int(indicators.get("forecast_max_delay_days", 0) or 0)
        if forecast_conf >= 0.45 and forecast_risk >= 70.0:
            add(
                "FORECAST_HIGH_RISK",
                "Dự báo rủi ro tiến độ cao",
                f"Risk score={forecast_risk:.1f}/100, confidence={forecast_conf:.0%}, độ trễ lớn nhất ngoại suy={forecast_delay} ngày.",
                10.0,
                metric=forecast_risk,
                threshold=70.0,
                action="Rà soát task critical, nguồn lực và kế hoạch bù; dùng dự báo như cảnh báo sớm, không coi là cam kết.",
            )
        elif forecast_conf >= 0.45 and forecast_risk >= 50.0:
            add(
                "FORECAST_WATCH",
                "Cần theo dõi rủi ro tiến độ dự báo",
                f"Risk score={forecast_risk:.1f}/100, confidence={forecast_conf:.0%}.",
                5.0,
                metric=forecast_risk,
                threshold=50.0,
                action="Theo dõi velocity và cập nhật dữ liệu tiến độ thường xuyên để tăng độ tin cậy dự báo.",
            )

        # PAYMENT_OVERDUE intentionally disabled. V9.5 may calculate verified cash
        # needs whose due dates are in the future, but those values are never called
        # overdue and never reduce Project Health through a payment alarm.

        score = max(0.0, 100.0 - min(100.0, penalty))
        findings.sort(key=lambda item: {RiskLevel.CRITICAL: 4, RiskLevel.HIGH: 3, RiskLevel.MEDIUM: 2, RiskLevel.LOW: 1}[item.severity], reverse=True)
        return ProjectHealthReport(int(project_id), round(score, 1), tuple(findings))

    def proposed_actions(self, report: ProjectHealthReport) -> list[dict[str, Any]]:
        actions: list[dict[str, Any]] = []
        for finding in report.findings:
            tool = "create_work_task"
            if finding.code == "DATA_INTEGRITY":
                tool = "check_data_integrity"
            elif finding.code == "PRODUCTION_DATA_STALE":
                tool = "sync_google_data"
            elif finding.code in {"NCR_OVERDUE", "RFI_OVERDUE", "CONTRACT_EXPIRING"}:
                tool = "send_notification"
            elif finding.code in {"CONTRACT_OBLIGATION_OVERDUE", "CONTRACT_OBLIGATION_DUE"}:
                tool = "audit_contract_obligations"
            elif finding.code == "IPC_BOQ_MISMATCH":
                tool = "reconcile_ipc_boq"
            elif finding.code in {"FORECAST_HIGH_RISK", "FORECAST_WATCH"}:
                tool = "forecast_project_risk"
            actions.append({
                "tool": tool,
                "finding": finding.code,
                "reason": finding.recommended_action or finding.detail,
                "severity": finding.severity.value,
            })
        return actions
