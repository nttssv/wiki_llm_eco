"""Markdown helpers for Neo4j-backed weekly narrative reports."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


@dataclass(slots=True)
class NarrativeTrend:
    id: str
    name: str
    thesis: str
    extracted_status: str
    trend_status: str
    importance_score: int
    mention_count: int
    first_seen_date: str
    last_seen_date: str
    week_mentions: int


def _markdown_table(headers: list[str], rows: list[list[object]]) -> str:
    header_row = "| " + " | ".join(headers) + " |"
    separator_row = "|" + "|".join("---" for _ in headers) + "|"
    body_rows = [
        "| " + " | ".join(str(value) for value in row) + " |"
        for row in rows
    ]
    if not body_rows:
        body_rows = ["| " + " | ".join("None" for _ in headers) + " |"]
    return "\n".join([header_row, separator_row, *body_rows])


def detect_narrative_changes(
    trends: list[NarrativeTrend],
) -> dict[str, list[str]]:
    """Detect simple week-over-week narrative change categories."""

    changes = {
        "New": [],
        "Strengthening": [],
        "Recurring": [],
        "Weakening": [],
    }
    for trend in trends:
        if trend.trend_status == "NEW":
            changes["New"].append(trend.name)
        elif trend.trend_status == "STRENGTHENING":
            changes["Strengthening"].append(trend.name)
        elif trend.trend_status == "RECURRING":
            changes["Recurring"].append(trend.name)
        elif trend.trend_status == "WEAKENING":
            changes["Weakening"].append(trend.name)

    for names in changes.values():
        names.sort()
    return changes


def write_report(report_path: Path, content: str) -> Path:
    """Write the report markdown file."""

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(content, encoding="utf-8")
    return report_path


def _build_report_markdown(
    week_label: str,
    summary_counts: dict[str, int],
    week_articles: list[Mapping[str, Any]],
    top_entities: list[Mapping[str, Any]],
    top_themes: list[Mapping[str, Any]],
    week_narratives: list[NarrativeTrend],
    narrative_changes: dict[str, list[str]],
    key_events: list[Mapping[str, Any]],
    graph_edges: list[Mapping[str, Any]],
) -> str:
    del week_articles
    top_entities_table = _markdown_table(
        ["Entity", "Type", "Linked Articles"],
        [[row["name"], row["type"], row["linked_articles"]] for row in top_entities],
    )
    top_themes_table = _markdown_table(
        ["Theme", "Linked Articles"],
        [[row["name"], row["linked_articles"]] for row in top_themes],
    )
    key_narratives_table = _markdown_table(
        ["Narrative", "Status", "Importance", "Thesis"],
        [
            [trend.name, trend.trend_status, trend.importance_score, trend.thesis]
            for trend in week_narratives
        ],
    )
    key_events_table = _markdown_table(
        ["Date", "Event", "Location", "Importance"],
        [
            [
                row["event_date"] or "Undated",
                row["event_summary"],
                row["location"],
                row["importance_score"],
            ]
            for row in key_events
        ],
    )
    graph_relationships_table = _markdown_table(
        ["Source", "Relationship", "Target", "Confidence"],
        [
            [
                row["source_node"],
                row["relationship"],
                row["target_node"],
                f"{float(row['confidence']):.2f}",
            ]
            for row in graph_edges
        ],
    )

    return f"""# Weekly Narrative Report: {week_label}

## Summary
- Articles processed: {summary_counts['articles']}
- Total entities: {summary_counts['entities']}
- Total themes: {summary_counts['themes']}
- Total narratives: {summary_counts['narratives']}
- Total graph edges: {summary_counts['graph_edges']}

## Top Entities
{top_entities_table}

## Top Themes
{top_themes_table}

## Key Narratives
{key_narratives_table}

## Narrative Changes
### New Narratives
{chr(10).join(f"- {name}" for name in narrative_changes["New"]) or "- None"}

### Strengthening Narratives
{chr(10).join(f"- {name}" for name in narrative_changes["Strengthening"]) or "- None"}

### Recurring Narratives
{chr(10).join(f"- {name}" for name in narrative_changes["Recurring"]) or "- None"}

### Weakening Narratives
{chr(10).join(f"- {name}" for name in narrative_changes["Weakening"]) or "- None"}

## Key Events
{key_events_table}

## Graph Relationships
{graph_relationships_table}
"""
