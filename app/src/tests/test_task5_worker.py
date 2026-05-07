from decimal import Decimal

from ml_service.init_db import initialize_database
from ml_service.model_catalog import DEFAULT_MODEL_NAME, LOCAL_DEMO_MODEL_NAME
from ml_service.model_runtime import ModelRuntimePrediction
from ml_service.models import PriorityClass
from ml_service.schemas import PredictionTaskMessage
from ml_service.services import (
    MLModelService,
    MLTaskStatus,
    PredictionService,
    TransactionService,
    UserService,
)
from ml_service.worker import process_delivery


class StubRuntimeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, float]]] = []

    def predict_priority(self, model_tag: str, features: dict[str, float]) -> ModelRuntimePrediction:
        self.calls.append((model_tag, features))
        return ModelRuntimePrediction(
            predicted_priority=PriorityClass.HIGH,
            confidence=0.91,
            reason="stubbed runtime response",
        )


def test_worker_processes_queued_task_and_charges_balance(session_factory):
    initialize_database(session_factory=session_factory)

    with session_factory() as session:
        user = UserService.create_user(
            session,
            email="worker-success@example.com",
            password_hash="worker-password",
            initial_balance=Decimal("10.00"),
        )
        model = MLModelService.get_active_model_by_name(session, DEFAULT_MODEL_NAME)
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

    runtime_client = StubRuntimeClient()
    result = process_delivery(
        body=message.model_dump_json().encode("utf-8"),
        session_factory=session_factory,
        worker_id="worker-1",
        runtime_client=runtime_client,
    )

    assert result["status"] == "success"
    assert result["worker_id"] == "worker-1"
    assert runtime_client.calls == [(DEFAULT_MODEL_NAME, {"x1": 6.0, "x2": 8.0})]

    with session_factory() as session:
        task = PredictionService.get_task(session, message.task_id)
        transactions = TransactionService.get_transaction_history(session, user.id)

    assert task.status == MLTaskStatus.COMPLETED
    assert task.result is not None
    assert task.result.worker_id == "worker-1"
    assert round(float(result["prediction"]), 2) == round(float(task.result.prediction_value), 2)
    assert task.result.predicted_priority.value == "high"
    assert Decimal(str(task.spent_credits)) == Decimal("2.50")
    assert [transaction.type.value for transaction in transactions] == [
        "prediction_charge",
        "top_up",
    ]


def test_completed_task_is_not_downgraded_or_charged_twice(session_factory):
    initialize_database(session_factory=session_factory)

    with session_factory() as session:
        user = UserService.create_user(
            session,
            email="worker-idempotent@example.com",
            password_hash="worker-password",
            initial_balance=Decimal("10.00"),
        )
        model = MLModelService.get_active_model_by_name(session, DEFAULT_MODEL_NAME)
        task = PredictionService.create_queued_task(
            session,
            user_id=user.id,
            model_id=model.id,
            input_payload={
                "task_id": "task-worker-idempotent",
                "model": model.name,
                "features": {"x1": 6.0, "x2": 8.0},
                "timestamp": "2026-01-01T12:00:00Z",
            },
            task_id="task-worker-idempotent",
        )
        PredictionService.complete_task(
            session,
            task_id=task.id,
            predicted_priority=PriorityClass.HIGH,
            confidence=0.9,
            worker_id="worker-1",
        )
        PredictionService.fail_task(session, task.id, "late duplicate failure")
        PredictionService.complete_task(
            session,
            task_id=task.id,
            predicted_priority=PriorityClass.LOW,
            confidence=0.1,
            worker_id="worker-2",
        )

    with session_factory() as session:
        task = PredictionService.get_task(session, "task-worker-idempotent")
        transactions = TransactionService.get_transaction_history(session, user.id)

    assert task.status == MLTaskStatus.COMPLETED
    assert task.error_message is None
    assert task.result is not None
    assert task.result.worker_id == "worker-1"
    assert task.result.predicted_priority == PriorityClass.HIGH
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
        model = MLModelService.get_active_model_by_name(session, DEFAULT_MODEL_NAME)
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
            b'{"task_id":"task-worker-fail","model":"gemma3:4b","features":{"x1":"bad-value"},'
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


def test_worker_keeps_local_demo_model_as_fallback(session_factory):
    initialize_database(session_factory=session_factory)

    with session_factory() as session:
        user = UserService.create_user(
            session,
            email="worker-local-demo@example.com",
            password_hash="worker-password",
            initial_balance=Decimal("10.00"),
        )
        model = MLModelService.get_model_by_name(session, LOCAL_DEMO_MODEL_NAME)
        message = PredictionTaskMessage(
            task_id="task-worker-local-demo",
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
        worker_id="worker-local",
    )

    assert result["status"] == "success"

    with session_factory() as session:
        task = PredictionService.get_task(session, message.task_id)

    assert task.status == MLTaskStatus.COMPLETED
    assert task.result is not None
    assert task.result.predicted_priority.value == "high"
