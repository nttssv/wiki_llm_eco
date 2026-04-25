"""Generate a markdown weekly narrative report from SQLite."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path
import sqlite3

from .date_utils import is_date_in_week, parse_week_label
from .db import NarrativeDatabase
from .narrative_tracking import NarrativeTrend, get_narrative_trends
from .paths import DB_PATH, REPORTS_DIR


def get_connection() -> sqlite3.Connection:
    """Create a SQLite connection for report queries."""

    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def _article_ids(rows: list[sqlite3.Row]) -> list[str]:
    return [str(row["id"]) for row in rows]


def _placeholders(items: list[str]) -> str:
    return ", ".join("?" for _ in items)


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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a weekly markdown narrative report.")
    parser.add_argument("--week", required=True, help="Week-ending date in YYYY-MM-DD format.")
    return parser.parse_args()


def get_week_articles(connection: sqlite3.Connection, week_label: str) -> list[sqlite3.Row]:
    """Return articles whose published date falls in the selected week."""

    rows = connection.execute(
        """
        SELECT id, source, section, title, subtitle, published_date, category, summary, importance_score
        FROM articles
        ORDER BY title ASC
        """
    ).fetchall()
    return [row for row in rows if is_date_in_week(str(row["published_date"] or ""), week_label)]


def get_top_entities(connection: sqlite3.Connection, week_articles: list[sqlite3.Row]) -> list[sqlite3.Row]:
    """Return top entities by linked-article count for the selected week."""

    article_ids = _article_ids(week_articles)
    if not article_ids:
        return []

    query = f"""
        SELECT e.name, e.type, COUNT(DISTINCT ae.article_id) AS linked_articles
        FROM article_entities AS ae
        JOIN entities AS e ON e.id = ae.entity_id
        WHERE ae.article_id IN ({_placeholders(article_ids)})
        GROUP BY e.id, e.name, e.type
        ORDER BY linked_articles DESC, e.name ASC
        LIMIT 10
    """
    return connection.execute(query, article_ids).fetchall()


def get_week_summary_counts(connection: sqlite3.Connection, week_articles: list[sqlite3.Row]) -> dict[str, int]:
    """Return selected-week summary counts."""

    article_ids = _article_ids(week_articles)
    if not article_ids:
        return {
            "articles": 0,
            "entities": 0,
            "themes": 0,
            "narratives": 0,
            "graph_edges": 0,
        }

    entity_count = connection.execute(
        f"""
        SELECT COUNT(DISTINCT entity_id)
        FROM article_entities
        WHERE article_id IN ({_placeholders(article_ids)})
        """,
        article_ids,
    ).fetchone()[0]
    theme_count = connection.execute(
        f"""
        SELECT COUNT(DISTINCT theme_id)
        FROM article_themes
        WHERE article_id IN ({_placeholders(article_ids)})
        """,
        article_ids,
    ).fetchone()[0]
    narrative_count = connection.execute(
        f"""
        SELECT COUNT(DISTINCT narrative_id)
        FROM article_narratives
        WHERE article_id IN ({_placeholders(article_ids)})
        """,
        article_ids,
    ).fetchone()[0]
    graph_edge_count = connection.execute(
        f"""
        SELECT COUNT(*)
        FROM graph_edges
        WHERE evidence_article_id IN ({_placeholders(article_ids)})
        """,
        article_ids,
    ).fetchone()[0]

    return {
        "articles": len(article_ids),
        "entities": int(entity_count),
        "themes": int(theme_count),
        "narratives": int(narrative_count),
        "graph_edges": int(graph_edge_count),
    }


def get_top_themes(connection: sqlite3.Connection, week_articles: list[sqlite3.Row]) -> list[sqlite3.Row]:
    """Return top themes by linked-article count for the selected week."""

    article_ids = _article_ids(week_articles)
    if not article_ids:
        return []

    query = f"""
        SELECT t.name, COUNT(DISTINCT at.article_id) AS linked_articles
        FROM article_themes AS at
        JOIN themes AS t ON t.id = at.theme_id
        WHERE at.article_id IN ({_placeholders(article_ids)})
        GROUP BY t.id, t.name
        ORDER BY linked_articles DESC, t.name ASC
        LIMIT 10
    """
    return connection.execute(query, article_ids).fetchall()


def get_week_narratives(connection: sqlite3.Connection, week_label: str) -> list[NarrativeTrend]:
    """Return narratives linked to articles from the selected week."""

    return [trend for trend in get_narrative_trends(connection, week_label) if trend.week_mentions > 0]


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


def get_key_events(connection: sqlite3.Connection, week_articles: list[sqlite3.Row]) -> list[sqlite3.Row]:
    """Return key events for the selected week."""

    article_ids = _article_ids(week_articles)
    if not article_ids:
        return []

    query = f"""
        SELECT event_date, event_summary, location, importance_score
        FROM events
        WHERE article_id IN ({_placeholders(article_ids)})
        ORDER BY importance_score DESC, event_date DESC, event_summary ASC
    """
    return connection.execute(query, article_ids).fetchall()


def get_graph_edges(connection: sqlite3.Connection, week_articles: list[sqlite3.Row]) -> list[sqlite3.Row]:
    """Return graph edges supported by articles from the selected week."""

    article_ids = _article_ids(week_articles)
    if not article_ids:
        return []

    query = f"""
        SELECT source_node, relationship, target_node, confidence
        FROM graph_edges
        WHERE evidence_article_id IN ({_placeholders(article_ids)})
        ORDER BY confidence DESC, relationship ASC, source_node ASC
    """
    return connection.execute(query, article_ids).fetchall()


def write_report(report_path: Path, content: str) -> Path:
    """Write the report markdown file."""

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(content, encoding="utf-8")
    return report_path


def _build_report_markdown(
    week_label: str,
    summary_counts: dict[str, int],
    week_articles: list[sqlite3.Row],
    top_entities: list[sqlite3.Row],
    top_themes: list[sqlite3.Row],
    week_narratives: list[NarrativeTrend],
    narrative_changes: dict[str, list[str]],
    key_events: list[sqlite3.Row],
    graph_edges: list[sqlite3.Row],
) -> str:
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


def main() -> None:
    args = parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s", force=True)

    if not DB_PATH.exists():
        print("Database not found. Run weekly extraction first.")
        raise SystemExit(1)

    try:
        parse_week_label(args.week)
    except ValueError as exc:
        raise SystemExit(f"Invalid --week value: {args.week}") from exc

    database = NarrativeDatabase(DB_PATH)
    database.initialize()
    connection = database.connection
    try:
        week_articles = get_week_articles(connection, args.week)
        summary_counts = get_week_summary_counts(connection, week_articles)
        top_entities = get_top_entities(connection, week_articles)
        top_themes = get_top_themes(connection, week_articles)
        week_narratives = get_week_narratives(connection, args.week)
        narrative_changes = detect_narrative_changes(get_narrative_trends(connection, args.week))
        key_events = get_key_events(connection, week_articles)
        graph_edges = get_graph_edges(connection, week_articles)
    finally:
        database.close()

    report_content = _build_report_markdown(
        args.week,
        summary_counts,
        week_articles,
        top_entities,
        top_themes,
        week_narratives,
        narrative_changes,
        key_events,
        graph_edges,
    )

    report_path = REPORTS_DIR / f"{args.week}_weekly.md"
    write_report(report_path, report_content)
    logging.info("Generated weekly report: %s", report_path)
    print(f"weekly report written: {report_path}")


if __name__ == "__main__":
    main()
