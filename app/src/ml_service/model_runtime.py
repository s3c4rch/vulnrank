from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Protocol

import httpx

from ml_service.config import Settings, get_settings
from ml_service.models import PriorityClass


class ModelRuntimeError(Exception):
    """Raised when the external model runtime cannot return a usable prediction."""


class ModelRuntimeClient(Protocol):
    def predict_priority(
        self,
        model_tag: str,
        features: dict[str, Any],
    ) -> "ModelRuntimePrediction":
        """Return a parsed priority prediction for one finding payload."""


@dataclass(frozen=True)
class ModelRuntimePrediction:
    predicted_priority: PriorityClass
    confidence: float
    reason: str | None = None


def build_triage_messages(features: dict[str, Any]) -> list[dict[str, str]]:
    input_payload = json.dumps({"finding": features}, ensure_ascii=True, sort_keys=True)
    return [
        {
            "role": "system",
            "content": (
                "You are a defensive application security triage assistant. "
                "Classify the priority for reviewing one vulnerability finding. "
                "Return only valid JSON with keys predicted_priority, confidence, and reason. "
                "predicted_priority must be one of low, medium, high. "
                "confidence must be a number from 0 to 1. "
                "reason must be one short sentence."
            ),
        },
        {
            "role": "user",
            "content": (
                "Analyze this vulnerability finding for triage priority. "
                "The payload may be a normalized scanner finding or legacy numeric feature signals. "
                f"Input JSON: {input_payload}"
            ),
        },
    ]


def parse_prediction_content(content: str) -> ModelRuntimePrediction:
    try:
        payload = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ModelRuntimeError("ml-runtime returned invalid JSON content") from exc

    if not isinstance(payload, dict):
        raise ModelRuntimeError("ml-runtime JSON content must be an object")

    raw_priority = payload.get("predicted_priority")
    if not isinstance(raw_priority, str):
        raise ModelRuntimeError("ml-runtime response is missing predicted_priority")
    normalized_priority = raw_priority.strip().lower()
    try:
        predicted_priority = PriorityClass(normalized_priority)
    except ValueError as exc:
        raise ModelRuntimeError(
            "ml-runtime predicted_priority must be low, medium, or high"
        ) from exc

    raw_confidence = payload.get("confidence")
    if isinstance(raw_confidence, bool) or not isinstance(raw_confidence, (int, float)):
        raise ModelRuntimeError("ml-runtime confidence must be a number")
    confidence = float(raw_confidence)
    if confidence < 0 or confidence > 1:
        raise ModelRuntimeError("ml-runtime confidence must be between 0 and 1")

    raw_reason = payload.get("reason")
    if raw_reason is not None and not isinstance(raw_reason, str):
        raise ModelRuntimeError("ml-runtime reason must be a string when provided")

    return ModelRuntimePrediction(
        predicted_priority=predicted_priority,
        confidence=confidence,
        reason=raw_reason.strip() if isinstance(raw_reason, str) else None,
    )


class OllamaModelRuntimeClient:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def list_models(self) -> set[str]:
        try:
            with httpx.Client(timeout=self.settings.ml_runtime_timeout) as client:
                response = client.get(f"{self.settings.ml_runtime_url.rstrip('/')}/api/tags")
                response.raise_for_status()
                parsed_response = response.json()
        except httpx.HTTPStatusError as exc:
            raise ModelRuntimeError(
                build_runtime_http_error_message(
                    status_code=exc.response.status_code,
                    response_text=exc.response.text,
                    model_tag="",
                )
            ) from exc
        except httpx.HTTPError as exc:
            raise ModelRuntimeError("ml-runtime model list request failed") from exc
        except json.JSONDecodeError as exc:
            raise ModelRuntimeError("ml-runtime returned invalid model list JSON") from exc

        raw_models = parsed_response.get("models") if isinstance(parsed_response, dict) else None
        if not isinstance(raw_models, list):
            raise ModelRuntimeError("ml-runtime model list response is missing models")

        model_names: set[str] = set()
        for raw_model in raw_models:
            if isinstance(raw_model, dict) and isinstance(raw_model.get("name"), str):
                model_names.add(raw_model["name"])
        return model_names

    def pull_model(self, model_tag: str) -> None:
        payload = {"model": model_tag, "stream": False}
        try:
            with httpx.Client(timeout=self.settings.ml_runtime_pull_timeout) as client:
                response = client.post(
                    f"{self.settings.ml_runtime_url.rstrip('/')}/api/pull",
                    json=payload,
                )
                response.raise_for_status()
                parsed_response = response.json()
        except httpx.HTTPStatusError as exc:
            raise ModelRuntimeError(
                build_runtime_http_error_message(
                    status_code=exc.response.status_code,
                    response_text=exc.response.text,
                    model_tag=model_tag,
                )
            ) from exc
        except httpx.HTTPError as exc:
            raise ModelRuntimeError("ml-runtime model pull request failed") from exc
        except json.JSONDecodeError as exc:
            raise ModelRuntimeError("ml-runtime returned invalid pull response JSON") from exc

        if not isinstance(parsed_response, dict):
            raise ModelRuntimeError("ml-runtime pull response JSON must be an object")
        raw_error = parsed_response.get("error")
        if isinstance(raw_error, str) and raw_error.strip():
            raise ModelRuntimeError(_shorten_error_detail(raw_error))

    def predict_priority(
        self,
        model_tag: str,
        features: dict[str, Any],
    ) -> ModelRuntimePrediction:
        response_payload = self._chat(model_tag=model_tag, features=features)
        message = response_payload.get("message")
        if not isinstance(message, dict):
            raise ModelRuntimeError("ml-runtime response is missing message")
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            raise ModelRuntimeError("ml-runtime response is missing message content")
        return parse_prediction_content(content)

    def _chat(self, model_tag: str, features: dict[str, Any]) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model_tag,
            "messages": build_triage_messages(features),
            "stream": False,
            "format": "json",
            "options": {
                "temperature": self.settings.ml_runtime_temperature,
                "num_ctx": self.settings.ml_runtime_context_length,
            },
        }
        if self.settings.ml_runtime_keep_alive:
            payload["keep_alive"] = self.settings.ml_runtime_keep_alive

        try:
            with httpx.Client(timeout=self.settings.ml_runtime_timeout) as client:
                response = client.post(
                    f"{self.settings.ml_runtime_url.rstrip('/')}/api/chat",
                    json=payload,
                )
                response.raise_for_status()
                parsed_response = response.json()
        except httpx.HTTPStatusError as exc:
            raise ModelRuntimeError(
                build_runtime_http_error_message(
                    status_code=exc.response.status_code,
                    response_text=exc.response.text,
                    model_tag=model_tag,
                )
            ) from exc
        except httpx.HTTPError as exc:
            raise ModelRuntimeError("ml-runtime request failed") from exc
        except json.JSONDecodeError as exc:
            raise ModelRuntimeError("ml-runtime returned invalid response JSON") from exc

        if not isinstance(parsed_response, dict):
            raise ModelRuntimeError("ml-runtime response JSON must be an object")
        return parsed_response


class OpenAIModelRuntimeClient:
    def __init__(
        self,
        api_key: str,
        settings: Settings | None = None,
    ) -> None:
        self.api_key = api_key
        self.settings = settings or get_settings()

    def predict_priority(
        self,
        model_tag: str,
        features: dict[str, Any],
    ) -> ModelRuntimePrediction:
        response_payload = self._chat(model_tag=model_tag, features=features)
        choices = response_payload.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ModelRuntimeError("OpenAI response is missing choices")
        first_choice = choices[0]
        if not isinstance(first_choice, dict):
            raise ModelRuntimeError("OpenAI response choice must be an object")
        message = first_choice.get("message")
        if not isinstance(message, dict):
            raise ModelRuntimeError("OpenAI response is missing message")
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            raise ModelRuntimeError("OpenAI response is missing message content")
        return parse_prediction_content(content)

    def _chat(self, model_tag: str, features: dict[str, Any]) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model_tag,
            "messages": build_triage_messages(features),
            "stream": False,
            "response_format": {"type": "json_object"},
        }

        try:
            with httpx.Client(timeout=self.settings.openai_timeout) as client:
                response = client.post(
                    f"{self.settings.openai_base_url.rstrip('/')}/chat/completions",
                    json=payload,
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                )
                response.raise_for_status()
                parsed_response = response.json()
        except httpx.HTTPStatusError as exc:
            raise ModelRuntimeError(
                build_openai_http_error_message(
                    status_code=exc.response.status_code,
                    response_text=exc.response.text,
                )
            ) from exc
        except httpx.HTTPError as exc:
            raise ModelRuntimeError("OpenAI request failed") from exc
        except json.JSONDecodeError as exc:
            raise ModelRuntimeError("OpenAI returned invalid response JSON") from exc

        if not isinstance(parsed_response, dict):
            raise ModelRuntimeError("OpenAI response JSON must be an object")
        return parsed_response


def build_runtime_http_error_message(
    *,
    status_code: int,
    response_text: str,
    model_tag: str,
) -> str:
    detail = _extract_runtime_error_detail(response_text)
    if status_code == 404 and "not found" in detail.lower():
        return (
            f"ml-runtime model '{model_tag}' is not available. "
            f"Pull it with: docker compose exec ml-runtime ollama pull {model_tag}"
        )
    if detail:
        return f"ml-runtime returned HTTP {status_code}: {detail}"
    return f"ml-runtime returned HTTP {status_code}"


def build_openai_http_error_message(
    *,
    status_code: int,
    response_text: str,
) -> str:
    detail = _extract_runtime_error_detail(response_text)
    if detail:
        return f"OpenAI returned HTTP {status_code}: {detail}"
    return f"OpenAI returned HTTP {status_code}"


def _extract_runtime_error_detail(response_text: str) -> str:
    if not response_text.strip():
        return ""
    try:
        payload = json.loads(response_text)
    except json.JSONDecodeError:
        return _shorten_error_detail(response_text)

    if isinstance(payload, dict):
        raw_error = payload.get("error") or payload.get("message") or payload.get("detail")
        if isinstance(raw_error, str):
            return _shorten_error_detail(raw_error)
        if isinstance(raw_error, dict):
            nested_message = raw_error.get("message") or raw_error.get("code")
            if isinstance(nested_message, str):
                return _shorten_error_detail(nested_message)
    return _shorten_error_detail(response_text)


def _shorten_error_detail(value: str, limit: int = 300) -> str:
    normalized = " ".join(value.strip().split())
    if len(normalized) <= limit:
        return normalized
    return f"{normalized[: limit - 3]}..."
