"""Generate a markdown weekly narrative report from Neo4j."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path
from typing import Any

from .config import get_neo4j_database
from .date_utils import parse_week_label
from .neo4j_store import neo4j_driver
from .paths import REPORTS_DIR
from .weekly_report import NarrativeTrend, _build_report_markdown, detect_narrative_changes, write_report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a weekly markdown narrative report from Neo4j.")
    parser.add_argument("--week", required=True, help="Week-ending date in YYYY-MM-DD format.")
    return parser.parse_args()


def get_week_articles(session: Any, week_label: str) -> list[dict[str, Any]]:
    """Return articles whose stored week matches the selected week."""

    return session.run(
        """
        MATCH (a:Article {week: $week})
        RETURN
            a.id AS id,
            a.source AS source,
            a.section AS section,
            a.title AS title,
            a.subtitle AS subtitle,
            a.published_date AS published_date,
            a.category AS category,
            a.summary AS summary,
            coalesce(a.importance_score, 0) AS importance_score
        ORDER BY title ASC
        """,
        week=week_label,
    ).data()


def _count(session: Any, query: str, week_label: str) -> int:
    row = session.run(query, week=week_label).single()
    return int(row["count"] or 0) if row else 0


def get_week_summary_counts(session: Any, week_label: str) -> dict[str, int]:
    """Return selected-week summary counts."""

    return {
        "articles": _count(session, "MATCH (a:Article {week: $week}) RETURN count(a) AS count", week_label),
        "entities": _count(
            session,
            """
            MATCH (a:Article {week: $week})-[:MENTIONS]->(e:Entity)
            RETURN count(DISTINCT e) AS count
            """,
            week_label,
        ),
        "themes": _count(
            session,
            """
            MATCH (a:Article {week: $week})-[:HAS_THEME]->(t:Theme)
            RETURN count(DISTINCT t) AS count
            """,
            week_label,
        ),
        "narratives": _count(
            session,
            """
            MATCH (a:Article {week: $week})-[:HAS_NARRATIVE]->(n:Narrative)
            RETURN count(DISTINCT n) AS count
            """,
            week_label,
        ),
        "graph_edges": _count(
            session,
            """
            MATCH (:Entity)-[rel:RELATES_TO]->(:Entity)
            MATCH (a:Article {id: rel.evidence_article_id})
            WHERE a.week = $week
            RETURN count(rel) AS count
            """,
            week_label,
        ),
    }


def get_top_entities(session: Any, week_label: str) -> list[dict[str, Any]]:
    """Return top entities by linked-article count for the selected week."""

    return session.run(
        """
        MATCH (a:Article {week: $week})-[:MENTIONS]->(e:Entity)
        RETURN
            e.name AS name,
            e.type AS type,
            count(DISTINCT a) AS linked_articles
        ORDER BY linked_articles DESC, name ASC
        LIMIT 10
        """,
        week=week_label,
    ).data()


def get_top_themes(session: Any, week_label: str) -> list[dict[str, Any]]:
    """Return top themes by linked-article count for the selected week."""

    return session.run(
        """
        MATCH (a:Article {week: $week})-[:HAS_THEME]->(t:Theme)
        RETURN
            t.name AS name,
            count(DISTINCT a) AS linked_articles
        ORDER BY linked_articles DESC, name ASC
        LIMIT 10
        """,
        week=week_label,
    ).data()


def _classify_narrative_trend(
    week_label: str,
    first_seen_date: str,
    last_seen_date: str,
    week_mentions: int,
) -> str:
    if first_seen_date == week_label and week_mentions > 0:
        return "NEW"
    if week_mentions > 1:
        return "STRENGTHENING"
    if week_mentions == 1 and first_seen_date and first_seen_date < week_label:
        return "RECURRING"
    if week_mentions == 0 and first_seen_date and first_seen_date < week_label and last_seen_date < week_label:
        return "WEAKENING"
    return "NEW" if week_mentions > 0 else "WEAKENING"


def get_narrative_trends(session: Any, week_label: str) -> list[NarrativeTrend]:
    """Return narrative trend classifications for a selected week."""

    rows = session.run(
        """
        MATCH (n:Narrative)
        OPTIONAL MATCH (a:Article)-[:HAS_NARRATIVE]->(n)
        WITH n, [article IN collect(DISTINCT a) WHERE article IS NOT NULL] AS articles
        WITH n, articles, [article IN articles WHERE article.week = $week] AS week_articles
        RETURN
            n.id AS id,
            coalesce(n.name, '') AS name,
            coalesce(n.thesis, '') AS thesis,
            coalesce(n.status, '') AS extracted_status,
            coalesce(n.importance_score, 0) AS importance_score,
            size(articles) AS mention_count,
            coalesce(n.first_seen_date, '') AS first_seen_date,
            coalesce(n.last_seen_date, '') AS last_seen_date,
            size(week_articles) AS week_mentions
        ORDER BY mention_count DESC, importance_score DESC, name ASC
        """,
        week=week_label,
    ).data()

    trends: list[NarrativeTrend] = []
    for row in rows:
        first_seen_date = str(row.get("first_seen_date") or "")
        last_seen_date = str(row.get("last_seen_date") or "")
        week_mentions = int(row.get("week_mentions") or 0)
        if first_seen_date and first_seen_date > week_label and week_mentions == 0:
            continue
        trends.append(
            NarrativeTrend(
                id=str(row.get("id") or ""),
                name=str(row.get("name") or ""),
                thesis=str(row.get("thesis") or ""),
                extracted_status=str(row.get("extracted_status") or ""),
                trend_status=_classify_narrative_trend(
                    week_label,
                    first_seen_date,
                    last_seen_date,
                    week_mentions,
                ),
                importance_score=int(row.get("importance_score") or 0),
                mention_count=int(row.get("mention_count") or 0),
                first_seen_date=first_seen_date,
                last_seen_date=last_seen_date,
                week_mentions=week_mentions,
            )
        )
    return trends


def get_key_events(session: Any, week_label: str) -> list[dict[str, Any]]:
    """Return key events for the selected week."""

    return session.run(
        """
        MATCH (a:Article {week: $week})-[:HAS_EVENT]->(ev:Event)
        RETURN
            ev.event_date AS event_date,
            ev.event_summary AS event_summary,
            ev.location AS location,
            coalesce(ev.importance_score, 0) AS importance_score
        ORDER BY importance_score DESC, event_date DESC, event_summary ASC
        """,
        week=week_label,
    ).data()


def get_graph_edges(session: Any, week_label: str) -> list[dict[str, Any]]:
    """Return graph edges supported by articles from the selected week."""

    return session.run(
        """
        MATCH (source:Entity)-[rel:RELATES_TO]->(target:Entity)
        MATCH (a:Article {id: rel.evidence_article_id})
        WHERE a.week = $week
        RETURN
            source.name AS source_node,
            rel.relationship AS relationship,
            target.name AS target_node,
            coalesce(rel.confidence, 0.0) AS confidence
        ORDER BY confidence DESC, relationship ASC, source_node ASC
        """,
        week=week_label,
    ).data()


def generate_report(week_label: str) -> Path:
    """Generate a Neo4j-backed weekly report and return its path."""

    parse_week_label(week_label)

    with neo4j_driver() as driver:
        driver.verify_connectivity()
        with driver.session(database=get_neo4j_database()) as session:
            week_articles = get_week_articles(session, week_label)
            summary_counts = get_week_summary_counts(session, week_label)
            top_entities = get_top_entities(session, week_label)
            top_themes = get_top_themes(session, week_label)
            narrative_trends = get_narrative_trends(session, week_label)
            week_narratives = [trend for trend in narrative_trends if trend.week_mentions > 0]
            narrative_changes = detect_narrative_changes(narrative_trends)
            key_events = get_key_events(session, week_label)
            graph_edges = get_graph_edges(session, week_label)

    report_content = _build_report_markdown(
        week_label,
        summary_counts,
        week_articles,
        top_entities,
        top_themes,
        week_narratives,
        narrative_changes,
        key_events,
        graph_edges,
    )

    report_path = REPORTS_DIR / f"{week_label}_weekly.md"
    return write_report(report_path, report_content)


def main() -> None:
    args = parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s", force=True)

    try:
        report_path = generate_report(args.week)
    except ValueError as exc:
        raise SystemExit(f"Invalid --week value: {args.week}") from exc

    logging.info("Generated Neo4j weekly report: %s", report_path)
    print(f"neo4j weekly report written: {report_path}")


if __name__ == "__main__":
    main()
