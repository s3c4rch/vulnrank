from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, Enum as SqlEnum, ForeignKey, Integer, JSON, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ml_service.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def generate_id() -> str:
    return str(uuid4())


class UserRole(str, Enum):
    USER = "user"
    ADMIN = "admin"


class MLTaskStatus(str, Enum):
    CREATED = "created"
    VALIDATING = "validating"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class PriorityClass(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class TransactionType(str, Enum):
    TOP_UP = "top_up"
    PREDICTION_CHARGE = "prediction_charge"


class TransactionStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    COMPLETED = "completed"


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_id)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[UserRole] = mapped_column(SqlEnum(UserRole), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

    balance: Mapped["Balance"] = relationship(
        back_populates="user",
        uselist=False,
        cascade="all, delete-orphan",
    )
    transactions: Mapped[list["Transaction"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )
    tasks: Mapped[list["MLTask"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )


class Balance(Base):
    __tablename__ = "balances"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), unique=True, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=Decimal("0.00"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)

    user: Mapped[User] = relationship(back_populates="balance")


class MLModel(Base):
    __tablename__ = "ml_models"
    __table_args__ = (UniqueConstraint("name", "version", name="uq_ml_models_name_version"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_id)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    version: Mapped[str] = mapped_column(String(50), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    cost_per_prediction: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

    tasks: Mapped[list["MLTask"]] = relationship(back_populates="model")


class MLTask(Base):
    __tablename__ = "ml_tasks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    model_id: Mapped[str] = mapped_column(ForeignKey("ml_models.id"), nullable=False)
    status: Mapped[MLTaskStatus] = mapped_column(SqlEnum(MLTaskStatus), nullable=False)
    input_payload: Mapped[list[dict[str, object]]] = mapped_column(JSON, nullable=False)
    spent_credits: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=Decimal("0.00"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped[User] = relationship(back_populates="tasks")
    model: Mapped[MLModel] = relationship(back_populates="tasks")
    result: Mapped["PredictionResult"] = relationship(
        back_populates="task",
        uselist=False,
        cascade="all, delete-orphan",
    )
    transactions: Mapped[list["Transaction"]] = relationship(back_populates="task")


class PredictionResult(Base):
    __tablename__ = "prediction_results"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_id)
    task_id: Mapped[str] = mapped_column(ForeignKey("ml_tasks.id"), unique=True, nullable=False)
    predicted_priority: Mapped[PriorityClass] = mapped_column(SqlEnum(PriorityClass), nullable=False)
    confidence: Mapped[float] = mapped_column(nullable=False)
    processed_count: Mapped[int] = mapped_column(Integer, nullable=False)
    rejected_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    spent_credits: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=Decimal("0.00"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

    task: Mapped[MLTask] = relationship(back_populates="result")


class Transaction(Base):
    __tablename__ = "transactions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    task_id: Mapped[str | None] = mapped_column(ForeignKey("ml_tasks.id"), nullable=True)
    type: Mapped[TransactionType] = mapped_column(SqlEnum(TransactionType), nullable=False)
    status: Mapped[TransactionStatus] = mapped_column(SqlEnum(TransactionStatus), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    review_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

    user: Mapped[User] = relationship(back_populates="transactions")
    task: Mapped[MLTask | None] = relationship(back_populates="transactions")
