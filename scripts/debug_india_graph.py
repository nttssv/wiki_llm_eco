from __future__ import annotations

import json
from pathlib import Path
import sys


GRAPH_PATH = Path("data/exports/graph.json")


def main() -> int:
    if not GRAPH_PATH.exists():
        print(f"missing graph export: {GRAPH_PATH}")
        return 1

    payload = json.loads(GRAPH_PATH.read_text(encoding="utf-8"))
    nodes = payload.get("nodes", [])
    edges = payload.get("edges", [])
    node_by_id = {str(node["id"]): node for node in nodes}

    india_nodes = [node for node in nodes if str(node.get("label", "")) == "India"]
    print(f"india_nodes={len(india_nodes)}")
    if len(india_nodes) != 1:
        for node in india_nodes:
            print(node)
        return 1

    india_node = india_nodes[0]
    india_id = str(india_node["id"])
    print(f"india_node_id={india_id}")

    touching_edges = [
        edge for edge in edges
        if str(edge.get("source")) == india_id or str(edge.get("target")) == india_id
    ]
    print("india_edges=")
    for edge in touching_edges:
        print(edge)

    article_neighbor_ids = sorted(
        {
            str(edge["source"])
            for edge in touching_edges
            if str(edge.get("source", "")).startswith("article_") and str(edge.get("target")) == india_id
        }
        |
        {
            str(edge["target"])
            for edge in touching_edges
            if str(edge.get("target", "")).startswith("article_") and str(edge.get("source")) == india_id
        }
    )
    print(f"article_neighbor_ids={article_neighbor_ids}")
    article_neighbor_titles = [
        str(node_by_id[article_id]["label"])
        for article_id in article_neighbor_ids
        if article_id in node_by_id
    ]
    print(f"article_neighbor_titles={article_neighbor_titles}")

    mention_entity_edges = [
        edge for edge in touching_edges
        if str(edge.get("relationship")) == "MENTIONS_ENTITY"
        and (
            (str(edge.get("source", "")).startswith("article_") and str(edge.get("target")) == india_id)
            or (str(edge.get("target", "")).startswith("article_") and str(edge.get("source")) == india_id)
        )
    ]
    print(f"mentions_entity_edges={len(mention_entity_edges)}")

    if not article_neighbor_ids or not mention_entity_edges:
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
