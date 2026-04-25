"""Read-only Streamlit dashboard for the narrative database."""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import sys
from typing import Any
from zipfile import ZipFile

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
DOCX_PREVIEW_LIMIT = 5000
DOCX_IMAGE_LIMIT = 4
NODE_COLORS = {
    "article": "#2f6fed",
    "entity": "#2da44e",
    "theme": "#fb8c00",
    "narrative": "#8e44ad",
}


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
def load_metrics() -> dict[str, int]:
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
def load_articles() -> pd.DataFrame:
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
def load_narratives() -> pd.DataFrame:
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
def load_narrative_trends(week_label: str) -> pd.DataFrame:
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
def load_entities() -> pd.DataFrame:
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


@st.cache_data(show_spinner=False)
def load_graph() -> dict[str, list[dict[str, Any]]] | None:
    """Load the exported graph JSON if present."""

    if not GRAPH_EXPORT_PATH.exists():
        return None
    return json.loads(GRAPH_EXPORT_PATH.read_text(encoding="utf-8"))


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


def render_graph(
    payload: dict[str, list[dict[str, Any]]],
    article_ids: set[str],
    selected_types: list[str],
    selected_relationships: list[str],
    search_node: str,
) -> None:
    """Render a filtered graph using pyvis."""

    nodes = payload.get("nodes", [])
    edges = payload.get("edges", [])

    edges = [edge for edge in edges if str(edge.get("evidence_article_id", "")) in article_ids]

    if selected_relationships:
        edges = [edge for edge in edges if str(edge.get("relationship", "")) in selected_relationships]

    node_by_id = {str(node["id"]): node for node in nodes}
    node_search = search_node.strip().lower()

    if selected_types:
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

    if node_search:
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
    graph = nx.DiGraph()

    for node_id in connected_ids:
        node = node_by_id.get(node_id)
        if node is None:
            continue
        node_type = str(node.get("type", "entity"))
        graph.add_node(
            node_id,
            label=str(node.get("label", node_id)),
            node_type=node_type,
            color=NODE_COLORS.get(node_type, "#6e7781"),
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
        )

    if graph.number_of_nodes() == 0:
        st.info("No graph data matches the current filters.")
        return

    network = Network(height="720px", width="100%", directed=True, bgcolor="#ffffff", font_color="#111111")
    network.barnes_hut()

    for node_id, attributes in graph.nodes(data=True):
        network.add_node(
            node_id,
            label=str(attributes["label"]),
            title=f"{attributes['label']} ({attributes['node_type']})",
            color=str(attributes["color"]),
        )

    for source_id, target_id, attributes in graph.edges(data=True):
        network.add_edge(
            source_id,
            target_id,
            label=str(attributes["relationship"]),
            title=f"{attributes['relationship']} | confidence={attributes['confidence']:.2f}",
            value=float(attributes["confidence"]),
        )

    components.html(network.generate_html(), height=760, scrolling=True)


def main() -> None:
    st.set_page_config(page_title="Narrative Agent Dashboard", layout="wide")
    st.title("Narrative Agent Dashboard")

    if not DB_PATH.exists():
        st.error("Database not found. Run weekly extraction first.")
        return

    ensure_database_schema()

    metrics = load_metrics()
    articles_df = load_articles()
    narratives_df = load_narratives()
    entities_df = load_entities()

    metric_columns = st.columns(5)
    metric_columns[0].metric("Total Articles", metrics["articles"])
    metric_columns[1].metric("Total Entities", metrics["entities"])
    metric_columns[2].metric("Total Themes", metrics["themes"])
    metric_columns[3].metric("Total Narratives", metrics["narratives"])
    metric_columns[4].metric("Total Graph Edges", metrics["graph_edges"])

    st.sidebar.header("Filters")
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

    articles_tab, narratives_tab, entities_tab, trends_tab, graph_tab = st.tabs(
        ["Articles", "Narratives", "Entities", "Narrative Trends", "Graph View"]
    )

    with articles_tab:
        if filtered_articles.empty:
            st.info("No articles match the current filters.")
        else:
            left_col, right_col = st.columns([1, 1])
            article_options = filtered_articles["selection_label"].tolist()

            with left_col:
                selected_label = st.selectbox("Select article", article_options, key="article_select")
                selected_article = filtered_articles[
                    filtered_articles["selection_label"] == selected_label
                ].iloc[0]

                st.markdown(f"## {selected_article['title']}")
                st.markdown(f"**Source:** {selected_article['source']}")
                st.markdown(f"**Date:** {selected_article['published_date'] or 'Undated'}")
                st.markdown(f"**Category:** {selected_article['category'] or 'Uncategorized'}")
                st.markdown(f"**Importance Score:** {selected_article['importance_score']}")

                st.markdown("### Summary")
                st.write(selected_article["summary"] or "No summary available.")

                st.markdown("### Entities")
                entity_names = _split_names(selected_article["linked_entities"])
                st.markdown("\n".join(f"- {name}" for name in entity_names) or "- None")

                st.markdown("### Themes")
                theme_names = _split_names(selected_article["linked_themes"])
                st.markdown("\n".join(f"- {name}" for name in theme_names) or "- None")

                st.markdown("### Narratives")
                narrative_names = _split_names(selected_article["linked_narratives"])
                st.markdown("\n".join(f"- {name}" for name in narrative_names) or "- None")

            with right_col:
                st.markdown("### Original DOCX Preview")
                original_file_path = str(selected_article["original_file_path"] or "")
                preview_text = preview_docx(original_file_path)
                docx_missing = preview_text == "Original DOCX not found"
                st.text_area(
                    "Document preview",
                    preview_text,
                    height=600,
                )
                if docx_missing:
                    st.info("Original DOCX not found")
                elif _is_docx_preview_truncated(original_file_path):
                    st.caption("Preview truncated for display.")

                st.markdown("### Embedded Images")
                preview_images = preview_docx_images(original_file_path)
                if docx_missing:
                    st.caption("Image preview unavailable because the source DOCX could not be found.")
                elif not preview_images:
                    st.info("No embedded images found in this DOCX.")
                else:
                    for index, image_payload in enumerate(preview_images, start=1):
                        st.image(
                            image_payload,
                            caption=f"Embedded image {index}",
                            use_container_width=True,
                        )
                    if len(preview_images) == DOCX_IMAGE_LIMIT:
                        st.caption("Image preview limited for display.")

    with narratives_tab:
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

        filtered_narratives = _filter_related_rows(narratives_df, filtered_article_ids)
        filtered_narratives = filtered_narratives[
            filtered_narratives["status"].isin(selected_statuses)
        ]
        if search_term:
            filtered_narratives = filtered_narratives[
                filtered_narratives["name"].str.contains(search_term, case=False, na=False)
                | filtered_narratives["thesis"].str.contains(search_term, case=False, na=False)
            ]

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
        filtered_entities = _filter_related_rows(entities_df, filtered_article_ids)
        if search_term:
            filtered_entities = filtered_entities[
                filtered_entities["name"].str.contains(search_term, case=False, na=False)
                | filtered_entities["description"].str.contains(search_term, case=False, na=False)
            ]

        st.dataframe(
            filtered_entities[["name", "type", "description", "linked_articles", "graph_relationships"]],
            use_container_width=True,
            hide_index=True,
        )

    with trends_tab:
        if selected_week == "All":
            st.info("Select a specific week in the sidebar to view narrative trends.")
        else:
            trend_df = load_narrative_trends(selected_week)
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
        raw_graph = load_graph()
        if raw_graph is None:
            st.info("Graph export not found. Run python -m src.export_graph --format json first.")
        else:
            graph_payload = raw_graph
            graph_node_types = sorted({str(node.get("type", "")) for node in graph_payload.get("nodes", [])})
            graph_relationships = sorted(
                {str(edge.get("relationship", "")) for edge in graph_payload.get("edges", [])}
            )

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

            render_graph(
                graph_payload,
                filtered_article_ids,
                selected_node_types,
                selected_relationship_types,
                graph_search,
            )


if __name__ == "__main__":
    main()
