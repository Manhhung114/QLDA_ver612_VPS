from __future__ import annotations

from dataclasses import dataclass

from .models import ActionMode, RiskLevel, ToolSpec


@dataclass(frozen=True, slots=True)
class AutonomyDecision:
    allowed: bool
    requires_approval: bool
    reason: str


class AutonomyPolicy:
    """V8.2 policy: low-risk automation, human approval for material decisions."""

    def decide(self, spec: ToolSpec, *, role: str, data_valid: bool = True) -> AutonomyDecision:
        normalized_role = str(role or "read").lower()
        if normalized_role not in {x.lower() for x in spec.allowed_roles}:
            return AutonomyDecision(False, False, "Người dùng không có quyền dùng công cụ này.")
        if not data_valid and spec.mode != ActionMode.READ_ONLY:
            return AutonomyDecision(False, False, "Dữ liệu chưa qua Data Integrity Gate.")
        if spec.mode == ActionMode.READ_ONLY:
            return AutonomyDecision(True, False, "Tác vụ chỉ đọc.")
        if spec.mode == ActionMode.DRAFT:
            return AutonomyDecision(True, False, "AI chỉ tạo bản nháp, chưa thay đổi trạng thái nghiệp vụ.")
        if spec.risk == RiskLevel.LOW and spec.mode == ActionMode.AUTO:
            return AutonomyDecision(True, False, "Tác vụ rủi ro thấp được tự động hóa.")
        if spec.risk == RiskLevel.MEDIUM and normalized_role == "admin" and spec.mode == ActionMode.AUTO:
            return AutonomyDecision(True, False, "Admin cho phép tự động hóa tác vụ trung bình theo policy.")
        return AutonomyDecision(True, True, "Tác vụ có ảnh hưởng nghiệp vụ/tài chính cần người có quyền phê duyệt.")


PROTECTED_ACTIONS = {
    "approve_document",
    "approve_ipc",
    "approve_vo",
    "close_ncr",
    "update_schedule_progress",
}


def validate_protected_action(name: str, *, approved: bool) -> None:
    if str(name) in PROTECTED_ACTIONS and not approved:
        raise PermissionError(f"{name} là tác vụ được bảo vệ và bắt buộc có phê duyệt của người dùng.")
