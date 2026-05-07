from __future__ import annotations

from datetime import datetime
from decimal import Decimal
import re
from typing import Any

from pydantic import BaseModel, Field, field_validator

from ml_service.model_catalog import DEFAULT_MODEL_NAME, OPENAI_DEFAULT_MODEL_NAME


EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class ErrorBody(BaseModel):
    code: str
    message: str
    details: Any | None = None


class ErrorResponse(BaseModel):
    error: ErrorBody


class BalanceView(BaseModel):
    amount: Decimal
    updated_at: datetime


class UserView(BaseModel):
    id: str
    email: str
    role: str
    created_at: datetime
    balance: BalanceView


class UserListResponse(BaseModel):
    items: list[UserView]


class RegisterRequest(BaseModel):
    email: str
    password: str = Field(min_length=8, max_length=128)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not EMAIL_PATTERN.match(normalized):
            raise ValueError("email must be a valid email address")
        return normalized


class LoginRequest(BaseModel):
    email: str
    password: str = Field(min_length=8, max_length=128)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not EMAIL_PATTERN.match(normalized):
            raise ValueError("email must be a valid email address")
        return normalized


class AuthResponse(BaseModel):
    access_token: str
    token_type: str
    user: UserView


class TopUpRequest(BaseModel):
    amount: Decimal = Field(gt=Decimal("0.00"), max_digits=12, decimal_places=2)


class TopUpReviewRequest(BaseModel):
    review_comment: str | None = Field(default=None, max_length=500)


class TransactionView(BaseModel):
    id: str
    user_email: str | None = None
    type: str
    status: str
    amount: Decimal
    task_id: str | None
    review_comment: str | None
    created_at: datetime


class BalanceOperationResponse(BaseModel):
    balance: BalanceView
    transaction: TransactionView


class PredictionRequest(BaseModel):
    features: dict[str, float] = Field(min_length=1)
    model: str = Field(default=DEFAULT_MODEL_NAME, min_length=1)

    @field_validator("features")
    @classmethod
    def validate_features(cls, value: dict[str, float]) -> dict[str, float]:
        normalized: dict[str, float] = {}
        for raw_name, raw_value in value.items():
            name = raw_name.strip()
            if not name:
                raise ValueError("feature names must be non-empty strings")
            normalized[name] = float(raw_value)
        return normalized

    @field_validator("model")
    @classmethod
    def normalize_model_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("model must be a non-empty string")
        return normalized


class FindingRecordInput(BaseModel):
    scanner_name: str = Field(min_length=1)
    finding_type: str = Field(min_length=1)
    severity_reported: str
    cvss_score: float | None = Field(default=None, ge=0, le=10)
    asset_type: str | None = None
    port: int | None = Field(default=None, ge=0, le=65535)
    has_cve: bool = False
    description_length: int | None = Field(default=None, ge=0)

    @field_validator("scanner_name", "finding_type")
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("field must be a non-empty string")
        return normalized

    @field_validator("severity_reported")
    @classmethod
    def normalize_severity(cls, value: str) -> str:
        normalized = value.strip().lower()
        allowed_values = {"low", "medium", "high", "critical"}
        if normalized not in allowed_values:
            raise ValueError(f"severity_reported must be one of {sorted(allowed_values)}")
        return normalized


class InvalidRecordView(BaseModel):
    index: int
    record: dict[str, Any]
    errors: list[dict[str, Any]]


class InvalidFileView(BaseModel):
    filename: str
    errors: list[str]


class SourceFileSummaryView(BaseModel):
    filename: str
    format: str
    accepted_count: int
    rejected_count: int
    tool: str | None = None
    status: str


class ProcessedRecordPredictionView(BaseModel):
    record_index: int
    finding_type: str | None
    predicted_priority: str
    confidence: float
    reason: str | None = None


class PredictionTaskMessage(BaseModel):
    task_id: str
    features: dict[str, float] = Field(min_length=1)
    model: str = Field(min_length=1)
    timestamp: datetime


class ScanUploadTaskMessage(BaseModel):
    message_type: str = "scan_upload"
    task_id: str
    records: list[dict[str, Any]] = Field(min_length=1)
    model: str = Field(min_length=1)
    timestamp: datetime


class PredictionResponse(BaseModel):
    task_id: str
    status: str
    model: str
    created_at: datetime


class ScanUploadResponse(BaseModel):
    task_id: str
    status: str
    model: str
    created_at: datetime
    upload_kind: str | None = None
    accepted_count: int
    rejected_count: int
    invalid_records: list[InvalidRecordView]
    source_files: list[SourceFileSummaryView] | None = None
    invalid_files: list[InvalidFileView] | None = None


class PredictionTaskDetailResponse(BaseModel):
    task_id: str
    model_id: str
    model_name: str
    model_version: str
    status: str
    prediction_value: float | None
    predicted_priority: str | None
    confidence: float | None
    worker_id: str | None
    original_filename: str | None = None
    content_type: str | None = None
    upload_kind: str | None = None
    accepted_count: int | None = None
    processed_count: int | None = None
    rejected_count: int | None = None
    invalid_records: list[InvalidRecordView] | None = None
    source_files: list[SourceFileSummaryView] | None = None
    invalid_files: list[InvalidFileView] | None = None
    processed_predictions: list[ProcessedRecordPredictionView] | None = None
    spent_credits: Decimal
    error_message: str | None
    created_at: datetime
    finished_at: datetime | None


class ModelView(BaseModel):
    id: str
    name: str
    version: str
    description: str
    cost_per_prediction: Decimal
    provider: str = "local"
    is_external: bool = False


class ModelListResponse(BaseModel):
    items: list[ModelView]


class AdminLocalModelView(BaseModel):
    id: str
    name: str
    version: str
    description: str
    cost_per_prediction: Decimal
    is_active: bool
    is_pulled: bool | None = None


class AdminLocalModelListResponse(BaseModel):
    items: list[AdminLocalModelView]
    runtime_error: str | None = None


class AdminActivateLocalModelRequest(BaseModel):
    model: str = Field(min_length=1)

    @field_validator("model")
    @classmethod
    def normalize_model_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("model must be a non-empty string")
        return normalized


class AdminActivateLocalModelResponse(BaseModel):
    model: AdminLocalModelView
    pulled: bool = True


class OpenAICredentialRequest(BaseModel):
    api_key: str = Field(min_length=8, max_length=500)
    model_name: str = Field(default=OPENAI_DEFAULT_MODEL_NAME, min_length=1, max_length=100)

    @field_validator("api_key")
    @classmethod
    def normalize_api_key(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("api_key must be a non-empty string")
        return normalized

    @field_validator("model_name")
    @classmethod
    def normalize_openai_model_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("model_name must be a non-empty string")
        return normalized


class OpenAICredentialView(BaseModel):
    provider: str = "openai"
    is_configured: bool
    model_name: str | None = None
    key_preview: str | None = None


class PredictionHistoryItem(BaseModel):
    task_id: str
    user_email: str | None = None
    model_id: str
    model_name: str
    model_version: str
    status: str
    original_filename: str | None = None
    upload_kind: str | None = None
    accepted_count: int | None = None
    prediction_value: float | None
    predicted_priority: str | None
    confidence: float | None
    worker_id: str | None
    processed_count: int | None
    rejected_count: int | None
    source_files: list[SourceFileSummaryView] | None = None
    invalid_files: list[InvalidFileView] | None = None
    spent_credits: Decimal
    error_message: str | None
    created_at: datetime
    finished_at: datetime | None


class HistoryResponse(BaseModel):
    items: list[PredictionHistoryItem]


class TransactionHistoryResponse(BaseModel):
    items: list[TransactionView]
