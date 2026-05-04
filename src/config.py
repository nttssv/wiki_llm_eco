"""Environment-backed configuration helpers."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


_ENV_LOADED = False


def load_env() -> None:
    """Load environment variables from .env_local or .env once."""

    global _ENV_LOADED
    if _ENV_LOADED:
        return

    base = Path(__file__).resolve().parent.parent
    env_local = base / ".env_local"
    env_default = base / ".env"

    if env_local.exists():
        load_dotenv(env_local)
    elif env_default.exists():
        load_dotenv(env_default)

    _ENV_LOADED = True


def get_openai_api_key() -> str | None:
    load_env()
    value = os.getenv("OPENAI_API_KEY", "").strip()
    return value or None


def get_openai_base_url() -> str | None:
    load_env()
    value = os.getenv("OPENAI_BASE_URL", "").strip()
    return value or None


def get_openai_model() -> str:
    load_env()
    return os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip() or "gpt-4o-mini"


def get_openai_temperature() -> float:
    load_env()
    raw_value = os.getenv("OPENAI_TEMPERATURE", "0").strip() or "0"
    return float(raw_value)


def get_openai_max_retries() -> int:
    load_env()
    raw_value = os.getenv("OPENAI_MAX_RETRIES", "3").strip() or "3"
    return max(1, int(raw_value))


def get_openai_timeout_seconds() -> float:
    load_env()
    raw_value = os.getenv("OPENAI_TIMEOUT_SECONDS", "12").strip() or "12"
    return max(1.0, float(raw_value))


def get_openai_max_output_tokens() -> int:
    load_env()
    raw_value = os.getenv("OPENAI_MAX_OUTPUT_TOKENS", "900").strip() or "900"
    return max(128, int(raw_value))


def get_neo4j_uri() -> str:
    load_env()
    return os.getenv("NEO4J_URI", "bolt://localhost:7687").strip() or "bolt://localhost:7687"


def get_neo4j_user() -> str:
    load_env()
    return os.getenv("NEO4J_USER", "neo4j").strip() or "neo4j"


def get_neo4j_password() -> str | None:
    load_env()
    value = os.getenv("NEO4J_PASSWORD", "").strip()
    return value or None


def get_neo4j_database() -> str:
    load_env()
    return os.getenv("NEO4J_DATABASE", "neo4j").strip() or "neo4j"


def get_embedding_model() -> str:
    load_env()
    return os.getenv("EMBEDDING_MODEL", "text-embedding-3-small").strip() or "text-embedding-3-small"


def get_embedding_dimensions() -> int:
    load_env()
    raw_value = os.getenv("EMBEDDING_DIMENSIONS", "1536").strip() or "1536"
    return max(1, int(raw_value))
