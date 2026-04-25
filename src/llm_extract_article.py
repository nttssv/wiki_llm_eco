"""LLM-backed article extraction with structured output validation."""

from __future__ import annotations

import json
import logging
from typing import Any

from openai import OpenAI
from pydantic import ValidationError

from .config import (
    get_openai_api_key,
    get_openai_base_url,
    get_openai_max_retries,
    get_openai_model,
    get_openai_temperature,
    load_env,
)
from .extract_schema import ArticleExtraction


load_env()


SYSTEM_PROMPT = """
You extract structured narrative intelligence from a single news or analysis article.

Return VALID JSON ONLY matching the provided schema.
Do not include any explanation outside JSON.
Keep summaries short, original, and non-copyright-invasive.
Do not copy large chunks of article text.

Prioritize signal about:
- economy
- finance
- politics
- science
- technology
- geopolitics

Extraction rules:
- Fill article metadata fields from provided metadata and article text when possible.
- Extract only meaningful entities.
- Events must be concrete developments, not vague opinions.
- Narratives must be trackable over time.
- Graph edges must be useful for network visualization.
- importance_score values must be integers from 1 to 10.
- confidence values must be numbers from 0.0 to 1.0.
- If information is not available, use an empty string or empty list rather than inventing details.
- Use only these relationship labels when applicable:
  LED_BY
  SUCCESSOR
  DEPENDS_ON
  MANUFACTURES_IN
  EXPANDS_TO
  CHALLENGES
  PRESSURE_ON
  SUPPORTS
  AFFECTS
  RELATED_TO

Required output shape:
- source
- section
- title
- subtitle
- published_date
- category
- summary
- importance_score
- entities: [{name, type, role, description}]
- events: [{event_date, event_summary, location, importance_score}]
- themes: [{name, description}]
- narratives: [{name, thesis, status, importance_score}]
- graph_edges: [{source_node, relationship, target_node, confidence}]
""".strip()


def _summarize_error(exc: Exception) -> str:
    message = str(exc)
    if "invalid_api_key" in message or "Incorrect API key" in message:
        return "OpenAI API rejected the configured API key."
    return message


def _build_user_prompt(text: str, metadata: dict[str, str]) -> str:
    metadata_json = json.dumps(metadata, ensure_ascii=False, indent=2)
    return (
        "Extract structured narrative intelligence from the article below.\n\n"
        f"Article metadata:\n{metadata_json}\n\n"
        f"Article text:\n{text}"
    )


def _normalise_parsed_output(parsed_output: Any) -> ArticleExtraction:
    if isinstance(parsed_output, ArticleExtraction):
        return parsed_output
    if hasattr(parsed_output, "model_dump"):
        return ArticleExtraction.model_validate(parsed_output.model_dump())
    return ArticleExtraction.model_validate(parsed_output)


def extract_article_with_llm(text: str, metadata: dict[str, str]) -> ArticleExtraction:
    """Extract structured article data with an OpenAI model."""

    load_env()
    api_key = get_openai_api_key()
    if api_key is None:
        raise ValueError("OPENAI_API_KEY not found. Please set it in .env_local")

    client = OpenAI(api_key=api_key, base_url=get_openai_base_url())
    model = get_openai_model()
    temperature = get_openai_temperature()
    max_retries = get_openai_max_retries()
    last_error: Exception | None = None

    for attempt in range(1, max_retries + 1):
        try:
            response = client.responses.parse(
                model=model,
                temperature=temperature,
                input=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": _build_user_prompt(text, metadata)},
                ],
                text_format=ArticleExtraction,
            )

            if getattr(response, "output_parsed", None) is not None:
                return _normalise_parsed_output(response.output_parsed)

            output_text = getattr(response, "output_text", "")
            if not output_text:
                raise ValueError("LLM returned no structured output.")

            payload = json.loads(output_text)
            return ArticleExtraction.model_validate(payload)
        except (json.JSONDecodeError, ValidationError, ValueError) as exc:
            last_error = exc
            logging.warning(
                "Structured LLM extraction attempt %s/%s failed validation: %s",
                attempt,
                max_retries,
                _summarize_error(exc),
            )
        except Exception as exc:
            last_error = exc
            logging.warning(
                "LLM extraction attempt %s/%s failed: %s",
                attempt,
                max_retries,
                _summarize_error(exc),
            )

    raise RuntimeError(
        f"LLM extraction failed after {max_retries} attempts: {_summarize_error(last_error or RuntimeError())}"
    )
