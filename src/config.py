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
