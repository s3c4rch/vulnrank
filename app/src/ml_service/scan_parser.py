from __future__ import annotations

import csv
from dataclasses import dataclass
import io
import json
from pathlib import PurePath
from typing import Any
import zipfile

from pydantic import ValidationError

from ml_service.schemas import FindingRecordInput, InvalidFileView, InvalidRecordView, SourceFileSummaryView


class ScanUploadParseError(ValueError):
    """Raised when an uploaded scan file cannot be parsed as an MVP format."""


@dataclass(frozen=True)
class ParsedScanUpload:
    upload_kind: str
    accepted_records: list[dict[str, Any]]
    invalid_records: list[InvalidRecordView]
    source_files: list[SourceFileSummaryView]
    invalid_files: list[InvalidFileView]

    @property
    def accepted_count(self) -> int:
        return len(self.accepted_records)

    @property
    def rejected_count(self) -> int:
        return len(self.invalid_records)


def parse_scan_upload(
    *,
    filename: str | None,
    content_type: str | None,
    content: bytes,
) -> ParsedScanUpload:
    if not content:
        raise ScanUploadParseError("Uploaded scan file is empty")

    file_format = _detect_format(filename, content_type)
    if file_format == "zip":
        return _parse_zip_upload(filename=filename, content=content)
    if file_format not in {"json", "csv"}:
        raise ScanUploadParseError("Unsupported scan file format. Use JSON, CSV or ZIP")

    records = _load_records(filename=filename, content_type=content_type, content=content)
    source_filename = PurePath(filename or "upload").name or "upload"
    source_tool = _infer_source_tool(records, fallback_format=file_format)
    accepted_records: list[dict[str, Any]] = []
    invalid_records: list[InvalidRecordView] = []

    for index, raw_record in enumerate(records):
        if not isinstance(raw_record, dict):
            invalid_records.append(
                InvalidRecordView(
                    index=index,
                    record={"value": raw_record},
                    errors=[
                        {
                            "loc": ["record"],
                            "msg": "record must be a JSON object or CSV row",
                            "type": "record_type",
                        }
                    ],
                )
            )
            continue

        normalized_candidate = _normalize_empty_values(raw_record)
        try:
            finding = FindingRecordInput.model_validate(normalized_candidate)
        except ValidationError as exc:
            invalid_records.append(
                InvalidRecordView(
                    index=index,
                    record=dict(raw_record),
                    errors=_serialize_validation_errors(exc),
                )
            )
            continue

        normalized_record = {
            **_json_safe_record(raw_record),
            **finding.model_dump(),
            "record_index": index,
            "source_filename": source_filename,
            "source_format": file_format,
            "source_archive": None,
            "source_tool": source_tool,
        }
        accepted_records.append(normalized_record)

    return ParsedScanUpload(
        upload_kind="single_file",
        accepted_records=accepted_records,
        invalid_records=invalid_records,
        source_files=[
            SourceFileSummaryView(
                filename=source_filename,
                format=file_format,
                accepted_count=len(accepted_records),
                rejected_count=len(invalid_records),
                tool=source_tool,
                status=_file_status(len(accepted_records), len(invalid_records)),
            )
        ],
        invalid_files=[],
    )


def _load_records(
    *,
    filename: str | None,
    content_type: str | None,
    content: bytes,
) -> list[Any]:
    file_format = _detect_format(filename, content_type)
    if file_format == "json":
        return _load_json_records(content)
    if file_format == "csv":
        return _load_csv_records(content)
    raise ScanUploadParseError("Unsupported scan file format. Use JSON, CSV or ZIP")


def _detect_format(filename: str | None, content_type: str | None) -> str:
    suffix = PurePath(filename or "").suffix.lower()
    normalized_content_type = (content_type or "").split(";")[0].strip().lower()

    if suffix == ".json" or normalized_content_type in {"application/json", "text/json"}:
        return "json"
    if suffix == ".csv" or normalized_content_type in {"text/csv", "application/csv"}:
        return "csv"
    if suffix == ".zip" or normalized_content_type in {"application/zip", "application/x-zip-compressed"}:
        return "zip"
    return ""


def _parse_zip_upload(
    *,
    filename: str | None,
    content: bytes,
) -> ParsedScanUpload:
    archive_name = PurePath(filename or "upload.zip").name or "upload.zip"
    try:
        archive = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile as exc:
        raise ScanUploadParseError("Uploaded ZIP archive is invalid or corrupted") from exc

    entries = [entry for entry in archive.infolist() if not entry.is_dir()]
    if not entries:
        raise ScanUploadParseError("Uploaded ZIP archive is empty")

    max_files = 25
    max_total_size = 10 * 1024 * 1024
    if len(entries) > max_files:
        raise ScanUploadParseError(f"ZIP archive contains too many files. Limit is {max_files}")

    total_uncompressed_size = 0
    accepted_records: list[dict[str, Any]] = []
    invalid_records: list[InvalidRecordView] = []
    source_files: list[SourceFileSummaryView] = []
    invalid_files: list[InvalidFileView] = []
    global_record_index = 0

    for entry in entries:
        member_name = entry.filename
        safe_name = _safe_member_name(member_name)
        if safe_name is None:
            invalid_files.append(
                InvalidFileView(
                    filename=member_name,
                    errors=["ZIP entry path is unsafe and was skipped"],
                )
            )
            continue

        total_uncompressed_size += int(entry.file_size)
        if total_uncompressed_size > max_total_size:
            raise ScanUploadParseError(
                "ZIP archive is too large after extraction. Limit is 10485760 bytes"
            )

        file_format = _detect_format(safe_name, None)
        if file_format not in {"json", "csv"}:
            invalid_files.append(
                InvalidFileView(
                    filename=safe_name,
                    errors=["Unsupported file inside ZIP. Only JSON and CSV are processed"],
                )
            )
            continue

        try:
            file_bytes = archive.read(entry)
            records = _load_records(filename=safe_name, content_type=None, content=file_bytes)
        except (KeyError, RuntimeError, ValueError, ScanUploadParseError) as exc:
            invalid_files.append(
                InvalidFileView(
                    filename=safe_name,
                    errors=[str(exc)],
                )
            )
            continue

        file_tool = _infer_source_tool(records, fallback_format=file_format)
        file_accepted_count = 0
        file_rejected_count = 0

        for raw_record in records:
            if not isinstance(raw_record, dict):
                invalid_records.append(
                    InvalidRecordView(
                        index=global_record_index,
                        record={"value": raw_record, "source_filename": safe_name},
                        errors=[
                            {
                                "loc": ["record"],
                                "msg": "record must be a JSON object or CSV row",
                                "type": "record_type",
                            }
                        ],
                    )
                )
                file_rejected_count += 1
                global_record_index += 1
                continue

            normalized_candidate = _normalize_empty_values(raw_record)
            try:
                finding = FindingRecordInput.model_validate(normalized_candidate)
            except ValidationError as exc:
                invalid_records.append(
                    InvalidRecordView(
                        index=global_record_index,
                        record={**dict(raw_record), "source_filename": safe_name},
                        errors=_serialize_validation_errors(exc),
                    )
                )
                file_rejected_count += 1
                global_record_index += 1
                continue

            normalized_record = {
                **_json_safe_record(raw_record),
                **finding.model_dump(),
                "record_index": global_record_index,
                "source_filename": safe_name,
                "source_format": file_format,
                "source_archive": archive_name,
                "source_tool": file_tool,
            }
            accepted_records.append(normalized_record)
            file_accepted_count += 1
            global_record_index += 1

        source_files.append(
            SourceFileSummaryView(
                filename=safe_name,
                format=file_format,
                accepted_count=file_accepted_count,
                rejected_count=file_rejected_count,
                tool=file_tool,
                status=_file_status(file_accepted_count, file_rejected_count),
            )
        )

    if not source_files:
        raise ScanUploadParseError("ZIP archive does not contain supported JSON or CSV files")

    return ParsedScanUpload(
        upload_kind="archive",
        accepted_records=accepted_records,
        invalid_records=invalid_records,
        source_files=source_files,
        invalid_files=invalid_files,
    )


def _load_json_records(content: bytes) -> list[Any]:
    try:
        payload = json.loads(content.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise ScanUploadParseError("JSON scan file must be UTF-8 encoded") from exc
    except json.JSONDecodeError as exc:
        if _looks_like_semgrep_text_report(content):
            raise ScanUploadParseError(
                "Uploaded file looks like Semgrep terminal output, not JSON. "
                "Export Semgrep results with: semgrep --json -o semgrep.json"
            ) from exc
        raise ScanUploadParseError("JSON scan file contains invalid JSON") from exc

    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        findings = payload.get("findings")
        if isinstance(findings, list):
            return findings
        semgrep_results = payload.get("results")
        if isinstance(semgrep_results, list):
            return _normalize_semgrep_results(semgrep_results)
    raise ScanUploadParseError(
        'JSON scan file must be an array, an object with "findings" array, or Semgrep JSON with "results" array'
    )


def _looks_like_semgrep_text_report(content: bytes) -> bool:
    text = content.decode("utf-8", errors="ignore")
    return "Code Findings" in text or "Semgrep" in text and "Findings" in text


def _normalize_semgrep_results(results: list[Any]) -> list[Any]:
    normalized_results: list[Any] = []
    for index, result in enumerate(results):
        if not isinstance(result, dict):
            normalized_results.append(result)
            continue

        extra = result.get("extra") if isinstance(result.get("extra"), dict) else {}
        metadata = extra.get("metadata") if isinstance(extra.get("metadata"), dict) else {}
        message = _optional_text(extra.get("message")) or _optional_text(result.get("message")) or ""
        lines = _optional_text(extra.get("lines")) or ""

        normalized_record = {
            **_json_safe_record(result),
            "scanner_name": "semgrep",
            "finding_type": _semgrep_finding_type(result, index),
            "severity_reported": _semgrep_severity(extra, metadata),
            "asset_type": "source_code",
            "has_cve": _metadata_has_key(metadata, "cve"),
            "description_length": len(message or lines),
            "semgrep_path": _optional_text(result.get("path")),
            "semgrep_message": message,
        }
        normalized_results.append(normalized_record)

    return normalized_results


def _semgrep_finding_type(result: dict[str, Any], index: int) -> str:
    check_id = _optional_text(result.get("check_id"))
    if check_id:
        return check_id
    return f"semgrep_result_{index}"


def _semgrep_severity(extra: dict[str, Any], metadata: dict[str, Any]) -> str:
    raw_severity = (
        _optional_text(extra.get("severity"))
        or _optional_text(metadata.get("severity"))
        or _optional_text(metadata.get("impact"))
        or "info"
    ).strip().lower()
    return {
        "critical": "critical",
        "blocker": "critical",
        "error": "high",
        "high": "high",
        "warning": "medium",
        "medium": "medium",
        "warn": "medium",
        "info": "low",
        "low": "low",
        "inventory": "low",
        "experiment": "low",
    }.get(raw_severity, "low")


def _metadata_has_key(metadata: dict[str, Any], expected_key: str) -> bool:
    for key, value in metadata.items():
        if str(key).lower() == expected_key and value:
            return True
    return False


def _optional_text(value: Any) -> str | None:
    if isinstance(value, str):
        normalized = value.strip()
        return normalized or None
    return None


def _infer_source_tool(records: list[Any], fallback_format: str) -> str | None:
    if fallback_format == "json" and _looks_like_semgrep_results(records):
        return "semgrep"
    if fallback_format == "csv":
        return "generic_csv"
    if fallback_format == "json":
        return "generic_json"
    return None


def _looks_like_semgrep_results(records: list[Any]) -> bool:
    if not records:
        return False
    first_record = records[0]
    if not isinstance(first_record, dict):
        return False
    return first_record.get("scanner_name") == "semgrep" or "semgrep_path" in first_record


def _load_csv_records(content: bytes) -> list[dict[str, Any]]:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ScanUploadParseError("CSV scan file must be UTF-8 encoded") from exc

    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise ScanUploadParseError("CSV scan file must contain a header row")

    return [
        {key: value for key, value in row.items() if key is not None}
        for row in reader
    ]


def _safe_member_name(member_name: str) -> str | None:
    parts = PurePath(member_name).parts
    if not parts:
        return None
    if any(part in {"..", ""} for part in parts):
        return None
    if PurePath(member_name).is_absolute():
        return None
    return PurePath(member_name).as_posix()


def _file_status(accepted_count: int, rejected_count: int) -> str:
    if accepted_count > 0 and rejected_count > 0:
        return "partial_invalid"
    if accepted_count > 0:
        return "accepted"
    return "all_invalid"


def _normalize_empty_values(record: dict[str, Any]) -> dict[str, Any]:
    optional_empty_fields = {"cvss_score", "asset_type", "port", "description_length"}
    normalized: dict[str, Any] = {}

    for raw_key, raw_value in record.items():
        key = str(raw_key).strip()
        if not key:
            continue
        if isinstance(raw_value, str):
            value = raw_value.strip()
            if key == "has_cve" and value == "":
                continue
            if key in optional_empty_fields and value == "":
                normalized[key] = None
                continue
            normalized[key] = value
            continue
        normalized[key] = raw_value

    return normalized


def _json_safe_record(record: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(record, ensure_ascii=True, default=str))


def _serialize_validation_errors(exc: ValidationError) -> list[dict[str, Any]]:
    serialized_errors: list[dict[str, Any]] = []
    for error in exc.errors():
        serialized_errors.append(
            {
                "loc": list(error.get("loc", [])),
                "msg": str(error.get("msg", "validation error")),
                "type": str(error.get("type", "value_error")),
            }
        )
    return serialized_errors
