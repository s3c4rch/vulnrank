from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

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
    def charge(
        session: Session,
        user_id: str,
        amount: Decimal | int | float | str,
        task_id: str | None = None,
        review_comment: str | None = None,
        commit: bool = True,
    ) -> Transaction:
        normalized_amount = normalize_amount(amount)
        balance = BalanceService.get_balance(session, user_id)
        current_amount = Decimal(str(balance.amount))

        if current_amount < normalized_amount:
            raise InsufficientBalanceError(
                f"User {user_id} has insufficient balance for charge {normalized_amount}"
            )

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


class PredictionService:
    @staticmethod
    def build_mock_predictions(
        records: list[dict[str, object]],
    ) -> tuple[PriorityClass, float, list[dict[str, object]]]:
        predictions: list[dict[str, object]] = []
        priorities: list[PriorityClass] = []
        confidences: list[float] = []

        for index, record in enumerate(records):
            severity = str(record.get("severity_reported", "low")).lower()
            cvss_score = float(record.get("cvss_score") or 0.0)
            has_cve = bool(record.get("has_cve", False))

            if severity == "critical" or severity == "high" or cvss_score >= 8.0:
                priority = PriorityClass.HIGH
                confidence = 0.93 if has_cve or cvss_score >= 9.0 else 0.88
            elif severity == "medium" or cvss_score >= 5.0:
                priority = PriorityClass.MEDIUM
                confidence = 0.79
            else:
                priority = PriorityClass.LOW
                confidence = 0.68

            priorities.append(priority)
            confidences.append(confidence)
            predictions.append(
                {
                    "record_index": index,
                    "finding_type": record.get("finding_type"),
                    "predicted_priority": priority.value,
                    "confidence": round(confidence, 2),
                }
            )

        overall_priority = max(
            priorities,
            key=lambda item: {
                PriorityClass.LOW: 1,
                PriorityClass.MEDIUM: 2,
                PriorityClass.HIGH: 3,
            }[item],
        )
        average_confidence = round(sum(confidences) / len(confidences), 2)
        return overall_priority, average_confidence, predictions

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
                joinedload(MLTask.model),
                joinedload(MLTask.result),
                joinedload(MLTask.transactions),
            )
            .order_by(MLTask.created_at.desc())
        )
        return list(session.execute(statement).unique().scalars())


class TransactionService:
    @staticmethod
    def get_transaction_history(session: Session, user_id: str) -> list[Transaction]:
        statement = (
            select(Transaction)
            .where(Transaction.user_id == user_id)
            .order_by(Transaction.created_at.desc())
        )
        return list(session.scalars(statement))
