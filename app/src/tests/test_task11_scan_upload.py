from __future__ import annotations

import io
import json
from decimal import Decimal
import zipfile

from ml_service.model_catalog import DEFAULT_MODEL_NAME
from ml_service.model_runtime import ModelRuntimePrediction
from ml_service.models import PriorityClass
from ml_service.services import PredictionService, TransactionService
from ml_service.worker import process_delivery


class StubRuntimeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def predict_priority(self, model_tag: str, features: dict) -> ModelRuntimePrediction:
        self.calls.append((model_tag, features))
        return ModelRuntimePrediction(
            predicted_priority=PriorityClass.HIGH,
            confidence=0.93,
            reason=f"stubbed {features.get('finding_type', 'finding')}",
        )


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
        json={"review_comment": "approved in task11 test"},
    )
    assert approve_response.status_code == 200, approve_response.text


def upload_json(client, token: str, records: list[dict], filename: str = "scan.json"):
    return client.post(
        "/predict/upload",
        headers=auth_header(token),
        data={"model": DEFAULT_MODEL_NAME},
        files={
            "file": (
                filename,
                json.dumps(records).encode("utf-8"),
                "application/json",
            )
        },
    )


def make_zip_file(entries: dict[str, tuple[bytes, str] | bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for filename, payload in entries.items():
            if isinstance(payload, tuple):
                content, _content_type = payload
            else:
                content = payload
            archive.writestr(filename, content)
    return buffer.getvalue()


def valid_record(**overrides) -> dict:
    record = {
        "scanner_name": "zap",
        "finding_type": "sql_injection",
        "severity_reported": "high",
        "cvss_score": 8.7,
        "asset_type": "web",
        "port": 443,
        "has_cve": True,
        "description_length": 180,
    }
    record.update(overrides)
    return record


def test_json_upload_with_partial_invalid_records_processes_batch_and_charges(
    client,
    published_messages,
    session_factory,
):
    user_payload = register_user(client, "task11-json@example.com")
    token = user_payload["access_token"]
    request_and_approve_top_up(client, token, "10.00")

    response = upload_json(
        client,
        token,
        [
            valid_record(finding_type="sql_injection"),
            {"scanner_name": "zap", "severity_reported": "urgent"},
            valid_record(finding_type="xss", severity_reported="medium", has_cve=False),
        ],
    )

    assert response.status_code == 202, response.text
    payload = response.json()
    assert payload["accepted_count"] == 2
    assert payload["rejected_count"] == 1
    assert payload["invalid_records"][0]["index"] == 1
    assert len(published_messages) == 1
    assert published_messages[0].task_id == payload["task_id"]
    assert len(published_messages[0].records) == 2

    runtime_client = StubRuntimeClient()
    result = process_delivery(
        body=published_messages[0].model_dump_json().encode("utf-8"),
        session_factory=session_factory,
        worker_id="worker-batch",
        runtime_client=runtime_client,
    )

    assert result["status"] == "success"
    assert result["processed_count"] == 2
    assert result["rejected_count"] == 1
    assert len(runtime_client.calls) == 2

    detail = client.get(f"/predict/{payload['task_id']}", headers=auth_header(token))
    assert detail.status_code == 200
    detail_payload = detail.json()
    assert detail_payload["status"] == "completed"
    assert detail_payload["original_filename"] == "scan.json"
    assert detail_payload["accepted_count"] == 2
    assert detail_payload["processed_count"] == 2
    assert detail_payload["rejected_count"] == 1
    assert len(detail_payload["processed_predictions"]) == 2
    assert Decimal(str(detail_payload["spent_credits"])) == Decimal("5.00")

    balance = client.get("/balance", headers=auth_header(token))
    assert Decimal(str(balance.json()["amount"])) == Decimal("5.00")

    with session_factory() as session:
        transactions = TransactionService.get_transaction_history(session, user_payload["user"]["id"])
    assert [transaction.type.value for transaction in transactions] == [
        "prediction_charge",
        "top_up",
    ]


def test_csv_upload_with_valid_record_creates_batch_task(client, published_messages):
    user_payload = register_user(client, "task11-csv@example.com")
    token = user_payload["access_token"]
    request_and_approve_top_up(client, token, "5.00")

    csv_payload = (
        "scanner_name,finding_type,severity_reported,cvss_score,asset_type,port,has_cve,description_length\n"
        "nuclei,exposed_admin,critical,9.1,web,8443,true,120\n"
    )
    response = client.post(
        "/predict/upload",
        headers=auth_header(token),
        data={"model": DEFAULT_MODEL_NAME},
        files={"file": ("scan.csv", csv_payload.encode("utf-8"), "text/csv")},
    )

    assert response.status_code == 202, response.text
    assert response.json()["accepted_count"] == 1
    assert response.json()["rejected_count"] == 0
    assert len(published_messages) == 1
    assert published_messages[0].records[0]["scanner_name"] == "nuclei"
    assert published_messages[0].records[0]["has_cve"] is True


def test_zip_upload_aggregates_multiple_supported_files_and_tracks_file_breakdown(
    client,
    published_messages,
    session_factory,
):
    user_payload = register_user(client, "task13-archive@example.com")
    token = user_payload["access_token"]
    request_and_approve_top_up(client, token, "10.00")

    zip_payload = make_zip_file(
        {
            "semgrep.json": json.dumps({"results": [{"check_id": "semgrep.rule", "path": "src/app.py", "extra": {"severity": "ERROR", "message": "danger"}}]}).encode("utf-8"),
            "reports/nuclei.csv": (
                "scanner_name,finding_type,severity_reported,cvss_score,asset_type,port,has_cve,description_length\n"
                "nuclei,exposed_admin,critical,9.1,web,8443,true,120\n"
            ).encode("utf-8"),
            "notes.txt": b"ignore me",
            "../unsafe.json": json.dumps([valid_record(finding_type="unsafe")]).encode("utf-8"),
        }
    )

    response = client.post(
        "/predict/upload",
        headers=auth_header(token),
        data={"model": DEFAULT_MODEL_NAME},
        files={"file": ("batch.zip", zip_payload, "application/zip")},
    )

    assert response.status_code == 202, response.text
    payload = response.json()
    assert payload["upload_kind"] == "archive"
    assert payload["accepted_count"] == 2
    assert payload["rejected_count"] == 0
    assert [item["filename"] for item in payload["source_files"]] == ["semgrep.json", "reports/nuclei.csv"]
    assert payload["invalid_files"] == [
        {"filename": "notes.txt", "errors": ["Unsupported file inside ZIP. Only JSON and CSV are processed"]},
        {"filename": "../unsafe.json", "errors": ["ZIP entry path is unsafe and was skipped"]},
    ]

    assert len(published_messages) == 1
    assert len(published_messages[0].records) == 2
    assert {record["source_filename"] for record in published_messages[0].records} == {"semgrep.json", "reports/nuclei.csv"}
    assert {record["source_archive"] for record in published_messages[0].records} == {"batch.zip"}

    runtime_client = StubRuntimeClient()
    result = process_delivery(
        body=published_messages[0].model_dump_json().encode("utf-8"),
        session_factory=session_factory,
        worker_id="worker-archive",
        runtime_client=runtime_client,
    )

    assert result["status"] == "success"
    assert result["processed_count"] == 2

    detail = client.get(f"/predict/{payload['task_id']}", headers=auth_header(token))
    assert detail.status_code == 200
    detail_payload = detail.json()
    assert detail_payload["upload_kind"] == "archive"
    assert len(detail_payload["source_files"]) == 2
    assert len(detail_payload["invalid_files"]) == 2
    assert detail_payload["processed_count"] == 2

    history = client.get("/history/predictions", headers=auth_header(token))
    assert history.status_code == 200
    history_item = next(item for item in history.json()["items"] if item["task_id"] == payload["task_id"])
    assert history_item["upload_kind"] == "archive"
    assert len(history_item["source_files"]) == 2
    assert len(history_item["invalid_files"]) == 2


def test_zip_upload_without_supported_files_returns_400(client, published_messages):
    user_payload = register_user(client, "task13-empty-archive@example.com")
    token = user_payload["access_token"]

    zip_payload = make_zip_file(
        {
            "readme.txt": b"text only",
            "nested/image.png": b"png",
        }
    )

    response = client.post(
        "/predict/upload",
        headers=auth_header(token),
        data={"model": DEFAULT_MODEL_NAME},
        files={"file": ("unsupported.zip", zip_payload, "application/zip")},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_scan_upload"
    assert "does not contain supported JSON or CSV files" in response.json()["error"]["message"]
    assert published_messages == []


def test_semgrep_json_results_upload_normalizes_findings(client, published_messages):
    user_payload = register_user(client, "task11-semgrep@example.com")
    token = user_payload["access_token"]
    request_and_approve_top_up(client, token, "5.00")

    semgrep_payload = {
        "version": "1.0.0",
        "results": [
            {
                "check_id": "javascript.audit.detect-replaceall-sanitization",
                "path": "app/src/ml_service/web_static/app.js",
                "start": {"line": 884, "col": 10},
                "end": {"line": 888, "col": 30},
                "extra": {
                    "message": "Detected a manual HTML escaping pattern.",
                    "severity": "ERROR",
                    "metadata": {"cwe": ["CWE-116"], "cve": ["CVE-2026-0001"]},
                    "lines": "return String(value).replaceAll(...)",
                },
            }
        ],
    }

    response = client.post(
        "/predict/upload",
        headers=auth_header(token),
        data={"model": DEFAULT_MODEL_NAME},
        files={
            "file": (
                "semgrep.json",
                json.dumps(semgrep_payload).encode("utf-8"),
                "application/json",
            )
        },
    )

    assert response.status_code == 202, response.text
    assert response.json()["accepted_count"] == 1
    assert response.json()["rejected_count"] == 0
    assert len(published_messages) == 1
    record = published_messages[0].records[0]
    assert record["scanner_name"] == "semgrep"
    assert record["finding_type"] == "javascript.audit.detect-replaceall-sanitization"
    assert record["severity_reported"] == "high"
    assert record["asset_type"] == "source_code"
    assert record["has_cve"] is True
    assert record["semgrep_path"] == "app/src/ml_service/web_static/app.js"


def test_semgrep_terminal_report_gets_specific_json_error(client, published_messages):
    user_payload = register_user(client, "task11-semgrep-text@example.com")
    token = user_payload["access_token"]

    semgrep_text_report = (
        "┌─────────────────┐\n"
        "│ 4 Code Findings │\n"
        "└─────────────────┘\n"
        "app/src/ml_service/web_static/app.js\n"
    )

    response = client.post(
        "/predict/upload",
        headers=auth_header(token),
        data={"model": DEFAULT_MODEL_NAME},
        files={
            "file": (
                "semgrep.json",
                semgrep_text_report.encode("utf-8"),
                "application/json",
            )
        },
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_scan_upload"
    assert "Semgrep terminal output" in response.json()["error"]["message"]
    assert published_messages == []


def test_all_invalid_upload_creates_failed_task_without_charge(client, published_messages):
    user_payload = register_user(client, "task11-invalid@example.com")
    token = user_payload["access_token"]

    response = upload_json(
        client,
        token,
        [{"scanner_name": "zap", "finding_type": "", "severity_reported": "urgent"}],
        filename="bad-scan.json",
    )

    assert response.status_code == 202, response.text
    payload = response.json()
    assert payload["status"] == "failed"
    assert payload["accepted_count"] == 0
    assert payload["rejected_count"] == 1
    assert published_messages == []

    detail = client.get(f"/predict/{payload['task_id']}", headers=auth_header(token))
    assert detail.status_code == 200
    assert detail.json()["status"] == "failed"
    assert detail.json()["rejected_count"] == 1
    assert Decimal(str(detail.json()["spent_credits"])) == Decimal("0.00")

    balance = client.get("/balance", headers=auth_header(token))
    assert Decimal(str(balance.json()["amount"])) == Decimal("0.00")


def test_upload_balance_check_uses_accepted_record_count(client, published_messages):
    user_payload = register_user(client, "task11-balance@example.com")
    token = user_payload["access_token"]
    request_and_approve_top_up(client, token, "2.50")

    response = upload_json(
        client,
        token,
        [valid_record(finding_type="xss"), valid_record(finding_type="ssrf")],
    )

    assert response.status_code == 402
    assert response.json()["error"]["code"] == "insufficient_balance"
    assert published_messages == []


def test_user_cannot_view_another_users_upload_detail_and_admin_sees_failed_uploads(
    client,
):
    owner_payload = register_user(client, "task11-owner@example.com")
    other_payload = register_user(client, "task11-other@example.com")

    response = upload_json(
        client,
        owner_payload["access_token"],
        [{"scanner_name": "zap", "finding_type": "", "severity_reported": "urgent"}],
        filename="owner-bad-scan.json",
    )
    assert response.status_code == 202, response.text
    task_id = response.json()["task_id"]

    forbidden = client.get(
        f"/predict/{task_id}",
        headers=auth_header(other_payload["access_token"]),
    )
    assert forbidden.status_code == 403

    admin_payload = login_user(client, "demo-admin@example.com", "demo-admin-password")
    admin_history = client.get(
        "/admin/history/predictions?failed_only=true",
        headers=auth_header(admin_payload["access_token"]),
    )

    assert admin_history.status_code == 200
    failed_upload = next(item for item in admin_history.json()["items"] if item["task_id"] == task_id)
    assert failed_upload["user_email"] == "task11-owner@example.com"
    assert failed_upload["original_filename"] == "owner-bad-scan.json"
    assert failed_upload["rejected_count"] == 1
