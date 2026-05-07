from decimal import Decimal

from fastapi import APIRouter, Depends, File, Form, UploadFile, status
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from ml_service.broker import RabbitMQPublishError
from ml_service.database import get_db_session
from ml_service.dependencies import get_current_user, get_task_publisher
from ml_service.errors import ApiError
from ml_service.model_catalog import DEFAULT_MODEL_NAME
from ml_service.models import MLTask, MLTaskStatus, User, UserRole, generate_id, utcnow
from ml_service.reporting import build_prediction_task_report
from ml_service.scan_parser import ScanUploadParseError, parse_scan_upload
from ml_service.schemas import (
    PredictionRequest,
    PredictionResponse,
    PredictionTaskDetailResponse,
    PredictionTaskMessage,
    ScanUploadResponse,
    ScanUploadTaskMessage,
)
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
    model = MLModelService.get_available_model_for_user(session, current_user.id, payload.model)
    if Decimal(str(model.cost_per_prediction)) > Decimal("0.00"):
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


@router.post("/predict/upload", response_model=ScanUploadResponse, status_code=status.HTTP_202_ACCEPTED)
def predict_upload(
    model: str = Form(default=DEFAULT_MODEL_NAME),
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db_session),
    task_publisher=Depends(get_task_publisher),
) -> ScanUploadResponse:
    resolved_model = MLModelService.get_available_model_for_user(session, current_user.id, model.strip())
    try:
        parsed_upload = parse_scan_upload(
            filename=file.filename,
            content_type=file.content_type,
            content=file.file.read(),
        )
    except ScanUploadParseError as exc:
        raise ApiError(
            status.HTTP_400_BAD_REQUEST,
            "invalid_scan_upload",
            str(exc),
        ) from exc

    if parsed_upload.accepted_count > 0:
        required_credits = PredictionService.calculate_prediction_cost(
            resolved_model,
            parsed_upload.accepted_count,
        )
        if required_credits > Decimal("0.00"):
            BalanceService.ensure_sufficient_balance(session, current_user.id, required_credits)

    task_id = generate_id()
    input_payload = {
        "task_type": "scan_upload",
        "upload_kind": parsed_upload.upload_kind,
        "original_filename": file.filename,
        "content_type": file.content_type,
        "accepted_count": parsed_upload.accepted_count,
        "rejected_count": parsed_upload.rejected_count,
        "accepted_records": parsed_upload.accepted_records,
        "invalid_records": [
            invalid_record.model_dump(mode="json")
            for invalid_record in parsed_upload.invalid_records
        ],
        "source_files": [source_file.model_dump(mode="json") for source_file in parsed_upload.source_files],
        "invalid_files": [invalid_file.model_dump(mode="json") for invalid_file in parsed_upload.invalid_files],
    }
    task = PredictionService.create_queued_task(
        session,
        user_id=current_user.id,
        model_id=resolved_model.id,
        input_payload=input_payload,
        task_id=task_id,
    )

    if parsed_upload.accepted_count == 0:
        task = PredictionService.fail_task(
            session,
            task.id,
            "Scan upload did not contain valid records",
        )
        return ScanUploadResponse(
            task_id=task.id,
            status=task.status.value,
            model=resolved_model.name,
            created_at=task.created_at,
            upload_kind=parsed_upload.upload_kind,
            accepted_count=parsed_upload.accepted_count,
            rejected_count=parsed_upload.rejected_count,
            invalid_records=parsed_upload.invalid_records,
            source_files=parsed_upload.source_files,
            invalid_files=parsed_upload.invalid_files,
        )

    try:
        task_message = ScanUploadTaskMessage(
            task_id=task.id,
            records=parsed_upload.accepted_records,
            model=resolved_model.name,
            timestamp=utcnow(),
        )
        task_publisher.publish(task_message)
    except RabbitMQPublishError as exc:
        PredictionService.fail_task(session, task.id, str(exc))
        raise ApiError(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "queue_unavailable",
            "Scan upload task could not be published to RabbitMQ",
        ) from exc

    return ScanUploadResponse(
        task_id=task.id,
        status=task.status.value,
        model=resolved_model.name,
        created_at=task.created_at,
        upload_kind=parsed_upload.upload_kind,
        accepted_count=parsed_upload.accepted_count,
        rejected_count=parsed_upload.rejected_count,
        invalid_records=parsed_upload.invalid_records,
        source_files=parsed_upload.source_files,
        invalid_files=parsed_upload.invalid_files,
    )


@router.get("/predict/{task_id}", response_model=PredictionTaskDetailResponse)
def get_prediction_task(
    task_id: str,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> PredictionTaskDetailResponse:
    task = _get_accessible_task(task_id, current_user, session)
    return serialize_prediction_task_detail(task)


@router.get("/predict/{task_id}/report", response_class=HTMLResponse)
def get_prediction_task_report(
    task_id: str,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> HTMLResponse:
    task = _get_accessible_task(task_id, current_user, session)
    _ensure_report_available(task)
    return HTMLResponse(content=build_prediction_task_report(task))


@router.get("/predict/{task_id}/report/download", response_class=HTMLResponse)
def download_prediction_task_report(
    task_id: str,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> HTMLResponse:
    task = _get_accessible_task(task_id, current_user, session)
    _ensure_report_available(task)
    filename = f"vulnrank-report-{task.id}.html"
    return HTMLResponse(
        content=build_prediction_task_report(task),
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _get_accessible_task(task_id: str, current_user: User, session: Session) -> MLTask:
    task = PredictionService.get_task(session, task_id)
    if current_user.role != UserRole.ADMIN and task.user_id != current_user.id:
        raise ApiError(
            status.HTTP_403_FORBIDDEN,
            "task_access_forbidden",
            "You cannot access another user's prediction task",
        )
    return task


def _ensure_report_available(task: MLTask) -> None:
    if task.status != MLTaskStatus.COMPLETED or task.result is None:
        raise ApiError(
            status.HTTP_409_CONFLICT,
            "report_unavailable",
            "HTML report is available only for completed prediction tasks",
        )
