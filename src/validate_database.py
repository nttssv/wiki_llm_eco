"""Validate database integrity and required content fields."""

from __future__ import annotations

from dataclasses import dataclass
import argparse

from .db import NarrativeDatabase
from .paths import DB_PATH


@dataclass(slots=True)
class ValidationIssue:
    check: str
    detail: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate the narrative SQLite database.")
    return parser.parse_args()


def validate_required_article_fields(database: NarrativeDatabase) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    rows = database.fetch_all(
        """
        SELECT id, title, source, published_date, summary
        FROM articles
        WHERE TRIM(COALESCE(title, '')) = ''
           OR TRIM(COALESCE(source, '')) = ''
           OR TRIM(COALESCE(published_date, '')) = ''
           OR TRIM(COALESCE(summary, '')) = ''
        ORDER BY id ASC
        """
    )
    for row in rows:
        missing_fields = [
            field_name
            for field_name in ("title", "source", "published_date", "summary")
            if not str(row[field_name] or "").strip()
        ]
        issues.append(
            ValidationIssue(
                check="required_article_fields",
                detail=f"article_id={row['id']} missing {', '.join(missing_fields)}",
            )
        )
    return issues


def validate_event_article_links(database: NarrativeDatabase) -> list[ValidationIssue]:
    rows = database.fetch_all(
        """
        SELECT e.id, e.article_id
        FROM events AS e
        LEFT JOIN articles AS a ON a.id = e.article_id
        WHERE a.id IS NULL
        ORDER BY e.id ASC
        """
    )
    return [
        ValidationIssue(
            check="event_article_link",
            detail=f"event_id={row['id']} references missing article_id={row['article_id']}",
        )
        for row in rows
    ]


def validate_graph_edges(database: NarrativeDatabase) -> list[ValidationIssue]:
    rows = database.fetch_all(
        """
        SELECT id, source_node, relationship, target_node
        FROM graph_edges
        WHERE TRIM(COALESCE(source_node, '')) = ''
           OR TRIM(COALESCE(relationship, '')) = ''
           OR TRIM(COALESCE(target_node, '')) = ''
        ORDER BY id ASC
        """
    )
    issues: list[ValidationIssue] = []
    for row in rows:
        missing_fields = [
            field_name
            for field_name in ("source_node", "relationship", "target_node")
            if not str(row[field_name] or "").strip()
        ]
        issues.append(
            ValidationIssue(
                check="graph_edge_fields",
                detail=f"graph_edge_id={row['id']} missing {', '.join(missing_fields)}",
            )
        )
    return issues


def validate_duplicate_article_ids(database: NarrativeDatabase) -> list[ValidationIssue]:
    rows = database.fetch_all(
        """
        SELECT id, COUNT(*) AS duplicate_count
        FROM articles
        GROUP BY id
        HAVING COUNT(*) > 1
        ORDER BY id ASC
        """
    )
    return [
        ValidationIssue(
            check="duplicate_article_id",
            detail=f"article_id={row['id']} appears {row['duplicate_count']} times",
        )
        for row in rows
    ]


def validate_entity_names(database: NarrativeDatabase) -> list[ValidationIssue]:
    rows = database.fetch_all(
        """
        SELECT id
        FROM entities
        WHERE TRIM(COALESCE(name, '')) = ''
        ORDER BY id ASC
        """
    )
    return [
        ValidationIssue(
            check="entity_name",
            detail=f"entity_id={row['id']} has an empty name",
        )
        for row in rows
    ]


def validate_narrative_theses(database: NarrativeDatabase) -> list[ValidationIssue]:
    rows = database.fetch_all(
        """
        SELECT id, name
        FROM narratives
        WHERE TRIM(COALESCE(thesis, '')) = ''
        ORDER BY id ASC
        """
    )
    return [
        ValidationIssue(
            check="narrative_thesis",
            detail=f"narrative_id={row['id']} ({row['name']}) has an empty thesis",
        )
        for row in rows
    ]


def run_validation(database: NarrativeDatabase) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    issues.extend(validate_required_article_fields(database))
    issues.extend(validate_event_article_links(database))
    issues.extend(validate_graph_edges(database))
    issues.extend(validate_duplicate_article_ids(database))
    issues.extend(validate_entity_names(database))
    issues.extend(validate_narrative_theses(database))
    return issues


def main() -> None:
    parse_args()

    if not DB_PATH.exists():
        raise FileNotFoundError(f"Database does not exist: {DB_PATH}")

    database = NarrativeDatabase(DB_PATH)
    database.initialize()

    try:
        issues = run_validation(database)
    finally:
        database.close()

    if issues:
        print(f"validation status: failed ({len(issues)} issues)")
        for issue in issues:
            print(f"- [{issue.check}] {issue.detail}")
        raise SystemExit(1)

    print("validation status: passed")
    print("issues found: 0")


if __name__ == "__main__":
    main()

