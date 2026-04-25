"""Helpers for tracking narrative changes over time."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import sqlite3

from .date_utils import week_label_for_published_date


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


def get_narrative_trends(connection: sqlite3.Connection, week_label: str) -> list[NarrativeTrend]:
    """Return narrative trend classifications for a selected week."""

    rows = connection.execute(
        """
        SELECT
            n.id,
            n.name,
            n.thesis,
            n.status,
            n.importance_score,
            COALESCE(n.first_seen_date, '') AS first_seen_date,
            COALESCE(n.last_seen_date, '') AS last_seen_date,
            COALESCE(n.mention_count, 0) AS mention_count,
            a.published_date
        FROM narratives AS n
        LEFT JOIN article_narratives AS an ON an.narrative_id = n.id
        LEFT JOIN articles AS a ON a.id = an.article_id
        ORDER BY n.name ASC
        """
    ).fetchall()

    grouped: dict[str, dict[str, object]] = {}
    for row in rows:
        narrative_id = str(row["id"])
        if narrative_id not in grouped:
            grouped[narrative_id] = {
                "id": narrative_id,
                "name": str(row["name"]),
                "thesis": str(row["thesis"] or ""),
                "extracted_status": str(row["status"] or ""),
                "importance_score": int(row["importance_score"] or 0),
                "mention_count": int(row["mention_count"] or 0),
                "first_seen_date": str(row["first_seen_date"] or ""),
                "last_seen_date": str(row["last_seen_date"] or ""),
                "week_mentions": 0,
            }

        published_date = str(row["published_date"] or "")
        if week_label_for_published_date(published_date) == week_label:
            grouped[narrative_id]["week_mentions"] = int(grouped[narrative_id]["week_mentions"]) + 1

    trends: list[NarrativeTrend] = []
    for item in grouped.values():
        week_mentions = int(item["week_mentions"])
        first_seen_date = str(item["first_seen_date"])
        last_seen_date = str(item["last_seen_date"])

        if first_seen_date and first_seen_date > week_label and week_mentions == 0:
            continue

        if first_seen_date == week_label and week_mentions > 0:
            trend_status = "NEW"
        elif week_mentions > 1:
            trend_status = "STRENGTHENING"
        elif week_mentions == 1 and first_seen_date and first_seen_date < week_label:
            trend_status = "RECURRING"
        elif week_mentions == 0 and first_seen_date and first_seen_date < week_label and last_seen_date < week_label:
            trend_status = "WEAKENING"
        else:
            trend_status = "NEW" if week_mentions > 0 else "WEAKENING"

        trends.append(
            NarrativeTrend(
                id=str(item["id"]),
                name=str(item["name"]),
                thesis=str(item["thesis"]),
                extracted_status=str(item["extracted_status"]),
                trend_status=trend_status,
                importance_score=int(item["importance_score"]),
                mention_count=int(item["mention_count"]),
                first_seen_date=first_seen_date,
                last_seen_date=last_seen_date,
                week_mentions=week_mentions,
            )
        )

    trends.sort(key=lambda trend: (-trend.mention_count, -trend.importance_score, trend.name.lower()))
    return trends


def narrative_trends_as_dicts(trends: list[NarrativeTrend]) -> list[dict[str, object]]:
    """Convert trend models into plain dicts."""

    return [asdict(trend) for trend in trends]
