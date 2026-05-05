from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from ml_service.inference import build_feature_vector, predict_priority
from ml_service.models import (
    AuthSession,
    Balance,
    MLModel,
    MLTask,
    MLTaskStatus,
    PredictionResult,
    PriorityClass,
    Transaction,
    TransactionStatus,
    TransactionType,
    User,
    UserRole,
    generate_id,
    utcnow,
)
from ml_service.security import generate_auth_token, hash_password, verify_password


TWOPLACES = Decimal("0.01")


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


def normalize_amount(raw_amount: Decimal | int | float | str) -> Decimal:
    amount = Decimal(str(raw_amount)).quantize(TWOPLACES, rounding=ROUND_HALF_UP)
    if amount <= Decimal("0.00"):
        raise ValueError("Amount must be positive")
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
            cost_per_prediction=normalize_amount(cost_per_prediction),
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
        statement = (
            select(MLModel)
            .where(MLModel.is_active.is_(True))
            .order_by(MLModel.created_at.desc(), MLModel.version.desc())
        )
        model = session.scalars(statement).first()
        if model is None:
            raise EntityNotFoundError("No active ML model is available")
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


class PredictionService:
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
    def process_task(
        session: Session,
        task_id: str,
        model_name: str,
        features: dict[str, object],
        worker_id: str,
    ) -> MLTask:
        task = PredictionService.get_task(session, task_id)

        if task.status == MLTaskStatus.COMPLETED and task.result is not None:
            return task

        if task.model.name != model_name:
            raise ValueError(
                f"task model mismatch: expected {task.model.name}, received {model_name}"
            )

        task.status = MLTaskStatus.PROCESSING
        task.error_message = None
        session.commit()
        session.refresh(task)

        normalized_features = PredictionService.validate_prediction_features(features)
        prediction_value, predicted_priority, confidence = PredictionService.build_model_inference(
            normalized_features
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
        result.prediction_value = prediction_value
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
