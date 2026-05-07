from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from ml_service.inference import PRIORITY_SCORES, build_feature_vector, predict_priority
from ml_service.model_catalog import (
    DEFAULT_MODEL_NAME,
    OLLAMA_MODEL_NAMES,
    OLLAMA_MODEL_VERSION,
    OPENAI_MODEL_VERSION,
    OPENAI_PROVIDER_MODEL_NAME,
    is_ollama_model,
    is_openai_provider_model,
)
from ml_service.models import (
    AuthSession,
    Balance,
    ExternalProvider,
    MLModel,
    MLTask,
    MLTaskStatus,
    PredictionResult,
    PriorityClass,
    Transaction,
    TransactionStatus,
    TransactionType,
    User,
    UserExternalModelCredential,
    UserRole,
    generate_id,
    utcnow,
)
from ml_service.security import generate_auth_token, hash_password, verify_password


TWOPLACES = Decimal("0.01")


@dataclass(frozen=True)
class TaskProcessingContext:
    task_id: str
    user_id: str
    model_name: str
    features: dict[str, float]
    already_completed: bool = False
    existing_prediction_value: float | None = None


@dataclass(frozen=True)
class BatchTaskProcessingContext:
    task_id: str
    user_id: str
    model_name: str
    records: list[dict[str, Any]]
    already_completed: bool = False
    existing_prediction_value: float | None = None
    existing_processed_count: int = 0
    existing_rejected_count: int = 0


class DomainError(Exception):
    """Base domain error."""


class EntityNotFoundError(DomainError):
    """Raised when an entity is missing."""


class UserAlreadyExistsError(DomainError):
    """Raised when a user with the same email already exists."""


class InsufficientBalanceError(DomainError):
    """Raised when balance is not enough for debit."""


class AuthenticationError(DomainError):
    """Raised when authentication fails."""


class InvalidTransactionStateError(DomainError):
    """Raised when a transaction cannot move to the requested state."""


def normalize_amount(raw_amount: Decimal | int | float | str) -> Decimal:
    amount = Decimal(str(raw_amount)).quantize(TWOPLACES, rounding=ROUND_HALF_UP)
    if amount <= Decimal("0.00"):
        raise ValueError("Amount must be positive")
    return amount


def normalize_model_cost(raw_amount: Decimal | int | float | str) -> Decimal:
    amount = Decimal(str(raw_amount)).quantize(TWOPLACES, rounding=ROUND_HALF_UP)
    if amount < Decimal("0.00"):
        raise ValueError("Model cost must be zero or positive")
    return amount


class UserService:
    @staticmethod
    def create_user(
        session: Session,
        email: str,
        password_hash: str,
        role: UserRole = UserRole.USER,
        initial_balance: Decimal | int | float | str = Decimal("0.00"),
    ) -> User:
        existing_user = session.scalar(select(User).where(User.email == email))
        if existing_user is not None:
            raise UserAlreadyExistsError(f"User with email {email} already exists")

        user = User(email=email, password_hash=password_hash, role=role)
        user.balance = Balance(amount=Decimal("0.00"))
        session.add(user)
        session.flush()

        normalized_initial_balance = Decimal(str(initial_balance)).quantize(TWOPLACES, rounding=ROUND_HALF_UP)
        if normalized_initial_balance < Decimal("0.00"):
            raise ValueError("Initial balance must be zero or positive")
        if normalized_initial_balance > Decimal("0.00"):
            BalanceService.top_up(
                session,
                user_id=user.id,
                amount=normalized_initial_balance,
                review_comment="initial user balance",
                commit=False,
            )

        session.commit()
        session.refresh(user)
        return user

    @staticmethod
    def get_user(session: Session, user_id: str) -> User:
        statement = (
            select(User)
            .where(User.id == user_id)
            .options(
                joinedload(User.balance),
                joinedload(User.transactions),
                joinedload(User.tasks).joinedload(MLTask.result),
            )
        )
        user = session.execute(statement).unique().scalar_one_or_none()
        if user is None:
            raise EntityNotFoundError(f"User {user_id} was not found")
        return user

    @staticmethod
    def get_user_by_email(session: Session, email: str) -> User | None:
        return session.scalar(select(User).where(User.email == email))

    @staticmethod
    def list_users(session: Session) -> list[User]:
        statement = (
            select(User)
            .options(joinedload(User.balance))
            .order_by(User.created_at.desc())
        )
        return list(session.execute(statement).unique().scalars())


class AuthService:
    @staticmethod
    def register_user(session: Session, email: str, password: str) -> User:
        return UserService.create_user(
            session,
            email=email,
            password_hash=hash_password(password),
            role=UserRole.USER,
        )

    @staticmethod
    def create_session(session: Session, user_id: str) -> AuthSession:
        auth_session = AuthSession(user_id=user_id, token=generate_auth_token())
        session.add(auth_session)
        session.commit()
        session.refresh(auth_session)
        return auth_session

    @staticmethod
    def login(session: Session, email: str, password: str) -> tuple[User, AuthSession]:
        user = UserService.get_user_by_email(session, email)
        if user is None or not verify_password(password, user.password_hash):
            raise AuthenticationError("Invalid email or password")

        auth_session = AuthService.create_session(session, user.id)
        return UserService.get_user(session, user.id), auth_session

    @staticmethod
    def get_user_by_token(session: Session, token: str) -> User:
        statement = (
            select(AuthSession)
            .where(AuthSession.token == token)
            .options(joinedload(AuthSession.user).joinedload(User.balance))
        )
        auth_session = session.execute(statement).unique().scalar_one_or_none()
        if auth_session is None:
            raise AuthenticationError("Invalid authentication token")
        return UserService.get_user(session, auth_session.user_id)


class BalanceService:
    @staticmethod
    def get_balance(session: Session, user_id: str) -> Balance:
        user = session.get(User, user_id)
        if user is None:
            raise EntityNotFoundError(f"User {user_id} was not found")
        if user.balance is None:
            user.balance = Balance(amount=Decimal("0.00"))
            session.add(user.balance)
            session.flush()
        return user.balance

    @staticmethod
    def top_up(
        session: Session,
        user_id: str,
        amount: Decimal | int | float | str,
        review_comment: str | None = None,
        commit: bool = True,
    ) -> Transaction:
        normalized_amount = normalize_amount(amount)
        balance = BalanceService.get_balance(session, user_id)
        balance.amount = Decimal(str(balance.amount)) + normalized_amount

        transaction = Transaction(
            user_id=user_id,
            type=TransactionType.TOP_UP,
            status=TransactionStatus.COMPLETED,
            amount=normalized_amount,
            review_comment=review_comment,
        )
        session.add(transaction)

        if commit:
            session.commit()
            session.refresh(transaction)
        else:
            session.flush()

        return transaction

    @staticmethod
    def request_top_up(
        session: Session,
        user_id: str,
        amount: Decimal | int | float | str,
        review_comment: str | None = None,
        commit: bool = True,
    ) -> Transaction:
        normalized_amount = normalize_amount(amount)
        BalanceService.get_balance(session, user_id)

        transaction = Transaction(
            user_id=user_id,
            type=TransactionType.TOP_UP,
            status=TransactionStatus.PENDING,
            amount=normalized_amount,
            review_comment=review_comment,
        )
        session.add(transaction)

        if commit:
            session.commit()
            session.refresh(transaction)
        else:
            session.flush()

        return transaction

    @staticmethod
    def approve_top_up(
        session: Session,
        transaction_id: str,
        review_comment: str | None = None,
        commit: bool = True,
    ) -> Transaction:
        transaction = BalanceService._get_pending_top_up(session, transaction_id)
        balance = BalanceService.get_balance(session, transaction.user_id)

        balance.amount = Decimal(str(balance.amount)) + Decimal(str(transaction.amount))
        transaction.status = TransactionStatus.APPROVED
        if review_comment is not None:
            transaction.review_comment = review_comment

        if commit:
            session.commit()
            session.refresh(transaction)
        else:
            session.flush()

        return transaction

    @staticmethod
    def reject_top_up(
        session: Session,
        transaction_id: str,
        review_comment: str | None = None,
        commit: bool = True,
    ) -> Transaction:
        transaction = BalanceService._get_pending_top_up(session, transaction_id)
        transaction.status = TransactionStatus.REJECTED
        if review_comment is not None:
            transaction.review_comment = review_comment

        if commit:
            session.commit()
            session.refresh(transaction)
        else:
            session.flush()

        return transaction

    @staticmethod
    def _get_pending_top_up(session: Session, transaction_id: str) -> Transaction:
        statement = (
            select(Transaction)
            .where(Transaction.id == transaction_id)
            .options(joinedload(Transaction.user).joinedload(User.balance))
        )
        transaction = session.execute(statement).unique().scalar_one_or_none()
        if transaction is None:
            raise EntityNotFoundError(f"Transaction {transaction_id} was not found")
        if transaction.type != TransactionType.TOP_UP:
            raise InvalidTransactionStateError("Only top-up transactions can be moderated")
        if transaction.status != TransactionStatus.PENDING:
            raise InvalidTransactionStateError(
                f"Top-up transaction {transaction_id} is already {transaction.status.value}"
            )
        return transaction

    @staticmethod
    def ensure_sufficient_balance(
        session: Session,
        user_id: str,
        amount: Decimal | int | float | str,
    ) -> Balance:
        normalized_amount = normalize_amount(amount)
        balance = BalanceService.get_balance(session, user_id)
        current_amount = Decimal(str(balance.amount))

        if current_amount < normalized_amount:
            raise InsufficientBalanceError(
                f"User {user_id} has insufficient balance for charge {normalized_amount}"
            )

        return balance

    @staticmethod
    def charge(
        session: Session,
        user_id: str,
        amount: Decimal | int | float | str,
        task_id: str | None = None,
        review_comment: str | None = None,
        commit: bool = True,
    ) -> Transaction:
        normalized_amount = normalize_amount(amount)
        balance = BalanceService.ensure_sufficient_balance(session, user_id, normalized_amount)
        current_amount = Decimal(str(balance.amount))

        balance.amount = current_amount - normalized_amount
        transaction = Transaction(
            user_id=user_id,
            task_id=task_id,
            type=TransactionType.PREDICTION_CHARGE,
            status=TransactionStatus.COMPLETED,
            amount=normalized_amount,
            review_comment=review_comment,
        )
        session.add(transaction)

        if commit:
            session.commit()
            session.refresh(transaction)
        else:
            session.flush()

        return transaction


class MLModelService:
    @staticmethod
    def create_model(
        session: Session,
        name: str,
        version: str,
        description: str,
        cost_per_prediction: Decimal | int | float | str,
        is_active: bool = True,
    ) -> MLModel:
        existing_model = session.scalar(
            select(MLModel).where(MLModel.name == name, MLModel.version == version)
        )
        if existing_model is not None:
            return existing_model

        model = MLModel(
            name=name,
            version=version,
            description=description,
            cost_per_prediction=normalize_model_cost(cost_per_prediction),
            is_active=is_active,
        )
        session.add(model)
        session.commit()
        session.refresh(model)
        return model

    @staticmethod
    def get_model(session: Session, model_id: str) -> MLModel:
        model = session.get(MLModel, model_id)
        if model is None:
            raise EntityNotFoundError(f"ML model {model_id} was not found")
        return model

    @staticmethod
    def get_default_active_model(session: Session) -> MLModel:
        default_model = session.scalar(
            select(MLModel).where(
                MLModel.is_active.is_(True),
                MLModel.name == DEFAULT_MODEL_NAME,
                MLModel.version == OLLAMA_MODEL_VERSION,
            )
        )
        if default_model is not None:
            return default_model

        statement = (
            select(MLModel)
            .where(MLModel.is_active.is_(True), MLModel.version == OLLAMA_MODEL_VERSION)
            .order_by(MLModel.created_at.desc(), MLModel.version.desc())
        )
        model = session.scalars(statement).first()
        if model is None:
            raise EntityNotFoundError("No active ML model is available")
        return model

    @staticmethod
    def get_model_by_name(session: Session, model_name: str) -> MLModel:
        statement = (
            select(MLModel)
            .where(MLModel.name == model_name)
            .order_by(MLModel.created_at.desc(), MLModel.version.desc())
        )
        model = session.scalars(statement).first()
        if model is None:
            raise EntityNotFoundError(f"ML model {model_name} was not found")
        return model

    @staticmethod
    def get_active_model_by_name(session: Session, model_name: str) -> MLModel:
        statement = (
            select(MLModel)
            .where(MLModel.is_active.is_(True), MLModel.name == model_name)
            .order_by(MLModel.created_at.desc(), MLModel.version.desc())
        )
        model = session.scalars(statement).first()
        if model is None:
            raise EntityNotFoundError(f"Active ML model {model_name} was not found")
        return model

    @staticmethod
    def get_active_models(session: Session) -> list[MLModel]:
        statement = (
            select(MLModel)
            .where(MLModel.is_active.is_(True))
            .order_by(MLModel.name.asc(), MLModel.version.desc())
        )
        return list(session.scalars(statement))

    @staticmethod
    def get_available_models_for_user(session: Session, user_id: str) -> list[MLModel]:
        statement = (
            select(MLModel)
            .where(
                MLModel.is_active.is_(True),
                MLModel.version == OLLAMA_MODEL_VERSION,
            )
            .order_by(MLModel.name.asc(), MLModel.version.desc())
        )
        models = list(session.scalars(statement))

        if ExternalModelCredentialService.get_openai_credential(session, user_id) is not None:
            openai_model = session.scalar(
                select(MLModel).where(
                    MLModel.is_active.is_(True),
                    MLModel.name == OPENAI_PROVIDER_MODEL_NAME,
                    MLModel.version == OPENAI_MODEL_VERSION,
                )
            )
            if openai_model is not None:
                models.append(openai_model)

        return models

    @staticmethod
    def get_available_model_for_user(session: Session, user_id: str, model_name: str) -> MLModel:
        normalized_model_name = model_name.strip()
        if is_openai_provider_model(normalized_model_name):
            if ExternalModelCredentialService.get_openai_credential(session, user_id) is None:
                raise EntityNotFoundError("OpenAI credentials are not configured for this user")
            return MLModelService.get_active_model_by_name(session, normalized_model_name)

        try:
            model = MLModelService.get_active_model_by_name(session, normalized_model_name)
        except EntityNotFoundError:
            if normalized_model_name == DEFAULT_MODEL_NAME:
                return MLModelService.get_current_active_ollama_model(session)
            raise
        if model.version != OLLAMA_MODEL_VERSION:
            raise EntityNotFoundError(f"Active local ML model {normalized_model_name} was not found")
        return model

    @staticmethod
    def get_current_active_ollama_model(session: Session) -> MLModel:
        statement = (
            select(MLModel)
            .where(
                MLModel.is_active.is_(True),
                MLModel.version == OLLAMA_MODEL_VERSION,
            )
            .order_by(MLModel.created_at.desc(), MLModel.version.desc())
        )
        model = session.scalars(statement).first()
        if model is None:
            raise EntityNotFoundError("No active local Ollama model is available")
        return model

    @staticmethod
    def get_ollama_catalog_models(session: Session) -> list[MLModel]:
        statement = (
            select(MLModel)
            .where(MLModel.version == OLLAMA_MODEL_VERSION)
            .order_by(MLModel.name.asc(), MLModel.version.desc())
        )
        return list(session.scalars(statement))

    @staticmethod
    def get_ollama_model_by_name(session: Session, model_name: str) -> MLModel:
        if not is_ollama_model(model_name):
            raise EntityNotFoundError(f"Ollama model {model_name} is not in the local allowlist")
        statement = (
            select(MLModel)
            .where(MLModel.name == model_name, MLModel.version == OLLAMA_MODEL_VERSION)
            .order_by(MLModel.created_at.desc(), MLModel.version.desc())
        )
        model = session.scalars(statement).first()
        if model is None:
            raise EntityNotFoundError(f"Ollama model {model_name} was not found")
        return model

    @staticmethod
    def activate_ollama_model(session: Session, model_name: str) -> MLModel:
        selected_model = MLModelService.get_ollama_model_by_name(session, model_name)
        models = MLModelService.get_ollama_catalog_models(session)
        for model in models:
            model.is_active = model.name == selected_model.name
        session.commit()
        session.refresh(selected_model)
        return selected_model

    @staticmethod
    def ensure_single_active_ollama_model(session: Session) -> None:
        models = MLModelService.get_ollama_catalog_models(session)
        active_models = [model for model in models if model.is_active]
        if len(active_models) == 1:
            return

        preferred_name = DEFAULT_MODEL_NAME if DEFAULT_MODEL_NAME in OLLAMA_MODEL_NAMES else None
        for model in models:
            model.is_active = model.name == preferred_name
        session.commit()


class ExternalModelCredentialService:
    @staticmethod
    def get_openai_credential(
        session: Session,
        user_id: str,
    ) -> UserExternalModelCredential | None:
        return session.scalar(
            select(UserExternalModelCredential).where(
                UserExternalModelCredential.user_id == user_id,
                UserExternalModelCredential.provider == ExternalProvider.OPENAI,
                UserExternalModelCredential.is_enabled.is_(True),
            )
        )

    @staticmethod
    def upsert_openai_credential(
        session: Session,
        user_id: str,
        api_key: str,
        model_name: str,
    ) -> UserExternalModelCredential:
        user = session.get(User, user_id)
        if user is None:
            raise EntityNotFoundError(f"User {user_id} was not found")

        credential = session.scalar(
            select(UserExternalModelCredential).where(
                UserExternalModelCredential.user_id == user_id,
                UserExternalModelCredential.provider == ExternalProvider.OPENAI,
            )
        )
        if credential is None:
            credential = UserExternalModelCredential(
                user_id=user_id,
                provider=ExternalProvider.OPENAI,
                api_key=api_key,
                model_name=model_name,
                is_enabled=True,
            )
            session.add(credential)
        else:
            credential.api_key = api_key
            credential.model_name = model_name
            credential.is_enabled = True
            credential.updated_at = utcnow()

        session.commit()
        session.refresh(credential)
        return credential

    @staticmethod
    def disable_openai_credential(session: Session, user_id: str) -> None:
        credential = session.scalar(
            select(UserExternalModelCredential).where(
                UserExternalModelCredential.user_id == user_id,
                UserExternalModelCredential.provider == ExternalProvider.OPENAI,
            )
        )
        if credential is None:
            return
        credential.is_enabled = False
        credential.updated_at = utcnow()
        session.commit()


class PredictionService:
    @staticmethod
    def calculate_prediction_cost(model: MLModel, record_count: int) -> Decimal:
        if record_count < 0:
            raise ValueError("record_count must be zero or positive")
        return (
            Decimal(str(model.cost_per_prediction)) * Decimal(record_count)
        ).quantize(TWOPLACES, rounding=ROUND_HALF_UP)

    @staticmethod
    def create_queued_task(
        session: Session,
        user_id: str,
        model_id: str,
        input_payload: dict[str, Any],
        task_id: str | None = None,
    ) -> MLTask:
        user = session.get(User, user_id)
        if user is None:
            raise EntityNotFoundError(f"User {user_id} was not found")

        model = session.get(MLModel, model_id)
        if model is None:
            raise EntityNotFoundError(f"ML model {model_id} was not found")

        task = MLTask(
            id=task_id or generate_id(),
            user_id=user_id,
            model_id=model_id,
            status=MLTaskStatus.CREATED,
            input_payload=[input_payload],
            spent_credits=Decimal("0.00"),
            error_message=None,
        )
        session.add(task)
        session.commit()
        session.refresh(task)
        return task

    @staticmethod
    def get_task(session: Session, task_id: str) -> MLTask:
        statement = (
            select(MLTask)
            .where(MLTask.id == task_id)
            .options(
                joinedload(MLTask.model),
                joinedload(MLTask.result),
                joinedload(MLTask.transactions),
            )
        )
        task = session.execute(statement).unique().scalar_one_or_none()
        if task is None:
            raise EntityNotFoundError(f"ML task {task_id} was not found")
        return task

    @staticmethod
    def fail_task(session: Session, task_id: str, error_message: str) -> MLTask:
        task = PredictionService.get_task(session, task_id)
        if task.status == MLTaskStatus.COMPLETED and task.result is not None:
            return task
        task.status = MLTaskStatus.FAILED
        task.error_message = error_message
        task.finished_at = utcnow()
        session.commit()
        session.refresh(task)
        return task

    @staticmethod
    def validate_prediction_features(features: dict[str, object]) -> dict[str, float]:
        if not isinstance(features, dict) or not features:
            raise ValueError("features must contain numeric values for x1 and x2")

        normalized_features: dict[str, float] = {}
        for raw_name, raw_value in features.items():
            name = str(raw_name).strip()
            if not name:
                raise ValueError("feature names must be non-empty strings")
            try:
                normalized_features[name] = float(raw_value)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"feature {name} must be numeric") from exc

        build_feature_vector(normalized_features)
        return normalized_features

    @staticmethod
    def build_model_inference(features: dict[str, float]) -> tuple[float, PriorityClass, float]:
        return predict_priority(features)

    @staticmethod
    def prediction_value_for_priority(priority: PriorityClass) -> float:
        return float(PRIORITY_SCORES[priority.value])

    @staticmethod
    def priority_for_batch(predictions: list[dict[str, Any]]) -> PriorityClass:
        if not predictions:
            raise ValueError("predictions must contain at least one item")

        def score(item: dict[str, Any]) -> float:
            return PRIORITY_SCORES[str(item["predicted_priority"])]

        highest = max(predictions, key=score)
        return PriorityClass(str(highest["predicted_priority"]))

    @staticmethod
    def start_task_processing(
        session: Session,
        task_id: str,
        model_name: str,
        features: dict[str, object],
        worker_id: str,
    ) -> TaskProcessingContext:
        task = PredictionService.get_task(session, task_id)

        if task.status == MLTaskStatus.COMPLETED and task.result is not None:
            return TaskProcessingContext(
                task_id=task.id,
                user_id=task.user_id,
                model_name=task.model.name,
                features={},
                already_completed=True,
                existing_prediction_value=task.result.prediction_value,
            )

        if task.model.name != model_name:
            raise ValueError(
                f"task model mismatch: expected {task.model.name}, received {model_name}"
            )

        normalized_features = PredictionService.validate_prediction_features(features)
        task.status = MLTaskStatus.PROCESSING
        task.error_message = None
        session.commit()
        session.refresh(task)

        return TaskProcessingContext(
            task_id=task.id,
            user_id=task.user_id,
            model_name=task.model.name,
            features=normalized_features,
        )

    @staticmethod
    def start_batch_task_processing(
        session: Session,
        task_id: str,
        model_name: str,
        records: list[dict[str, Any]],
        worker_id: str,
    ) -> BatchTaskProcessingContext:
        task = PredictionService.get_task(session, task_id)

        if task.status == MLTaskStatus.COMPLETED and task.result is not None:
            return BatchTaskProcessingContext(
                task_id=task.id,
                user_id=task.user_id,
                model_name=task.model.name,
                records=[],
                already_completed=True,
                existing_prediction_value=task.result.prediction_value,
                existing_processed_count=task.result.processed_count,
                existing_rejected_count=task.result.rejected_count,
            )

        if task.model.name != model_name:
            raise ValueError(
                f"task model mismatch: expected {task.model.name}, received {model_name}"
            )
        if not records:
            raise ValueError("batch task must contain at least one accepted record")
        for record in records:
            if not isinstance(record, dict):
                raise ValueError("batch records must be objects")

        task.status = MLTaskStatus.PROCESSING
        task.error_message = None
        session.commit()
        session.refresh(task)

        return BatchTaskProcessingContext(
            task_id=task.id,
            user_id=task.user_id,
            model_name=task.model.name,
            records=records,
        )

    @staticmethod
    def complete_task(
        session: Session,
        task_id: str,
        predicted_priority: PriorityClass,
        confidence: float,
        worker_id: str,
        prediction_value: float | None = None,
    ) -> MLTask:
        task = PredictionService.get_task(session, task_id)
        if task.status == MLTaskStatus.COMPLETED and task.result is not None:
            return task
        resolved_prediction_value = (
            prediction_value
            if prediction_value is not None
            else PredictionService.prediction_value_for_priority(predicted_priority)
        )
        spent_credits = Decimal(str(task.model.cost_per_prediction)).quantize(
            TWOPLACES,
            rounding=ROUND_HALF_UP,
        )

        if spent_credits > Decimal("0.00"):
            BalanceService.charge(
                session,
                user_id=task.user_id,
                amount=spent_credits,
                task_id=task.id,
                review_comment=f"prediction charge by {worker_id}",
                commit=False,
            )

        task.status = MLTaskStatus.COMPLETED
        task.spent_credits = spent_credits
        task.finished_at = utcnow()
        task.error_message = None

        result = task.result or PredictionResult(task_id=task.id)
        result.predicted_priority = predicted_priority
        result.prediction_value = resolved_prediction_value
        result.confidence = confidence
        result.processed_count = 1
        result.rejected_count = 0
        result.spent_credits = spent_credits
        result.worker_id = worker_id
        session.add(result)

        session.commit()
        session.refresh(task)
        return task

    @staticmethod
    def complete_batch_task(
        session: Session,
        task_id: str,
        processed_predictions: list[dict[str, Any]],
        rejected_count: int,
        worker_id: str,
        error_message: str | None = None,
    ) -> MLTask:
        task = PredictionService.get_task(session, task_id)
        if task.status == MLTaskStatus.COMPLETED and task.result is not None:
            return task
        processed_count = len(processed_predictions)
        if processed_count <= 0:
            raise ValueError("batch task must have at least one processed prediction")

        predicted_priority = PredictionService.priority_for_batch(processed_predictions)
        prediction_value = PredictionService.prediction_value_for_priority(predicted_priority)
        confidence = (
            sum(float(item["confidence"]) for item in processed_predictions) / processed_count
        )
        spent_credits = PredictionService.calculate_prediction_cost(task.model, processed_count)

        if spent_credits > Decimal("0.00"):
            BalanceService.charge(
                session,
                user_id=task.user_id,
                amount=spent_credits,
                task_id=task.id,
                review_comment=f"batch prediction charge by {worker_id}",
                commit=False,
            )

        task.status = MLTaskStatus.COMPLETED
        task.spent_credits = spent_credits
        task.finished_at = utcnow()
        task.error_message = error_message
        task.input_payload = PredictionService._updated_task_payload(
            task,
            {
                "processed_count": processed_count,
                "rejected_count": rejected_count,
                "processed_predictions": processed_predictions,
            },
        )

        result = task.result or PredictionResult(task_id=task.id)
        result.predicted_priority = predicted_priority
        result.prediction_value = prediction_value
        result.confidence = confidence
        result.processed_count = processed_count
        result.rejected_count = rejected_count
        result.spent_credits = spent_credits
        result.worker_id = worker_id
        session.add(result)

        session.commit()
        session.refresh(task)
        return task

    @staticmethod
    def _updated_task_payload(task: MLTask, updates: dict[str, Any]) -> list[dict[str, Any]]:
        payload = task.input_payload[0] if task.input_payload else {}
        if not isinstance(payload, dict):
            payload = {"payload": payload}
        return [{**payload, **updates}]

    @staticmethod
    def process_task(
        session: Session,
        task_id: str,
        model_name: str,
        features: dict[str, object],
        worker_id: str,
    ) -> MLTask:
        processing_context = PredictionService.start_task_processing(
            session,
            task_id=task_id,
            model_name=model_name,
            features=features,
            worker_id=worker_id,
        )
        if processing_context.already_completed:
            return PredictionService.get_task(session, task_id)

        prediction_value, predicted_priority, confidence = PredictionService.build_model_inference(
            processing_context.features
        )
        return PredictionService.complete_task(
            session,
            task_id=task_id,
            predicted_priority=predicted_priority,
            confidence=confidence,
            worker_id=worker_id,
            prediction_value=prediction_value,
        )

    @staticmethod
    def record_prediction(
        session: Session,
        user_id: str,
        model_id: str,
        input_payload: list[dict[str, object]],
        predicted_priority: PriorityClass,
        confidence: float,
        processed_count: int,
        rejected_count: int = 0,
    ) -> MLTask:
        user = session.get(User, user_id)
        if user is None:
            raise EntityNotFoundError(f"User {user_id} was not found")

        model = session.get(MLModel, model_id)
        if model is None:
            raise EntityNotFoundError(f"ML model {model_id} was not found")

        spent_credits = Decimal(str(model.cost_per_prediction)) * Decimal(processed_count)
        spent_credits = spent_credits.quantize(TWOPLACES, rounding=ROUND_HALF_UP)

        task = MLTask(
            user_id=user_id,
            model_id=model_id,
            status=MLTaskStatus.COMPLETED,
            input_payload=input_payload,
            spent_credits=spent_credits,
            finished_at=utcnow(),
        )
        session.add(task)
        session.flush()

        if spent_credits > Decimal("0.00"):
            BalanceService.charge(
                session,
                user_id=user_id,
                amount=spent_credits,
                task_id=task.id,
                review_comment="prediction charge",
                commit=False,
            )

        result = PredictionResult(
            task_id=task.id,
            predicted_priority=predicted_priority,
            confidence=confidence,
            processed_count=processed_count,
            rejected_count=rejected_count,
            spent_credits=spent_credits,
        )
        session.add(result)
        session.commit()
        session.refresh(task)
        return task

    @staticmethod
    def get_prediction_history(session: Session, user_id: str) -> list[MLTask]:
        statement = (
            select(MLTask)
            .where(MLTask.user_id == user_id)
            .options(
                joinedload(MLTask.user),
                joinedload(MLTask.model),
                joinedload(MLTask.result),
                joinedload(MLTask.transactions),
            )
            .order_by(MLTask.created_at.desc())
        )
        return list(session.execute(statement).unique().scalars())

    @staticmethod
    def get_all_prediction_history(
        session: Session,
        failed_only: bool = False,
    ) -> list[MLTask]:
        statement = (
            select(MLTask)
            .options(
                joinedload(MLTask.user),
                joinedload(MLTask.model),
                joinedload(MLTask.result),
                joinedload(MLTask.transactions),
            )
            .order_by(MLTask.created_at.desc())
        )
        if failed_only:
            statement = statement.where(MLTask.status == MLTaskStatus.FAILED)
        return list(session.execute(statement).unique().scalars())


class TransactionService:
    @staticmethod
    def get_transaction_history(session: Session, user_id: str) -> list[Transaction]:
        statement = (
            select(Transaction)
            .where(Transaction.user_id == user_id)
            .options(joinedload(Transaction.user))
            .order_by(Transaction.created_at.desc())
        )
        return list(session.scalars(statement))

    @staticmethod
    def get_all_transaction_history(session: Session) -> list[Transaction]:
        statement = (
            select(Transaction)
            .options(joinedload(Transaction.user))
            .order_by(Transaction.created_at.desc())
        )
        return list(session.scalars(statement))

    @staticmethod
    def get_pending_top_ups(session: Session) -> list[Transaction]:
        statement = (
            select(Transaction)
            .where(
                Transaction.type == TransactionType.TOP_UP,
                Transaction.status == TransactionStatus.PENDING,
            )
            .options(joinedload(Transaction.user))
            .order_by(Transaction.created_at.asc())
        )
        return list(session.scalars(statement))
