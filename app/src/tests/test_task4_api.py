from decimal import Decimal


def auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def register_user(client, email: str = "api-user@example.com", password: str = "strong-pass-1"):
    response = client.post(
        "/auth/register",
        json={"email": email, "password": password},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_register_and_login_flow(client):
    register_payload = register_user(client)
    token = register_payload["access_token"]

    me_response = client.get("/users/me", headers=auth_header(token))
    assert me_response.status_code == 200
    assert me_response.json()["email"] == "api-user@example.com"

    login_response = client.post(
        "/auth/login",
        json={"email": "api-user@example.com", "password": "strong-pass-1"},
    )
    assert login_response.status_code == 200
    assert login_response.json()["token_type"] == "bearer"
    assert login_response.json()["user"]["id"] == register_payload["user"]["id"]


def test_balance_endpoint_and_top_up(client):
    register_payload = register_user(client, email="billing-api@example.com")
    token = register_payload["access_token"]

    balance_response = client.get("/balance", headers=auth_header(token))
    assert balance_response.status_code == 200
    assert Decimal(str(balance_response.json()["amount"])) == Decimal("0.00")

    top_up_response = client.post(
        "/balance/top-up",
        headers=auth_header(token),
        json={"amount": "25.50"},
    )
    assert top_up_response.status_code == 200
    assert Decimal(str(top_up_response.json()["balance"]["amount"])) == Decimal("25.50")
    assert top_up_response.json()["transaction"]["type"] == "top_up"


def test_predict_enqueues_task_and_returns_task_id(client, published_messages):
    register_payload = register_user(client, email="predict-api@example.com")
    token = register_payload["access_token"]

    top_up_response = client.post(
        "/balance/top-up",
        headers=auth_header(token),
        json={"amount": "20.00"},
    )
    assert top_up_response.status_code == 200

    predict_response = client.post(
        "/predict",
        headers=auth_header(token),
        json={
            "model": "demo_model",
            "features": {
                "x1": 1.2,
                "x2": 5.7,
            },
        },
    )

    assert predict_response.status_code == 202, predict_response.text
    predict_payload = predict_response.json()
    assert predict_payload["status"] == "created"
    assert predict_payload["model"] == "demo_model"
    assert len(published_messages) == 1
    assert published_messages[0].task_id == predict_payload["task_id"]
    assert published_messages[0].features == {"x1": 1.2, "x2": 5.7}

    prediction_history = client.get("/history/predictions", headers=auth_header(token))
    assert prediction_history.status_code == 200
    assert len(prediction_history.json()["items"]) == 1
    assert prediction_history.json()["items"][0]["task_id"] == predict_payload["task_id"]
    assert prediction_history.json()["items"][0]["status"] == "created"
    assert prediction_history.json()["items"][0]["prediction_value"] is None


def test_predict_returns_error_when_balance_is_insufficient(client):
    register_payload = register_user(client, email="no-balance@example.com")
    token = register_payload["access_token"]

    response = client.post(
        "/predict",
        headers=auth_header(token),
        json={
            "model": "demo_model",
            "features": {
                "x1": 8.6,
                "x2": 5.1,
            },
        },
    )

    assert response.status_code == 402
    assert response.json()["error"]["code"] == "insufficient_balance"


def test_validation_error_has_consistent_format(client):
    register_payload = register_user(client, email="validation-api@example.com")
    token = register_payload["access_token"]

    response = client.post(
        "/predict",
        headers=auth_header(token),
        json={"model": "", "features": {}},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
