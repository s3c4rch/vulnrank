from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ml_service.database import get_db_session
from ml_service.dependencies import get_current_admin
from ml_service.models import User
from ml_service.schemas import UserListResponse
from ml_service.serializers import serialize_user
from ml_service.services import UserService


router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/users", response_model=UserListResponse)
def get_admin_users(
    _: User = Depends(get_current_admin),
    session: Session = Depends(get_db_session),
) -> UserListResponse:
    users = UserService.list_users(session)
    return UserListResponse(items=[serialize_user(user) for user in users])
