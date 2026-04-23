from decimal import Decimal

from ml_service.init_db import initialize_database
from ml_service.schemas import PredictionTaskMessage
from ml_service.services import (
    MLModelService,
    MLTaskStatus,
    PredictionService,
    TransactionService,
    UserService,
)
from ml_service.worker import process_delivery


def test_worker_processes_queued_task_and_charges_balance(session_factory):
    initialize_database(session_factory=session_factory)

    with session_factory() as session:
        user = UserService.create_user(
            session,
            email="worker-success@example.com",
            password_hash="worker-password",
            initial_balance=Decimal("10.00"),
        )
        model = MLModelService.get_active_model_by_name(session, "demo_model")
        message = PredictionTaskMessage(
            task_id="task-worker-success",
            model=model.name,
            features={"x1": 6.0, "x2": 8.0},
            timestamp="2026-01-01T12:00:00Z",
        )
        PredictionService.create_queued_task(
            session,
            user_id=user.id,
            model_id=model.id,
            input_payload=message.model_dump(mode="json"),
            task_id=message.task_id,
        )

    result = process_delivery(
        body=message.model_dump_json().encode("utf-8"),
        session_factory=session_factory,
        worker_id="worker-1",
    )

    assert result["status"] == "success"
    assert result["worker_id"] == "worker-1"
    assert result["prediction"] == 7.3

    with session_factory() as session:
        task = PredictionService.get_task(session, message.task_id)
        transactions = TransactionService.get_transaction_history(session, user.id)

    assert task.status == MLTaskStatus.COMPLETED
    assert task.result is not None
    assert task.result.worker_id == "worker-1"
    assert task.result.prediction_value == 7.3
    assert task.result.predicted_priority.value == "high"
    assert Decimal(str(task.spent_credits)) == Decimal("2.50")
    assert [transaction.type.value for transaction in transactions] == [
        "prediction_charge",
        "top_up",
    ]


def test_worker_marks_task_failed_when_message_is_invalid(session_factory):
    initialize_database(session_factory=session_factory)

    with session_factory() as session:
        user = UserService.create_user(
            session,
            email="worker-fail@example.com",
            password_hash="worker-password",
            initial_balance=Decimal("10.00"),
        )
        model = MLModelService.get_active_model_by_name(session, "demo_model")
        PredictionService.create_queued_task(
            session,
            user_id=user.id,
            model_id=model.id,
            input_payload={
                "task_id": "task-worker-fail",
                "model": model.name,
                "features": {"x1": "bad-value"},
                "timestamp": "2026-01-01T12:00:00Z",
            },
            task_id="task-worker-fail",
        )

    result = process_delivery(
        body=(
            b'{"task_id":"task-worker-fail","model":"demo_model","features":{"x1":"bad-value"},'
            b'"timestamp":"2026-01-01T12:00:00Z"}'
        ),
        session_factory=session_factory,
        worker_id="worker-2",
    )

    assert result["status"] == "failed"
    assert result["prediction"] is None

    with session_factory() as session:
        task = PredictionService.get_task(session, "task-worker-fail")
        transactions = TransactionService.get_transaction_history(session, user.id)

    assert task.status == MLTaskStatus.FAILED
    assert task.error_message is not None
    assert task.result is None
    assert [transaction.type.value for transaction in transactions] == ["top_up"]
