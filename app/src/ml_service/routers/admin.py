from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from ml_service.database import get_db_session
from ml_service.dependencies import get_current_admin, get_model_runtime_client
from ml_service.errors import ApiError
from ml_service.model_runtime import ModelRuntimeError, OllamaModelRuntimeClient
from ml_service.models import User
from ml_service.schemas import (
    AdminActivateLocalModelRequest,
    AdminActivateLocalModelResponse,
    AdminLocalModelListResponse,
    BalanceOperationResponse,
    TopUpReviewRequest,
    TransactionHistoryResponse,
    UserListResponse,
)
from ml_service.serializers import (
    serialize_admin_local_model,
    serialize_balance,
    serialize_transaction,
    serialize_user,
)
from ml_service.services import BalanceService, MLModelService, TransactionService, UserService


router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/users", response_model=UserListResponse)
def get_admin_users(
    _: User = Depends(get_current_admin),
    session: Session = Depends(get_db_session),
) -> UserListResponse:
    users = UserService.list_users(session)
    return UserListResponse(items=[serialize_user(user) for user in users])


@router.get("/top-ups/pending", response_model=TransactionHistoryResponse)
def get_pending_top_ups(
    _: User = Depends(get_current_admin),
    session: Session = Depends(get_db_session),
) -> TransactionHistoryResponse:
    transactions = TransactionService.get_pending_top_ups(session)
    return TransactionHistoryResponse(items=[serialize_transaction(transaction) for transaction in transactions])


@router.get("/models/local", response_model=AdminLocalModelListResponse)
def get_admin_local_models(
    _: User = Depends(get_current_admin),
    session: Session = Depends(get_db_session),
    runtime_client: OllamaModelRuntimeClient = Depends(get_model_runtime_client),
) -> AdminLocalModelListResponse:
    runtime_error = None
    pulled_model_names: set[str] | None = None
    try:
        pulled_model_names = runtime_client.list_models()
    except ModelRuntimeError as exc:
        runtime_error = str(exc)

    models = MLModelService.get_ollama_catalog_models(session)
    return AdminLocalModelListResponse(
        items=[serialize_admin_local_model(model, pulled_model_names) for model in models],
        runtime_error=runtime_error,
    )


@router.post("/models/local/activate", response_model=AdminActivateLocalModelResponse)
def activate_admin_local_model(
    payload: AdminActivateLocalModelRequest,
    _: User = Depends(get_current_admin),
    session: Session = Depends(get_db_session),
    runtime_client: OllamaModelRuntimeClient = Depends(get_model_runtime_client),
) -> AdminActivateLocalModelResponse:
    model = MLModelService.get_ollama_model_by_name(session, payload.model)
    try:
        runtime_client.pull_model(model.name)
    except ModelRuntimeError as exc:
        raise ApiError(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "model_runtime_unavailable",
            str(exc),
        ) from exc

    activated_model = MLModelService.activate_ollama_model(session, model.name)
    return AdminActivateLocalModelResponse(
        model=serialize_admin_local_model(activated_model, {activated_model.name}),
        pulled=True,
    )


@router.post("/top-ups/{transaction_id}/approve", response_model=BalanceOperationResponse)
def approve_top_up(
    transaction_id: str,
    payload: TopUpReviewRequest | None = None,
    _: User = Depends(get_current_admin),
    session: Session = Depends(get_db_session),
) -> BalanceOperationResponse:
    transaction = BalanceService.approve_top_up(
        session,
        transaction_id=transaction_id,
        review_comment=payload.review_comment if payload else None,
    )
    updated_user = UserService.get_user(session, transaction.user_id)
    return BalanceOperationResponse(
        balance=serialize_balance(updated_user),
        transaction=serialize_transaction(transaction),
    )


@router.post("/top-ups/{transaction_id}/reject", response_model=BalanceOperationResponse)
def reject_top_up(
    transaction_id: str,
    payload: TopUpReviewRequest | None = None,
    _: User = Depends(get_current_admin),
    session: Session = Depends(get_db_session),
) -> BalanceOperationResponse:
    transaction = BalanceService.reject_top_up(
        session,
        transaction_id=transaction_id,
        review_comment=payload.review_comment if payload else None,
    )
    updated_user = UserService.get_user(session, transaction.user_id)
    return BalanceOperationResponse(
        balance=serialize_balance(updated_user),
        transaction=serialize_transaction(transaction),
    )
