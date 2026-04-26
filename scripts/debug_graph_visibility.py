from __future__ import annotations

import json
from pathlib import Path


GRAPH_PATH = Path("data/exports/graph.json")


def main() -> int:
    payload = json.loads(GRAPH_PATH.read_text(encoding="utf-8"))
    nodes = payload.get("nodes", [])
    edges = payload.get("edges", [])
    node_by_id = {str(node["id"]): node for node in nodes}

    search_node = "India"
    selected_types = sorted({str(node.get("type", "")) for node in nodes})
    selected_relationships = sorted({str(edge.get("relationship", "")) for edge in edges})

    if selected_relationships:
        filtered_edges: list[dict[str, object]] = []
        for edge in edges:
            source_type = str(node_by_id.get(str(edge.get("source")), {}).get("type", ""))
            target_type = str(node_by_id.get(str(edge.get("target")), {}).get("type", ""))
            if source_type == "article" or target_type == "article":
                filtered_edges.append(edge)
                continue
            if str(edge.get("relationship", "")) in selected_relationships:
                filtered_edges.append(edge)
        edges = filtered_edges

    allowed_type_ids = {
        str(node["id"])
        for node in nodes
        if str(node.get("type", "")) in selected_types
    }
    edges = [
        edge
        for edge in edges
        if str(edge.get("source")) in allowed_type_ids and str(edge.get("target")) in allowed_type_ids
    ]

    node_search = search_node.strip().lower()
    matching_ids = {
        str(node["id"])
        for node in nodes
        if node_search in str(node.get("label", "")).lower()
    }
    edges = [
        edge
        for edge in edges
        if str(edge.get("source")) in matching_ids or str(edge.get("target")) in matching_ids
    ]

    connected_ids = {str(edge["source"]) for edge in edges} | {str(edge["target"]) for edge in edges}
    visible_nodes = [node_by_id[node_id] for node_id in sorted(connected_ids)]

    print("visible nodes")
    for node in visible_nodes:
        print(f"- {node['label']} [{node['type']}] ({node['id']})")

    print("\nvisible edges")
    for edge in edges:
        source_label = str(node_by_id[str(edge["source"])]["label"])
        target_label = str(node_by_id[str(edge["target"])]["label"])
        print(f"- {source_label} -- {edge['relationship']} --> {target_label}")

    india_nodes = [node for node in visible_nodes if str(node["label"]) == "India"]
    if not india_nodes:
        print("\nIndia not visible")
        return 1

    india_id = str(india_nodes[0]["id"])
    article_neighbors = sorted(
        {
            str(edge["source"])
            for edge in edges
            if str(edge["target"]) == india_id and str(edge["source"]).startswith("article_")
        }
        |
        {
            str(edge["target"])
            for edge in edges
            if str(edge["source"]) == india_id and str(edge["target"]).startswith("article_")
        }
    )

    print("\narticle neighbors")
    for article_id in article_neighbors:
        article = node_by_id[article_id]
        print(f"- {article['label']} ({article_id})")

    relationship_types = sorted({str(edge["relationship"]) for edge in edges})
    print("\nrelationship types")
    for relationship in relationship_types:
        print(f"- {relationship}")

    has_expected = any(
        (
            str(edge["relationship"]) == "MENTIONS_ENTITY"
            and (
                (
                    str(edge["source"]) == india_id
                    and node_by_id[str(edge["target"])]["label"] == "Vitamin-D therapy"
                )
                or (
                    str(edge["target"]) == india_id
                    and node_by_id[str(edge["source"])]["label"] == "Vitamin-D therapy"
                )
            )
        )
        for edge in edges
    )
    print(f"\nacceptance_edge_present={has_expected}")
    return 0 if has_expected else 1


if __name__ == "__main__":
    raise SystemExit(main())
