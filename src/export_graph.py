"""Export graph-oriented data from SQLite."""

from __future__ import annotations

from dataclasses import dataclass
import argparse
import json

from .db import NarrativeDatabase
from .extract_article import stable_id
from .paths import DB_PATH, EXPORTS_DIR


@dataclass(slots=True)
class GraphNode:
    id: str
    label: str
    type: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export the narrative graph.")
    parser.add_argument("--format", choices=["json"], default="json", help="Export format.")
    return parser.parse_args()


def _normalise_label(value: str) -> str:
    return " ".join(value.strip().lower().split())


def build_graph_payload(database: NarrativeDatabase) -> dict[str, list[dict[str, object]]]:
    nodes_by_id: dict[str, GraphNode] = {}
    label_lookup: dict[str, str] = {}
    type_priority = {"article": 0, "narrative": 1, "theme": 2, "entity": 3}

    def add_node(node_id: str, label: str, node_type: str) -> str:
        if node_id in nodes_by_id:
            return node_id

        normalized_label = _normalise_label(label)
        existing_id = label_lookup.get(normalized_label)
        nodes_by_id[node_id] = GraphNode(id=node_id, label=label, type=node_type)

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

    for row in database.fetch_articles():
        add_node(row["id"], row["title"], "article")

    for row in database.fetch_entities():
        add_node(row["id"], row["name"], "entity")

    for row in database.fetch_themes():
        add_node(row["id"], row["name"], "theme")

    for row in database.fetch_narratives():
        add_node(row["id"], row["name"], "narrative")

    edges: list[dict[str, object]] = []

    for row in database.fetch_article_entities():
        role_slug = str(row["role"] or "").strip().upper().replace(" ", "_") or "ENTITY"
        edges.append(
            {
                "source": row["article_id"],
                "target": row["entity_id"],
                "relationship": f"MENTIONS_{role_slug}",
                "evidence_article_id": row["article_id"],
                "confidence": 1.0,
            }
        )

    for row in database.fetch_article_themes():
        edges.append(
            {
                "source": row["article_id"],
                "target": row["theme_id"],
                "relationship": "HAS_THEME",
                "evidence_article_id": row["article_id"],
                "confidence": 1.0,
            }
        )

    for row in database.fetch_article_narratives():
        edges.append(
            {
                "source": row["article_id"],
                "target": row["narrative_id"],
                "relationship": "HAS_NARRATIVE",
                "evidence_article_id": row["article_id"],
                "confidence": 1.0,
            }
        )

    for row in database.fetch_graph_edges():
        source_id = resolve_or_create_node(row["source_node"])
        target_id = resolve_or_create_node(row["target_node"])
        edges.append(
            {
                "source": source_id,
                "target": target_id,
                "relationship": row["relationship"],
                "evidence_article_id": row["evidence_article_id"],
                "confidence": row["confidence"],
            }
        )

    node_payload = [
        {"id": node.id, "label": node.label, "type": node.type}
        for node in sorted(nodes_by_id.values(), key=lambda item: (item.type, item.label.lower()))
    ]

    return {"nodes": node_payload, "edges": edges}


def main() -> None:
    args = parse_args()

    if args.format != "json":
        raise ValueError(f"Unsupported export format: {args.format}")

    if not DB_PATH.exists():
        raise FileNotFoundError(f"Database does not exist: {DB_PATH}")

    database = NarrativeDatabase(DB_PATH)
    database.initialize()

    try:
        payload = build_graph_payload(database)
    finally:
        database.close()

    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
    output_path = EXPORTS_DIR / "graph.json"
    output_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"graph export written: {output_path}")
    print(f"nodes exported: {len(payload['nodes'])}")
    print(f"edges exported: {len(payload['edges'])}")


if __name__ == "__main__":
    main()
