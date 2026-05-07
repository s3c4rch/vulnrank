from __future__ import annotations

from decimal import Decimal


DEFAULT_MODEL_NAME = "gemma3:4b"
LOCAL_DEMO_MODEL_NAME = "demo_model"
OPENAI_PROVIDER_MODEL_NAME = "chatgpt"
OPENAI_DEFAULT_MODEL_NAME = "gpt-5.4-mini"
OLLAMA_MODEL_VERSION = "ollama"
OPENAI_MODEL_VERSION = "openai"

OLLAMA_MODEL_SEEDS = (
    {
        "name": DEFAULT_MODEL_NAME,
        "version": OLLAMA_MODEL_VERSION,
        "description": "Default MVP Ollama model for vulnerability finding triage",
        "cost_per_prediction": Decimal("2.50"),
        "is_active": True,
    },
    {
        "name": "qwen3:4b",
        "version": OLLAMA_MODEL_VERSION,
        "description": "Instruction/reasoning Ollama candidate for structured JSON triage",
        "cost_per_prediction": Decimal("2.50"),
        "is_active": False,
    },
    {
        "name": "llama3.2:3b",
        "version": OLLAMA_MODEL_VERSION,
        "description": "Lightweight Ollama fallback model for local MVP runs",
        "cost_per_prediction": Decimal("2.50"),
        "is_active": False,
    },
    {
        "name": "phi4-mini:3.8b",
        "version": OLLAMA_MODEL_VERSION,
        "description": "Compact reasoning Ollama candidate for priority classification",
        "cost_per_prediction": Decimal("2.50"),
        "is_active": False,
    },
)

EXTERNAL_MODEL_SEEDS = (
    {
        "name": OPENAI_PROVIDER_MODEL_NAME,
        "version": OPENAI_MODEL_VERSION,
        "description": "ChatGPT/OpenAI via the user's own API key",
        "cost_per_prediction": Decimal("0.00"),
        "is_active": True,
    },
)

LOCAL_DEMO_MODEL_SEEDS = (
    {
        "name": LOCAL_DEMO_MODEL_NAME,
        "version": "1.0",
        "description": "Inactive local scikit-learn fallback for unit tests and legacy queued tasks",
        "cost_per_prediction": Decimal("2.50"),
        "is_active": False,
    },
    {
        "name": "priority-classifier",
        "version": "1.0",
        "description": "Inactive legacy demo security finding priority classifier",
        "cost_per_prediction": Decimal("2.50"),
        "is_active": False,
    },
    {
        "name": "priority-classifier",
        "version": "1.1",
        "description": "Inactive legacy updated demo security finding priority classifier",
        "cost_per_prediction": Decimal("3.00"),
        "is_active": False,
    },
)

MODEL_SEEDS = (*LOCAL_DEMO_MODEL_SEEDS, *OLLAMA_MODEL_SEEDS, *EXTERNAL_MODEL_SEEDS)
OLLAMA_MODEL_NAMES = frozenset(model["name"] for model in OLLAMA_MODEL_SEEDS)


def is_local_demo_model(model_name: str) -> bool:
    return model_name == LOCAL_DEMO_MODEL_NAME


def is_ollama_model(model_name: str) -> bool:
    return model_name in OLLAMA_MODEL_NAMES


def is_openai_provider_model(model_name: str) -> bool:
    return model_name == OPENAI_PROVIDER_MODEL_NAME
