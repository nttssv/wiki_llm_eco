"""Helpers for parsing article dates and grouping them into weeks."""

from __future__ import annotations

from datetime import date, datetime, timedelta


ARTICLE_DATE_FORMATS = (
    "%Y-%m-%d",
    "%d %b %Y",
    "%d %B %Y",
)


def parse_article_date(value: str) -> date | None:
    """Parse a published-date string into a date."""

    cleaned = " ".join(value.strip().split())
    if not cleaned:
        return None

    for fmt in ARTICLE_DATE_FORMATS:
        try:
            return datetime.strptime(cleaned, fmt).date()
        except ValueError:
            continue

    return None


def parse_week_label(value: str) -> date:
    """Parse a CLI week label like 2026-04-26."""

    return datetime.strptime(value, "%Y-%m-%d").date()


def week_end_for_date(value: date) -> date:
    """Return the Sunday-ending week label for a date."""

    return value + timedelta(days=(6 - value.weekday()))


def week_bounds(week_label: str) -> tuple[date, date]:
    """Return inclusive Monday-Sunday bounds for a week label."""

    week_end = parse_week_label(week_label)
    return week_end - timedelta(days=6), week_end


def week_label_for_published_date(value: str) -> str | None:
    """Convert an article published date into a week label."""

    parsed = parse_article_date(value)
    if parsed is None:
        return None
    return week_end_for_date(parsed).isoformat()


def is_date_in_week(value: str, week_label: str) -> bool:
    """Check whether a published date falls inside a target week."""

    parsed = parse_article_date(value)
    if parsed is None:
        return False
    start_date, end_date = week_bounds(week_label)
    return start_date <= parsed <= end_date

