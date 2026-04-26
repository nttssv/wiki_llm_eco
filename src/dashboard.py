"""Read-only Streamlit dashboard for the narrative database."""

from __future__ import annotations

from html import escape
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any
from zipfile import ZipFile

import altair as alt
from docx import Document
import networkx as nx
import pandas as pd
from pyvis.network import Network
import streamlit as st
import streamlit.components.v1 as components

try:
    from .db import NarrativeDatabase
    from .narrative_tracking import narrative_trends_as_dicts, get_narrative_trends
    from .date_utils import week_label_for_published_date
    from .paths import DB_PATH, EXPORTS_DIR, PROJECT_ROOT
except ImportError:
    PROJECT_ROOT = Path(__file__).resolve().parents[1]
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
    from src.db import NarrativeDatabase
    from src.narrative_tracking import narrative_trends_as_dicts, get_narrative_trends
    from src.date_utils import week_label_for_published_date
    from src.paths import DB_PATH, EXPORTS_DIR, PROJECT_ROOT


GRAPH_EXPORT_PATH = EXPORTS_DIR / "graph.json"
GRAPH_LATEST_PATH = EXPORTS_DIR / "graph_latest.json"
DOCX_PREVIEW_LIMIT = 5000
DOCX_IMAGE_LIMIT = 4
NODE_COLORS = {
    "article": "#1d4ed8",
    "entity": "#0f766e",
    "theme": "#c2410c",
    "narrative": "#7c3f00",
}
NODE_SHAPES = {
    "article": "box",
    "entity": "dot",
    "theme": "triangle",
    "narrative": "diamond",
}


def _path_version(path: Path) -> tuple[int, int]:
    """Return a stable cache key component for a file-backed data source."""

    if not path.exists():
        return (0, 0)

    stat = path.stat()
    return (stat.st_mtime_ns, stat.st_size)


def get_connection() -> sqlite3.Connection:
    """Return a read-only SQLite connection."""

    connection = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def ensure_database_schema() -> None:
    """Run lightweight migrations before opening read-only dashboard connections."""

    database = NarrativeDatabase(DB_PATH)
    try:
        database.initialize()
    finally:
        database.close()


@st.cache_data(show_spinner=False)
def load_metrics(_db_version: tuple[int, int]) -> dict[str, int]:
    """Load top-line database metrics."""

    with get_connection() as connection:
        return {
            "articles": int(connection.execute("SELECT COUNT(*) FROM articles").fetchone()[0]),
            "entities": int(connection.execute("SELECT COUNT(*) FROM entities").fetchone()[0]),
            "themes": int(connection.execute("SELECT COUNT(*) FROM themes").fetchone()[0]),
            "narratives": int(connection.execute("SELECT COUNT(*) FROM narratives").fetchone()[0]),
            "graph_edges": int(connection.execute("SELECT COUNT(*) FROM graph_edges").fetchone()[0]),
        }


@st.cache_data(show_spinner=False)
def load_articles(_db_version: tuple[int, int]) -> pd.DataFrame:
    """Load article-level dashboard data."""

    query = """
        SELECT
            a.id,
            a.title,
            a.source,
            a.published_date,
            a.category,
            a.summary,
            a.importance_score,
            a.original_file_path,
            COALESCE(
                (
                    SELECT GROUP_CONCAT(e.name, ', ')
                    FROM article_entities AS ae
                    JOIN entities AS e ON e.id = ae.entity_id
                    WHERE ae.article_id = a.id
                ),
                ''
            ) AS linked_entities,
            COALESCE(
                (
                    SELECT GROUP_CONCAT(t.name, ', ')
                    FROM article_themes AS at
                    JOIN themes AS t ON t.id = at.theme_id
                    WHERE at.article_id = a.id
                ),
                ''
            ) AS linked_themes,
            COALESCE(
                (
                    SELECT GROUP_CONCAT(n.name, ', ')
                    FROM article_narratives AS an
                    JOIN narratives AS n ON n.id = an.narrative_id
                    WHERE an.article_id = a.id
                ),
                ''
            ) AS linked_narratives
        FROM articles AS a
        ORDER BY a.published_date DESC, a.title ASC
    """
    with get_connection() as connection:
        dataframe = pd.read_sql_query(query, connection)

    dataframe["week_label"] = dataframe["published_date"].apply(
        lambda value: week_label_for_published_date(str(value or ""))
    )
    dataframe["selection_label"] = dataframe.apply(
        lambda row: f"{row['title']} | {row['published_date'] or 'Undated'} | {row['source']}",
        axis=1,
    )
    return dataframe


@st.cache_data(show_spinner=False)
def load_narratives(_db_version: tuple[int, int]) -> pd.DataFrame:
    """Load narrative-level dashboard data."""

    query = """
        SELECT
            n.id,
            n.name,
            n.thesis,
            n.status,
            n.importance_score,
            n.first_seen_date,
            n.last_seen_date,
            n.mention_count,
            COALESCE(GROUP_CONCAT(a.title, ' | '), '') AS linked_articles,
            COALESCE(GROUP_CONCAT(a.id, '|'), '') AS article_ids
        FROM narratives AS n
        LEFT JOIN article_narratives AS an ON an.narrative_id = n.id
        LEFT JOIN articles AS a ON a.id = an.article_id
        GROUP BY n.id, n.name, n.thesis, n.status, n.importance_score, n.first_seen_date, n.last_seen_date, n.mention_count
        ORDER BY n.mention_count DESC, n.importance_score DESC, n.name ASC
    """
    with get_connection() as connection:
        return pd.read_sql_query(query, connection)


@st.cache_data(show_spinner=False)
def load_narrative_trends(week_label: str, _db_version: tuple[int, int]) -> pd.DataFrame:
    """Load narrative trends for a selected week."""

    with get_connection() as connection:
        trends = narrative_trends_as_dicts(get_narrative_trends(connection, week_label))
    return pd.DataFrame(
        trends,
        columns=[
            "id",
            "name",
            "thesis",
            "extracted_status",
            "trend_status",
            "importance_score",
            "mention_count",
            "first_seen_date",
            "last_seen_date",
            "week_mentions",
        ],
    )


@st.cache_data(show_spinner=False)
def load_entities(_db_version: tuple[int, int]) -> pd.DataFrame:
    """Load entity-level dashboard data."""

    query = """
        SELECT
            e.id,
            e.name,
            e.type,
            e.description,
            COALESCE(GROUP_CONCAT(a.title, ' | '), '') AS linked_articles,
            COALESCE(GROUP_CONCAT(a.id, '|'), '') AS article_ids,
            COALESCE(
                (
                    SELECT GROUP_CONCAT(
                        ge.source_node || ' --' || ge.relationship || '--> ' || ge.target_node,
                        ' | '
                    )
                    FROM graph_edges AS ge
                    WHERE ge.source_node = e.name OR ge.target_node = e.name
                ),
                ''
            ) AS graph_relationships
        FROM entities AS e
        LEFT JOIN article_entities AS ae ON ae.entity_id = e.id
        LEFT JOIN articles AS a ON a.id = ae.article_id
        GROUP BY e.id, e.name, e.type, e.description
        ORDER BY e.name ASC
    """
    with get_connection() as connection:
        return pd.read_sql_query(query, connection)


def _graph_snapshot_path(week_label: str | None) -> Path:
    if not week_label or week_label == "latest":
        return GRAPH_LATEST_PATH if GRAPH_LATEST_PATH.exists() else GRAPH_EXPORT_PATH
    return EXPORTS_DIR / f"graph_{week_label}.json"


def list_available_graph_weeks() -> list[str]:
    return sorted(
        [
            path.stem.removeprefix("graph_")
            for path in EXPORTS_DIR.glob("graph_*.json")
            if path.stem not in {"graph_latest"}
        ]
    )


@st.cache_data(show_spinner=False)
def load_graph_snapshot(week_label: str | None, _graph_version: tuple[int, int]) -> dict[str, Any] | None:
    """Load one exported graph snapshot."""

    graph_path = _graph_snapshot_path(week_label)
    if not graph_path.exists():
        return None
    return json.loads(graph_path.read_text(encoding="utf-8"))


def _node_signature(node: dict[str, Any]) -> tuple[str, str]:
    return str(node.get("type", "")), str(node.get("label", ""))


def _edge_signature(edge: dict[str, Any], node_by_id: dict[str, dict[str, Any]]) -> tuple[str, str, str]:
    source_node = node_by_id.get(str(edge.get("source")), {})
    target_node = node_by_id.get(str(edge.get("target")), {})
    return (
        str(source_node.get("label", edge.get("source", ""))),
        str(edge.get("relationship", "")),
        str(target_node.get("label", edge.get("target", ""))),
    )


def build_comparison_payload(
    current_payload: dict[str, Any],
    previous_payload: dict[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, int], list[tuple[str, int]]]:
    current_nodes = current_payload.get("nodes", [])
    current_edges = current_payload.get("edges", [])
    current_node_by_id = {str(node["id"]): node for node in current_nodes}
    current_node_signatures = {_node_signature(node): node for node in current_nodes}
    current_edge_signatures = {
        _edge_signature(edge, current_node_by_id): edge
        for edge in current_edges
    }

    previous_nodes = previous_payload.get("nodes", []) if previous_payload else []
    previous_edges = previous_payload.get("edges", []) if previous_payload else []
    previous_node_by_id = {str(node["id"]): node for node in previous_nodes}
    previous_node_signatures = {_node_signature(node): node for node in previous_nodes}
    previous_edge_signatures = {
        _edge_signature(edge, previous_node_by_id): edge
        for edge in previous_edges
    }

    new_node_signatures = set(current_node_signatures) - set(previous_node_signatures)
    removed_node_signatures = set(previous_node_signatures) - set(current_node_signatures)
    new_edge_signatures = set(current_edge_signatures) - set(previous_edge_signatures)
    removed_edge_signatures = set(previous_edge_signatures) - set(current_edge_signatures)

    merged_nodes: list[dict[str, Any]] = []
    signature_to_node_id: dict[tuple[str, str], str] = {}
    for signature, node in current_node_signatures.items():
        compare_status = "new" if signature in new_node_signatures else "unchanged"
        merged_node = {**node, "compare_status": compare_status}
        merged_nodes.append(merged_node)
        signature_to_node_id[signature] = str(node["id"])

    for signature in removed_node_signatures:
        node = previous_node_signatures[signature]
        removed_id = f"removed::{node['id']}"
        merged_node = {**node, "id": removed_id, "compare_status": "removed"}
        merged_nodes.append(merged_node)
        signature_to_node_id[signature] = removed_id

    merged_edges: list[dict[str, Any]] = []
    for signature, edge in current_edge_signatures.items():
        source_signature = _node_signature(current_node_by_id[str(edge["source"])])
        target_signature = _node_signature(current_node_by_id[str(edge["target"])])
        merged_edges.append(
            {
                **edge,
                "source": signature_to_node_id[source_signature],
                "target": signature_to_node_id[target_signature],
                "compare_status": "new" if signature in new_edge_signatures else "unchanged",
            }
        )

    for signature in removed_edge_signatures:
        edge = previous_edge_signatures[signature]
        source_signature = _node_signature(previous_node_by_id[str(edge["source"])])
        target_signature = _node_signature(previous_node_by_id[str(edge["target"])])
        merged_edges.append(
            {
                **edge,
                "source": signature_to_node_id[source_signature],
                "target": signature_to_node_id[target_signature],
                "compare_status": "removed",
            }
        )

    current_entity_degrees: dict[str, int] = {}
    previous_entity_degrees: dict[str, int] = {}
    for signature in current_edge_signatures:
        source_label, _, target_label = signature
        if ("entity", source_label) in current_node_signatures:
            current_entity_degrees[source_label] = current_entity_degrees.get(source_label, 0) + 1
        if ("entity", target_label) in current_node_signatures:
            current_entity_degrees[target_label] = current_entity_degrees.get(target_label, 0) + 1
    for signature in previous_edge_signatures:
        source_label, _, target_label = signature
        if ("entity", source_label) in previous_node_signatures:
            previous_entity_degrees[source_label] = previous_entity_degrees.get(source_label, 0) + 1
        if ("entity", target_label) in previous_node_signatures:
            previous_entity_degrees[target_label] = previous_entity_degrees.get(target_label, 0) + 1
    emerging_entities = sorted(
        [
            (label, current_entity_degrees.get(label, 0) - previous_entity_degrees.get(label, 0))
            for label in current_entity_degrees
            if current_entity_degrees.get(label, 0) - previous_entity_degrees.get(label, 0) > 0
        ],
        key=lambda item: item[1],
        reverse=True,
    )[:5]

    summary = {
        "current_nodes": len(current_nodes),
        "previous_nodes": len(previous_nodes),
        "new_nodes": len(new_node_signatures),
        "removed_nodes": len(removed_node_signatures),
        "new_edges": len(new_edge_signatures),
        "removed_edges": len(removed_edge_signatures),
    }

    merged_payload = {
        "week": current_payload.get("week", ""),
        "nodes": merged_nodes,
        "edges": merged_edges,
    }
    return merged_payload, summary, emerging_entities


def _split_ids(serialized_ids: str) -> set[str]:
    return {item for item in str(serialized_ids or "").split("|") if item}


def _split_names(serialized_names: str) -> list[str]:
    return [item.strip() for item in str(serialized_names or "").split(",") if item.strip()]


def _resolve_article_file_path(file_path: str) -> Path | None:
    raw_value = str(file_path or "").strip()
    if not raw_value:
        return None

    candidate = Path(raw_value)
    if candidate.is_absolute() and candidate.exists():
        return candidate

    if not candidate.is_absolute():
        project_relative = PROJECT_ROOT / candidate
        if project_relative.exists():
            return project_relative

    parts = candidate.parts
    if "data" in parts:
        data_index = parts.index("data")
        remapped = PROJECT_ROOT.joinpath(*parts[data_index:])
        if remapped.exists():
            return remapped

    return None


def _is_docx_preview_truncated(file_path: str) -> bool:
    resolved_path = _resolve_article_file_path(file_path)
    if resolved_path is None:
        return False

    paragraphs = [
        paragraph.text.strip()
        for paragraph in Document(resolved_path).paragraphs
        if paragraph.text.strip()
    ]
    preview_text = "\n\n".join(paragraphs)
    return len(preview_text) > DOCX_PREVIEW_LIMIT


@st.cache_data(show_spinner=False)
def preview_docx(file_path: str) -> str:
    """Render a live preview from the original DOCX file."""

    resolved_path = _resolve_article_file_path(file_path)
    if resolved_path is None:
        return "Original DOCX not found"

    paragraphs = [
        paragraph.text.strip()
        for paragraph in Document(resolved_path).paragraphs
        if paragraph.text.strip()
    ]
    preview_text = "\n\n".join(paragraphs)
    return preview_text[:DOCX_PREVIEW_LIMIT]


@st.cache_data(show_spinner=False)
def preview_docx_images(file_path: str) -> list[bytes]:
    """Extract embedded images from the original DOCX for dashboard display."""

    resolved_path = _resolve_article_file_path(file_path)
    if resolved_path is None:
        return []

    image_suffixes = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}
    image_payloads: list[bytes] = []

    with ZipFile(resolved_path) as archive:
        media_names = sorted(
            name
            for name in archive.namelist()
            if name.startswith("word/media/")
            and Path(name).suffix.lower() in image_suffixes
        )
        for media_name in media_names[:DOCX_IMAGE_LIMIT]:
            image_payloads.append(archive.read(media_name))

    return image_payloads


def _filter_articles(
    articles_df: pd.DataFrame,
    week_filter: str,
    source_filter: str,
    category_filter: str,
    search_term: str,
) -> pd.DataFrame:
    filtered = articles_df.copy()

    if week_filter != "All":
        filtered = filtered[filtered["week_label"] == week_filter]

    if source_filter != "All":
        filtered = filtered[filtered["source"] == source_filter]

    if category_filter != "All":
        filtered = filtered[filtered["category"] == category_filter]

    if search_term:
        mask = (
            filtered["title"].str.contains(search_term, case=False, na=False)
            | filtered["summary"].str.contains(search_term, case=False, na=False)
            | filtered["linked_entities"].str.contains(search_term, case=False, na=False)
            | filtered["linked_narratives"].str.contains(search_term, case=False, na=False)
        )
        filtered = filtered[mask]

    return filtered


def _filter_related_rows(dataframe: pd.DataFrame, article_ids: set[str]) -> pd.DataFrame:
    if not article_ids:
        return dataframe.iloc[0:0].copy()
    return dataframe[dataframe["article_ids"].apply(lambda value: bool(_split_ids(str(value)) & article_ids))].copy()


def _node_visual_weight(attributes: dict[str, Any], visible_connections: int) -> tuple[int, str]:
    """Calculate a display size and importance label for graph nodes."""

    importance_score = int(attributes.get("importance_score", 0) or 0)
    linked_articles = int(attributes.get("linked_articles", 0) or 0)
    mention_count = int(attributes.get("mention_count", 0) or 0)

    if importance_score > 0:
        prominence = importance_score
        importance_label = f"Importance: {importance_score}/10"
    elif mention_count > 0:
        prominence = min(10, max(2, mention_count * 2))
        importance_label = f"Importance proxy: {mention_count} narrative mention(s)"
    else:
        prominence = min(10, max(2, linked_articles + visible_connections))
        importance_label = f"Importance proxy: {linked_articles} linked article(s)"

    size = 16 + (prominence * 2) + min(visible_connections, 6)
    return size, importance_label


def _looks_like_raw_graph_id(value: str) -> bool:
    lowered = str(value or "").strip().lower()
    return lowered.startswith(("entity_", "theme_", "narrative_", "article_"))


def _safe_canvas_label(label: str) -> str:
    clean_label = str(label or "").strip()
    if not clean_label or _looks_like_raw_graph_id(clean_label):
        return ""
    return clean_label


def render_graph(
    payload: dict[str, list[dict[str, Any]]],
    selected_types: list[str],
    selected_relationships: list[str],
    search_node: str,
) -> None:
    """Render a filtered graph using pyvis."""

    nodes = payload.get("nodes", [])
    edges = payload.get("edges", [])
    node_by_id = {str(node["id"]): node for node in nodes}

    article_scoped_edges = list(edges)
    edges = list(article_scoped_edges)

    if selected_relationships:
        filtered_edges: list[dict[str, Any]] = []
        for edge in edges:
            source_type = str(node_by_id.get(str(edge.get("source")), {}).get("type", ""))
            target_type = str(node_by_id.get(str(edge.get("target")), {}).get("type", ""))
            compare_status = str(edge.get("compare_status", ""))
            if compare_status == "removed":
                filtered_edges.append(edge)
                continue
            if source_type == "article" or target_type == "article":
                filtered_edges.append(edge)
                continue
            if str(edge.get("relationship", "")) in selected_relationships:
                filtered_edges.append(edge)
        edges = filtered_edges

    node_search = search_node.strip().lower()

    if selected_types and not node_search:
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

    debug_selected_label = ""
    debug_neighbors: list[dict[str, str]] = []
    debug_edges: list[str] = []

    if node_search:
        matching_ids = {
            str(node["id"])
            for node in nodes
            if node_search in str(node.get("label", "")).lower()
        }
        neighborhood_seed_edges = [
            edge
            for edge in article_scoped_edges
            if str(edge.get("source")) in matching_ids or str(edge.get("target")) in matching_ids
        ]
        visible_node_ids = set(matching_ids)
        for edge in neighborhood_seed_edges:
            visible_node_ids.add(str(edge["source"]))
            visible_node_ids.add(str(edge["target"]))

        edges = [
            edge
            for edge in article_scoped_edges
            if str(edge.get("source")) in visible_node_ids and str(edge.get("target")) in visible_node_ids
        ]

        if matching_ids:
            selected_id = sorted(matching_ids)[0]
            debug_selected_label = str(node_by_id.get(selected_id, {}).get("label", ""))
            neighbor_ids = sorted(visible_node_ids - {selected_id})
            debug_neighbors = [
                {
                    "label": str(node_by_id.get(node_id, {}).get("label", node_id)),
                    "type": str(node_by_id.get(node_id, {}).get("type", "")),
                }
                for node_id in neighbor_ids
            ]
            debug_edges = [
                f"{node_by_id[str(edge['source'])]['label']} -- {edge['relationship']} --> {node_by_id[str(edge['target'])]['label']}"
                for edge in edges
            ]

    connected_ids = {str(edge["source"]) for edge in edges} | {str(edge["target"]) for edge in edges}
    show_visible_edge_labels = bool(node_search)
    graph = nx.DiGraph()

    for node_id in connected_ids:
        node = node_by_id.get(node_id)
        if node is None:
            continue
        node_type = str(node.get("type", "entity"))
        readable_label = _safe_canvas_label(str(node.get("label", "")))
        graph.add_node(
            node_id,
            label=readable_label,
            node_type=node_type,
            color=NODE_COLORS.get(node_type, "#6e7781"),
            compare_status=str(node.get("compare_status", "unchanged")),
            detail=str(node.get("detail", "")),
            importance_score=int(node.get("importance_score", 0) or 0),
            linked_articles=int(node.get("linked_articles", 0) or 0),
            mention_count=int(node.get("mention_count", 0) or 0),
            week=str(node.get("week", "")),
        )

    for edge in edges:
        source_id = str(edge["source"])
        target_id = str(edge["target"])
        if source_id not in graph.nodes or target_id not in graph.nodes:
            continue
        graph.add_edge(
            source_id,
            target_id,
            relationship=str(edge.get("relationship", "")),
            confidence=float(edge.get("confidence", 0.0)),
            narrative_sentence=str(edge.get("narrative_sentence", "")),
            evidence_title=str(edge.get("evidence_title", "")),
            compare_status=str(edge.get("compare_status", "unchanged")),
            week=str(edge.get("week", "")),
        )

    if graph.number_of_nodes() == 0:
        st.info("No graph data matches the current filters.")
        return

    if node_search:
        with st.expander("DEBUG SELECTED NODE"):
            st.write(f"Selected node: {debug_selected_label or search_node}")
            st.write("Neighbor labels and types:")
            if debug_neighbors:
                st.write(debug_neighbors)
            else:
                st.write("No neighbors")
            st.write("Edge relationships:")
            if debug_edges:
                st.write(debug_edges)
            else:
                st.write("No visible edges")

    network = Network(height="720px", width="100%", directed=True, bgcolor="#ffffff", font_color="#111111")
    network.barnes_hut()

    for node_id, attributes in graph.nodes(data=True):
        visible_connections = int(graph.degree(node_id))
        node_size, importance_label = _node_visual_weight(attributes, visible_connections)
        detail = str(attributes.get("detail", "") or "")
        raw_id = str(node_id)
        tooltip_label = str(attributes["label"] or raw_id)
        compare_status = str(attributes.get("compare_status", "unchanged"))
        tooltip_lines = [
            tooltip_label,
            f"Type: {str(attributes['node_type'])}",
            f"Raw ID: {raw_id}",
            f"Week: {str(attributes.get('week', ''))}",
            importance_label,
            f"Visible connections: {visible_connections}",
        ]
        if compare_status != "unchanged":
            tooltip_lines.append(f"Change: {compare_status}")
        if detail:
            tooltip_lines.append(detail)
        node_color = str(attributes["color"])
        if compare_status == "new":
            node_color = "#16a34a"
        elif compare_status == "removed":
            node_color = "#dc2626"
        network.add_node(
            node_id,
            label=str(attributes["label"]),
            title="\n".join(tooltip_lines),
            color=node_color,
            shape=NODE_SHAPES.get(str(attributes["node_type"]), "dot"),
            size=node_size,
            borderWidth=max(2, node_size // 10),
            font={"size": 10, "face": "Georgia"},
        )

    for source_id, target_id, attributes in graph.edges(data=True):
        source_label = str(graph.nodes[source_id]["label"])
        target_label = str(graph.nodes[target_id]["label"])
        source_type = str(graph.nodes[source_id]["node_type"])
        target_type = str(graph.nodes[target_id]["node_type"])
        is_article_context_edge = source_type == "article" or target_type == "article"
        compare_status = str(attributes.get("compare_status", "unchanged"))
        narrative_sentence = str(attributes.get("narrative_sentence", "") or "")
        evidence_title = str(attributes.get("evidence_title", "") or "")
        tooltip_lines = [
            f"{source_label} → {target_label}",
            f"Relationship: {str(attributes['relationship'])}",
            f"Week: {str(attributes.get('week', ''))}",
        ]
        if compare_status != "unchanged":
            tooltip_lines.append(f"Change: {compare_status}")
        if narrative_sentence:
            tooltip_lines.append(f"Narrative: {narrative_sentence}")
        if evidence_title:
            tooltip_lines.append(f"Evidence article: {evidence_title}")
        tooltip_lines.append(f"Confidence: {attributes['confidence']:.2f}")
        edge_color = "#1d4ed8" if is_article_context_edge else "#516071"
        edge_width = (3.2 if is_article_context_edge else 1) + (float(attributes["confidence"]) * 2.4)
        edge_dashes = False if is_article_context_edge else True
        if compare_status == "new":
            edge_color = "#16a34a"
            edge_width += 1.4
            edge_dashes = False
        elif compare_status == "removed":
            edge_color = "#dc2626"
            edge_width = max(2.0, edge_width)
            edge_dashes = True
        network.add_edge(
            source_id,
            target_id,
            label=str(attributes["relationship"]) if show_visible_edge_labels else "",
            title="\n".join(tooltip_lines),
            value=2 + (float(attributes["confidence"]) * 4),
            width=edge_width,
            color=edge_color,
            dashes=edge_dashes,
            font={"size": 11 if show_visible_edge_labels else 8, "align": "middle"},
        )

    network.set_options(
        """
        const options = {
          "interaction": {
            "hover": true,
            "navigationButtons": true,
            "tooltipDelay": 120
          },
          "edges": {
            "font": {
              "size": 8
            },
            "smooth": {
              "enabled": true,
              "type": "dynamic"
            }
          },
          "layout": {
            "improvedLayout": true
          },
          "nodes": {
            "shadow": {
              "enabled": true,
              "color": "rgba(23, 32, 51, 0.12)",
              "size": 12,
              "x": 0,
              "y": 4
            }
          },
          "physics": {
            "barnesHut": {
              "gravitationalConstant": -4200,
              "springLength": 145
            },
            "minVelocity": 0.75
          }
        }
        """
    )

    network_html = network.generate_html()
    fit_script = """
    <script type="text/javascript">
    setTimeout(function() {
      if (typeof network !== "undefined") {
        network.once("stabilizationIterationsDone", function () {
          network.fit({animation: false});
          network.moveTo({scale: 0.82, animation: false});
        });
      }
    }, 0);
    </script>
    """
    network_html = network_html.replace("</body>", fit_script + "\n</body>")

    components.html(network_html, height=760, scrolling=True)


def inject_dashboard_css() -> None:
    """Apply a custom visual treatment to the Streamlit dashboard."""

    st.markdown(
        """
        <style>
        .stApp {
            background:
                radial-gradient(circle at top left, rgba(29, 78, 216, 0.10), transparent 28%),
                radial-gradient(circle at top right, rgba(15, 118, 110, 0.10), transparent 26%),
                linear-gradient(180deg, #f7f3eb 0%, #f9f7f2 36%, #f4efe5 100%);
            color: #172033;
        }
        .block-container {
            max-width: 1480px;
            padding-top: 2.1rem;
            padding-bottom: 3rem;
        }
        h1, h2, h3 {
            font-family: "Iowan Old Style", "Palatino Linotype", "Book Antiqua", Georgia, serif;
            letter-spacing: -0.02em;
            color: #172033;
        }
        h1 {
            font-size: 3rem;
            margin-bottom: 0.3rem;
        }
        .stMarkdown, [data-testid="stMetricLabel"], [data-testid="stMetricValue"] {
            font-family: "Avenir Next", "Segoe UI", "Trebuchet MS", sans-serif;
        }
        [data-testid="stSidebar"] {
            background: linear-gradient(180deg, rgba(255,255,255,0.94), rgba(244,239,229,0.95));
            border-right: 1px solid rgba(23, 32, 51, 0.08);
        }
        [data-testid="stSidebar"] [data-testid="stMarkdown"] h2,
        [data-testid="stSidebar"] [data-testid="stMarkdown"] h3 {
            color: #172033;
        }
        [data-baseweb="select"] > div,
        .stTextInput > div > div,
        .stTextArea textarea {
            background: rgba(255, 255, 255, 0.92);
            border-radius: 14px;
        }
        [data-testid="stTabs"] {
            background: rgba(255, 255, 255, 0.65);
            border: 1px solid rgba(23, 32, 51, 0.08);
            border-radius: 24px;
            padding: 0.4rem 0.6rem 1rem 0.6rem;
            backdrop-filter: blur(8px);
        }
        [data-testid="stTabs"] button[role="tab"] {
            border-radius: 999px;
            padding: 0.7rem 1rem;
            color: #556277;
        }
        [data-testid="stTabs"] button[aria-selected="true"] {
            background: linear-gradient(135deg, #172033 0%, #1d4ed8 100%);
            color: #ffffff;
        }
        .hero-panel {
            background:
                linear-gradient(135deg, rgba(23, 32, 51, 0.96), rgba(29, 78, 216, 0.90)),
                linear-gradient(135deg, #172033, #1d4ed8);
            color: #f8fafc;
            border-radius: 28px;
            padding: 1.6rem 1.8rem;
            box-shadow: 0 24px 60px rgba(23, 32, 51, 0.18);
            margin-bottom: 1rem;
        }
        .hero-kicker {
            text-transform: uppercase;
            letter-spacing: 0.18em;
            font-size: 0.74rem;
            opacity: 0.72;
            margin-bottom: 0.45rem;
        }
        .hero-title {
            font-family: "Iowan Old Style", "Palatino Linotype", Georgia, serif;
            font-size: 2.4rem;
            line-height: 1;
            margin-bottom: 0.4rem;
        }
        .hero-copy {
            max-width: 62rem;
            font-size: 1rem;
            color: rgba(248, 250, 252, 0.86);
            margin-bottom: 1rem;
        }
        .hero-badges, .pill-row {
            display: flex;
            flex-wrap: wrap;
            gap: 0.5rem;
        }
        .hero-badge, .signal-pill {
            display: inline-flex;
            align-items: center;
            border-radius: 999px;
            padding: 0.35rem 0.72rem;
            font-size: 0.82rem;
            font-weight: 600;
            border: 1px solid rgba(255, 255, 255, 0.14);
            background: rgba(255, 255, 255, 0.10);
            color: #f8fafc;
        }
        .overview-card, .panel-card, .list-card {
            background: rgba(255, 255, 255, 0.82);
            border: 1px solid rgba(23, 32, 51, 0.08);
            border-radius: 24px;
            padding: 1rem 1.1rem;
            box-shadow: 0 16px 40px rgba(23, 32, 51, 0.08);
        }
        .metric-card {
            background: linear-gradient(180deg, rgba(255,255,255,0.94), rgba(247,243,235,0.92));
            border: 1px solid rgba(23, 32, 51, 0.08);
            border-radius: 22px;
            padding: 1rem 1.1rem;
            min-height: 8.5rem;
            box-shadow: 0 14px 32px rgba(23, 32, 51, 0.08);
        }
        .metric-label {
            color: #556277;
            text-transform: uppercase;
            letter-spacing: 0.12em;
            font-size: 0.74rem;
            margin-bottom: 0.45rem;
        }
        .metric-value {
            font-size: 2rem;
            line-height: 1;
            font-weight: 700;
            color: #172033;
            margin-bottom: 0.35rem;
        }
        .metric-note {
            color: #6a7485;
            font-size: 0.9rem;
        }
        .section-kicker {
            text-transform: uppercase;
            letter-spacing: 0.16em;
            color: #7c3f00;
            font-size: 0.72rem;
            margin-bottom: 0.2rem;
        }
        .section-title {
            font-family: "Iowan Old Style", "Palatino Linotype", Georgia, serif;
            font-size: 1.6rem;
            color: #172033;
            margin-bottom: 0.2rem;
        }
        .section-copy {
            color: #5d6676;
            margin-bottom: 0.6rem;
        }
        .meta-grid {
            display: grid;
            grid-template-columns: repeat(2, minmax(0, 1fr));
            gap: 0.7rem;
            margin-bottom: 0.75rem;
        }
        .meta-card {
            background: rgba(247, 243, 235, 0.88);
            border: 1px solid rgba(23, 32, 51, 0.08);
            border-radius: 18px;
            padding: 0.85rem 0.95rem;
        }
        .meta-label {
            color: #6a7485;
            font-size: 0.72rem;
            text-transform: uppercase;
            letter-spacing: 0.12em;
            margin-bottom: 0.25rem;
        }
        .meta-value {
            color: #172033;
            font-size: 1rem;
            font-weight: 600;
        }
        .summary-card {
            background: linear-gradient(135deg, rgba(29, 78, 216, 0.08), rgba(15, 118, 110, 0.08));
            border: 1px solid rgba(29, 78, 216, 0.10);
            border-radius: 22px;
            padding: 1rem 1.1rem;
            margin-bottom: 0.9rem;
        }
        .summary-title {
            color: #1d4ed8;
            font-size: 0.74rem;
            text-transform: uppercase;
            letter-spacing: 0.14em;
            margin-bottom: 0.35rem;
        }
        .summary-body {
            color: #172033;
            font-size: 1rem;
            line-height: 1.6;
        }
        .signal-group {
            margin-top: 0.85rem;
            padding-top: 0.85rem;
            border-top: 1px solid rgba(23, 32, 51, 0.08);
        }
        .signal-label {
            color: #5d6676;
            text-transform: uppercase;
            letter-spacing: 0.12em;
            font-size: 0.72rem;
            margin-bottom: 0.45rem;
        }
        .signal-pill {
            background: rgba(23, 32, 51, 0.06);
            color: #172033;
            border: 1px solid rgba(23, 32, 51, 0.08);
        }
        .doc-preview {
            background: rgba(248, 250, 252, 0.9);
            border: 1px solid rgba(23, 32, 51, 0.08);
            border-radius: 22px;
            padding: 1rem;
            max-height: 40rem;
            overflow: auto;
        }
        .doc-preview pre {
            font-family: "SFMono-Regular", "Menlo", "Consolas", monospace;
            white-space: pre-wrap;
            line-height: 1.55;
            color: #172033;
            margin: 0;
        }
        .media-shell {
            background: linear-gradient(180deg, rgba(255,255,255,0.88), rgba(244,239,229,0.84));
            border: 1px solid rgba(23, 32, 51, 0.08);
            border-radius: 28px;
            padding: 1rem 1rem 0.9rem 1rem;
            box-shadow: 0 18px 42px rgba(23, 32, 51, 0.08);
            margin-top: 0.2rem;
        }
        .media-intro {
            display: flex;
            flex-wrap: wrap;
            align-items: center;
            justify-content: space-between;
            gap: 0.7rem;
            margin-bottom: 0.9rem;
        }
        .media-title {
            font-family: "Iowan Old Style", "Palatino Linotype", Georgia, serif;
            font-size: 1.32rem;
            color: #172033;
        }
        .media-note {
            color: #5d6676;
            font-size: 0.92rem;
            max-width: 34rem;
        }
        .media-chip-row {
            display: flex;
            flex-wrap: wrap;
            gap: 0.45rem;
            margin-bottom: 0.8rem;
        }
        .media-chip {
            display: inline-flex;
            align-items: center;
            gap: 0.35rem;
            border-radius: 999px;
            padding: 0.38rem 0.72rem;
            background: rgba(23, 32, 51, 0.06);
            border: 1px solid rgba(23, 32, 51, 0.08);
            color: #172033;
            font-size: 0.82rem;
            font-weight: 600;
        }
        .image-rail {
            background: rgba(248, 250, 252, 0.82);
            border: 1px solid rgba(23, 32, 51, 0.08);
            border-radius: 22px;
            padding: 0.85rem;
            min-height: 100%;
        }
        .image-rail-title {
            color: #7c3f00;
            text-transform: uppercase;
            letter-spacing: 0.14em;
            font-size: 0.72rem;
            margin-bottom: 0.35rem;
        }
        .image-rail-copy {
            color: #5d6676;
            font-size: 0.92rem;
            margin-bottom: 0.75rem;
        }
        .image-empty {
            border: 1px dashed rgba(23, 32, 51, 0.18);
            border-radius: 18px;
            padding: 1rem;
            color: #5d6676;
            background: rgba(255,255,255,0.72);
        }
        .legend-strip {
            display: flex;
            flex-wrap: wrap;
            gap: 0.6rem;
            margin: 0.4rem 0 0.8rem 0;
        }
        .legend-chip {
            display: inline-flex;
            align-items: center;
            gap: 0.45rem;
            border-radius: 999px;
            padding: 0.42rem 0.78rem;
            background: rgba(255,255,255,0.82);
            border: 1px solid rgba(23, 32, 51, 0.08);
            font-size: 0.84rem;
        }
        .graph-shell {
            background: rgba(255, 255, 255, 0.84);
            border: 1px solid rgba(23, 32, 51, 0.08);
            border-radius: 26px;
            padding: 1.05rem 1.1rem 1.15rem 1.1rem;
            box-shadow: 0 18px 42px rgba(23, 32, 51, 0.08);
        }
        .graph-stage-title {
            font-family: "Iowan Old Style", "Palatino Linotype", Georgia, serif;
            font-size: 1.4rem;
            color: #172033;
            margin-bottom: 0.2rem;
        }
        .graph-stage-copy {
            color: #5d6676;
            margin-bottom: 0.7rem;
        }
        .graph-control-card {
            background: rgba(255, 255, 255, 0.86);
            border: 1px solid rgba(23, 32, 51, 0.08);
            border-radius: 24px;
            padding: 1rem 1rem 0.55rem 1rem;
            box-shadow: 0 14px 32px rgba(23, 32, 51, 0.07);
        }
        .graph-control-title {
            font-family: "Iowan Old Style", "Palatino Linotype", Georgia, serif;
            font-size: 1.28rem;
            color: #172033;
            margin-bottom: 0.2rem;
        }
        .graph-control-copy {
            color: #5d6676;
            margin-bottom: 0.8rem;
        }
        .legend-dot {
            width: 0.7rem;
            height: 0.7rem;
            border-radius: 999px;
            display: inline-block;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _render_metric_card(label: str, value: str, note: str) -> None:
    st.markdown(
        f"""
        <div class="metric-card">
          <div class="metric-label">{escape(label)}</div>
          <div class="metric-value">{escape(value)}</div>
          <div class="metric-note">{escape(note)}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_section_intro(title: str, copy: str, kicker: str = "Briefing") -> None:
    st.markdown(
        f"""
        <div class="section-kicker">{escape(kicker)}</div>
        <div class="section-title">{escape(title)}</div>
        <div class="section-copy">{escape(copy)}</div>
        """,
        unsafe_allow_html=True,
    )


def _render_signal_group(label: str, items: list[str], empty_label: str) -> None:
    pills = "".join(f'<span class="signal-pill">{escape(item)}</span>' for item in items) or (
        f'<span class="signal-pill">{escape(empty_label)}</span>'
    )
    st.markdown(
        f"""
        <div class="signal-group">
          <div class="signal-label">{escape(label)}</div>
          <div class="pill-row">{pills}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_article_metadata(selected_article: pd.Series) -> None:
    st.markdown(
        f"""
        <div class="meta-grid">
          <div class="meta-card">
            <div class="meta-label">Source</div>
            <div class="meta-value">{escape(str(selected_article['source'] or 'Unknown'))}</div>
          </div>
          <div class="meta-card">
            <div class="meta-label">Published</div>
            <div class="meta-value">{escape(str(selected_article['published_date'] or 'Undated'))}</div>
          </div>
          <div class="meta-card">
            <div class="meta-label">Category</div>
            <div class="meta-value">{escape(str(selected_article['category'] or 'Uncategorized'))}</div>
          </div>
          <div class="meta-card">
            <div class="meta-label">Importance</div>
            <div class="meta-value">{escape(str(selected_article['importance_score']))} / 10</div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_summary_card(summary: str) -> None:
    st.markdown(
        f"""
        <div class="summary-card">
          <div class="summary-title">Analyst Summary</div>
          <div class="summary-body">{escape(summary or 'No summary available.')}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_doc_preview(preview_text: str) -> None:
    st.markdown(
        f"""
        <div class="doc-preview">
          <pre>{escape(preview_text)}</pre>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_media_shell_intro(image_count: int, preview_text: str, truncated: bool) -> None:
    chips = [
        f'<span class="media-chip">Preview length: {len(preview_text):,} chars</span>',
        f'<span class="media-chip">Images: {image_count}</span>',
    ]
    if truncated:
        chips.append('<span class="media-chip">Text preview truncated</span>')
    st.markdown(
        f"""
        <div class="media-shell">
          <div class="media-intro">
            <div>
              <div class="media-title">Document And Media Board</div>
              <div class="media-note">
                Read the extracted source text and inspect embedded visuals in one place instead of jumping between separate blocks.
              </div>
            </div>
          </div>
          <div class="media-chip-row">{''.join(chips)}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_image_rail(preview_images: list[bytes], docx_missing: bool) -> None:
    st.markdown(
        """
        <div class="image-rail">
          <div class="image-rail-title">Embedded Visuals</div>
          <div class="image-rail-copy">Images pulled from the original DOCX are pinned beside the text preview for easier comparison.</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    if docx_missing:
        st.markdown(
            '<div class="image-empty">Image preview unavailable because the source DOCX could not be found.</div>',
            unsafe_allow_html=True,
        )
        return

    if not preview_images:
        st.markdown(
            '<div class="image-empty">No embedded images were found in this DOCX.</div>',
            unsafe_allow_html=True,
        )
        return

    st.image(preview_images[0], caption="Lead embedded image", use_container_width=True)
    if len(preview_images) > 1:
        st.markdown("**Additional images**")
        thumbnail_cols = st.columns(2, gap="small")
        for index, image_payload in enumerate(preview_images[1:], start=2):
            with thumbnail_cols[(index - 2) % 2]:
                st.image(
                    image_payload,
                    caption=f"Embedded image {index}",
                    use_container_width=True,
                )
    if len(preview_images) == DOCX_IMAGE_LIMIT:
        st.caption("Image preview limited for display.")


def _render_graph_legend() -> None:
    st.markdown("**Legend**")
    legend_left, legend_right = st.columns(2, gap="small")
    node_items = list(NODE_COLORS.items())

    for index, (node_type, _) in enumerate(node_items):
        target_column = legend_left if index % 2 == 0 else legend_right
        shape_label = NODE_SHAPES.get(node_type, "dot").title()
        with target_column:
            st.markdown(f"`{node_type.title()}`  \nShape: {shape_label}")

    st.caption("Edge labels show relationship types.")
    st.caption("Larger nodes indicate higher importance or stronger article linkage.")
    st.caption("Hover nodes for detail and hover edges for the one-sentence narrative and evidence article.")


def _render_chart_card(title: str, chart: alt.Chart) -> None:
    st.markdown(
        f"""
        <div class="overview-card">
          <div class="section-kicker">Distribution</div>
          <div class="section-title" style="font-size:1.2rem;">{escape(title)}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.altair_chart(chart, use_container_width=True)


def _top_count_frame(dataframe: pd.DataFrame, column_name: str, label: str, limit: int = 8) -> pd.DataFrame:
    counts = (
        dataframe[column_name]
        .fillna("")
        .replace("", "Unspecified")
        .value_counts()
        .head(limit)
        .rename_axis(label)
        .reset_index(name="count")
    )
    return counts


def _count_chart(dataframe: pd.DataFrame, category_column: str, label: str, color: str) -> alt.Chart:
    chart_data = _top_count_frame(dataframe, category_column, label)
    return (
        alt.Chart(chart_data)
        .mark_bar(cornerRadiusTopRight=7, cornerRadiusBottomRight=7, color=color)
        .encode(
            x=alt.X("count:Q", title="Articles"),
            y=alt.Y(f"{label}:N", sort="-x", title=None),
            tooltip=[alt.Tooltip(f"{label}:N", title=label.title()), alt.Tooltip("count:Q", title="Articles")],
        )
        .properties(height=280)
    )


def _render_overview_tab(
    filtered_articles: pd.DataFrame,
    filtered_narratives: pd.DataFrame,
    selected_week: str,
) -> None:
    if filtered_articles.empty:
        st.info("No articles match the current filters.")
        return

    avg_importance = float(filtered_articles["importance_score"].fillna(0).mean())
    source_count = int(filtered_articles["source"].replace("", pd.NA).dropna().nunique())
    narrative_count = int(filtered_narratives["name"].nunique()) if not filtered_narratives.empty else 0
    high_priority_count = int((filtered_articles["importance_score"].fillna(0) >= 8).sum())

    top_row = st.columns(4)
    with top_row[0]:
        _render_metric_card("Articles in Scope", str(len(filtered_articles)), "Current filtered working set")
    with top_row[1]:
        _render_metric_card(
            "Average Importance",
            f"{avg_importance:.1f}",
            "Mean article score across the current view",
        )
    with top_row[2]:
        _render_metric_card("Sources Active", str(source_count), "Distinct publications in view")
    with top_row[3]:
        _render_metric_card("Priority Coverage", str(high_priority_count), "Articles scoring 8 or higher")

    st.markdown("")
    left_col, right_col = st.columns([1.15, 0.85], gap="large")

    with left_col:
        _render_section_intro(
            "Coverage Shape",
            "Quick distribution checks help reveal whether the week is concentrated in one source or one topic cluster.",
            kicker="Overview",
        )
        chart_cols = st.columns(2, gap="large")
        with chart_cols[0]:
            _render_chart_card("Top Sources", _count_chart(filtered_articles, "source", "source", "#1d4ed8"))
        with chart_cols[1]:
            _render_chart_card("Top Categories", _count_chart(filtered_articles, "category", "category", "#0f766e"))

    with right_col:
        _render_section_intro(
            "Operating Snapshot",
            "This panel compresses the current filtered view into the main signals you would scan before reading individual documents.",
            kicker="Status",
        )
        recent_view = filtered_articles[
            ["title", "source", "published_date", "importance_score"]
        ].head(8).rename(columns={"published_date": "date", "importance_score": "importance"})
        st.dataframe(recent_view, use_container_width=True, hide_index=True, height=318)
        st.markdown(
            f"""
            <div class="panel-card" style="margin-top:0.8rem;">
              <div class="section-kicker">Narratives</div>
              <div class="section-title" style="font-size:1.25rem;">{narrative_count} active narrative tracks</div>
              <div class="section-copy">Week filter: {escape(selected_week)}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )


def main() -> None:
    st.set_page_config(
        page_title="Narrative Agent Dashboard",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    inject_dashboard_css()

    if not DB_PATH.exists():
        st.error("Database not found. Run weekly extraction first.")
        return

    ensure_database_schema()
    db_version = _path_version(DB_PATH)

    metrics = load_metrics(db_version)
    articles_df = load_articles(db_version)
    narratives_df = load_narratives(db_version)
    entities_df = load_entities(db_version)

    st.sidebar.markdown("## Filters")
    st.sidebar.caption("Scope the intelligence view before drilling into articles, narratives, and graph relationships.")
    week_options = ["All", *sorted([value for value in articles_df["week_label"].dropna().unique()], reverse=True)]
    source_options = ["All", *sorted([value for value in articles_df["source"].dropna().unique() if value])]
    category_options = ["All", *sorted([value for value in articles_df["category"].dropna().unique() if value])]

    selected_week = st.sidebar.selectbox("Week", options=week_options)
    selected_source = st.sidebar.selectbox("Source", options=source_options)
    selected_category = st.sidebar.selectbox("Category", options=category_options)
    search_term = st.sidebar.text_input("Search", value="")

    filtered_articles = _filter_articles(
        articles_df,
        selected_week,
        selected_source,
        selected_category,
        search_term,
    )
    filtered_article_ids = set(filtered_articles["id"].astype(str))
    filtered_narratives = _filter_related_rows(narratives_df, filtered_article_ids)
    active_filters = [
        f"Week: {selected_week}",
        f"Source: {selected_source}",
        f"Category: {selected_category}",
        f"Search: {search_term or 'None'}",
    ]
    hero_badges = "".join(f'<span class="hero-badge">{escape(item)}</span>' for item in active_filters)
    total_filtered = len(filtered_articles)
    avg_importance = float(filtered_articles["importance_score"].fillna(0).mean()) if total_filtered else 0.0

    st.markdown(
        f"""
        <div class="hero-panel">
          <div class="hero-kicker">Narrative Intelligence Console</div>
          <div class="hero-title">Narrative Agent Dashboard</div>
          <div class="hero-copy">
            Structured weekly coverage, entity tracking, narrative movement, and graph relationships in one operating view.
            Current scope includes {total_filtered} article(s) with an average importance score of {avg_importance:.1f}.
          </div>
          <div class="hero-badges">{hero_badges}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    metric_columns = st.columns(5)
    with metric_columns[0]:
        _render_metric_card("Articles", str(metrics["articles"]), "Total article records")
    with metric_columns[1]:
        _render_metric_card("Entities", str(metrics["entities"]), "Distinct tracked entities")
    with metric_columns[2]:
        _render_metric_card("Themes", str(metrics["themes"]), "Reusable topic clusters")
    with metric_columns[3]:
        _render_metric_card("Narratives", str(metrics["narratives"]), "Trackable narrative lines")
    with metric_columns[4]:
        _render_metric_card("Graph Edges", str(metrics["graph_edges"]), "Relationship statements")

    overview_tab, articles_tab, narratives_tab, entities_tab, trends_tab, graph_tab = st.tabs(
        ["Overview", "Articles", "Narratives", "Entities", "Narrative Trends", "Graph View"]
    )

    with overview_tab:
        _render_overview_tab(filtered_articles, filtered_narratives, selected_week)

    with articles_tab:
        if filtered_articles.empty:
            st.info("No articles match the current filters.")
        else:
            _render_section_intro(
                "Article Workbench",
                "Use this pane to inspect structured extraction alongside the source document preview.",
                kicker="Primary View",
            )
            left_col, right_col = st.columns([0.95, 1.05], gap="large")
            article_options = filtered_articles["selection_label"].tolist()

            with left_col:
                st.caption(f"{len(filtered_articles)} article(s) match the current filter set.")
                selected_label = st.selectbox("Select article", article_options, key="article_select")
                selected_article = filtered_articles[
                    filtered_articles["selection_label"] == selected_label
                ].iloc[0]

                st.markdown(f"## {selected_article['title']}")
                _render_article_metadata(selected_article)
                _render_summary_card(str(selected_article["summary"] or ""))

                entity_names = _split_names(selected_article["linked_entities"])
                theme_names = _split_names(selected_article["linked_themes"])
                narrative_names = _split_names(selected_article["linked_narratives"])
                _render_signal_group("Entities", entity_names, "No linked entities")
                _render_signal_group("Themes", theme_names, "No linked themes")
                _render_signal_group("Narratives", narrative_names, "No linked narratives")

            with right_col:
                _render_section_intro(
                    "Source Document",
                    "The reading panel now keeps the source text and embedded visuals visible together so the article can be inspected with less scrolling.",
                    kicker="Document",
                )
                original_file_path = str(selected_article["original_file_path"] or "")
                preview_text = preview_docx(original_file_path)
                docx_missing = preview_text == "Original DOCX not found"
                preview_images = preview_docx_images(original_file_path)
                preview_truncated = False if docx_missing else _is_docx_preview_truncated(original_file_path)
                _render_media_shell_intro(len(preview_images), preview_text, preview_truncated)
                media_left, media_right = st.columns([1.35, 0.9], gap="large")
                with media_left:
                    _render_doc_preview(preview_text)
                with media_right:
                    _render_image_rail(preview_images, docx_missing)
                if docx_missing:
                    st.info("Original DOCX not found")
                elif preview_truncated:
                    st.caption("Preview truncated for display.")

    with narratives_tab:
        _render_section_intro(
            "Narrative Register",
            "Filter the narrative layer independently to review theses, status labels, and mention longevity.",
            kicker="Narratives",
        )
        status_options = [
            "emerging",
            "strengthening",
            "weakening",
            "transitioning",
            "reversed",
            "stable",
        ]
        selected_statuses = st.multiselect(
            "Narrative status",
            options=status_options,
            default=status_options,
        )

        filtered_narratives = filtered_narratives[
            filtered_narratives["status"].isin(selected_statuses)
        ]
        if search_term:
            filtered_narratives = filtered_narratives[
                filtered_narratives["name"].str.contains(search_term, case=False, na=False)
                | filtered_narratives["thesis"].str.contains(search_term, case=False, na=False)
            ]

        narrative_metric_cols = st.columns(3)
        with narrative_metric_cols[0]:
            _render_metric_card("Visible Narratives", str(len(filtered_narratives)), "Rows after filters")
        with narrative_metric_cols[1]:
            _render_metric_card(
                "Average Importance",
                f"{filtered_narratives['importance_score'].fillna(0).mean():.1f}" if not filtered_narratives.empty else "0.0",
                "Mean importance across visible narratives",
            )
        with narrative_metric_cols[2]:
            _render_metric_card(
                "Average Mentions",
                f"{filtered_narratives['mention_count'].fillna(0).mean():.1f}" if not filtered_narratives.empty else "0.0",
                "Mean article mentions per narrative",
            )

        st.dataframe(
            filtered_narratives[
                [
                    "name",
                    "thesis",
                    "status",
                    "importance_score",
                    "mention_count",
                    "first_seen_date",
                    "last_seen_date",
                    "linked_articles",
                ]
            ],
            use_container_width=True,
            hide_index=True,
        )

    with entities_tab:
        _render_section_intro(
            "Entity Register",
            "Review the monitored actors and the article/narrative contexts in which they appear.",
            kicker="Entities",
        )
        filtered_entities = _filter_related_rows(entities_df, filtered_article_ids)
        if search_term:
            filtered_entities = filtered_entities[
                filtered_entities["name"].str.contains(search_term, case=False, na=False)
                | filtered_entities["description"].str.contains(search_term, case=False, na=False)
            ]

        entity_metric_cols = st.columns(3)
        with entity_metric_cols[0]:
            _render_metric_card("Visible Entities", str(len(filtered_entities)), "Rows after filters")
        with entity_metric_cols[1]:
            _render_metric_card(
                "Entity Types",
                str(filtered_entities["type"].replace("", pd.NA).dropna().nunique()),
                "Distinct entity classes in view",
            )
        with entity_metric_cols[2]:
            relationship_count = int(filtered_entities["graph_relationships"].fillna("").astype(str).str.len().gt(0).sum())
            _render_metric_card("Linked Relationships", str(relationship_count), "Entities with graph evidence")

        st.dataframe(
            filtered_entities[["name", "type", "description", "linked_articles", "graph_relationships"]],
            use_container_width=True,
            hide_index=True,
        )

    with trends_tab:
        _render_section_intro(
            "Narrative Movement",
            "Trend labels are computed from article-linked appearances inside the selected week window.",
            kicker="Trends",
        )
        if selected_week == "All":
            st.info("Select a specific week in the sidebar to view narrative trends.")
        else:
            trend_df = load_narrative_trends(selected_week, db_version)
            if search_term:
                trend_df = trend_df[
                    trend_df["name"].str.contains(search_term, case=False, na=False)
                    | trend_df["thesis"].str.contains(search_term, case=False, na=False)
                ]

            status_options = ["NEW", "STRENGTHENING", "RECURRING", "WEAKENING"]
            selected_trend_statuses = st.multiselect(
                "Trend status",
                options=status_options,
                default=status_options,
                key="trend_status_filter",
            )
            trend_df = trend_df[trend_df["trend_status"].isin(selected_trend_statuses)]
            trend_df = trend_df.sort_values(by=["mention_count", "name"], ascending=[False, True])

            trend_metric_cols = st.columns(3)
            with trend_metric_cols[0]:
                _render_metric_card("Visible Trends", str(len(trend_df)), "Rows after filters")
            with trend_metric_cols[1]:
                strongest_count = int((trend_df["trend_status"] == "STRENGTHENING").sum())
                _render_metric_card("Strengthening", str(strongest_count), "Narratives accelerating this week")
            with trend_metric_cols[2]:
                new_count = int((trend_df["trend_status"] == "NEW").sum())
                _render_metric_card("New Signals", str(new_count), "Narratives first seen this week")

            st.dataframe(
                trend_df[
                    [
                        "name",
                        "trend_status",
                        "mention_count",
                        "first_seen_date",
                        "last_seen_date",
                    ]
                ].rename(columns={"trend_status": "status"}),
                use_container_width=True,
                hide_index=True,
            )

    with graph_tab:
        _render_section_intro(
            "Relationship Graph",
            "Use the left control rail to narrow the network, while keeping the graph centered for easier scanning.",
            kicker="Graph",
        )
        available_graph_weeks = list_available_graph_weeks()
        if not available_graph_weeks:
            st.info("Graph export not found. Run python -m src.export_graph --format json first.")
        else:
            latest_index = len(available_graph_weeks) - 1
            control_top_left, control_top_right = st.columns([0.55, 0.45], gap="large")
            with control_top_left:
                selected_graph_week = st.selectbox(
                    "Select Week",
                    options=available_graph_weeks,
                    index=latest_index,
                    key="graph_week_select",
                )
            selected_week_index = available_graph_weeks.index(selected_graph_week)
            previous_graph_week = (
                available_graph_weeks[selected_week_index - 1] if selected_week_index > 0 else None
            )
            with control_top_right:
                compare_with_previous = st.toggle(
                    "Compare with previous week",
                    value=False,
                    disabled=previous_graph_week is None,
                    key="graph_compare_toggle",
                )
                if compare_with_previous and previous_graph_week is None:
                    st.caption("No previous week snapshot is available yet.")

            current_graph_path = _graph_snapshot_path(selected_graph_week)
            current_graph = load_graph_snapshot(
                selected_graph_week,
                _path_version(current_graph_path),
            )
            if current_graph is None:
                st.info(f"Graph snapshot not found for {selected_graph_week}.")
                return

            previous_graph = None
            if compare_with_previous and previous_graph_week is not None:
                previous_graph_path = _graph_snapshot_path(previous_graph_week)
                previous_graph = load_graph_snapshot(
                    previous_graph_week,
                    _path_version(previous_graph_path),
                )

            if compare_with_previous and previous_graph is not None:
                graph_payload, comparison_summary, emerging_entities = build_comparison_payload(
                    current_graph,
                    previous_graph,
                )
            else:
                graph_payload = current_graph
                comparison_summary = {
                    "current_nodes": len(current_graph.get("nodes", [])),
                    "previous_nodes": len(previous_graph.get("nodes", [])) if previous_graph else 0,
                    "new_nodes": 0,
                    "removed_nodes": 0,
                    "new_edges": 0,
                    "removed_edges": 0,
                }
                emerging_entities = []

            summary_cols = st.columns(6)
            with summary_cols[0]:
                _render_metric_card("Nodes This Week", str(comparison_summary["current_nodes"]), selected_graph_week)
            with summary_cols[1]:
                previous_label = previous_graph_week if previous_graph_week else "No prior snapshot"
                _render_metric_card("Nodes Last Week", str(comparison_summary["previous_nodes"]), previous_label)
            with summary_cols[2]:
                _render_metric_card("New Nodes", str(comparison_summary["new_nodes"]), "Added vs previous week")
            with summary_cols[3]:
                _render_metric_card("Removed Nodes", str(comparison_summary["removed_nodes"]), "Missing this week")
            with summary_cols[4]:
                _render_metric_card("New Edges", str(comparison_summary["new_edges"]), "New relationships")
            with summary_cols[5]:
                _render_metric_card("Removed Edges", str(comparison_summary["removed_edges"]), "Dropped relationships")

            if compare_with_previous and previous_graph_week is not None:
                if emerging_entities:
                    st.caption(
                        "Top emerging entities: "
                        + ", ".join(f"{label} (+{delta})" for label, delta in emerging_entities)
                    )
                else:
                    st.caption(f"Comparing {selected_graph_week} against {previous_graph_week}. No emerging entities yet.")

            graph_node_types = sorted({str(node.get("type", "")) for node in graph_payload.get("nodes", [])})
            graph_relationships = sorted(
                {str(edge.get("relationship", "")) for edge in graph_payload.get("edges", [])}
            )
            left_col, right_col = st.columns([0.28, 0.72], gap="large")

            with left_col:
                st.markdown(
                    """
                    <div class="graph-control-card">
                      <div class="section-kicker">Controls</div>
                      <div class="graph-control-title">Graph Filters</div>
                      <div class="graph-control-copy">The code-like values you were seeing under the title were interface clutter from the filter widgets, not broken graph data.</div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
                _render_graph_legend()
                selected_node_types = st.multiselect(
                    "Node types",
                    options=graph_node_types,
                    default=graph_node_types,
                    key="graph_node_types",
                )
                selected_relationship_types = st.multiselect(
                    "Relationship types",
                    options=graph_relationships,
                    default=graph_relationships,
                    key="graph_relationship_types",
                )
                graph_search = st.text_input("Search node", value="", key="graph_search")

            with right_col:
                st.markdown(
                    """
                    <div class="graph-shell">
                      <div class="section-kicker">Canvas</div>
                      <div class="graph-stage-title">Centered Network View</div>
                      <div class="graph-stage-copy">Hover nodes for importance and hover edges for a one-sentence narrative explanation.</div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
                render_graph(
                    graph_payload,
                    selected_node_types,
                    selected_relationship_types,
                    graph_search,
                )


if __name__ == "__main__":
    main()
