from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ml_service.database import get_db_session
from ml_service.dependencies import get_current_user
from ml_service.models import User
from ml_service.schemas import ModelListResponse
from ml_service.serializers import serialize_model
from ml_service.services import MLModelService


router = APIRouter(tags=["models"])


@router.get("/models", response_model=ModelListResponse)
def get_models(
    _: User = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> ModelListResponse:
    models = MLModelService.get_active_models(session)
    return ModelListResponse(items=[serialize_model(model) for model in models])
