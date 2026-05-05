from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from sqlalchemy.orm import sessionmaker

from ml_service.broker import RabbitMQTaskPublisher
from ml_service.database import get_session_factory
from ml_service.errors import register_exception_handlers
from ml_service.lifespan import create_lifespan
from ml_service.routers import register_routers
from ml_service.schemas import ErrorResponse
from ml_service.web import register_web_routes


def create_app(
    session_factory: sessionmaker | None = None,
    initialize_on_startup: bool = True,
    task_publisher: Any | None = None,
) -> FastAPI:
    app = FastAPI(
        title="ML Service REST API",
        version="0.1.0",
        lifespan=create_lifespan(initialize_on_startup),
        responses={
            400: {"model": ErrorResponse},
            401: {"model": ErrorResponse},
            402: {"model": ErrorResponse},
            403: {"model": ErrorResponse},
            404: {"model": ErrorResponse},
            409: {"model": ErrorResponse},
            422: {"model": ErrorResponse},
        },
    )
    app.state.session_factory = session_factory or get_session_factory()
    app.state.task_publisher = task_publisher or RabbitMQTaskPublisher()

    register_exception_handlers(app)
    register_routers(app)
    register_web_routes(app)
    return app
