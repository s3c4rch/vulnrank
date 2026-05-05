from __future__ import annotations

from decimal import Decimal

from ml_service.models import MLModel, MLTask, Transaction, User
from ml_service.schemas import (
    BalanceView,
    ModelView,
    PredictionHistoryItem,
    PredictionTaskDetailResponse,
    TransactionView,
    UserView,
)


def serialize_balance(user: User) -> BalanceView:
    return BalanceView(
        amount=Decimal(str(user.balance.amount)),
        updated_at=user.balance.updated_at,
    )


def serialize_user(user: User) -> UserView:
    return UserView(
        id=user.id,
        email=user.email,
        role=user.role.value,
        created_at=user.created_at,
        balance=serialize_balance(user),
    )


def serialize_transaction(transaction: Transaction) -> TransactionView:
    return TransactionView(
        id=transaction.id,
        user_email=transaction.user.email if transaction.user else None,
        type=transaction.type.value,
        status=transaction.status.value,
        amount=Decimal(str(transaction.amount)),
        task_id=transaction.task_id,
        review_comment=transaction.review_comment,
        created_at=transaction.created_at,
    )


def serialize_prediction_history_item(task: MLTask) -> PredictionHistoryItem:
    return PredictionHistoryItem(
        task_id=task.id,
        user_email=task.user.email if task.user else None,
        model_id=task.model.id,
        model_name=task.model.name,
        model_version=task.model.version,
        status=task.status.value,
        prediction_value=task.result.prediction_value if task.result else None,
        predicted_priority=task.result.predicted_priority.value if task.result else None,
        confidence=task.result.confidence if task.result else None,
        worker_id=task.result.worker_id if task.result else None,
        processed_count=task.result.processed_count if task.result else None,
        rejected_count=task.result.rejected_count if task.result else None,
        spent_credits=Decimal(str(task.spent_credits)),
        error_message=task.error_message,
        created_at=task.created_at,
        finished_at=task.finished_at,
    )


def serialize_prediction_task_detail(task: MLTask) -> PredictionTaskDetailResponse:
    return PredictionTaskDetailResponse(
        task_id=task.id,
        model_id=task.model.id,
        model_name=task.model.name,
        model_version=task.model.version,
        status=task.status.value,
        prediction_value=task.result.prediction_value if task.result else None,
        predicted_priority=task.result.predicted_priority.value if task.result else None,
        confidence=task.result.confidence if task.result else None,
        worker_id=task.result.worker_id if task.result else None,
        spent_credits=Decimal(str(task.spent_credits)),
        error_message=task.error_message,
        created_at=task.created_at,
        finished_at=task.finished_at,
    )


def serialize_model(model: MLModel) -> ModelView:
    return ModelView(
        id=model.id,
        name=model.name,
        version=model.version,
        description=model.description,
        cost_per_prediction=Decimal(str(model.cost_per_prediction)),
    )
