from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from ml_service.broker import RabbitMQPublishError
from ml_service.database import get_db_session
from ml_service.dependencies import get_current_user, get_task_publisher
from ml_service.errors import ApiError
from ml_service.models import User, UserRole, generate_id, utcnow
from ml_service.schemas import PredictionRequest, PredictionResponse, PredictionTaskDetailResponse, PredictionTaskMessage
from ml_service.serializers import serialize_prediction_task_detail
from ml_service.services import BalanceService, MLModelService, PredictionService


router = APIRouter(tags=["predictions"])


@router.post("/predict", response_model=PredictionResponse, status_code=status.HTTP_202_ACCEPTED)
def predict(
    payload: PredictionRequest,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db_session),
    task_publisher=Depends(get_task_publisher),
) -> PredictionResponse:
    model = MLModelService.get_active_model_by_name(session, payload.model)
    BalanceService.ensure_sufficient_balance(session, current_user.id, model.cost_per_prediction)

    task_message = PredictionTaskMessage(
        task_id=generate_id(),
        features=payload.features,
        model=model.name,
        timestamp=utcnow(),
    )
    task = PredictionService.create_queued_task(
        session,
        user_id=current_user.id,
        model_id=model.id,
        input_payload=task_message.model_dump(mode="json"),
        task_id=task_message.task_id,
    )
    try:
        task_publisher.publish(task_message)
    except RabbitMQPublishError as exc:
        PredictionService.fail_task(session, task.id, str(exc))
        raise ApiError(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "queue_unavailable",
            "Prediction task could not be published to RabbitMQ",
        ) from exc

    return PredictionResponse(
        task_id=task.id,
        status=task.status.value,
        model=model.name,
        created_at=task.created_at,
    )


@router.get("/predict/{task_id}", response_model=PredictionTaskDetailResponse)
def get_prediction_task(
    task_id: str,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> PredictionTaskDetailResponse:
    task = PredictionService.get_task(session, task_id)
    if current_user.role != UserRole.ADMIN and task.user_id != current_user.id:
        raise ApiError(
            status.HTTP_403_FORBIDDEN,
            "task_access_forbidden",
            "You cannot access another user's prediction task",
        )
    return serialize_prediction_task_detail(task)
