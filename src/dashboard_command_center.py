from __future__ import annotations

from html import escape
from pathlib import Path
import sys
from typing import Any

import pandas as pd
import streamlit as st

try:
    from .dashboard import (
        _render_chat_text,
        _render_graph_chat_result,
        build_comparison_payload,
        _filter_articles,
        _filter_related_rows,
        _first_sentence,
        _graph_snapshot_path,
        _path_version,
        list_available_graph_weeks,
        list_available_graph_weeks_neo4j,
        load_articles,
        load_articles_neo4j,
        load_entities,
        load_entities_neo4j,
        load_graph_snapshot,
        load_graph_snapshot_neo4j,
        load_metrics,
        load_metrics_neo4j,
        load_narrative_trends,
        load_narrative_trends_neo4j,
        load_narratives,
        load_narratives_neo4j,
        neo4j_dashboard_available,
        render_graph,
    )
    from .graph_rag import answer_question
    from .paths import PROCESSED_MANIFEST_PATH
except ImportError:
    PROJECT_ROOT = Path(__file__).resolve().parents[1]
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
    from src.dashboard import (
        _render_chat_text,
        _render_graph_chat_result,
        build_comparison_payload,
        _filter_articles,
        _filter_related_rows,
        _first_sentence,
        _graph_snapshot_path,
        _path_version,
        list_available_graph_weeks,
        list_available_graph_weeks_neo4j,
        load_articles,
        load_articles_neo4j,
        load_entities,
        load_entities_neo4j,
        load_graph_snapshot,
        load_graph_snapshot_neo4j,
        load_metrics,
        load_metrics_neo4j,
        load_narrative_trends,
        load_narrative_trends_neo4j,
        load_narratives,
        load_narratives_neo4j,
        neo4j_dashboard_available,
        render_graph,
    )
    from src.graph_rag import answer_question
    from src.paths import PROCESSED_MANIFEST_PATH


def inject_command_center_css() -> None:
    st.markdown(
        """
        <style>
        :root {
            --na-ink: #0f172a;
            --na-muted: #64748b;
            --na-line: #dbe3ef;
            --na-bg: #f4f7fb;
            --na-rail: #0f172a;
            --na-blue: #2563eb;
            --na-teal: #0f766e;
            --na-amber: #b45309;
            --na-red: #dc2626;
            --na-violet: #7c3aed;
        }
        .stApp {
            background: var(--na-bg) !important;
            color: var(--na-ink) !important;
            font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
        }
        .block-container {
            max-width: 1360px;
            padding-top: 1.15rem;
            padding-bottom: 3rem;
        }
        [data-testid="stHeader"],
        [data-testid="stToolbar"],
        .stDeployButton {
            display: none !important;
        }
        #MainMenu,
        footer {
            visibility: hidden !important;
        }
        h1, h2, h3 {
            font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif !important;
            letter-spacing: 0 !important;
            color: var(--na-ink);
        }
        [data-testid="stSidebar"] {
            background: var(--na-rail) !important;
            border-right: 1px solid #1e293b !important;
        }
        [data-testid="stSidebar"] * {
            color: #e2e8f0;
        }
        [data-testid="stSidebar"] [data-testid="stCaptionContainer"],
        [data-testid="stSidebar"] [data-testid="stCaptionContainer"] p,
        [data-testid="stSidebar"] .stMarkdown p {
            color: #94a3b8 !important;
        }
        [data-testid="stSidebar"] [data-baseweb="select"] > div,
        [data-testid="stSidebar"] .stTextInput > div > div {
            background: #172a45 !important;
            border: 1px solid #254465 !important;
            border-radius: 7px !important;
            color: #e2e8f0 !important;
        }
        [data-testid="stSidebar"] input,
        [data-testid="stSidebar"] [data-baseweb="select"] span {
            color: #e2e8f0 !important;
        }
        [data-testid="stSidebar"] label,
        [data-testid="stSidebar"] label p {
            color: #7dd3fc !important;
            font-size: 0.72rem !important;
            font-weight: 800 !important;
            text-transform: uppercase;
            letter-spacing: 0.08em;
        }
        .na-sidebar-brand {
            margin-bottom: 1rem;
        }
        .na-sidebar-title {
            color: #f8fafc;
            font-size: 1.45rem;
            line-height: 1.2;
            font-weight: 850;
        }
        .na-sidebar-copy {
            color: #94a3b8;
            font-size: 0.78rem;
            line-height: 1.35;
            margin-top: 0.25rem;
        }
        .na-source-card {
            display: flex;
            align-items: center;
            gap: 0.65rem;
            background: #12213a;
            border: 1px solid #243b5a;
            border-radius: 8px;
            padding: 0.8rem;
            margin: 0.7rem 0 1rem;
        }
        .na-source-dot {
            width: 0.62rem;
            height: 0.62rem;
            flex: 0 0 auto;
            border-radius: 999px;
            background: #2dd4bf;
        }
        .na-source-label {
            color: #94a3b8;
            font-size: 0.72rem;
            line-height: 1.1;
        }
        .na-source-value {
            color: #f8fafc;
            font-size: 0.95rem;
            font-weight: 800;
            line-height: 1.2;
        }
        .na-sidebar-snapshot {
            background: #f8fafc;
            border-radius: 8px;
            padding: 0.9rem;
            margin-top: 1rem;
        }
        .na-sidebar-snapshot-title {
            color: #0f172a;
            font-size: 0.9rem;
            font-weight: 850;
            margin-bottom: 0.45rem;
        }
        .na-sidebar-stat {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 0.65rem;
            padding: 0.22rem 0;
            font-size: 0.8rem;
        }
        .na-sidebar-stat span {
            color: #334155 !important;
        }
        .na-sidebar-stat strong {
            color: #0f172a !important;
        }
        .na-nav {
            display: flex;
            align-items: center;
            gap: 0.6rem;
            border-bottom: 1px solid var(--na-line);
            padding-bottom: 0.7rem;
            margin-bottom: 1.1rem;
            overflow-x: auto;
        }
        .na-nav-item {
            display: inline-flex;
            align-items: center;
            min-height: 2.45rem;
            border-radius: 8px;
            color: #475569;
            font-size: 0.86rem;
            font-weight: 800;
            padding: 0 0.95rem;
            white-space: nowrap;
        }
        .na-nav-item.active {
            background: #111827;
            color: #ffffff;
        }
        .na-command-bar {
            display: grid;
            grid-template-columns: minmax(18rem, 1fr) minmax(18rem, 23rem) auto auto;
            gap: 0.9rem;
            align-items: center;
            min-height: 5.4rem;
            background: #ffffff;
            border: 1px solid var(--na-line);
            border-radius: 8px;
            box-shadow: 0 14px 32px rgba(15, 23, 42, 0.06);
            padding: 1.1rem 1.35rem;
            margin-bottom: 1.15rem;
        }
        .na-command-title {
            font-size: 1.32rem;
            line-height: 1.28;
            font-weight: 850;
            color: var(--na-ink);
        }
        .na-command-subtitle {
            color: #7b8da8;
            font-size: 0.78rem;
            line-height: 1.35;
            margin-top: 0.15rem;
        }
        .na-command-query {
            min-height: 2.85rem;
            display: flex;
            align-items: center;
            border: 1px solid #cbd5e1;
            background: #f8fafc;
            border-radius: 8px;
            padding: 0 0.9rem;
            color: var(--na-muted);
            font-size: 0.9rem;
            overflow: hidden;
            white-space: nowrap;
            text-overflow: ellipsis;
        }
        .na-command-button {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            min-height: 2.5rem;
            border-radius: 8px;
            padding: 0 1rem;
            color: #ffffff;
            background: #111827;
            font-size: 0.82rem;
            font-weight: 850;
            white-space: nowrap;
        }
        .na-command-button.teal {
            background: var(--na-teal);
        }
        .na-metric-card {
            background: #ffffff;
            border: 1px solid var(--na-line);
            border-radius: 8px;
            box-shadow: 0 14px 32px rgba(15, 23, 42, 0.05);
            min-height: 7.1rem;
            padding: 1rem;
        }
        .na-metric-label {
            display: flex;
            align-items: center;
            justify-content: space-between;
            color: var(--na-muted);
            letter-spacing: 0.06em;
            font-size: 0.68rem;
            font-weight: 850;
            text-transform: uppercase;
            margin-bottom: 0.55rem;
        }
        .na-metric-dot {
            width: 0.55rem;
            height: 0.55rem;
            border-radius: 999px;
            background: var(--metric-accent, var(--na-blue));
        }
        .na-metric-value {
            color: var(--na-ink);
            font-size: 2rem;
            font-weight: 850;
            line-height: 1;
        }
        .na-metric-note {
            color: #475569;
            font-size: 0.78rem;
            line-height: 1.35;
            margin-top: 0.45rem;
        }
        .na-command-center {
            display: grid;
            grid-template-columns: minmax(32rem, 1.55fr) minmax(19rem, 0.9fr);
            gap: 1.5rem;
            margin: 1.35rem 0 1.5rem;
        }
        .na-panel {
            background: #ffffff;
            border: 1px solid var(--na-line);
            border-radius: 8px;
            box-shadow: 0 14px 32px rgba(15, 23, 42, 0.06);
            padding: 1.5rem;
            min-width: 0;
        }
        .na-panel.dark {
            background: #111827;
            border-color: #243244;
            color: #f8fafc;
        }
        .na-panel-head {
            display: flex;
            align-items: flex-start;
            justify-content: space-between;
            gap: 1rem;
            margin-bottom: 1.2rem;
        }
        .na-panel-title {
            color: inherit;
            font-size: 1.12rem;
            line-height: 1.25;
            font-weight: 850;
        }
        .na-panel-copy {
            color: #7b8da8;
            font-size: 0.78rem;
            line-height: 1.35;
            margin-top: 0.15rem;
        }
        .na-panel.dark .na-panel-copy {
            color: #94a3b8;
        }
        .na-chip-row {
            display: flex;
            flex-wrap: wrap;
            gap: 0.5rem;
            align-items: center;
        }
        .na-chip {
            display: inline-flex;
            align-items: center;
            min-height: 1.75rem;
            border-radius: 7px;
            padding: 0 0.65rem;
            background: #eff6ff;
            color: #1d4ed8;
            font-size: 0.7rem;
            font-weight: 850;
            white-space: nowrap;
        }
        .na-chip.teal { background: #ecfdf5; color: #047857; }
        .na-chip.amber { background: #fef3c7; color: #92400e; }
        .na-chip.violet { background: #f5f3ff; color: #6d28d9; }
        .na-chip.slate { background: #f1f5f9; color: #475569; }
        .na-live-stage {
            border-radius: 8px;
            border: 1px solid var(--na-line);
            background: #ffffff;
            overflow: hidden;
            margin-top: 0.9rem;
        }
        .na-graph-stage {
            height: 24rem;
            border-radius: 8px;
            border: 1px solid var(--na-line);
            background: #f8fafc;
            overflow: hidden;
        }
        .na-graph-stage svg {
            width: 100%;
            height: 100%;
            display: block;
        }
        .na-question-card {
            border: 1px solid #334155;
            background: #1e293b;
            border-radius: 8px;
            padding: 1rem;
            margin-bottom: 1.15rem;
        }
        .na-question-label,
        .na-answer-label {
            display: block;
            color: #7dd3fc;
            font-size: 0.64rem;
            font-weight: 900;
            letter-spacing: 0.06em;
            text-transform: uppercase;
            margin-bottom: 0.5rem;
        }
        .na-question-card strong {
            color: #f8fafc;
            font-size: 1rem;
            line-height: 1.25;
        }
        .na-answer-card {
            background: #f8fafc;
            color: var(--na-ink);
            border-radius: 8px;
            padding: 1rem;
            margin-bottom: 1.15rem;
        }
        .na-answer-label {
            color: #475569;
        }
        .na-answer-card p {
            color: var(--na-ink);
            font-size: 0.95rem;
            line-height: 1.34;
            margin: 0;
        }
        .na-lower-grid {
            display: grid;
            grid-template-columns: repeat(3, minmax(0, 1fr));
            gap: 1.5rem;
            margin-bottom: 1.5rem;
        }
        .na-trend-row,
        .na-dist-row {
            display: grid;
            align-items: center;
            min-height: 1.9rem;
            font-size: 0.78rem;
        }
        .na-trend-row {
            grid-template-columns: 6.8rem 1fr 2.2rem;
            gap: 0.65rem;
        }
        .na-dist-row {
            grid-template-columns: minmax(5.8rem, 0.62fr) 1fr 2.2rem;
            gap: 0.65rem;
        }
        .na-trend-row span,
        .na-dist-row span {
            color: #64748b;
            overflow: hidden;
            white-space: nowrap;
            text-overflow: ellipsis;
        }
        .na-trend-row strong,
        .na-dist-row strong {
            color: #0f172a;
            text-align: right;
        }
        .na-bar {
            height: 0.5rem;
            border-radius: 999px;
            background: #e2e8f0;
            overflow: hidden;
        }
        .na-fill {
            height: 100%;
            border-radius: inherit;
            background: var(--bar-color, var(--na-teal));
            width: var(--bar-width, 0%);
        }
        .na-article-title {
            margin-top: 1.05rem;
            color: var(--na-ink);
            font-size: 1.25rem;
            line-height: 1.25;
            font-weight: 850;
        }
        .na-article-copy {
            color: #334155;
            font-size: 0.84rem;
            line-height: 1.45;
            margin: 0.65rem 0 1rem;
        }
        .na-pipeline-row {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 0.75rem;
            border-top: 1px solid #eef2f7;
            padding-top: 0.65rem;
            margin-top: 0.65rem;
            font-size: 0.8rem;
        }
        .na-table-wrap {
            background: #ffffff;
            border: 1px solid var(--na-line);
            border-radius: 8px;
            box-shadow: 0 14px 32px rgba(15, 23, 42, 0.05);
            padding: 1.1rem;
            margin-bottom: 1.5rem;
        }
        .na-live-ask [data-testid="stForm"],
        .na-live-ask [data-testid="stChatMessage"] {
            border-radius: 8px !important;
        }
        .na-live-ask textarea {
            min-height: 7rem !important;
        }
        .stDataFrame {
            border-radius: 8px;
            overflow: hidden;
        }
        @media (max-width: 1100px) {
            .na-command-bar,
            .na-command-center,
            .na-lower-grid {
                grid-template-columns: 1fr;
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _load_dashboard_data() -> tuple[
    dict[str, int],
    pd.DataFrame,
    pd.DataFrame,
    pd.DataFrame,
    str,
    str,
    tuple[int, int],
]:
    registry_version = _path_version(PROCESSED_MANIFEST_PATH)
    data_backend = "Processed JSON"
    neo4j_warning = ""

    if neo4j_dashboard_available(0):
        try:
            return (
                load_metrics_neo4j(0),
                load_articles_neo4j(0),
                load_narratives_neo4j(0),
                load_entities_neo4j(0),
                "Neo4j",
                "",
                registry_version,
            )
        except Exception as exc:
            neo4j_warning = f"Neo4j dashboard read failed; using processed JSON fallback. Details: {exc}"

    return (
        load_metrics(registry_version),
        load_articles(registry_version),
        load_narratives(registry_version),
        load_entities(registry_version),
        data_backend,
        neo4j_warning,
        registry_version,
    )


def _render_sidebar(metrics: dict[str, int], data_backend: str, neo4j_warning: str) -> None:
    st.sidebar.markdown(
        f"""
        <div class="na-sidebar-brand">
          <div class="na-sidebar-title">Narrative Agent</div>
          <div class="na-sidebar-copy">Neo4j-first GraphRAG command center</div>
        </div>
        <div class="na-source-card">
          <span class="na-source-dot"></span>
          <div>
            <div class="na-source-label">Primary graph source</div>
            <div class="na-source-value">{escape(data_backend)}</div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    if neo4j_warning:
        st.sidebar.warning(neo4j_warning)
    st.sidebar.caption("Filters")
    st.sidebar.markdown(
        f"""
        <div class="na-sidebar-snapshot">
          <div class="na-sidebar-snapshot-title">Database Snapshot</div>
          <div class="na-sidebar-stat"><span>Articles</span><strong>{metrics.get('articles', 0)}</strong></div>
          <div class="na-sidebar-stat"><span>Entities</span><strong>{metrics.get('entities', 0)}</strong></div>
          <div class="na-sidebar-stat"><span>Themes</span><strong>{metrics.get('themes', 0)}</strong></div>
          <div class="na-sidebar-stat"><span>Narratives</span><strong>{metrics.get('narratives', 0)}</strong></div>
          <div class="na-sidebar-stat"><span>Graph Edges</span><strong>{metrics.get('graph_edges', 0)}</strong></div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_nav() -> None:
    nav_items = ["Overview", "Articles", "Narratives", "Entities", "Graph View"]
    nav_html = "".join(
        f'<span class="na-nav-item{" active" if item == "Overview" else ""}">{escape(item)}</span>'
        for item in nav_items
    )
    st.markdown(f'<nav class="na-nav">{nav_html}</nav>', unsafe_allow_html=True)


def _render_command_bar(selected_week: str, total_filtered: int, avg_importance: float, search_term: str) -> None:
    scope_label = selected_week if selected_week != "All" else "All indexed weeks"
    query_label = search_term or "GraphRAG and live network are connected below"
    st.markdown(
        f"""
        <div class="na-command-bar">
          <div>
            <div class="na-command-title">Narrative Intelligence Console</div>
            <div class="na-command-subtitle">
              Latest scope: {escape(scope_label)} | {total_filtered} article(s) | average importance {avg_importance:.1f}
            </div>
          </div>
          <div class="na-command-query">{escape(query_label)}</div>
          <div class="na-command-button teal">GraphRAG Ready</div>
          <div class="na-command-button">Live Graph</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_metric_card(label: str, value: str, note: str, accent: str) -> None:
    st.markdown(
        f"""
        <div class="na-metric-card" style="--metric-accent:{escape(accent)};">
          <div class="na-metric-label">{escape(label)}<span class="na-metric-dot"></span></div>
          <div class="na-metric-value">{escape(value)}</div>
          <div class="na-metric-note">{escape(note)}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _graph_preview_stats(
    preview_graph: dict[str, Any] | None,
    metrics: dict[str, int],
) -> tuple[int, int, str]:
    if preview_graph:
        return (
            len(preview_graph.get("nodes", [])),
            len(preview_graph.get("edges", [])),
            str(preview_graph.get("week") or "latest"),
        )
    return (
        int(metrics.get("entities", 0)) + int(metrics.get("themes", 0)) + int(metrics.get("narratives", 0)),
        int(metrics.get("graph_edges", 0)),
        "latest",
    )


def _available_graph_weeks(data_backend: str) -> list[str]:
    try:
        if data_backend == "Neo4j":
            return list_available_graph_weeks_neo4j(0)
        return list_available_graph_weeks()
    except Exception:
        return []


def _load_graph_for_week(data_backend: str, week_label: str | None) -> dict[str, Any] | None:
    try:
        if data_backend == "Neo4j":
            return load_graph_snapshot_neo4j(week_label, 0)
        graph_path = _graph_snapshot_path(week_label)
        return load_graph_snapshot(week_label, _path_version(graph_path))
    except Exception:
        return None


def _resolved_graph_week(
    preview_graph: dict[str, Any] | None,
    data_backend: str,
    selected_week: str,
) -> str:
    if preview_graph and preview_graph.get("week"):
        return str(preview_graph["week"])
    if selected_week != "All":
        return selected_week
    weeks = _available_graph_weeks(data_backend)
    return weeks[-1] if weeks else "latest"


def _render_command_center(
    metrics: dict[str, int],
    preview_graph: dict[str, Any] | None,
    filtered_articles: pd.DataFrame,
    filtered_narratives: pd.DataFrame,
    selected_week: str,
    search_term: str,
    data_backend: str,
) -> None:
    node_count, edge_count, graph_week = _graph_preview_stats(preview_graph, metrics)
    resolved_week = _resolved_graph_week(preview_graph, data_backend, selected_week)
    graph_col, ask_col = st.columns([1.45, 0.85], gap="large")

    with graph_col:
        st.markdown(
            f"""
            <div class="na-panel">
              <div class="na-panel-head">
                <div>
                  <div class="na-panel-title">Relationship Graph</div>
                  <div class="na-panel-copy">Week-scoped evidence network | {escape(resolved_week)}</div>
                </div>
                <div class="na-chip-row">
                  <span class="na-chip">{node_count} nodes</span>
                  <span class="na-chip teal">{edge_count} links</span>
                </div>
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        _render_live_graph(preview_graph, data_backend, resolved_week)

    with ask_col:
        st.markdown(
            """
            <div class="na-panel">
              <div class="na-panel-title">Ask Graph</div>
              <div class="na-panel-copy">GraphRAG answer with retrieved facts and citations</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        _render_live_ask_graph(selected_week, data_backend, search_term)


def _render_live_graph(
    preview_graph: dict[str, Any] | None,
    data_backend: str,
    graph_week: str,
) -> None:
    if preview_graph is None:
        st.info("No graph snapshot is available for the current scope.")
        return

    available_weeks = _available_graph_weeks(data_backend)
    previous_week = None
    if graph_week in available_weeks:
        graph_week_index = available_weeks.index(graph_week)
        previous_week = available_weeks[graph_week_index - 1] if graph_week_index > 0 else None

    with st.expander("Graph controls", expanded=False):
        control_cols = st.columns([0.28, 0.52, 0.20], gap="large")
        graph_node_types = sorted({str(node.get("type", "")) for node in preview_graph.get("nodes", [])})
        graph_relationships = sorted({str(edge.get("relationship", "")) for edge in preview_graph.get("edges", [])})
        with control_cols[0]:
            selected_node_types = st.multiselect(
                "Node types",
                options=graph_node_types,
                default=graph_node_types,
                key="cc_graph_node_types",
            )
        with control_cols[1]:
            selected_relationship_types = st.multiselect(
                "Relationship types",
                options=graph_relationships,
                default=graph_relationships,
                key="cc_graph_relationship_types",
            )
        with control_cols[2]:
            graph_search = st.text_input("Search node", value="", key="cc_graph_search")
        compare_with_previous = st.toggle(
            "Compare with previous week",
            value=False,
            disabled=previous_week is None,
            key="cc_graph_compare",
        )

    graph_payload = preview_graph
    if compare_with_previous and previous_week is not None:
        previous_graph = _load_graph_for_week(data_backend, previous_week)
        if previous_graph is not None:
            graph_payload, _, emerging_entities = build_comparison_payload(preview_graph, previous_graph)
            if emerging_entities:
                st.caption(
                    "Top emerging entities: "
                    + ", ".join(f"{label} (+{delta})" for label, delta in emerging_entities)
                )

    st.markdown('<div class="na-live-stage">', unsafe_allow_html=True)
    render_graph(
        graph_payload,
        selected_node_types,
        selected_relationship_types,
        graph_search,
    )
    st.markdown("</div>", unsafe_allow_html=True)


def _render_live_ask_graph(selected_week: str, data_backend: str, search_term: str) -> None:
    if data_backend != "Neo4j":
        st.info("Graph chat is unavailable because Neo4j is not reachable.")
        return

    chat_week = None if selected_week == "All" else selected_week
    if "cc_graph_chat_messages" not in st.session_state:
        st.session_state.cc_graph_chat_messages = []

    control_cols = st.columns([0.62, 0.38], gap="small")
    with control_cols[0]:
        deep_immediately = st.toggle("Deep answer", value=False, key="cc_graph_deep_now")
    with control_cols[1]:
        if st.button("Clear", key="cc_graph_chat_clear"):
            st.session_state.cc_graph_chat_messages = []
            st.rerun()

    for message_index, message in enumerate(st.session_state.cc_graph_chat_messages):
        with st.chat_message(message["role"]):
            _render_chat_text(message["content"])
            if message.get("result"):
                _render_graph_chat_result(message["result"])
                evaluation = message["result"].get("evaluation", {})
                can_deepen = (
                    message.get("role") == "assistant"
                    and message.get("question")
                    and not evaluation.get("deep_search", False)
                )
                if can_deepen and st.button(
                    "Deep answer with embeddings",
                    key=f"cc_graph_chat_deep_{message_index}",
                ):
                    with st.spinner("Running embedding search and LLM synthesis..."):
                        try:
                            deep_result = answer_question(
                                str(message["question"]),
                                week=message.get("week"),
                                use_llm=True,
                                deep_search=True,
                                backend="neo4j",
                            )
                        except Exception as exc:
                            st.session_state.cc_graph_chat_messages.append(
                                {"role": "assistant", "content": f"Deep answer failed: {exc}"}
                            )
                            st.rerun()
                            return
                    st.session_state.cc_graph_chat_messages.append(
                        {
                            "role": "assistant",
                            "content": deep_result.answer,
                            "result": deep_result.model_dump(),
                            "question": message["question"],
                            "week": message.get("week"),
                        }
                    )
                    st.rerun()
                    return

    with st.form("cc_graph_rag_query_form", clear_on_submit=True):
        prompt = st.text_area(
            "Question",
            value=search_term,
            placeholder="Ask about entities, relationships, narratives, or recent changes",
            key="cc_graph_question",
            height=116,
        )
        submitted = st.form_submit_button("Ask graph", type="primary")

    if not submitted:
        return

    prompt = prompt.strip()
    if not prompt:
        st.warning("Please enter a question.")
        return

    st.session_state.cc_graph_chat_messages.append({"role": "user", "content": prompt})
    with st.spinner("Querying graph facts..."):
        try:
            result = answer_question(
                prompt,
                week=chat_week,
                use_llm=deep_immediately,
                deep_search=deep_immediately,
                backend="neo4j",
            )
        except Exception as exc:
            st.session_state.cc_graph_chat_messages.append(
                {"role": "assistant", "content": f"Neo4j backend is not available yet: {exc}"}
            )
            st.rerun()
            return

    st.session_state.cc_graph_chat_messages.append(
        {
            "role": "assistant",
            "content": result.answer,
            "result": result.model_dump(),
            "question": prompt,
            "week": chat_week,
        }
    )
    st.rerun()


def _trend_status_counts(trend_df: pd.DataFrame, filtered_narratives: pd.DataFrame) -> dict[str, int]:
    if not trend_df.empty and "trend_status" in trend_df.columns:
        counts = trend_df["trend_status"].fillna("").astype(str).str.upper().value_counts().to_dict()
        return {
            "New": int(counts.get("NEW", 0)),
            "Strengthening": int(counts.get("STRENGTHENING", 0)),
            "Recurring": int(counts.get("RECURRING", 0)),
            "Weakening": int(counts.get("WEAKENING", 0)),
        }
    if not filtered_narratives.empty and "status" in filtered_narratives.columns:
        counts = filtered_narratives["status"].fillna("").astype(str).str.lower().value_counts().to_dict()
        return {
            "New": int(counts.get("emerging", 0)),
            "Strengthening": int(counts.get("strengthening", 0)),
            "Recurring": int(counts.get("stable", 0)),
            "Weakening": int(counts.get("weakening", 0)),
        }
    return {"New": 0, "Strengthening": 0, "Recurring": 0, "Weakening": 0}


def _render_bar_rows(
    values: dict[str, int],
    *,
    labels: list[str] | None = None,
    color: str = "#0f766e",
    row_class: str = "na-dist-row",
) -> str:
    if labels is None:
        labels = list(values.keys())
    max_count = max([*values.values(), 1])
    rows = []
    for label in labels:
        value = values.get(label, 0)
        width = max(4, int((value / max_count) * 100)) if value else 4
        rows.append(
            f'<div class="{row_class}">'
            f'<span>{escape(str(label))}</span>'
            f'<div class="na-bar">'
            f'<div class="na-fill" style="--bar-width:{width}%;--bar-color:{escape(color)};"></div>'
            f'</div>'
            f'<strong>{value}</strong>'
            f'</div>'
        )
    return "".join(rows)


def _value_counts(dataframe: pd.DataFrame, column: str, limit: int = 4) -> dict[str, int]:
    if dataframe.empty or column not in dataframe.columns:
        return {}
    counts = dataframe[column].fillna("Unknown").astype(str).replace("", "Unknown").value_counts().head(limit)
    return {str(label): int(value) for label, value in counts.items()}


def _render_lower_panels(
    metrics: dict[str, int],
    filtered_articles: pd.DataFrame,
    filtered_narratives: pd.DataFrame,
    filtered_entities: pd.DataFrame,
    trend_df: pd.DataFrame,
    selected_week: str,
    data_backend: str,
) -> None:
    trend_counts = _trend_status_counts(trend_df, filtered_narratives)
    trend_rows = _render_bar_rows(
        trend_counts,
        labels=["New", "Strengthening", "Recurring", "Weakening"],
        color="#2563eb",
        row_class="na-trend-row",
    )
    source_rows = _render_bar_rows(_value_counts(filtered_articles, "source"), color="#0f766e")

    if filtered_articles.empty:
        article_title = "No article selected"
        article_summary = "Adjust filters to inspect an article extraction."
        article_category = "None"
        article_importance = "0"
    else:
        article = filtered_articles.sort_values(
            by=["importance_score", "published_date"],
            ascending=[False, False],
        ).iloc[0]
        article_title = str(article.get("title") or "Untitled")
        article_summary = _first_sentence(str(article.get("summary") or ""), 170)
        article_category = str(article.get("category") or "Uncategorized")
        article_importance = str(article.get("importance_score") or "0")

    st.markdown(
        f"""
        <div class="na-lower-grid">
          <section class="na-panel">
            <div class="na-panel-title">Narrative Movement</div>
            <div class="na-panel-copy">{escape(selected_week)} trend status</div>
            <div style="height:1rem;"></div>
            {trend_rows}
          </section>
          <section class="na-panel">
            <div class="na-panel-title">Article Workbench</div>
            <div class="na-panel-copy">Highest priority extraction</div>
            <div class="na-article-title">{escape(article_title)}</div>
            <p class="na-article-copy">{escape(article_summary)}</p>
            <div class="na-chip-row">
              <span class="na-chip">{escape(article_category)}</span>
              <span class="na-chip amber">Importance {escape(article_importance)}</span>
              <span class="na-chip violet">{len(filtered_narratives)} narratives</span>
            </div>
          </section>
          <section class="na-panel">
            <div class="na-panel-title">Coverage Shape</div>
            <div class="na-panel-copy">Active sources in current scope</div>
            <div style="height:1rem;"></div>
            {source_rows or '<div class="na-panel-copy">No source data in scope.</div>'}
            <div class="na-pipeline-row"><span>Graph source</span><span class="na-chip teal">{escape(data_backend)}</span></div>
            <div class="na-pipeline-row"><span>Visible entities</span><span class="na-chip">{len(filtered_entities)}</span></div>
            <div class="na-pipeline-row"><span>Total graph edges</span><span class="na-chip">{metrics.get('graph_edges', 0)}</span></div>
          </section>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_article_snapshot(filtered_articles: pd.DataFrame) -> None:
    st.markdown(
        """
        <div class="na-table-wrap">
          <div class="na-panel-title">Operating Snapshot</div>
          <div class="na-panel-copy">Priority articles in the current scope</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    if filtered_articles.empty:
        st.info("No articles match the current filters.")
        return
    display_columns = ["title", "source", "published_date", "category", "importance_score"]
    available_columns = [column for column in display_columns if column in filtered_articles.columns]
    snapshot = filtered_articles.sort_values(
        by=["importance_score", "published_date"],
        ascending=[False, False],
    )[available_columns].head(12)
    st.dataframe(snapshot, width="stretch", hide_index=True)


def _load_preview_graph(data_backend: str, selected_week: str) -> dict[str, Any] | None:
    preview_graph_week = None if selected_week == "All" else selected_week
    try:
        if data_backend == "Neo4j":
            return load_graph_snapshot_neo4j(preview_graph_week, 0)
        preview_graph_path = _graph_snapshot_path(preview_graph_week)
        return load_graph_snapshot(preview_graph_week, _path_version(preview_graph_path))
    except Exception:
        return None


def _load_trends(data_backend: str, selected_week: str, registry_version: tuple[int, int]) -> pd.DataFrame:
    if selected_week == "All":
        return pd.DataFrame()
    try:
        if data_backend == "Neo4j":
            return load_narrative_trends_neo4j(selected_week, 0)
        return load_narrative_trends(selected_week, registry_version)
    except Exception:
        return pd.DataFrame()


def main() -> None:
    st.set_page_config(
        page_title="Narrative Agent Command Center",
        page_icon="N",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    inject_command_center_css()

    metrics, articles_df, narratives_df, entities_df, data_backend, neo4j_warning, registry_version = _load_dashboard_data()
    if articles_df.empty:
        st.error("Neo4j is unavailable and no processed extraction artifacts were found. Run the pipeline first.")
        if neo4j_warning:
            st.caption(neo4j_warning)
        return

    _render_sidebar(metrics, data_backend, neo4j_warning)
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
    filtered_entities = _filter_related_rows(entities_df, filtered_article_ids)
    trend_df = _load_trends(data_backend, selected_week, registry_version)
    preview_graph = _load_preview_graph(data_backend, selected_week)

    total_filtered = len(filtered_articles)
    avg_importance = float(filtered_articles["importance_score"].fillna(0).mean()) if total_filtered else 0.0
    active_sources = filtered_articles["source"].replace("", pd.NA).dropna().nunique() if not filtered_articles.empty else 0
    priority_count = int(filtered_articles["importance_score"].fillna(0).ge(8).sum()) if not filtered_articles.empty else 0

    _render_nav()
    _render_command_bar(selected_week, total_filtered, avg_importance, search_term)

    metric_columns = st.columns(4)
    with metric_columns[0]:
        _render_metric_card("Articles in Scope", str(total_filtered), "Current filtered working set", "#2563eb")
    with metric_columns[1]:
        _render_metric_card("Average Importance", f"{avg_importance:.1f}", "Mean article score across the current view", "#0f766e")
    with metric_columns[2]:
        _render_metric_card("Sources Active", str(active_sources), "Distinct publications in view", "#b45309")
    with metric_columns[3]:
        _render_metric_card("Priority Coverage", str(priority_count), "Articles scoring 8 or higher", "#7c3aed")

    _render_command_center(
        metrics,
        preview_graph,
        filtered_articles,
        filtered_narratives,
        selected_week,
        search_term,
        data_backend,
    )
    _render_lower_panels(
        metrics,
        filtered_articles,
        filtered_narratives,
        filtered_entities,
        trend_df,
        selected_week,
        data_backend,
    )
    _render_article_snapshot(filtered_articles)


if __name__ == "__main__":
    main()
