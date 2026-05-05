from __future__ import annotations

from fastapi import Depends, Request, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from ml_service.broker import RabbitMQTaskPublisher
from ml_service.database import get_db_session
from ml_service.errors import ApiError
from ml_service.models import User, UserRole
from ml_service.services import AuthService


auth_scheme = HTTPBearer(auto_error=False)


def get_task_publisher(request: Request) -> RabbitMQTaskPublisher:
    return request.app.state.task_publisher


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Security(auth_scheme),
    session: Session = Depends(get_db_session),
) -> User:
    if credentials is None:
        raise ApiError(
            status.HTTP_401_UNAUTHORIZED,
            "missing_credentials",
            "Authorization header with bearer token is required",
        )
    return AuthService.get_user_by_token(session, credentials.credentials)


def get_current_admin(current_user: User = Depends(get_current_user)) -> User:
    if current_user.role != UserRole.ADMIN:
        raise ApiError(
            status.HTTP_403_FORBIDDEN,
            "admin_required",
            "Admin role is required for this operation",
        )
    return current_user
