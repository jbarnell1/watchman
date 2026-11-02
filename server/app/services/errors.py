"""The API's error shape: {"error": {"code", "message"}} (docs/api-contract.md)."""

from __future__ import annotations


class ApiError(Exception):
    status = 400
    code = "invalid"

    def __init__(self, message: str, code: str | None = None, status: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
        if status:
            self.status = status


class Unauthorized(ApiError):
    status = 401
    code = "unauthorized"


class Forbidden(ApiError):
    status = 403
    code = "forbidden"


class NotFound(ApiError):
    status = 404
    code = "not_found"


class RateLimited(ApiError):
    status = 429
    code = "rate_limited"
