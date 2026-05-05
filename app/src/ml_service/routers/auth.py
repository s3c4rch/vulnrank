from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from ml_service.database import get_db_session
from ml_service.schemas import AuthResponse, LoginRequest, RegisterRequest
from ml_service.serializers import serialize_user
from ml_service.services import AuthService, UserService


router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
def register(
    payload: RegisterRequest,
    session: Session = Depends(get_db_session),
) -> AuthResponse:
    user = AuthService.register_user(session, payload.email, payload.password)
    auth_session = AuthService.create_session(session, user.id)
    loaded_user = UserService.get_user(session, user.id)
    return AuthResponse(
        access_token=auth_session.token,
        token_type="bearer",
        user=serialize_user(loaded_user),
    )


@router.post("/login", response_model=AuthResponse)
def login(
    payload: LoginRequest,
    session: Session = Depends(get_db_session),
) -> AuthResponse:
    user, auth_session = AuthService.login(session, payload.email, payload.password)
    return AuthResponse(
        access_token=auth_session.token,
        token_type="bearer",
        user=serialize_user(user),
    )
