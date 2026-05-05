from fastapi import APIRouter, Depends

from ml_service.dependencies import get_current_user
from ml_service.models import User
from ml_service.schemas import UserView
from ml_service.serializers import serialize_user


router = APIRouter(prefix="/users", tags=["users"])


@router.get("/me", response_model=UserView)
def get_me(current_user: User = Depends(get_current_user)) -> UserView:
    return serialize_user(current_user)
