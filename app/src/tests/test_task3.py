from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from ml_service.init_db import initialize_database
from ml_service.models import MLModel, MLTask, PriorityClass, Transaction, TransactionType, User, utcnow
from ml_service.services import (
    BalanceService,
    InsufficientBalanceError,
    MLModelService,
    PredictionService,
    TransactionService,
    UserService,
)


def test_create_user_loads_balance_and_history(session):
    user = UserService.create_user(
        session,
        email="alice@example.com",
        password_hash="hashed-password",
        initial_balance=Decimal("25.00"),
    )

    loaded_user = UserService.get_user(session, user.id)

    assert loaded_user.email == "alice@example.com"
    assert Decimal(str(loaded_user.balance.amount)) == Decimal("25.00")
    assert len(loaded_user.transactions) == 1
    assert loaded_user.transactions[0].type == TransactionType.TOP_UP


def test_balance_top_up_and_charge_are_persisted(session):
    user = UserService.create_user(
        session,
        email="billing@example.com",
        password_hash="billing-password",
    )

    BalanceService.top_up(session, user.id, Decimal("30.00"))
    BalanceService.charge(session, user.id, Decimal("12.50"))

    balance = BalanceService.get_balance(session, user.id)
    transactions = TransactionService.get_transaction_history(session, user.id)

    assert Decimal(str(balance.amount)) == Decimal("17.50")
    assert [transaction.type for transaction in transactions] == [
        TransactionType.PREDICTION_CHARGE,
        TransactionType.TOP_UP,
    ]


def test_charge_fails_when_balance_is_insufficient(session):
    user = UserService.create_user(
        session,
        email="poor@example.com",
        password_hash="poor-password",
    )

    with pytest.raises(InsufficientBalanceError):
        BalanceService.charge(session, user.id, Decimal("1.00"))


def test_prediction_history_is_sorted_and_contains_related_charge(session):
    user = UserService.create_user(
        session,
        email="predictor@example.com",
        password_hash="predictor-password",
        initial_balance=Decimal("50.00"),
    )
    model = MLModelService.create_model(
        session,
        name="priority-model",
        version="1.0",
        description="Test model",
        cost_per_prediction=Decimal("5.00"),
    )

    first_task = PredictionService.record_prediction(
        session,
        user_id=user.id,
        model_id=model.id,
        input_payload=[{"finding_type": "sql_injection"}],
        predicted_priority=PriorityClass.HIGH,
        confidence=0.92,
        processed_count=1,
    )
    first_task.created_at = utcnow() - timedelta(days=1)
    session.commit()

    second_task = PredictionService.record_prediction(
        session,
        user_id=user.id,
        model_id=model.id,
        input_payload=[{"finding_type": "xss"}],
        predicted_priority=PriorityClass.MEDIUM,
        confidence=0.87,
        processed_count=2,
    )

    history = PredictionService.get_prediction_history(session, user.id)

    assert [task.id for task in history] == [second_task.id, first_task.id]
    assert history[0].transactions[0].task_id == second_task.id
    assert Decimal(str(history[0].transactions[0].amount)) == Decimal("10.00")
    assert history[0].result.predicted_priority == PriorityClass.MEDIUM


def test_database_initialization_is_idempotent(session_factory):
    initialize_database(session_factory=session_factory)
    initialize_database(session_factory=session_factory)

    with session_factory() as session:
        user_count = session.scalar(select(func.count()).select_from(User))
        model_count = session.scalar(select(func.count()).select_from(MLModel))
        transaction_count = session.scalar(select(func.count()).select_from(Transaction))
        task_count = session.scalar(select(func.count()).select_from(MLTask))

    assert user_count == 2
    assert model_count == 3
    assert transaction_count == 2
    assert task_count == 0
