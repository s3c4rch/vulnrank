from __future__ import annotations

from decimal import Decimal

from ml_service.model_catalog import OPENAI_MODEL_VERSION
from ml_service.models import MLModel, MLTask, Transaction, User, UserExternalModelCredential
from ml_service.schemas import (
    AdminLocalModelView,
    BalanceView,
    InvalidFileView,
    InvalidRecordView,
    ModelView,
    OpenAICredentialView,
    ProcessedRecordPredictionView,
    PredictionHistoryItem,
    PredictionTaskDetailResponse,
    SourceFileSummaryView,
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
    task_payload = _task_payload(task)
    return PredictionHistoryItem(
        task_id=task.id,
        user_email=task.user.email if task.user else None,
        model_id=task.model.id,
        model_name=task.model.name,
        model_version=task.model.version,
        status=task.status.value,
        original_filename=_optional_str(task_payload.get("original_filename")),
        upload_kind=_optional_str(task_payload.get("upload_kind")),
        accepted_count=_optional_int(task_payload.get("accepted_count")),
        prediction_value=task.result.prediction_value if task.result else None,
        predicted_priority=task.result.predicted_priority.value if task.result else None,
        confidence=task.result.confidence if task.result else None,
        worker_id=task.result.worker_id if task.result else None,
        processed_count=(
            task.result.processed_count
            if task.result
            else _optional_int(task_payload.get("processed_count"))
        ),
        rejected_count=(
            task.result.rejected_count
            if task.result
            else _optional_int(task_payload.get("rejected_count"))
        ),
        source_files=_source_files(task_payload.get("source_files")),
        invalid_files=_invalid_files(task_payload.get("invalid_files")),
        spent_credits=Decimal(str(task.spent_credits)),
        error_message=task.error_message,
        created_at=task.created_at,
        finished_at=task.finished_at,
    )


def serialize_prediction_task_detail(task: MLTask) -> PredictionTaskDetailResponse:
    task_payload = _task_payload(task)
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
        original_filename=_optional_str(task_payload.get("original_filename")),
        content_type=_optional_str(task_payload.get("content_type")),
        upload_kind=_optional_str(task_payload.get("upload_kind")),
        accepted_count=_optional_int(task_payload.get("accepted_count")),
        processed_count=(
            task.result.processed_count
            if task.result
            else _optional_int(task_payload.get("processed_count"))
        ),
        rejected_count=(
            task.result.rejected_count
            if task.result
            else _optional_int(task_payload.get("rejected_count"))
        ),
        invalid_records=_invalid_records(task_payload.get("invalid_records")),
        source_files=_source_files(task_payload.get("source_files")),
        invalid_files=_invalid_files(task_payload.get("invalid_files")),
        processed_predictions=_processed_predictions(task_payload.get("processed_predictions")),
        spent_credits=Decimal(str(task.spent_credits)),
        error_message=task.error_message,
        created_at=task.created_at,
        finished_at=task.finished_at,
    )


def serialize_model(model: MLModel) -> ModelView:
    is_external = model.version == OPENAI_MODEL_VERSION
    return ModelView(
        id=model.id,
        name=model.name,
        version=model.version,
        description=model.description,
        cost_per_prediction=Decimal(str(model.cost_per_prediction)),
        provider=model.version,
        is_external=is_external,
    )


def serialize_admin_local_model(
    model: MLModel,
    pulled_model_names: set[str] | None = None,
) -> AdminLocalModelView:
    return AdminLocalModelView(
        id=model.id,
        name=model.name,
        version=model.version,
        description=model.description,
        cost_per_prediction=Decimal(str(model.cost_per_prediction)),
        is_active=bool(model.is_active),
        is_pulled=(model.name in pulled_model_names) if pulled_model_names is not None else None,
    )


def serialize_openai_credential(
    credential: UserExternalModelCredential | None,
) -> OpenAICredentialView:
    if credential is None:
        return OpenAICredentialView(is_configured=False)
    return OpenAICredentialView(
        is_configured=bool(credential.is_enabled),
        model_name=credential.model_name,
        key_preview=_api_key_preview(credential.api_key),
    )


def _task_payload(task: MLTask) -> dict:
    if not task.input_payload:
        return {}
    payload = task.input_payload[0]
    return payload if isinstance(payload, dict) else {}


def _optional_str(value) -> str | None:
    return value if isinstance(value, str) else None


def _optional_int(value) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _invalid_records(value) -> list[InvalidRecordView] | None:
    if not isinstance(value, list):
        return None
    return [InvalidRecordView.model_validate(item) for item in value]


def _invalid_files(value) -> list[InvalidFileView] | None:
    if not isinstance(value, list):
        return None
    return [InvalidFileView.model_validate(item) for item in value]


def _processed_predictions(value) -> list[ProcessedRecordPredictionView] | None:
    if not isinstance(value, list):
        return None
    return [ProcessedRecordPredictionView.model_validate(item) for item in value]


def _source_files(value) -> list[SourceFileSummaryView] | None:
    if not isinstance(value, list):
        return None
    return [SourceFileSummaryView.model_validate(item) for item in value]


def _api_key_preview(api_key: str) -> str:
    if len(api_key) <= 8:
        return "***"
    return f"{api_key[:3]}...{api_key[-4:]}"
