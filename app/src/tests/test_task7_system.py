from decimal import Decimal

from ml_service.worker import process_delivery


def auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def register_user(client, email: str, password: str = "strong-pass-1") -> dict:
    response = client.post(
        "/auth/register",
        json={"email": email, "password": password},
    )
    assert response.status_code == 201, response.text
    return response.json()


def login_user(client, email: str, password: str = "strong-pass-1") -> dict:
    response = client.post(
        "/auth/login",
        json={"email": email, "password": password},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_authentication_flow_supports_repeated_login_and_invalid_credentials(client):
    register_payload = register_user(client, "task7-auth@example.com")

    second_login = login_user(client, "task7-auth@example.com")
    assert second_login["token_type"] == "bearer"
    assert second_login["user"]["id"] == register_payload["user"]["id"]
    assert second_login["access_token"] != register_payload["access_token"]

    invalid_login = client.post(
        "/auth/login",
        json={"email": "task7-auth@example.com", "password": "wrong-pass-1"},
    )
    assert invalid_login.status_code == 401
    assert invalid_login.json()["error"]["code"] == "authentication_failed"


def test_system_updates_balance_and_history_after_successful_prediction(
    client,
    published_messages,
    session_factory,
):
    register_payload = register_user(client, "task7-success@example.com")
    token = register_payload["access_token"]

    initial_balance = client.get("/balance", headers=auth_header(token))
    assert initial_balance.status_code == 200
    assert Decimal(str(initial_balance.json()["amount"])) == Decimal("0.00")

    top_up_response = client.post(
        "/balance/top-up",
        headers=auth_header(token),
        json={"amount": "10.00"},
    )
    assert top_up_response.status_code == 200
    assert Decimal(str(top_up_response.json()["balance"]["amount"])) == Decimal("10.00")

    predict_response = client.post(
        "/predict",
        headers=auth_header(token),
        json={
            "model": "demo_model",
            "features": {
                "x1": 7.8,
                "x2": 8.3,
            },
        },
    )
    assert predict_response.status_code == 202, predict_response.text
    task_id = predict_response.json()["task_id"]

    process_delivery(
        body=published_messages[0].model_dump_json().encode("utf-8"),
        session_factory=session_factory,
        worker_id="worker-1",
    )

    task_response = client.get(f"/predict/{task_id}", headers=auth_header(token))
    assert task_response.status_code == 200
    assert task_response.json()["status"] == "completed"
    assert task_response.json()["worker_id"] == "worker-1"
    assert task_response.json()["prediction_value"] is not None

    balance_response = client.get("/balance", headers=auth_header(token))
    assert balance_response.status_code == 200
    assert Decimal(str(balance_response.json()["amount"])) == Decimal("7.50")

    prediction_history = client.get("/history/predictions", headers=auth_header(token))
    assert prediction_history.status_code == 200
    assert prediction_history.json()["items"][0]["task_id"] == task_id
    assert prediction_history.json()["items"][0]["status"] == "completed"
    assert Decimal(str(prediction_history.json()["items"][0]["spent_credits"])) == Decimal("2.50")

    transaction_history = client.get("/history/transactions", headers=auth_header(token))
    assert transaction_history.status_code == 200
    transaction_types = [item["type"] for item in transaction_history.json()["items"]]
    assert transaction_types == ["prediction_charge", "top_up"]


def test_system_blocks_insufficient_balance_and_skips_charge_on_worker_failure(
    client,
    published_messages,
    session_factory,
):
    register_payload = register_user(client, "task7-failure@example.com")
    token = register_payload["access_token"]

    insufficient_balance = client.post(
        "/predict",
        headers=auth_header(token),
        json={
            "model": "demo_model",
            "features": {
                "x1": 5.0,
                "x2": 6.0,
            },
        },
    )
    assert insufficient_balance.status_code == 402
    assert insufficient_balance.json()["error"]["code"] == "insufficient_balance"

    top_up_response = client.post(
        "/balance/top-up",
        headers=auth_header(token),
        json={"amount": "10.00"},
    )
    assert top_up_response.status_code == 200

    predict_response = client.post(
        "/predict",
        headers=auth_header(token),
        json={
            "model": "demo_model",
            "features": {
                "x1": 4.5,
            },
        },
    )
    assert predict_response.status_code == 202, predict_response.text
    task_id = predict_response.json()["task_id"]

    process_delivery(
        body=published_messages[0].model_dump_json().encode("utf-8"),
        session_factory=session_factory,
        worker_id="worker-2",
    )

    task_response = client.get(f"/predict/{task_id}", headers=auth_header(token))
    assert task_response.status_code == 200
    assert task_response.json()["status"] == "failed"
    assert task_response.json()["error_message"] is not None
    assert Decimal(str(task_response.json()["spent_credits"])) == Decimal("0.00")

    balance_response = client.get("/balance", headers=auth_header(token))
    assert balance_response.status_code == 200
    assert Decimal(str(balance_response.json()["amount"])) == Decimal("10.00")

    transaction_history = client.get("/history/transactions", headers=auth_header(token))
    assert transaction_history.status_code == 200
    assert [item["type"] for item in transaction_history.json()["items"]] == ["top_up"]
