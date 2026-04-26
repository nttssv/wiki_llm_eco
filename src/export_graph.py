"""Export graph-oriented data from SQLite."""

from __future__ import annotations

from dataclasses import dataclass
import argparse
import json
import re

from .db import NarrativeDatabase
from .date_utils import week_label_for_published_date
from .extract_article import stable_id
from .paths import DB_PATH, EXPORTS_DIR


@dataclass(slots=True)
class GraphNode:
    id: str
    label: str
    type: str
    detail: str = ""
    importance_score: int = 0
    linked_articles: int = 0
    mention_count: int = 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export the narrative graph.")
    parser.add_argument("--format", choices=["json"], default="json", help="Export format.")
    parser.add_argument("--week", help="Week label to export, for example 2026-04-26.")
    parser.add_argument("--debug-node", action="append", default=[], help="Print debug graph linkage for a node label.")
    return parser.parse_args()


def _normalise_label(value: str) -> str:
    return " ".join(value.strip().lower().split())


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


def _is_article_context_node(node: GraphNode) -> bool:
    return node.type in {"entity", "theme", "narrative"}


def _context_relationship_for_node_type(node_type: str) -> str:
    if node_type == "theme":
        return "HAS_THEME"
    if node_type == "narrative":
        return "HAS_NARRATIVE"
    return "MENTIONS_ENTITY"


def build_graph_payload(database: NarrativeDatabase, week_label: str) -> dict[str, object]:
    nodes_by_id: dict[str, GraphNode] = {}
    label_lookup: dict[str, str] = {}
    entity_id_aliases: dict[str, str] = {}
    theme_id_aliases: dict[str, str] = {}
    narrative_id_aliases: dict[str, str] = {}
    type_priority = {"article": 0, "narrative": 1, "theme": 2, "entity": 3}
    article_rows = {
        str(row["id"]): row
        for row in database.fetch_articles()
        if week_label_for_published_date(str(row["published_date"] or "")) == week_label
    }
    selected_article_ids = set(article_rows)
    entity_rows = {
        str(row["id"]): row
        for row in database.fetch_entities()
    }
    theme_rows = {
        str(row["id"]): row
        for row in database.fetch_themes()
    }
    narrative_rows = {
        str(row["id"]): row
        for row in database.fetch_narratives()
    }
    article_to_narrative_ids: dict[str, list[str]] = {}
    raw_entity_article_links: dict[str, set[str]] = {}
    entity_article_links: dict[str, set[str]] = {}
    raw_theme_article_links: dict[str, set[str]] = {}
    theme_article_links: dict[str, set[str]] = {}
    raw_narrative_article_links: dict[str, set[str]] = {}
    narrative_article_links: dict[str, set[str]] = {}

    def add_node(
        node_id: str,
        label: str,
        node_type: str,
        *,
        detail: str = "",
        importance_score: int = 0,
        linked_articles: int = 0,
        mention_count: int = 0,
    ) -> str:
        if node_id in nodes_by_id:
            return node_id

        normalized_label = _normalise_label(label)
        existing_id = label_lookup.get(normalized_label)
        if existing_id is not None and nodes_by_id[existing_id].type == node_type == "entity":
            existing_node = nodes_by_id[existing_id]
            if not existing_node.detail and detail:
                existing_node.detail = detail
            existing_node.importance_score = max(existing_node.importance_score, importance_score)
            existing_node.linked_articles = max(existing_node.linked_articles, linked_articles)
            existing_node.mention_count = max(existing_node.mention_count, mention_count)
            return existing_id

        nodes_by_id[node_id] = GraphNode(
            id=node_id,
            label=label,
            type=node_type,
            detail=detail,
            importance_score=importance_score,
            linked_articles=linked_articles,
            mention_count=mention_count,
        )

        if existing_id is None:
            label_lookup[normalized_label] = node_id
        else:
            existing_type = nodes_by_id[existing_id].type
            if type_priority[node_type] > type_priority[existing_type]:
                label_lookup[normalized_label] = node_id

        return node_id

    def resolve_or_create_node(label: str) -> str:
        normalized_label = _normalise_label(label)
        existing_id = label_lookup.get(normalized_label)
        if existing_id is not None:
            return existing_id
        return add_node(stable_id("entity", label), label, "entity")

    def article_narrative_sentence(article_id: str) -> str:
        narrative_ids = article_to_narrative_ids.get(article_id, [])
        if narrative_ids:
            ranked_narratives = sorted(
                (
                    narrative_rows[narrative_id]
                    for narrative_id in narrative_ids
                    if narrative_id in narrative_rows
                ),
                key=lambda row: int(row["importance_score"] or 0),
                reverse=True,
            )
            if ranked_narratives:
                return _first_sentence(str(ranked_narratives[0]["thesis"] or ""))

        article_row = article_rows.get(article_id)
        if article_row is None:
            return ""
        return _first_sentence(str(article_row["summary"] or ""))

    def article_title(article_id: str) -> str:
        article_row = article_rows.get(article_id)
        if article_row is None:
            return ""
        return str(article_row["title"] or "")

    for row in database.fetch_article_entities():
        entity_id = str(row["entity_id"])
        article_id = str(row["article_id"])
        if article_id not in selected_article_ids:
            continue
        raw_entity_article_links.setdefault(entity_id, set()).add(article_id)

    for row in database.fetch_article_themes():
        theme_id = str(row["theme_id"])
        article_id = str(row["article_id"])
        if article_id not in selected_article_ids:
            continue
        raw_theme_article_links.setdefault(theme_id, set()).add(article_id)

    for row in database.fetch_article_narratives():
        article_id = str(row["article_id"])
        narrative_id = str(row["narrative_id"])
        if article_id not in selected_article_ids:
            continue
        article_to_narrative_ids.setdefault(article_id, []).append(narrative_id)
        raw_narrative_article_links.setdefault(narrative_id, set()).add(article_id)

    for row in article_rows.values():
        add_node(
            str(row["id"]),
            str(row["title"]),
            "article",
            detail=_first_sentence(str(row["summary"] or "")),
            importance_score=int(row["importance_score"] or 0),
            linked_articles=1,
        )

    for row in entity_rows.values():
        entity_id = str(row["id"])
        canonical_id = add_node(
            entity_id,
            str(row["name"]),
            "entity",
            detail=_first_sentence(str(row["description"] or "")),
            linked_articles=len(raw_entity_article_links.get(entity_id, set())),
        )
        entity_id_aliases[entity_id] = canonical_id
        entity_article_links.setdefault(canonical_id, set()).update(raw_entity_article_links.get(entity_id, set()))

    for canonical_id, article_ids in entity_article_links.items():
        if canonical_id in nodes_by_id:
            nodes_by_id[canonical_id].linked_articles = len(article_ids)

    for row in theme_rows.values():
        theme_id = str(row["id"])
        canonical_id = add_node(
            theme_id,
            str(row["name"]),
            "theme",
            detail=_first_sentence(str(row["description"] or "")),
            linked_articles=len(raw_theme_article_links.get(theme_id, set())),
        )
        theme_id_aliases[theme_id] = canonical_id
        theme_article_links.setdefault(canonical_id, set()).update(raw_theme_article_links.get(theme_id, set()))

    for canonical_id, article_ids in theme_article_links.items():
        if canonical_id in nodes_by_id:
            nodes_by_id[canonical_id].linked_articles = len(article_ids)

    for row in narrative_rows.values():
        narrative_id = str(row["id"])
        canonical_id = add_node(
            narrative_id,
            str(row["name"]),
            "narrative",
            detail=_first_sentence(str(row["thesis"] or "")),
            importance_score=int(row["importance_score"] or 0),
            linked_articles=len(raw_narrative_article_links.get(narrative_id, set())),
            mention_count=int(row["mention_count"] or 0),
        )
        narrative_id_aliases[narrative_id] = canonical_id
        narrative_article_links.setdefault(canonical_id, set()).update(raw_narrative_article_links.get(narrative_id, set()))

    for canonical_id, article_ids in narrative_article_links.items():
        if canonical_id in nodes_by_id:
            nodes_by_id[canonical_id].linked_articles = len(article_ids)

    edges: list[dict[str, object]] = []
    existing_article_context_edges: set[tuple[str, str]] = set()

    def ensure_article_context_edge(article_id: str, node_id: str, confidence: float = 0.9) -> None:
        node = nodes_by_id.get(node_id)
        if not article_id or node is None or not _is_article_context_node(node):
            return

        if node.type == "entity":
            entity_article_links.setdefault(node_id, set()).add(article_id)
        elif node.type == "theme":
            theme_article_links.setdefault(node_id, set()).add(article_id)
        elif node.type == "narrative":
            narrative_article_links.setdefault(node_id, set()).add(article_id)

        edge_key = (article_id, node_id)
        if edge_key in existing_article_context_edges:
            return

        edges.append(
            {
                "source": article_id,
                "target": node_id,
                "relationship": _context_relationship_for_node_type(node.type),
                "evidence_article_id": article_id,
                "confidence": confidence,
                "evidence_title": article_title(article_id),
                "narrative_sentence": article_narrative_sentence(article_id),
            }
        )
        existing_article_context_edges.add(edge_key)

    for row in database.fetch_article_entities():
        article_id = str(row["article_id"])
        if article_id not in selected_article_ids:
            continue
        entity_id = entity_id_aliases.get(str(row["entity_id"]), str(row["entity_id"]))
        ensure_article_context_edge(article_id, entity_id, confidence=1.0)

    for row in database.fetch_article_themes():
        article_id = str(row["article_id"])
        if article_id not in selected_article_ids:
            continue
        theme_id = theme_id_aliases.get(str(row["theme_id"]), str(row["theme_id"]))
        ensure_article_context_edge(article_id, theme_id, confidence=1.0)

    for row in database.fetch_article_narratives():
        article_id = str(row["article_id"])
        if article_id not in selected_article_ids:
            continue
        narrative_id = narrative_id_aliases.get(str(row["narrative_id"]), str(row["narrative_id"]))
        ensure_article_context_edge(article_id, narrative_id, confidence=1.0)

    for row in database.fetch_graph_edges():
        evidence_article_id = str(row["evidence_article_id"] or "")
        if evidence_article_id not in selected_article_ids:
            continue
        source_id = resolve_or_create_node(row["source_node"])
        target_id = resolve_or_create_node(row["target_node"])

        if evidence_article_id:
            ensure_article_context_edge(evidence_article_id, source_id)
            ensure_article_context_edge(evidence_article_id, target_id)

        edges.append(
            {
                "source": source_id,
                "target": target_id,
                "relationship": row["relationship"],
                "evidence_article_id": evidence_article_id,
                "confidence": row["confidence"],
                "evidence_title": article_title(evidence_article_id),
                "narrative_sentence": article_narrative_sentence(evidence_article_id),
            }
        )

    for node in nodes_by_id.values():
        if node.type == "entity":
            node.linked_articles = len(entity_article_links.get(node.id, set()))
        elif node.type == "theme":
            node.linked_articles = len(theme_article_links.get(node.id, set()))
        elif node.type == "narrative":
            node.linked_articles = len(narrative_article_links.get(node.id, set()))

    node_payload = [
        {
            "id": node.id,
            "label": node.label,
            "type": node.type,
                "detail": node.detail,
                "importance_score": node.importance_score,
                "linked_articles": node.linked_articles,
                "mention_count": node.mention_count,
                "week": week_label,
            }
            for node in sorted(nodes_by_id.values(), key=lambda item: (item.type, item.label.lower()))
            if not (node.type == "entity" and node.linked_articles == 0)
    ]

    edge_payload = [
        {
            **edge,
            "week": week_label,
        }
        for edge in edges
    ]

    return {"week": week_label, "nodes": node_payload, "edges": edge_payload}


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


def main() -> None:
    args = parse_args()

    if args.format != "json":
        raise ValueError(f"Unsupported export format: {args.format}")

    if not DB_PATH.exists():
        raise FileNotFoundError(f"Database does not exist: {DB_PATH}")

    database = NarrativeDatabase(DB_PATH)
    database.initialize()

    try:
        available_weeks = sorted(
            {
                week_value
                for row in database.fetch_articles()
                for week_value in [week_label_for_published_date(str(row["published_date"] or ""))]
                if week_value is not None
            }
        )
        if not available_weeks:
            raise ValueError("No week-labeled articles available for graph export.")
        selected_week = args.week or available_weeks[-1]
        payload = build_graph_payload(database, selected_week)
    finally:
        database.close()

    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
    output_path = EXPORTS_DIR / f"graph_{selected_week}.json"
    latest_path = EXPORTS_DIR / "graph_latest.json"
    compatibility_path = EXPORTS_DIR / "graph.json"
    serialized_payload = json.dumps(payload, indent=2, ensure_ascii=False)
    output_path.write_text(serialized_payload, encoding="utf-8")
    latest_path.write_text(serialized_payload, encoding="utf-8")
    compatibility_path.write_text(serialized_payload, encoding="utf-8")

    for debug_label in args.debug_node:
        print_debug_node(payload, debug_label)

    print(f"graph export written: {output_path}")
    print(f"graph latest written: {latest_path}")
    print(f"nodes exported: {len(payload['nodes'])}")
    print(f"edges exported: {len(payload['edges'])}")


if __name__ == "__main__":
    main()
