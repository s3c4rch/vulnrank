from __future__ import annotations

import json

from ml_service.model_catalog import DEFAULT_MODEL_NAME
from ml_service.model_runtime import ModelRuntimePrediction
from ml_service.models import PriorityClass
from ml_service.worker import process_delivery
from tests.test_task11_scan_upload import (
    auth_header,
    login_user,
    make_zip_file,
    register_user,
    request_and_approve_top_up,
    upload_json,
)


class StubRuntimeClient:
    def predict_priority(self, model_tag: str, features: dict) -> ModelRuntimePrediction:
        return ModelRuntimePrediction(
            predicted_priority=PriorityClass.HIGH,
            confidence=0.91,
            reason=f"stubbed report for {features.get('finding_type', 'finding')}",
        )


def _complete_uploaded_task(session_factory, published_messages) -> str:
    runtime_client = StubRuntimeClient()
    result = process_delivery(
        body=published_messages[0].model_dump_json().encode("utf-8"),
        session_factory=session_factory,
        worker_id="worker-report",
        runtime_client=runtime_client,
    )
    assert result["status"] == "success"
    return published_messages[0].task_id


def test_completed_single_file_task_renders_html_report(client, published_messages, session_factory):
    user_payload = register_user(client, "task14-single@example.com")
    token = user_payload["access_token"]
    request_and_approve_top_up(client, token, "10.00")

    response = upload_json(
        client,
        token,
        [
            {
                "scanner_name": "zap",
                "finding_type": "sql_injection",
                "severity_reported": "high",
                "cvss_score": 8.7,
                "asset_type": "web",
                "port": 443,
                "has_cve": True,
                "description_length": 180,
            },
            {"scanner_name": "zap", "severity_reported": "urgent"},
        ],
        filename="scan.json",
    )
    assert response.status_code == 202, response.text

    task_id = _complete_uploaded_task(session_factory, published_messages)
    report = client.get(f"/predict/{task_id}/report", headers=auth_header(token))

    assert report.status_code == 200
    assert "text/html" in report.headers["content-type"]
    assert "vulnrank MVP inference report" in report.text
    assert task_id in report.text
    assert "scan.json" in report.text
    assert "sql_injection" in report.text
    assert "severity_reported must be one of" in report.text
    assert DEFAULT_MODEL_NAME in report.text


def test_completed_archive_task_renders_html_with_file_breakdown(
    client,
    published_messages,
    session_factory,
):
    user_payload = register_user(client, "task14-archive@example.com")
    token = user_payload["access_token"]
    request_and_approve_top_up(client, token, "10.00")

    zip_payload = make_zip_file(
        {
            "semgrep.json": json.dumps(
                {
                    "results": [
                        {
                            "check_id": "semgrep.rule",
                            "path": "src/app.py",
                            "extra": {"severity": "ERROR", "message": "danger"},
                        }
                    ]
                }
            ).encode("utf-8"),
            "reports/nuclei.csv": (
                "scanner_name,finding_type,severity_reported,cvss_score,asset_type,port,has_cve,description_length\n"
                "nuclei,exposed_admin,critical,9.1,web,8443,true,120\n"
            ).encode("utf-8"),
            "notes.txt": b"ignore me",
        }
    )
    response = client.post(
        "/predict/upload",
        headers=auth_header(token),
        data={"model": DEFAULT_MODEL_NAME},
        files={"file": ("batch.zip", zip_payload, "application/zip")},
    )
    assert response.status_code == 202, response.text

    task_id = _complete_uploaded_task(session_factory, published_messages)
    report = client.get(f"/predict/{task_id}/report", headers=auth_header(token))

    assert report.status_code == 200
    assert "batch.zip" in report.text
    assert "File breakdown" in report.text
    assert "reports/nuclei.csv" in report.text
    assert "semgrep.json" in report.text
    assert "notes.txt" in report.text
    assert "Skipped files" in report.text


def test_processing_task_does_not_expose_report(client, published_messages):
    user_payload = register_user(client, "task14-processing@example.com")
    token = user_payload["access_token"]
    request_and_approve_top_up(client, token, "10.00")

    response = upload_json(
        client,
        token,
        [
            {
                "scanner_name": "zap",
                "finding_type": "xss",
                "severity_reported": "medium",
                "cvss_score": 6.1,
                "asset_type": "web",
                "port": 443,
                "has_cve": False,
                "description_length": 80,
            }
        ],
    )
    assert response.status_code == 202, response.text

    report = client.get(f"/predict/{response.json()['task_id']}/report", headers=auth_header(token))
    assert report.status_code == 409
    assert report.json()["error"]["code"] == "report_unavailable"
    assert published_messages


def test_report_access_control_blocks_other_user_and_allows_admin(
    client,
    published_messages,
    session_factory,
):
    owner_payload = register_user(client, "task14-owner@example.com")
    other_payload = register_user(client, "task14-other@example.com")
    request_and_approve_top_up(client, owner_payload["access_token"], "10.00")

    response = upload_json(
        client,
        owner_payload["access_token"],
        [
            {
                "scanner_name": "zap",
                "finding_type": "idor",
                "severity_reported": "high",
                "cvss_score": 7.3,
                "asset_type": "api",
                "port": 443,
                "has_cve": False,
                "description_length": 160,
            }
        ],
        filename="owner.json",
    )
    assert response.status_code == 202, response.text
    task_id = _complete_uploaded_task(session_factory, published_messages)

    forbidden = client.get(
        f"/predict/{task_id}/report",
        headers=auth_header(other_payload["access_token"]),
    )
    assert forbidden.status_code == 403

    admin_payload = login_user(client, "demo-admin@example.com", "demo-admin-password")
    admin_report = client.get(
        f"/predict/{task_id}/report",
        headers=auth_header(admin_payload["access_token"]),
    )
    assert admin_report.status_code == 200
    assert "owner.json" in admin_report.text


def test_download_endpoint_returns_attachment_header(client, published_messages, session_factory):
    user_payload = register_user(client, "task14-download@example.com")
    token = user_payload["access_token"]
    request_and_approve_top_up(client, token, "10.00")

    response = upload_json(
        client,
        token,
        [
            {
                "scanner_name": "zap",
                "finding_type": "csrf",
                "severity_reported": "medium",
                "cvss_score": 5.1,
                "asset_type": "web",
                "port": 443,
                "has_cve": False,
                "description_length": 70,
            }
        ],
        filename="download.json",
    )
    assert response.status_code == 202, response.text
    task_id = _complete_uploaded_task(session_factory, published_messages)

    report = client.get(f"/predict/{task_id}/report/download", headers=auth_header(token))
    assert report.status_code == 200
    assert "attachment;" in report.headers["content-disposition"]
    assert f"vulnrank-report-{task_id}.html" in report.headers["content-disposition"]
