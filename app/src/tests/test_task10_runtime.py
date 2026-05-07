from decimal import Decimal

import pytest

from ml_service.model_catalog import (
    DEFAULT_MODEL_NAME,
    LOCAL_DEMO_MODEL_NAME,
    OPENAI_DEFAULT_MODEL_NAME,
    OPENAI_PROVIDER_MODEL_NAME,
)
from ml_service.model_runtime import (
    ModelRuntimeError,
    ModelRuntimePrediction,
    build_runtime_http_error_message,
    parse_prediction_content,
)
from ml_service.models import MLTaskStatus, PriorityClass
from ml_service.services import PredictionService, TransactionService
from ml_service.worker import process_delivery


class StubRuntimeClient:
    def __init__(
        self,
        prediction: ModelRuntimePrediction | None = None,
        error: Exception | None = None,
    ) -> None:
        self.prediction = prediction or ModelRuntimePrediction(
            predicted_priority=PriorityClass.HIGH,
            confidence=0.92,
            reason="stubbed runtime response",
        )
        self.error = error
        self.calls: list[tuple[str, dict[str, float]]] = []

    def predict_priority(self, model_tag: str, features: dict[str, float]) -> ModelRuntimePrediction:
        self.calls.append((model_tag, features))
        if self.error is not None:
            raise self.error
        return self.prediction

    def list_models(self) -> set[str]:
        return {call[0] for call in self.calls}

    def pull_model(self, model_tag: str) -> None:
        self.calls.append((model_tag, {}))


def auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def register_user(client, email: str, password: str = "strong-pass-1") -> dict:
    response = client.post("/auth/register", json={"email": email, "password": password})
    assert response.status_code == 201, response.text
    return response.json()


def login_user(client, email: str, password: str) -> dict:
    response = client.post("/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return response.json()


def request_and_approve_top_up(client, user_token: str, amount: str = "10.00") -> None:
    top_up_response = client.post(
        "/balance/top-up",
        headers=auth_header(user_token),
        json={"amount": amount},
    )
    assert top_up_response.status_code == 200, top_up_response.text

    admin_payload = login_user(client, "demo-admin@example.com", "demo-admin-password")
    approve_response = client.post(
        f"/admin/top-ups/{top_up_response.json()['transaction']['id']}/approve",
        headers=auth_header(admin_payload["access_token"]),
        json={"review_comment": "approved in task10 test"},
    )
    assert approve_response.status_code == 200, approve_response.text


def test_models_endpoint_returns_active_local_model_only(client):
    user_payload = register_user(client, "task10-models@example.com")

    response = client.get("/models", headers=auth_header(user_payload["access_token"]))

    assert response.status_code == 200
    model_names = {item["name"] for item in response.json()["items"]}
    assert model_names == {DEFAULT_MODEL_NAME}
    assert LOCAL_DEMO_MODEL_NAME not in model_names


def test_admin_can_pull_and_activate_single_local_model(client):
    admin_payload = login_user(client, "demo-admin@example.com", "demo-admin-password")
    runtime_client = StubRuntimeClient()
    client.app.state.model_runtime_client = runtime_client

    response = client.post(
        "/admin/models/local/activate",
        headers=auth_header(admin_payload["access_token"]),
        json={"model": "qwen3:4b"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["model"]["name"] == "qwen3:4b"
    assert response.json()["model"]["is_active"] is True
    assert runtime_client.calls == [("qwen3:4b", {})]

    user_payload = register_user(client, "task10-active-local@example.com")
    models_response = client.get("/models", headers=auth_header(user_payload["access_token"]))
    assert models_response.status_code == 200
    assert {item["name"] for item in models_response.json()["items"]} == {"qwen3:4b"}


def test_predict_accepts_selected_ollama_model_and_rejects_inactive_model(client, published_messages):
    user_payload = register_user(client, "task10-predict@example.com")
    token = user_payload["access_token"]
    request_and_approve_top_up(client, token, "10.00")

    response = client.post(
        "/predict",
        headers=auth_header(token),
        json={"model": DEFAULT_MODEL_NAME, "features": {"x1": 7.8, "x2": 8.3}},
    )

    assert response.status_code == 202, response.text
    assert response.json()["model"] == DEFAULT_MODEL_NAME
    assert published_messages[0].model == DEFAULT_MODEL_NAME

    inactive_response = client.post(
        "/predict",
        headers=auth_header(token),
        json={"model": LOCAL_DEMO_MODEL_NAME, "features": {"x1": 7.8, "x2": 8.3}},
    )

    assert inactive_response.status_code == 404
    assert inactive_response.json()["error"]["code"] == "entity_not_found"


def test_user_can_configure_openai_credentials_and_enqueue_chatgpt_task(
    client,
    published_messages,
    session_factory,
):
    user_payload = register_user(client, "task10-openai@example.com")
    token = user_payload["access_token"]

    initial_models_response = client.get("/models", headers=auth_header(token))
    assert initial_models_response.status_code == 200
    assert OPENAI_PROVIDER_MODEL_NAME not in {
        item["name"] for item in initial_models_response.json()["items"]
    }

    credential_response = client.put(
        "/external-credentials/openai",
        headers=auth_header(token),
        json={"api_key": "sk-test-openai-key", "model_name": OPENAI_DEFAULT_MODEL_NAME},
    )
    assert credential_response.status_code == 200, credential_response.text
    assert credential_response.json()["is_configured"] is True
    assert credential_response.json()["model_name"] == OPENAI_DEFAULT_MODEL_NAME
    assert "sk-test-openai-key" not in credential_response.text

    models_response = client.get("/models", headers=auth_header(token))
    assert models_response.status_code == 200
    assert OPENAI_PROVIDER_MODEL_NAME in {item["name"] for item in models_response.json()["items"]}

    predict_response = client.post(
        "/predict",
        headers=auth_header(token),
        json={
            "model": OPENAI_PROVIDER_MODEL_NAME,
            "features": {"x1": 7.8, "x2": 8.3},
        },
    )
    assert predict_response.status_code == 202, predict_response.text

    runtime_client = StubRuntimeClient()
    result = process_delivery(
        body=published_messages[0].model_dump_json().encode("utf-8"),
        session_factory=session_factory,
        worker_id="worker-openai",
        runtime_client=runtime_client,
    )

    assert result["status"] == "success"
    assert runtime_client.calls == [(OPENAI_DEFAULT_MODEL_NAME, {"x1": 7.8, "x2": 8.3})]

    with session_factory() as session:
        task = PredictionService.get_task(session, predict_response.json()["task_id"])
        transactions = TransactionService.get_transaction_history(session, user_payload["user"]["id"])

    assert task.status == MLTaskStatus.COMPLETED
    assert Decimal(str(task.spent_credits)) == Decimal("0.00")
    assert transactions == []


def test_worker_runtime_error_fails_task_without_charging(client, published_messages, session_factory):
    user_payload = register_user(client, "task10-runtime-fail@example.com")
    token = user_payload["access_token"]
    request_and_approve_top_up(client, token, "10.00")

    predict_response = client.post(
        "/predict",
        headers=auth_header(token),
        json={"model": DEFAULT_MODEL_NAME, "features": {"x1": 7.8, "x2": 8.3}},
    )
    assert predict_response.status_code == 202, predict_response.text
    task_id = predict_response.json()["task_id"]

    result = process_delivery(
        body=published_messages[0].model_dump_json().encode("utf-8"),
        session_factory=session_factory,
        worker_id="worker-runtime-fail",
        runtime_client=StubRuntimeClient(error=ModelRuntimeError("invalid JSON from runtime")),
    )

    assert result["status"] == "failed"

    with session_factory() as session:
        task = PredictionService.get_task(session, task_id)
        transactions = TransactionService.get_transaction_history(session, user_payload["user"]["id"])

    assert task.status == MLTaskStatus.FAILED
    assert task.result is None
    assert Decimal(str(task.spent_credits)) == Decimal("0.00")
    assert [transaction.type.value for transaction in transactions] == ["top_up"]


def test_runtime_json_parser_is_strict():
    parsed = parse_prediction_content(
        '{"predicted_priority":"high","confidence":0.88,"reason":"Internet-facing critical asset."}'
    )

    assert parsed.predicted_priority == PriorityClass.HIGH
    assert parsed.confidence == 0.88
    assert parsed.reason == "Internet-facing critical asset."

    with pytest.raises(ModelRuntimeError):
        parse_prediction_content('{"predicted_priority":"urgent","confidence":0.88}')

    with pytest.raises(ModelRuntimeError):
        parse_prediction_content('{"predicted_priority":"high","confidence":1.7}')

    with pytest.raises(ModelRuntimeError):
        parse_prediction_content("not-json")


def test_runtime_http_error_message_explains_missing_ollama_model():
    message = build_runtime_http_error_message(
        status_code=404,
        response_text='{"error":"model \\"gemma3:4b\\" not found, try pulling it first"}',
        model_tag="gemma3:4b",
    )

    assert "ml-runtime model 'gemma3:4b' is not available" in message
    assert "ollama pull gemma3:4b" in message


def test_runtime_http_error_message_includes_non_404_detail():
    message = build_runtime_http_error_message(
        status_code=500,
        response_text='{"error":"runtime exploded noisily"}',
        model_tag="gemma3:4b",
    )

    assert message == "ml-runtime returned HTTP 500: runtime exploded noisily"
