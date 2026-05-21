"""Export graph-oriented data from Neo4j."""

from __future__ import annotations

import argparse
import json
import re
from typing import Any

from .config import get_neo4j_database
from .neo4j_store import neo4j_driver
from .paths import EXPORTS_DIR


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export the Neo4j narrative graph.")
    parser.add_argument("--format", choices=["json"], default="json", help="Export format.")
    parser.add_argument("--week", help="Week label to export, for example 2026-04-26.")
    parser.add_argument("--debug-node", action="append", default=[], help="Print debug graph linkage for a node label.")
    return parser.parse_args()


def _first_sentence(value: str) -> str:
    text = " ".join(str(value or "").strip().split())
    if not text:
        return ""

    abbreviation_pattern = re.compile(r"(?:\b[A-Z]\.){2,}$")
    common_abbreviations = ("e.g.", "i.e.", "etc.")

    for index, character in enumerate(text):
        next_character = text[index + 1] if index + 1 < len(text) else ""
        if character in ".!?" and (not next_character or next_character.isspace()):
            snippet = text[: index + 1]
            if abbreviation_pattern.search(snippet) or snippet.endswith(common_abbreviations):
                continue
            return text[: index + 1].strip()

    return text


def print_debug_node(payload: dict[str, list[dict[str, object]]], debug_label: str) -> None:
    label_value = debug_label.strip().lower()
    nodes = [node for node in payload["nodes"] if str(node.get("label", "")).strip().lower() == label_value]
    print(f"debug_label={debug_label}")
    print(f"matched_nodes={len(nodes)}")
    for node in nodes:
        print(f"canonical_node_id={node['id']}")
        print(f"readable_label={node['label']}")
        linked_article_ids = sorted(
            {
                str(edge["source"])
                for edge in payload["edges"]
                if str(edge.get("target")) == str(node["id"]) and str(edge.get("source", "")).startswith("article_")
            }
        )
        print(f"linked_article_ids={linked_article_ids}")
        print(f"linked_article_titles={[edge.get('evidence_title', '') for edge in payload['edges'] if str(edge.get('target')) == str(node['id']) and str(edge.get('source', '')).startswith('article_')]}")
        print("visible_edges=")
        for edge in payload["edges"]:
            if str(edge.get("source")) == str(node["id"]) or str(edge.get("target")) == str(node["id"]):
                print(edge)


def list_available_weeks(session: Any) -> list[str]:
    """Return article weeks available in Neo4j."""

    rows = session.run(
        """
        MATCH (a:Article)
        WHERE coalesce(a.week, '') <> ''
        RETURN DISTINCT a.week AS week
        ORDER BY week ASC
        """
    ).data()
    return [str(row["week"]) for row in rows if row.get("week")]


def _node_type(labels: list[str]) -> str:
    if "Theme" in labels:
        return "theme"
    if "Narrative" in labels:
        return "narrative"
    return "entity"


def _normalise_label(value: str) -> str:
    return " ".join(value.strip().lower().split())


def _context_relationship_for_node_type(node_type: str) -> str:
    if node_type == "theme":
        return "HAS_THEME"
    if node_type == "narrative":
        return "HAS_NARRATIVE"
    return "MENTIONS_ENTITY"


def build_graph_payload(session: Any, week_label: str) -> dict[str, Any]:
    """Build an export-compatible graph snapshot from Neo4j."""

    article_rows = session.run(
        """
        MATCH (a:Article {week: $week})
        RETURN
            a.id AS id,
            coalesce(a.title, '') AS title,
            coalesce(a.summary, '') AS summary,
            coalesce(a.importance_score, 0) AS importance_score,
            coalesce(a.published_date, '') AS published_date
        ORDER BY published_date DESC, title ASC
        """,
        week=week_label,
    ).data()
    article_ids = [str(row["id"]) for row in article_rows if row.get("id")]

    context_rows = session.run(
        """
        MATCH (a:Article)-[r:MENTIONS|HAS_THEME|HAS_NARRATIVE]->(node)
        WHERE a.id IN $article_ids
        RETURN
            a.id AS article_id,
            coalesce(a.title, '') AS article_title,
            coalesce(a.summary, '') AS article_summary,
            type(r) AS relationship,
            labels(node) AS labels,
            node.id AS node_id,
            coalesce(node.name, node.title, node.id) AS label,
            coalesce(node.description, node.thesis, '') AS detail,
            coalesce(node.importance_score, 0) AS importance_score,
            coalesce(node.mention_count, 0) AS mention_count
        ORDER BY article_title ASC, label ASC
        """,
        article_ids=article_ids,
    ).data() if article_ids else []

    relationship_rows = session.run(
        """
        MATCH (source:Entity)-[rel:RELATES_TO]->(target:Entity)
        MATCH (article:Article {id: rel.evidence_article_id})
        WHERE article.id IN $article_ids
        RETURN
            source.id AS source_id,
            coalesce(source.name, '') AS source_label,
            coalesce(source.description, '') AS source_detail,
            target.id AS target_id,
            coalesce(target.name, '') AS target_label,
            coalesce(target.description, '') AS target_detail,
            coalesce(rel.relationship, 'RELATES_TO') AS relationship,
            coalesce(rel.confidence, 0.0) AS confidence,
            article.id AS article_id,
            coalesce(article.title, '') AS article_title,
            coalesce(article.summary, '') AS article_summary
        ORDER BY article.published_date DESC, confidence DESC
        """,
        article_ids=article_ids,
    ).data() if article_ids else []

    nodes_by_id: dict[str, dict[str, Any]] = {}
    linked_articles_by_node: dict[str, set[str]] = {}
    entity_label_lookup: dict[str, str] = {}
    edges: list[dict[str, Any]] = []
    seen_edges: set[tuple[str, str, str, str]] = set()

    def add_edge(edge: dict[str, Any]) -> None:
        key = (
            str(edge.get("source", "")),
            str(edge.get("target", "")),
            str(edge.get("relationship", "")),
            str(edge.get("evidence_article_id", "")),
        )
        if key in seen_edges:
            return
        edges.append(edge)
        seen_edges.add(key)

    def add_related_node(
        node_id: str,
        label: str,
        node_type: str,
        detail: str,
        *,
        importance_score: int = 0,
        mention_count: int = 0,
    ) -> str | None:
        if not node_id:
            return None
        canonical_id = node_id
        if node_type == "entity":
            normalized_label = _normalise_label(label or node_id)
            existing_id = entity_label_lookup.get(normalized_label)
            if existing_id:
                canonical_id = existing_id
            else:
                entity_label_lookup[normalized_label] = node_id
        existing = nodes_by_id.get(canonical_id)
        if existing:
            existing["detail"] = existing["detail"] or detail or ""
            existing["importance_score"] = max(int(existing.get("importance_score") or 0), int(importance_score or 0))
            existing["mention_count"] = max(int(existing.get("mention_count") or 0), int(mention_count or 0))
            existing["linked_articles"] = len(linked_articles_by_node.get(canonical_id, set()))
            return canonical_id
        nodes_by_id[canonical_id] = {
            "id": canonical_id,
            "label": label or canonical_id,
            "type": node_type,
            "detail": _first_sentence(detail or ""),
            "importance_score": int(importance_score or 0),
            "linked_articles": len(linked_articles_by_node.get(canonical_id, set())),
            "mention_count": int(mention_count or 0),
            "week": week_label,
        }
        return canonical_id

    for row in article_rows:
        article_id = str(row.get("id") or "")
        if not article_id:
            continue
        nodes_by_id[article_id] = {
            "id": article_id,
            "label": str(row.get("title") or article_id),
            "type": "article",
            "detail": _first_sentence(str(row.get("summary") or "")),
            "importance_score": int(row.get("importance_score") or 0),
            "linked_articles": 1,
            "mention_count": 0,
            "week": week_label,
        }

    for row in context_rows:
        article_id = str(row.get("article_id") or "")
        node_id = str(row.get("node_id") or "")
        labels = row.get("labels") if isinstance(row.get("labels"), list) else []
        node_type = _node_type(labels)
        canonical_node_id = add_related_node(
            node_id,
            str(row.get("label") or ""),
            node_type,
            str(row.get("detail") or ""),
            importance_score=int(row.get("importance_score") or 0),
            mention_count=int(row.get("mention_count") or 0),
        )
        if article_id and canonical_node_id:
            linked_articles_by_node.setdefault(canonical_node_id, set()).add(article_id)
            nodes_by_id[canonical_node_id]["linked_articles"] = len(linked_articles_by_node[canonical_node_id])
            add_edge(
                {
                    "source": article_id,
                    "target": canonical_node_id,
                    "relationship": _context_relationship_for_node_type(node_type),
                    "evidence_article_id": article_id,
                    "confidence": 1.0,
                    "evidence_title": str(row.get("article_title") or ""),
                    "narrative_sentence": _first_sentence(str(row.get("article_summary") or "")),
                    "week": week_label,
                }
            )

    for row in relationship_rows:
        article_id = str(row.get("article_id") or "")
        source_raw_id = str(row.get("source_id") or "")
        target_raw_id = str(row.get("target_id") or "")
        source_id = add_related_node(
            source_raw_id,
            str(row.get("source_label") or ""),
            "entity",
            str(row.get("source_detail") or ""),
        )
        target_id = add_related_node(
            target_raw_id,
            str(row.get("target_label") or ""),
            "entity",
            str(row.get("target_detail") or ""),
        )
        if article_id and source_id:
            linked_articles_by_node.setdefault(source_id, set()).add(article_id)
            nodes_by_id[source_id]["linked_articles"] = len(linked_articles_by_node[source_id])
        if article_id and target_id:
            linked_articles_by_node.setdefault(target_id, set()).add(article_id)
            nodes_by_id[target_id]["linked_articles"] = len(linked_articles_by_node[target_id])
        if source_id and target_id:
            relationship = str(row.get("relationship") or "RELATES_TO")
            add_edge(
                {
                    "source": source_id,
                    "target": target_id,
                    "relationship": relationship,
                    "evidence_article_id": article_id,
                    "confidence": float(row.get("confidence") or 0.0),
                    "evidence_title": str(row.get("article_title") or ""),
                    "narrative_sentence": (
                        f"{row.get('source_label') or source_id} --{relationship}--> "
                        f"{row.get('target_label') or target_id}"
                    ),
                    "week": week_label,
                }
            )

    for node_id, article_ids_for_node in linked_articles_by_node.items():
        if node_id in nodes_by_id:
            nodes_by_id[node_id]["linked_articles"] = len(article_ids_for_node)

    nodes = sorted(
        nodes_by_id.values(),
        key=lambda node: (str(node.get("type", "")), str(node.get("label", "")).lower()),
    )
    return {"week": week_label, "nodes": nodes, "edges": edges}


def write_graph_payload(payload: dict[str, Any], week_label: str) -> tuple[Any, Any, Any]:
    """Write week-specific, latest, and compatibility JSON exports."""

    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
    output_path = EXPORTS_DIR / f"graph_{week_label}.json"
    latest_path = EXPORTS_DIR / "graph_latest.json"
    compatibility_path = EXPORTS_DIR / "graph.json"
    serialized_payload = json.dumps(payload, indent=2, ensure_ascii=False)
    output_path.write_text(serialized_payload, encoding="utf-8")
    latest_path.write_text(serialized_payload, encoding="utf-8")
    compatibility_path.write_text(serialized_payload, encoding="utf-8")
    return output_path, latest_path, compatibility_path


def main() -> None:
    args = parse_args()
    if args.format != "json":
        raise ValueError(f"Unsupported export format: {args.format}")

    with neo4j_driver() as driver:
        driver.verify_connectivity()
        with driver.session(database=get_neo4j_database()) as session:
            available_weeks = list_available_weeks(session)
            if not available_weeks:
                raise ValueError("No week-labeled articles available for Neo4j graph export.")
            selected_week = args.week or available_weeks[-1]
            if selected_week not in available_weeks:
                raise ValueError(f"Week not found in Neo4j: {selected_week}")
            payload = build_graph_payload(session, selected_week)

    output_path, latest_path, _compatibility_path = write_graph_payload(payload, selected_week)

    for debug_label in args.debug_node:
        print_debug_node(payload, debug_label)

    print(f"neo4j graph export written: {output_path}")
    print(f"graph latest written: {latest_path}")
    print(f"nodes exported: {len(payload['nodes'])}")
    print(f"edges exported: {len(payload['edges'])}")


if __name__ == "__main__":
    main()
