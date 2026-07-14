from __future__ import annotations


class LetheError(Exception):
    """Base error carrying a stable machine-readable reason code."""

    def __init__(self, code: str, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


class AuthorizationError(LetheError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(code, message, status_code=403)


class ConflictError(LetheError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(code, message, status_code=409)


class NotFoundError(LetheError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(code, message, status_code=404)


class StateUnavailableError(LetheError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(code, message, status_code=503)
