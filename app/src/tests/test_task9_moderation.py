from decimal import Decimal


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


def admin_token(client) -> str:
    return login_user(client, "demo-admin@example.com", "demo-admin-password")["access_token"]


def request_top_up(client, token: str, amount: str) -> dict:
    response = client.post(
        "/balance/top-up",
        headers=auth_header(token),
        json={"amount": amount},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_user_cannot_call_admin_top_up_endpoints(client):
    user_payload = register_user(client, "task9-rbac@example.com")
    token = user_payload["access_token"]

    pending_response = client.get("/admin/top-ups/pending", headers=auth_header(token))
    assert pending_response.status_code == 403
    assert pending_response.json()["error"]["code"] == "admin_required"

    approve_response = client.post(
        "/admin/top-ups/not-a-real-transaction/approve",
        headers=auth_header(token),
        json={"review_comment": "should not pass rbac"},
    )
    assert approve_response.status_code == 403
    assert approve_response.json()["error"]["code"] == "admin_required"


def test_user_top_up_creates_pending_transaction_without_changing_balance(client):
    user_payload = register_user(client, "task9-pending@example.com")
    token = user_payload["access_token"]

    response_payload = request_top_up(client, token, "40.00")

    assert Decimal(str(response_payload["balance"]["amount"])) == Decimal("0.00")
    assert response_payload["transaction"]["type"] == "top_up"
    assert response_payload["transaction"]["status"] == "pending"
    assert Decimal(str(response_payload["transaction"]["amount"])) == Decimal("40.00")

    balance_response = client.get("/balance", headers=auth_header(token))
    assert balance_response.status_code == 200
    assert Decimal(str(balance_response.json()["amount"])) == Decimal("0.00")

    history_response = client.get("/history/transactions", headers=auth_header(token))
    assert history_response.status_code == 200
    assert [item["id"] for item in history_response.json()["items"]] == [
        response_payload["transaction"]["id"]
    ]
    assert history_response.json()["items"][0]["status"] == "pending"


def test_admin_can_approve_pending_top_up_once(client):
    user_payload = register_user(client, "task9-approve@example.com")
    token = user_payload["access_token"]
    top_up_payload = request_top_up(client, token, "25.00")
    transaction_id = top_up_payload["transaction"]["id"]
    token_admin = admin_token(client)

    pending_response = client.get("/admin/top-ups/pending", headers=auth_header(token_admin))
    assert pending_response.status_code == 200
    assert transaction_id in {item["id"] for item in pending_response.json()["items"]}

    approve_response = client.post(
        f"/admin/top-ups/{transaction_id}/approve",
        headers=auth_header(token_admin),
        json={"review_comment": "approved by task9 test"},
    )
    assert approve_response.status_code == 200, approve_response.text
    assert approve_response.json()["transaction"]["status"] == "approved"
    assert Decimal(str(approve_response.json()["balance"]["amount"])) == Decimal("25.00")

    repeat_response = client.post(
        f"/admin/top-ups/{transaction_id}/approve",
        headers=auth_header(token_admin),
        json={"review_comment": "repeat approve must not double credit"},
    )
    assert repeat_response.status_code == 409
    assert repeat_response.json()["error"]["code"] == "invalid_transaction_state"

    balance_response = client.get("/balance", headers=auth_header(token))
    assert balance_response.status_code == 200
    assert Decimal(str(balance_response.json()["amount"])) == Decimal("25.00")


def test_admin_reject_keeps_balance_unchanged(client):
    user_payload = register_user(client, "task9-reject@example.com")
    token = user_payload["access_token"]
    top_up_payload = request_top_up(client, token, "12.00")
    transaction_id = top_up_payload["transaction"]["id"]
    token_admin = admin_token(client)

    reject_response = client.post(
        f"/admin/top-ups/{transaction_id}/reject",
        headers=auth_header(token_admin),
        json={"review_comment": "rejected by task9 test"},
    )
    assert reject_response.status_code == 200, reject_response.text
    assert reject_response.json()["transaction"]["status"] == "rejected"
    assert Decimal(str(reject_response.json()["balance"]["amount"])) == Decimal("0.00")

    repeat_response = client.post(
        f"/admin/top-ups/{transaction_id}/reject",
        headers=auth_header(token_admin),
        json={"review_comment": "repeat reject"},
    )
    assert repeat_response.status_code == 409

    balance_response = client.get("/balance", headers=auth_header(token))
    assert balance_response.status_code == 200
    assert Decimal(str(balance_response.json()["amount"])) == Decimal("0.00")


def test_users_see_only_own_top_ups_while_admin_sees_all(client):
    first_user = register_user(client, "task9-owner-a@example.com")
    second_user = register_user(client, "task9-owner-b@example.com")
    first_top_up = request_top_up(client, first_user["access_token"], "10.00")
    second_top_up = request_top_up(client, second_user["access_token"], "20.00")

    first_history = client.get(
        "/history/transactions",
        headers=auth_header(first_user["access_token"]),
    )
    assert first_history.status_code == 200
    first_ids = {item["id"] for item in first_history.json()["items"]}
    assert first_ids == {first_top_up["transaction"]["id"]}
    assert second_top_up["transaction"]["id"] not in first_ids

    token_admin = admin_token(client)
    pending_response = client.get("/admin/top-ups/pending", headers=auth_header(token_admin))
    assert pending_response.status_code == 200
    pending_ids = {item["id"] for item in pending_response.json()["items"]}
    assert {first_top_up["transaction"]["id"], second_top_up["transaction"]["id"]} <= pending_ids

    admin_transactions = client.get("/admin/history/transactions", headers=auth_header(token_admin))
    assert admin_transactions.status_code == 200
    admin_ids = {item["id"] for item in admin_transactions.json()["items"]}
    assert {first_top_up["transaction"]["id"], second_top_up["transaction"]["id"]} <= admin_ids


def test_task9_web_assets_include_role_aware_admin_dashboard(client):
    page_response = client.get("/")
    assert page_response.status_code == 200
    assert 'id="guest-home-nav"' in page_response.text
    assert 'id="guest-register-nav"' in page_response.text
    assert 'id="user-cabinet-nav"' in page_response.text
    assert 'id="hero" class="hero panel reveal"' in page_response.text
    assert 'id="register-page"' in page_response.text
    assert 'id="login-page"' in page_response.text
    assert 'id="workspace" class="workspace-grid" hidden' in page_response.text
    assert 'id="history" class="history-grid" hidden' in page_response.text
    assert 'id="admin-studio" class="admin-grid" hidden' in page_response.text
    assert 'id="admin-nav-button"' in page_response.text
    assert "Зарегистрироваться" in page_response.text
    assert "Войти" in page_response.text
    assert "Admin dashboard" in page_response.text
    assert "admin-pending-topups-body" in page_response.text

    script_response = client.get("/assets/app.js")
    assert script_response.status_code == 200
    assert "function showView" in script_response.text
    assert "const ROUTE_BY_VIEW" in script_response.text
    assert '"/cabinet"' in script_response.text
    assert '"/admin"' in script_response.text
    assert "/admin/top-ups/pending" in script_response.text
    assert "state.user.role === \"admin\"" in script_response.text
    assert 'setRoute("/login", { replace: true })' in script_response.text
    assert 'setRoute("/cabinet", { replace: true })' in script_response.text
    assert 'setRoute("/admin", { replace: true })' in script_response.text
    assert 'elements.landingPage.hidden = viewName !== "landing"' in script_response.text
    assert "elements.userCabinetNav.hidden = false" in script_response.text
    assert "elements.adminNavButton.hidden = false" in script_response.text

    styles_response = client.get("/assets/styles.css")
    assert styles_response.status_code == 200
    assert "[hidden]" in styles_response.text
    assert "display: none !important" in styles_response.text
