"""Markdown wiki generation."""

from __future__ import annotations

from pathlib import Path
import sqlite3

from .extract_article import markdown_filename
from .extract_schema import ArticleExtraction


def _yaml_list(values: list[str]) -> str:
    if not values:
        return "[]"
    return "[" + ", ".join(f'"{value}"' for value in values) + "]"


def build_article_markdown(article_id: str, extraction: ArticleExtraction) -> str:
    """Render a wiki page for one article."""

    theme_names = [theme.name for theme in extraction.themes]
    narrative_names = [narrative.name for narrative in extraction.narratives]
    thesis = extraction.narratives[0].thesis if extraction.narratives else "No narrative thesis extracted."

    entity_lines = "\n".join(
        f"- **{entity.name}** ({entity.type}): {entity.role}. {entity.description}"
        for entity in extraction.entities
    ) or "- None extracted."

    event_lines = "\n".join(
        f"- **{event.event_date or 'Undated'}** | {event.location}: {event.event_summary} "
        f"(importance: {event.importance_score})"
        for event in extraction.events
    ) or "- None extracted."

    relationship_lines = "\n".join(
        f"- `{edge.source_node} --{edge.relationship}--> {edge.target_node}` "
        f"(confidence: {edge.confidence:.2f})"
        for edge in extraction.graph_edges
    ) or "- None extracted."

    narrative_lines = "\n".join(
        f"- **{narrative.name}** ({narrative.status}): {narrative.thesis}"
        for narrative in extraction.narratives
    ) or "- None extracted."

    return f"""---
id: {article_id}
source: {extraction.source}
date: {extraction.published_date}
category: {extraction.category}
themes: {_yaml_list(theme_names)}
narratives: {_yaml_list(narrative_names)}
importance_score: {extraction.importance_score}
---

# {extraction.title}

## Core thesis

{thesis}

## Summary

{extraction.summary}

## Key entities

{entity_lines}

## Key events

{event_lines}

## Narrative shift

{narrative_lines}

## Graph relationships

{relationship_lines}

## Linked narratives

{', '.join(narrative_names) if narrative_names else 'None.'}
"""


def write_article_markdown(output_dir: Path, article_id: str, extraction: ArticleExtraction) -> Path:
    """Write the article markdown file and return its path."""

    output_dir.mkdir(parents=True, exist_ok=True)
    file_path = output_dir / markdown_filename(article_id, extraction.title)
    file_path.write_text(build_article_markdown(article_id, extraction), encoding="utf-8")
    return file_path


def build_wiki_home_index(
    article_rows: list[sqlite3.Row],
    entity_count: int,
    narrative_count: int,
) -> str:
    """Render the root wiki index page."""

    article_lines = "\n".join(
        (
            f"- [{row['title']}](articles/{markdown_filename(row['id'], row['title'])})"
            f" | {row['published_date'] or 'Undated'} | {row['source']}"
        )
        for row in article_rows
    ) or "- No article pages generated yet."

    return f"""# Narrative Wiki

## Overview

- Articles: {len(article_rows)}
- Entities: {entity_count}
- Narratives: {narrative_count}
- [Entity index](entities/index.md)
- [Narrative index](narratives/index.md)

## Articles

{article_lines}
"""


def build_entities_index(entity_rows: list[sqlite3.Row]) -> str:
    """Render the entities index page."""

    entity_lines = "\n".join(
        f"- **{row['name']}** ({row['type']}): {row['description']}"
        for row in entity_rows
    ) or "- No entities extracted yet."

    return f"""# Entity Index

## Entities

{entity_lines}
"""


def build_narratives_index(narrative_rows: list[sqlite3.Row]) -> str:
    """Render the narratives index page."""

    narrative_lines = "\n".join(
        f"- **{row['name']}** ({row['status']}): {row['thesis']}"
        for row in narrative_rows
    ) or "- No narratives extracted yet."

    return f"""# Narrative Index

## Narratives

{narrative_lines}
"""


def write_wiki_indexes(
    wiki_dir: Path,
    entities_dir: Path,
    narratives_dir: Path,
    article_rows: list[sqlite3.Row],
    entity_rows: list[sqlite3.Row],
    narrative_rows: list[sqlite3.Row],
) -> tuple[Path, Path, Path]:
    """Write simple wiki index pages."""

    wiki_dir.mkdir(parents=True, exist_ok=True)
    entities_dir.mkdir(parents=True, exist_ok=True)
    narratives_dir.mkdir(parents=True, exist_ok=True)

    wiki_index_path = wiki_dir / "index.md"
    entities_index_path = entities_dir / "index.md"
    narratives_index_path = narratives_dir / "index.md"

    wiki_index_path.write_text(
        build_wiki_home_index(article_rows, len(entity_rows), len(narrative_rows)),
        encoding="utf-8",
    )
    entities_index_path.write_text(build_entities_index(entity_rows), encoding="utf-8")
    narratives_index_path.write_text(build_narratives_index(narrative_rows), encoding="utf-8")

    return wiki_index_path, entities_index_path, narratives_index_path
