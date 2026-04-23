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


def test_predict_returns_error_when_balance_is_insufficient(client):
    register_payload = register_user(client, email="no-balance@example.com")
    token = register_payload["access_token"]

    response = client.post(
        "/predict",
        headers=auth_header(token),
        json={
            "records": [
                {
                    "scanner_name": "demo-scanner",
                    "finding_type": "sql_injection",
                    "severity_reported": "high",
                    "cvss_score": 8.6,
                    "has_cve": True,
                }
            ]
        },
    )

    assert response.status_code == 402
    assert response.json()["error"]["code"] == "insufficient_balance"


def test_predict_supports_partial_validation_and_persists_history(client):
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
            "records": [
                {
                    "scanner_name": "scanner-a",
                    "finding_type": "xss",
                    "severity_reported": "medium",
                    "cvss_score": 6.2,
                    "has_cve": False,
                },
                {
                    "scanner_name": "scanner-b",
                    "severity_reported": "high",
                },
                {
                    "scanner_name": "scanner-c",
                    "finding_type": "rce",
                    "severity_reported": "critical",
                    "cvss_score": 9.8,
                    "has_cve": True,
                },
            ]
        },
    )

    assert predict_response.status_code == 200, predict_response.text
    predict_payload = predict_response.json()
    assert predict_payload["processed_count"] == 2
    assert predict_payload["rejected_count"] == 1
    assert len(predict_payload["processed_records"]) == 2
    assert len(predict_payload["invalid_records"]) == 1
    assert Decimal(str(predict_payload["spent_credits"])) == Decimal("6.00")

    prediction_history = client.get("/history/predictions", headers=auth_header(token))
    assert prediction_history.status_code == 200
    assert len(prediction_history.json()["items"]) == 1
    assert prediction_history.json()["items"][0]["predicted_priority"] == "high"

    transaction_history = client.get("/history/transactions", headers=auth_header(token))
    assert transaction_history.status_code == 200
    assert [item["type"] for item in transaction_history.json()["items"]] == [
        "prediction_charge",
        "top_up",
    ]


def test_validation_error_has_consistent_format(client):
    response = client.post(
        "/auth/register",
        json={"email": "bad-email", "password": "123"},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
