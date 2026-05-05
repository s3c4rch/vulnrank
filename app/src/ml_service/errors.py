from __future__ import annotations

from typing import Any

from fastapi import FastAPI, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from ml_service.services import (
    AuthenticationError,
    EntityNotFoundError,
    InsufficientBalanceError,
    UserAlreadyExistsError,
)


class ApiError(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        details: Any | None = None,
    ) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details
        super().__init__(message)


def build_error_response(
    status_code: int,
    code: str,
    message: str,
    details: Any | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": code,
                "message": message,
                "details": details,
            }
        },
    )


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def handle_api_error(_, exc: ApiError) -> JSONResponse:
        return build_error_response(exc.status_code, exc.code, exc.message, exc.details)

    @app.exception_handler(RequestValidationError)
    async def handle_request_validation_error(_, exc: RequestValidationError) -> JSONResponse:
        return build_error_response(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "validation_error",
            "Request validation failed",
            exc.errors(),
        )

    @app.exception_handler(UserAlreadyExistsError)
    async def handle_user_already_exists(_, exc: UserAlreadyExistsError) -> JSONResponse:
        return build_error_response(status.HTTP_409_CONFLICT, "user_exists", str(exc))

    @app.exception_handler(AuthenticationError)
    async def handle_authentication_error(_, exc: AuthenticationError) -> JSONResponse:
        return build_error_response(status.HTTP_401_UNAUTHORIZED, "authentication_failed", str(exc))

    @app.exception_handler(EntityNotFoundError)
    async def handle_not_found(_, exc: EntityNotFoundError) -> JSONResponse:
        return build_error_response(status.HTTP_404_NOT_FOUND, "entity_not_found", str(exc))

    @app.exception_handler(InsufficientBalanceError)
    async def handle_insufficient_balance(_, exc: InsufficientBalanceError) -> JSONResponse:
        return build_error_response(status.HTTP_402_PAYMENT_REQUIRED, "insufficient_balance", str(exc))
