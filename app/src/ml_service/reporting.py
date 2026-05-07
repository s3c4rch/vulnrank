from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from html import escape
from pathlib import Path
from string import Template

from ml_service.models import MLTask, MLTaskStatus
from ml_service.serializers import serialize_prediction_task_detail


REPORT_TEMPLATE_PATH = Path(__file__).resolve().parent / "templates" / "prediction_report.html"
REPORT_TEMPLATE = Template(REPORT_TEMPLATE_PATH.read_text(encoding="utf-8"))


def build_prediction_task_report(task: MLTask) -> str:
    if task.status != MLTaskStatus.COMPLETED or task.result is None:
        raise ValueError("HTML report is available only for completed prediction tasks")

    detail = serialize_prediction_task_detail(task)
    original_filename = detail.original_filename or "direct prediction request"
    upload_kind = detail.upload_kind or "direct"
    processed_predictions = detail.processed_predictions or []
    invalid_records = detail.invalid_records or []
    source_files = detail.source_files or []
    invalid_files = detail.invalid_files or []

    return REPORT_TEMPLATE.substitute(
        report_title=escape(f"vulnrank report - {detail.task_id}"),
        task_id=escape(detail.task_id),
        report_kind=escape(_report_kind_label(upload_kind)),
        original_filename=escape(original_filename),
        created_at=escape(_format_timestamp(detail.created_at)),
        finished_at=escape(_format_timestamp(detail.finished_at)),
        model_name=escape(detail.model_name),
        model_version=escape(detail.model_version),
        model_badge=escape(f"{detail.model_name} {detail.model_version}"),
        summary_cards=_render_summary_cards(detail, original_filename, upload_kind),
        file_breakdown_section=_render_source_files_section(source_files),
        processed_predictions_section=_render_processed_predictions_section(processed_predictions),
        invalid_records_section=_render_invalid_records_section(invalid_records),
        invalid_files_section=_render_invalid_files_section(invalid_files),
        footer_note=escape(
            "MVP inference report generated on demand from stored task metadata and prediction results."
        ),
    )


def _render_summary_cards(detail, original_filename: str, upload_kind: str) -> str:
    cards = [
        ("Input", original_filename),
        ("Upload kind", upload_kind),
        ("Accepted", _format_optional_int(detail.accepted_count)),
        ("Processed", _format_optional_int(detail.processed_count)),
        ("Rejected", _format_optional_int(detail.rejected_count)),
        ("Prediction value", _format_optional_float(detail.prediction_value)),
        ("Priority", detail.predicted_priority or "-"),
        ("Confidence", _format_optional_float(detail.confidence, digits=2)),
        ("Spent credits", _format_decimal(detail.spent_credits)),
    ]
    return "".join(
        (
            '<div class="summary-card">'
            f"<span>{escape(label)}</span>"
            f"<strong>{escape(value)}</strong>"
            "</div>"
        )
        for label, value in cards
    )


def _render_source_files_section(source_files: list) -> str:
    if not source_files:
        return ""

    rows = "".join(
        (
            "<tr>"
            f"<td>{escape(item.filename)}</td>"
            f"<td>{escape(item.format)}</td>"
            f"<td>{escape(item.tool or '-')}</td>"
            f"<td>{item.accepted_count}</td>"
            f"<td>{item.rejected_count}</td>"
            f"<td>{escape(item.status)}</td>"
            "</tr>"
        )
        for item in source_files
    )
    return _wrap_section(
        "File breakdown",
        (
            "<div class=\"table-wrap\"><table><thead><tr>"
            "<th>File</th><th>Format</th><th>Tool</th><th>Accepted</th><th>Rejected</th><th>Status</th>"
            f"</tr></thead><tbody>{rows}</tbody></table></div>"
        ),
    )


def _render_processed_predictions_section(processed_predictions: list) -> str:
    if not processed_predictions:
        return _wrap_section(
            "Processed findings",
            '<p class="muted">This task does not include per-record prediction details.</p>',
        )

    rows = "".join(
        (
            "<tr>"
            f"<td>{item.record_index}</td>"
            f"<td>{escape(item.finding_type or '-')}</td>"
            f"<td>{escape(item.predicted_priority)}</td>"
            f"<td>{escape(_format_optional_float(item.confidence, digits=2))}</td>"
            f"<td>{escape(item.reason or '-')}</td>"
            "</tr>"
        )
        for item in processed_predictions
    )
    return _wrap_section(
        "Processed findings",
        (
            "<div class=\"table-wrap\"><table><thead><tr>"
            "<th>#</th><th>Finding type</th><th>Priority</th><th>Confidence</th><th>Reason</th>"
            f"</tr></thead><tbody>{rows}</tbody></table></div>"
        ),
    )


def _render_invalid_records_section(invalid_records: list) -> str:
    if not invalid_records:
        return _wrap_section(
            "Invalid records",
            '<p class="muted">No invalid records were captured for this task.</p>',
        )

    rows = "".join(
        (
            "<tr>"
            f"<td>{item.index}</td>"
            f"<td><code>{escape(str(item.record))}</code></td>"
            f"<td>{escape('; '.join(_error_message(error) for error in item.errors))}</td>"
            "</tr>"
        )
        for item in invalid_records
    )
    return _wrap_section(
        "Invalid records",
        (
            "<div class=\"table-wrap\"><table><thead><tr>"
            "<th>Index</th><th>Record</th><th>Errors</th>"
            f"</tr></thead><tbody>{rows}</tbody></table></div>"
        ),
    )


def _render_invalid_files_section(invalid_files: list) -> str:
    if not invalid_files:
        return ""

    rows = "".join(
        (
            "<tr>"
            f"<td>{escape(item.filename)}</td>"
            f"<td>{escape('; '.join(item.errors))}</td>"
            "</tr>"
        )
        for item in invalid_files
    )
    return _wrap_section(
        "Skipped files",
        (
            "<div class=\"table-wrap\"><table><thead><tr>"
            "<th>File</th><th>Reason</th>"
            f"</tr></thead><tbody>{rows}</tbody></table></div>"
        ),
    )


def _wrap_section(title: str, body: str) -> str:
    return (
        '<section class="report-section">'
        f"<h2>{escape(title)}</h2>"
        f"{body}"
        "</section>"
    )


def _format_timestamp(value: datetime | None) -> str:
    if value is None:
        return "-"
    return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _format_decimal(value: Decimal) -> str:
    return f"{Decimal(str(value)):.2f}"


def _format_optional_float(value: float | None, digits: int = 1) -> str:
    if value is None:
        return "-"
    return f"{float(value):.{digits}f}"


def _format_optional_int(value: int | None) -> str:
    return "-" if value is None else str(value)


def _report_kind_label(upload_kind: str) -> str:
    if upload_kind == "archive":
        return "Archive batch prediction"
    if upload_kind == "single_file":
        return "Single file batch prediction"
    return "Direct prediction"


def _error_message(error: dict) -> str:
    message = error.get("msg")
    return str(message) if message else str(error)
