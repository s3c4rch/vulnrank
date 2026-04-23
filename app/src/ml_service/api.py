from __future__ import annotations

from collections.abc import Generator
from decimal import Decimal
from typing import Any

from fastapi import Depends, FastAPI, Query, Security, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session, sessionmaker

from ml_service.broker import RabbitMQPublishError, RabbitMQTaskPublisher
from ml_service.database import get_engine, get_session_factory, wait_for_database
from ml_service.init_db import initialize_database
from ml_service.models import MLModel, MLTask, Transaction, User, UserRole, generate_id, utcnow
from ml_service.schemas import (
    AuthResponse,
    BalanceOperationResponse,
    BalanceView,
    ErrorResponse,
    HistoryResponse,
    LoginRequest,
    ModelListResponse,
    ModelView,
    PredictionHistoryItem,
    PredictionTaskMessage,
    PredictionRequest,
    PredictionResponse,
    RegisterRequest,
    TopUpRequest,
    TransactionHistoryResponse,
    TransactionView,
    UserListResponse,
    UserView,
)
from ml_service.services import (
    AuthService,
    AuthenticationError,
    BalanceService,
    EntityNotFoundError,
    InsufficientBalanceError,
    MLModelService,
    PredictionService,
    TransactionService,
    UserAlreadyExistsError,
    UserService,
)
from ml_service.web import register_web_routes


class ApiError(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        details: Any | None = None,
    ) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details
        super().__init__(message)


def build_error_response(
    status_code: int,
    code: str,
    message: str,
    details: Any | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": code,
                "message": message,
                "details": details,
            }
        },
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


def serialize_model(model: MLModel) -> ModelView:
    return ModelView(
        id=model.id,
        name=model.name,
        version=model.version,
        description=model.description,
        cost_per_prediction=Decimal(str(model.cost_per_prediction)),
    )


def create_app(
    session_factory: sessionmaker | None = None,
    initialize_on_startup: bool = True,
    task_publisher: Any | None = None,
) -> FastAPI:
    app = FastAPI(
        title="ML Service REST API",
        version="0.1.0",
        responses={
            400: {"model": ErrorResponse},
            401: {"model": ErrorResponse},
            403: {"model": ErrorResponse},
            402: {"model": ErrorResponse},
            404: {"model": ErrorResponse},
            409: {"model": ErrorResponse},
            422: {"model": ErrorResponse},
        },
    )
    auth_scheme = HTTPBearer(auto_error=False)
    resolved_session_factory = session_factory or get_session_factory()
    publisher = task_publisher or RabbitMQTaskPublisher()

    if initialize_on_startup:
        @app.on_event("startup")
        def startup() -> None:
            engine = get_engine()
            wait_for_database(engine)
            initialize_database(engine=engine)

    @app.exception_handler(ApiError)
    async def handle_api_error(_, exc: ApiError) -> JSONResponse:
        return build_error_response(exc.status_code, exc.code, exc.message, exc.details)

    @app.exception_handler(RequestValidationError)
    async def handle_request_validation_error(_, exc: RequestValidationError) -> JSONResponse:
        return build_error_response(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "validation_error",
            "Request validation failed",
            exc.errors(),
        )

    @app.exception_handler(UserAlreadyExistsError)
    async def handle_user_already_exists(_, exc: UserAlreadyExistsError) -> JSONResponse:
        return build_error_response(status.HTTP_409_CONFLICT, "user_exists", str(exc))

    @app.exception_handler(AuthenticationError)
    async def handle_authentication_error(_, exc: AuthenticationError) -> JSONResponse:
        return build_error_response(status.HTTP_401_UNAUTHORIZED, "authentication_failed", str(exc))

    @app.exception_handler(EntityNotFoundError)
    async def handle_not_found(_, exc: EntityNotFoundError) -> JSONResponse:
        return build_error_response(status.HTTP_404_NOT_FOUND, "entity_not_found", str(exc))

    @app.exception_handler(InsufficientBalanceError)
    async def handle_insufficient_balance(_, exc: InsufficientBalanceError) -> JSONResponse:
        return build_error_response(status.HTTP_402_PAYMENT_REQUIRED, "insufficient_balance", str(exc))

    def get_db_session() -> Generator[Session, None, None]:
        session = resolved_session_factory()
        try:
            yield session
        finally:
            session.close()

    def get_current_user(
        credentials: HTTPAuthorizationCredentials | None = Security(auth_scheme),
        session: Session = Depends(get_db_session),
    ) -> User:
        if credentials is None:
            raise ApiError(
                status.HTTP_401_UNAUTHORIZED,
                "missing_credentials",
                "Authorization header with bearer token is required",
            )
        return AuthService.get_user_by_token(session, credentials.credentials)

    def get_current_admin(current_user: User = Depends(get_current_user)) -> User:
        if current_user.role != UserRole.ADMIN:
            raise ApiError(
                status.HTTP_403_FORBIDDEN,
                "admin_required",
                "Admin role is required for this operation",
            )
        return current_user

    @app.get("/health")
    def health() -> dict[str, str]:
        return {
            "status": "ok",
            "service": "ml-service-app",
            "database": "initialized",
        }

    @app.post("/auth/register", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
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

    @app.post("/auth/login", response_model=AuthResponse)
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

    @app.get("/users/me", response_model=UserView)
    def get_me(current_user: User = Depends(get_current_user)) -> UserView:
        return serialize_user(current_user)

    @app.get("/balance", response_model=BalanceView)
    def get_balance(current_user: User = Depends(get_current_user)) -> BalanceView:
        return serialize_balance(current_user)

    @app.get("/models", response_model=ModelListResponse)
    def get_models(
        _: User = Depends(get_current_user),
        session: Session = Depends(get_db_session),
    ) -> ModelListResponse:
        models = MLModelService.get_active_models(session)
        return ModelListResponse(items=[serialize_model(model) for model in models])

    @app.post("/balance/top-up", response_model=BalanceOperationResponse)
    def top_up_balance(
        payload: TopUpRequest,
        current_user: User = Depends(get_current_user),
        session: Session = Depends(get_db_session),
    ) -> BalanceOperationResponse:
        transaction = BalanceService.top_up(
            session,
            user_id=current_user.id,
            amount=payload.amount,
            review_comment="balance top-up via api",
        )
        updated_user = UserService.get_user(session, current_user.id)
        return BalanceOperationResponse(
            balance=serialize_balance(updated_user),
            transaction=serialize_transaction(transaction),
        )

    @app.post("/predict", response_model=PredictionResponse, status_code=status.HTTP_202_ACCEPTED)
    def predict(
        payload: PredictionRequest,
        current_user: User = Depends(get_current_user),
        session: Session = Depends(get_db_session),
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
            publisher.publish(task_message)
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

    @app.get("/history/predictions", response_model=HistoryResponse)
    def get_prediction_history(
        current_user: User = Depends(get_current_user),
        session: Session = Depends(get_db_session),
    ) -> HistoryResponse:
        tasks = PredictionService.get_prediction_history(session, current_user.id)
        return HistoryResponse(items=[serialize_prediction_history_item(task) for task in tasks])

    @app.get("/history/transactions", response_model=TransactionHistoryResponse)
    def get_transaction_history(
        current_user: User = Depends(get_current_user),
        session: Session = Depends(get_db_session),
    ) -> TransactionHistoryResponse:
        transactions = TransactionService.get_transaction_history(session, current_user.id)
        return TransactionHistoryResponse(items=[serialize_transaction(transaction) for transaction in transactions])

    @app.get("/admin/users", response_model=UserListResponse)
    def get_admin_users(
        _: User = Depends(get_current_admin),
        session: Session = Depends(get_db_session),
    ) -> UserListResponse:
        users = UserService.list_users(session)
        return UserListResponse(items=[serialize_user(user) for user in users])

    @app.post("/admin/users/{user_id}/balance/top-up", response_model=BalanceOperationResponse)
    def admin_top_up_user(
        user_id: str,
        payload: TopUpRequest,
        _: User = Depends(get_current_admin),
        session: Session = Depends(get_db_session),
    ) -> BalanceOperationResponse:
        transaction = BalanceService.top_up(
            session,
            user_id=user_id,
            amount=payload.amount,
            review_comment="balance top-up via admin web",
        )
        updated_user = UserService.get_user(session, user_id)
        return BalanceOperationResponse(
            balance=serialize_balance(updated_user),
            transaction=serialize_transaction(transaction),
        )

    @app.get("/admin/history/predictions", response_model=HistoryResponse)
    def get_admin_prediction_history(
        failed_only: bool = Query(default=False),
        _: User = Depends(get_current_admin),
        session: Session = Depends(get_db_session),
    ) -> HistoryResponse:
        tasks = PredictionService.get_all_prediction_history(session, failed_only=failed_only)
        return HistoryResponse(items=[serialize_prediction_history_item(task) for task in tasks])

    @app.get("/admin/history/transactions", response_model=TransactionHistoryResponse)
    def get_admin_transaction_history(
        _: User = Depends(get_current_admin),
        session: Session = Depends(get_db_session),
    ) -> TransactionHistoryResponse:
        transactions = TransactionService.get_all_transaction_history(session)
        return TransactionHistoryResponse(items=[serialize_transaction(transaction) for transaction in transactions])

    register_web_routes(app)

    return app
