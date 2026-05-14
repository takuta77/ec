from __future__ import annotations


class AppError(Exception):
    code: str = "app_error"
    http_status: int = 500

    def __init__(
        self,
        message: str,
        *,
        details: dict[str, object] | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}
        self.retryable = retryable


class NotFoundError(AppError):
    code = "not_found"
    http_status = 404


class ConflictError(AppError):
    code = "conflict"
    http_status = 409


class OpenCartAlreadyExistsError(ConflictError):
    code = "open_cart_already_exists"


class ValidationError(AppError):
    code = "validation_error"
    http_status = 422


class AuthError(AppError):
    code = "auth_error"
    http_status = 401
