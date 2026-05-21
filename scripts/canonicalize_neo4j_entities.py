#!/usr/bin/env python
"""Canonicalize duplicate Neo4j Entity nodes by normalized display name."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config import get_neo4j_database  # noqa: E402
from src.neo4j_store import canonical_entity_id, neo4j_driver  # noqa: E402


def _key(value: str) -> str:
    return " ".join(str(value or "").casefold().split())


def _fetch_entities(session: Any) -> list[dict[str, Any]]:
    return session.run(
        """
        MATCH (e:Entity)
        OPTIONAL MATCH (a:Article)-[:MENTIONS]->(e)
        WITH e, count(DISTINCT a) AS article_mentions
        OPTIONAL MATCH (e)-[rel]-()
        RETURN
            e.id AS id,
            coalesce(e.name, '') AS name,
            coalesce(e.type, '') AS type,
            coalesce(e.description, '') AS description,
            coalesce(e.canonical_key, '') AS canonical_key,
            article_mentions AS article_mentions,
            count(rel) AS degree
        ORDER BY name ASC, id ASC
        """
    ).data()


def _group_entities(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        key = _key(str(row.get("canonical_key") or row.get("name") or ""))
        if not key:
            continue
        groups.setdefault(key, []).append(row)
    return groups


def _choose_winner(nodes: list[dict[str, Any]], canonical_id: str) -> dict[str, Any]:
    for node in nodes:
        if str(node.get("id") or "") == canonical_id:
            return node
    return sorted(
        nodes,
        key=lambda node: (
            -int(node.get("article_mentions") or 0),
            str(node.get("type") or "").casefold() == "inferred",
            not bool(str(node.get("description") or "").strip()),
            -int(node.get("degree") or 0),
            str(node.get("id") or ""),
        ),
    )[0]


def _best_metadata(nodes: list[dict[str, Any]], winner: dict[str, Any], key: str) -> dict[str, str]:
    name = str(winner.get("name") or "").strip()
    if not name:
        name = next((str(node.get("name") or "").strip() for node in nodes if str(node.get("name") or "").strip()), key)
    node_type = str(winner.get("type") or "").strip()
    if not node_type or node_type.casefold() == "inferred":
        node_type = next(
            (
                str(node.get("type") or "").strip()
                for node in nodes
                if str(node.get("type") or "").strip()
                and str(node.get("type") or "").strip().casefold() != "inferred"
            ),
            node_type,
        )
    description = str(winner.get("description") or "").strip()
    if not description:
        description = next(
            (str(node.get("description") or "").strip() for node in nodes if str(node.get("description") or "").strip()),
            "",
        )
    return {
        "name": name,
        "type": node_type,
        "description": description,
        "canonical_key": key,
    }


def _set_winner_metadata(
    session: Any,
    *,
    winner_id: str,
    canonical_id: str,
    metadata: dict[str, str],
) -> str:
    session.run(
        """
        MATCH (winner:Entity {id: $winner_id})
        SET winner.id = $canonical_id,
            winner.name = $name,
            winner.type = $type,
            winner.description = $description,
            winner.canonical_key = $canonical_key
        """,
        winner_id=winner_id,
        canonical_id=canonical_id,
        **metadata,
    ).consume()
    return canonical_id


def _move_duplicate_relationships(session: Any, *, duplicate_id: str, winner_id: str, group_ids: list[str]) -> None:
    statements = [
        """
        MATCH (dup:Entity {id: $duplicate_id}), (win:Entity {id: $winner_id})
        MATCH (a:Article)-[r:MENTIONS]->(dup)
        MERGE (a)-[nr:MENTIONS]->(win)
        SET nr += properties(r)
        DELETE r
        """,
        """
        MATCH (dup:Entity {id: $duplicate_id}), (win:Entity {id: $winner_id})
        MATCH (c:Chunk)-[r:MENTIONS]->(dup)
        MERGE (c)-[nr:MENTIONS]->(win)
        SET nr += properties(r)
        DELETE r
        """,
        """
        MATCH (dup:Entity {id: $duplicate_id}), (win:Entity {id: $winner_id})
        MATCH (a:Article)-[r:EVIDENCES]->(dup)
        MERGE (a)-[nr:EVIDENCES]->(win)
        SET nr += properties(r)
        DELETE r
        """,
        """
        MATCH (dup:Entity {id: $duplicate_id}), (win:Entity {id: $winner_id})
        MATCH (claim:Claim)-[r:ABOUT]->(dup)
        MERGE (claim)-[nr:ABOUT]->(win)
        SET nr += properties(r)
        DELETE r
        """,
        """
        MATCH (dup:Entity {id: $duplicate_id}), (win:Entity {id: $winner_id})
        MATCH (dup)-[r:RELATES_TO]->(target:Entity)
        WHERE NOT target.id IN $group_ids
        MERGE (win)-[nr:RELATES_TO {id: r.id}]->(target)
        SET nr += properties(r)
        DELETE r
        """,
        """
        MATCH (dup:Entity {id: $duplicate_id}), (win:Entity {id: $winner_id})
        MATCH (source:Entity)-[r:RELATES_TO]->(dup)
        WHERE NOT source.id IN $group_ids
        MERGE (source)-[nr:RELATES_TO {id: r.id}]->(win)
        SET nr += properties(r)
        DELETE r
        """,
        """
        MATCH (dup:Entity {id: $duplicate_id})
        DETACH DELETE dup
        """,
    ]
    for statement in statements:
        session.run(
            statement,
            duplicate_id=duplicate_id,
            winner_id=winner_id,
            group_ids=group_ids,
        ).consume()


def canonicalize(*, dry_run: bool = False) -> dict[str, Any]:
    with neo4j_driver() as driver:
        driver.verify_connectivity()
        with driver.session(database=get_neo4j_database()) as session:
            rows = _fetch_entities(session)
            groups = _group_entities(rows)
            planned_groups: list[dict[str, Any]] = []
            conflicts: list[dict[str, str]] = []
            all_ids_by_key = {
                str(node.get("id") or ""): key
                for key, nodes in groups.items()
                for node in nodes
            }

            for key, nodes in sorted(groups.items()):
                metadata_source = max(nodes, key=lambda node: int(node.get("article_mentions") or 0))
                canonical_id = canonical_entity_id(str(metadata_source.get("name") or key))
                needs_id_update = any(str(node.get("id") or "") != canonical_id for node in nodes)
                if len(nodes) <= 1 and not needs_id_update:
                    continue
                conflicting_key = all_ids_by_key.get(canonical_id)
                if conflicting_key is not None and conflicting_key != key:
                    conflicts.append({"canonical_key": key, "canonical_id": canonical_id, "conflicting_key": conflicting_key})
                    continue
                winner = _choose_winner(nodes, canonical_id)
                metadata = _best_metadata(nodes, winner, key)
                planned_groups.append(
                    {
                        "canonical_key": key,
                        "canonical_id": canonical_id,
                        "winner_id": str(winner.get("id") or ""),
                        "duplicate_ids": [
                            str(node.get("id") or "")
                            for node in nodes
                            if str(node.get("id") or "") != str(winner.get("id") or "")
                        ],
                        "metadata": metadata,
                    }
                )

            if conflicts:
                return {
                    "dry_run": dry_run,
                    "passed": False,
                    "groups_planned": len(planned_groups),
                    "conflicts": conflicts,
                    "error": "Canonical id conflicts found; no changes applied.",
                }

            if not dry_run:
                for group in planned_groups:
                    winner_id = _set_winner_metadata(
                        session,
                        winner_id=group["winner_id"],
                        canonical_id=group["canonical_id"],
                        metadata=group["metadata"],
                    )
                    group_ids = [winner_id, *group["duplicate_ids"]]
                    for duplicate_id in group["duplicate_ids"]:
                        _move_duplicate_relationships(
                            session,
                            duplicate_id=duplicate_id,
                            winner_id=winner_id,
                            group_ids=group_ids,
                        )

            duplicate_groups_after = session.run(
                """
                MATCH (e:Entity)
                WITH coalesce(e.canonical_key, toLower(trim(e.name))) AS canonical_key, count(*) AS count
                WHERE canonical_key <> '' AND count > 1
                RETURN count(*) AS count
                """
            ).single()
            entities_after = session.run("MATCH (e:Entity) RETURN count(e) AS count").single()

    return {
        "dry_run": dry_run,
        "passed": True,
        "groups_planned": len(planned_groups),
        "duplicate_nodes_planned": sum(len(group["duplicate_ids"]) for group in planned_groups),
        "groups": planned_groups[:20],
        "groups_truncated": max(0, len(planned_groups) - 20),
        "duplicate_groups_after": int(duplicate_groups_after["count"] or 0) if duplicate_groups_after else 0,
        "entities_after": int(entities_after["count"] or 0) if entities_after else 0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Canonicalize duplicate Neo4j Entity nodes.")
    parser.add_argument("--dry-run", action="store_true", help="Show planned entity merges without applying them.")
    args = parser.parse_args()

    result = canonicalize(dry_run=args.dry_run)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    if not result.get("passed"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
