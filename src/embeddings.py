"""Embedding helpers for optional Neo4j vector indexes."""

from __future__ import annotations

from typing import Iterable

from .config import (
    get_embedding_dimensions,
    get_embedding_model,
    get_openai_api_key,
    get_openai_base_url,
    get_openai_timeout_seconds,
)


def embeddings_enabled() -> bool:
    return get_openai_api_key() is not None


def embed_texts(texts: Iterable[str]) -> list[list[float]]:
    """Embed text values with OpenAI, preserving input order."""

    items = [str(text or "").strip() for text in texts]
    if not items:
        return []
    if get_openai_api_key() is None:
        raise RuntimeError("OPENAI_API_KEY is required to create embeddings.")

    from openai import OpenAI

    client = OpenAI(
        api_key=get_openai_api_key(),
        base_url=get_openai_base_url(),
        timeout=get_openai_timeout_seconds(),
        max_retries=0,
    )
    response = client.embeddings.create(
        model=get_embedding_model(),
        input=items,
        dimensions=get_embedding_dimensions(),
    )
    return [list(item.embedding) for item in response.data]
