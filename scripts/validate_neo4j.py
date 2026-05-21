#!/usr/bin/env python
"""Validate canonical Neo4j graph against processed extraction artifacts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.neo4j_store import neo4j_driver  # noqa: E402
from src.config import get_neo4j_database  # noqa: E402
from src.processed_registry import processed_article_records  # noqa: E402


def _count_neo4j(session: Any, query: str) -> int:
    row = session.run(query).single()
    return int(row["count"] or 0) if row else 0


def validate() -> dict[str, Any]:
    expected = {
        "articles": len(processed_article_records()),
    }

    with neo4j_driver() as driver:
        driver.verify_connectivity()
        with driver.session(database=get_neo4j_database()) as session:
            actual = {
                "articles": _count_neo4j(session, "MATCH (n:Article) RETURN count(n) AS count"),
                "entities": _count_neo4j(session, "MATCH (n:Entity) RETURN count(n) AS count"),
                "narratives": _count_neo4j(session, "MATCH (n:Narrative) RETURN count(n) AS count"),
                "article_narrative_links": _count_neo4j(
                    session,
                    "MATCH (:Article)-[r:HAS_NARRATIVE]->(:Narrative) RETURN count(r) AS count",
                ),
                "events": _count_neo4j(session, "MATCH (n:Event) RETURN count(n) AS count"),
                "graph_edges": _count_neo4j(session, "MATCH ()-[r:RELATES_TO]->() RETURN count(r) AS count"),
                "claims": _count_neo4j(session, "MATCH (n:Claim) RETURN count(n) AS count"),
                "chunks": _count_neo4j(session, "MATCH (n:Chunk) RETURN count(n) AS count"),
                "images": _count_neo4j(session, "MATCH (n:ImageAsset) RETURN count(n) AS count"),
                "weeks": _count_neo4j(session, "MATCH (n:Week) RETURN count(n) AS count"),
            }
            integrity = {
                "articles_missing_week": _count_neo4j(
                    session,
                    "MATCH (a:Article) WHERE NOT (a)-[:IN_WEEK]->(:Week) RETURN count(a) AS count",
                ),
                "relationships_missing_evidence_article": _count_neo4j(
                    session,
                    """
                    MATCH ()-[r:RELATES_TO]->()
                    WHERE coalesce(r.evidence_article_id, '') = ''
                       OR NOT EXISTS { MATCH (:Article {id: r.evidence_article_id}) }
                    RETURN count(r) AS count
                    """,
                ),
                "claims_missing_article": _count_neo4j(
                    session,
                    """
                    MATCH (claim:Claim)
                    WHERE coalesce(claim.article_id, '') = ''
                       OR NOT EXISTS { MATCH (:Article {id: claim.article_id}) }
                    RETURN count(claim) AS count
                    """,
                ),
                "narratives_missing_article": _count_neo4j(
                    session,
                    """
                    MATCH (n:Narrative)
                    WHERE NOT EXISTS { MATCH (:Article)-[:HAS_NARRATIVE]->(n) }
                    RETURN count(n) AS count
                    """,
                ),
                "duplicate_entity_canonical_keys": _count_neo4j(
                    session,
                    """
                    MATCH (e:Entity)
                    WITH coalesce(e.canonical_key, toLower(trim(e.name))) AS canonical_key, count(*) AS count
                    WHERE canonical_key <> '' AND count > 1
                    RETURN count(*) AS count
                    """,
                ),
                "entities_missing_canonical_key": _count_neo4j(
                    session,
                    """
                    MATCH (e:Entity)
                    WHERE coalesce(e.canonical_key, '') = ''
                    RETURN count(e) AS count
                    """,
                ),
            }

    failures: list[str] = []
    for key in ("articles",):
        if actual[key] != expected[key]:
            failures.append(f"{key}: expected {expected[key]}, got {actual[key]}")
    for key, value in integrity.items():
        if value:
            failures.append(f"{key}: {value}")

    return {
        "passed": not failures,
        "expected": expected,
        "actual": actual,
        "integrity": integrity,
        "failures": failures,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate canonical Neo4j graph.")
    parser.parse_args()

    result = validate()
    print(json.dumps(result, indent=2, ensure_ascii=False))
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
