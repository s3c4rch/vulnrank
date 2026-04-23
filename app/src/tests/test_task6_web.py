from decimal import Decimal

from ml_service.init_db import initialize_database
from ml_service.services import MLModelService, PredictionService, UserService


def auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def login(client, email: str, password: str) -> str:
    response = client.post("/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def register_user(client, email: str, password: str = "strong-pass-1") -> dict:
    response = client.post("/auth/register", json={"email": email, "password": password})
    assert response.status_code == 201, response.text
    return response.json()


def test_root_serves_web_interface(client):
    response = client.get("/")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "vulnrank Cabinet" in response.text
    assert "/assets/app.js" in response.text


def test_assets_are_served(client):
    response = client.get("/assets/app.js")

    assert response.status_code == 200
    assert "buildFeaturePayload" in response.text


def test_models_endpoint_returns_active_models_for_authenticated_user(client):
    payload = register_user(client, "models-ui@example.com")

    response = client.get("/models", headers=auth_header(payload["access_token"]))

    assert response.status_code == 200
    assert {item["name"] for item in response.json()["items"]} >= {"demo_model", "priority-classifier"}


def test_admin_endpoints_require_admin_role(client):
    payload = register_user(client, "plain-user@example.com")
    token = payload["access_token"]

    response = client.get("/admin/users", headers=auth_header(token))

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "admin_required"


def test_admin_can_list_users_top_up_and_view_transactions(client):
    admin_token = login(client, "demo-admin@example.com", "demo-admin-password")
    created_user = register_user(client, "managed-user@example.com")

    users_response = client.get("/admin/users", headers=auth_header(admin_token))
    assert users_response.status_code == 200
    assert any(item["email"] == "managed-user@example.com" for item in users_response.json()["items"])

    top_up_response = client.post(
        f"/admin/users/{created_user['user']['id']}/balance/top-up",
        headers=auth_header(admin_token),
        json={"amount": "15.00"},
    )
    assert top_up_response.status_code == 200
    assert Decimal(str(top_up_response.json()["balance"]["amount"])) == Decimal("15.00")

    transactions_response = client.get("/admin/history/transactions", headers=auth_header(admin_token))
    assert transactions_response.status_code == 200
    assert any(item["user_email"] == "managed-user@example.com" for item in transactions_response.json()["items"])


def test_admin_can_view_failed_prediction_tasks(client, session_factory):
    initialize_database(session_factory=session_factory)

    with session_factory() as session:
        user = UserService.create_user(
            session,
            email="failed-ui-user@example.com",
            password_hash="ui-password-hash",
            initial_balance=Decimal("5.00"),
        )
        model = MLModelService.get_active_model_by_name(session, "demo_model")
        PredictionService.create_queued_task(
            session,
            user_id=user.id,
            model_id=model.id,
            input_payload={
                "task_id": "failed-admin-task",
                "model": "demo_model",
                "features": {"x1": 1.0},
                "timestamp": "2026-01-01T12:00:00Z",
            },
            task_id="failed-admin-task",
        )
        PredictionService.fail_task(session, "failed-admin-task", "manual failure for admin view")

    admin_token = login(client, "demo-admin@example.com", "demo-admin-password")
    response = client.get(
        "/admin/history/predictions?failed_only=true",
        headers=auth_header(admin_token),
    )

    assert response.status_code == 200
    failed_items = response.json()["items"]
    assert any(item["task_id"] == "failed-admin-task" for item in failed_items)
    matched = next(item for item in failed_items if item["task_id"] == "failed-admin-task")
    assert matched["user_email"] == "failed-ui-user@example.com"
    assert matched["error_message"] == "manual failure for admin view"
