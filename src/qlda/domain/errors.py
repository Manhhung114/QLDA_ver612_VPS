from __future__ import annotations


class AuthenticationError(PermissionError):
    """Raised when the current QLDA session is missing or invalid."""


class AuthorizationError(PermissionError):
    """Raised when an authenticated account lacks permission."""


class AIApplicationError(RuntimeError):
    """Transport-neutral AI failure exposed by the application layer."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "ai_error",
        retryable: bool = False,
        action: str = "",
    ) -> None:
        super().__init__(message)
        self.code = str(code or "ai_error")
        self.retryable = bool(retryable)
        self.action = str(action or "")
