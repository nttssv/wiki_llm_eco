"""Neo4j-backed hybrid GraphRAG retrieval."""

from __future__ import annotations

import re
from typing import Any

from .config import get_neo4j_database
from .embeddings import embed_texts, embeddings_enabled
from .graph_rag import (
    Citation,
    GraphFact,
    GraphRAGService,
    GraphRAGState,
    QueryIntent,
    STOPWORDS,
    VALUATION_TERMS,
    _anchor_terms,
    _compact,
    _contains_any,
    _fold,
    _unique,
)
from .neo4j_store import neo4j_driver


class Neo4jGraphRAGService(GraphRAGService):
    """Run GraphRAG retrieval against Neo4j, reusing the base workflow."""

    def __init__(self) -> None:
        super().__init__()
        self._driver = neo4j_driver()
        self._driver.verify_connectivity()

    def _retrieve_graph_node(self, state: GraphRAGState) -> GraphRAGState:
        intent = state["intent"]
        deep_search = bool(state.get("deep_search", False))
        warnings = list(state.get("warnings", []))
        with self._driver.session(database=get_neo4j_database()) as session:
            selected_week = state.get("week") or self._default_week_neo4j(session, intent)
            labels = [
                str(row["name"])
                for row in session.run("MATCH (e:Entity) RETURN DISTINCT e.name AS name").data()
                if row.get("name")
            ]
            terms = self._resolve_terms(intent, labels)
            anchors = _anchor_terms(intent, terms)
            query_terms = _unique([*terms, *intent.search_terms])
            query_vector: list[float] | None = None
            if deep_search and embeddings_enabled():
                try:
                    query_vector = embed_texts([state["question"]])[0]
                except Exception:
                    warnings.append("Embedding search failed; used graph/text retrieval only.")
            facts = [
                *self._article_facts(session, query_terms, anchors, selected_week),
                *self._edge_facts(session, query_terms, anchors, intent.relationship_types, selected_week),
                *self._event_facts(session, query_terms, anchors, selected_week),
                *self._narrative_facts(session, query_terms, anchors, selected_week),
                *self._chunk_facts(session, query_terms, anchors, query_vector, selected_week),
                *self._image_facts(session, query_terms, anchors, query_vector, selected_week),
            ]
            facts = self._rank_facts(facts, intent)[:48 if deep_search else 32]
            citations = self._build_citations(facts)

        return {"facts": facts, "citations": citations, "warnings": warnings}

    def _default_week_neo4j(self, session: Any, intent: QueryIntent) -> str | None:
        if intent.time_scope != "latest":
            return None
        row = session.run(
            """
            MATCH (a:Article)
            WHERE coalesce(a.week, '') <> ''
            RETURN a.week AS week
            ORDER BY week DESC
            LIMIT 1
            """
        ).single()
        return str(row["week"]) if row and row.get("week") else None

    def _article_facts(self, session: Any, terms: list[str], anchors: list[str], week: str | None) -> list[GraphFact]:
        rows = session.run(
            """
            MATCH (article:Article)
            OPTIONAL MATCH (article)-[:MENTIONS]->(entity:Entity)
            WITH article, collect(toLower(entity.name)) AS entity_names
            WHERE ($week IS NULL OR article.week = $week)
              AND (
                size($anchors) = 0 OR any(term IN $anchors WHERE
                  toLower(article.title) CONTAINS term OR toLower(article.summary) CONTAINS term
                  OR any(entity_name IN entity_names WHERE entity_name CONTAINS term)
                )
              )
              AND (
                size($anchors) > 0 OR size($terms) = 0 OR any(term IN $terms WHERE
                  toLower(article.title) CONTAINS term OR toLower(article.summary) CONTAINS term
                  OR any(entity_name IN entity_names WHERE entity_name CONTAINS term)
                )
              )
            RETURN article.id AS article_id, article.title AS title, article.source AS source,
                   article.published_date AS published_date, article.summary AS summary,
                   article.importance_score AS importance_score
            ORDER BY article.published_date DESC, article.importance_score DESC
            LIMIT 8
            """,
            terms=_fold_terms(terms),
            anchors=_fold_terms(anchors),
            week=week,
        ).data()
        return [
            GraphFact(
                kind="article",
                article_id=str(row["article_id"] or ""),
                title=str(row["title"] or ""),
                source=str(row["source"] or ""),
                published_date=str(row["published_date"] or ""),
                statement=_compact(str(row["summary"] or ""), limit=900),
                quote=_compact(str(row["summary"] or ""), limit=900),
                confidence=float(row.get("importance_score") or 0) / 10,
            )
            for row in rows
        ]

    def _edge_facts(
        self,
        session: Any,
        terms: list[str],
        anchors: list[str],
        relationship_types: list[str],
        week: str | None,
    ) -> list[GraphFact]:
        rows = session.run(
            """
            MATCH (source:Entity)-[rel:RELATES_TO]->(target:Entity)
            MATCH (article:Article {id: rel.evidence_article_id})
            WHERE ($week IS NULL OR article.week = $week)
              AND (size($relationships) = 0 OR rel.relationship IN $relationships)
              AND (
                size($anchors) = 0 OR any(term IN $anchors WHERE
                  toLower(source.name) CONTAINS term OR toLower(target.name) CONTAINS term
                )
              )
              AND (
                size($anchors) > 0 OR size($terms) = 0 OR any(term IN $terms WHERE
                  toLower(source.name) CONTAINS term OR toLower(target.name) CONTAINS term
                  OR toLower(article.title) CONTAINS term
                )
              )
            RETURN source.name AS source_node, rel.relationship AS relationship,
                   target.name AS target_node, rel.confidence AS confidence,
                   article.id AS article_id, article.title AS title, article.source AS source,
                   article.published_date AS published_date, article.summary AS summary
            ORDER BY article.published_date DESC, rel.confidence DESC
            LIMIT 24
            """,
            terms=_fold_terms(terms),
            anchors=_fold_terms(anchors),
            relationships=relationship_types,
            week=week,
        ).data()
        return [
            GraphFact(
                kind="edge",
                article_id=str(row["article_id"] or ""),
                title=str(row["title"] or ""),
                source=str(row["source"] or ""),
                published_date=str(row["published_date"] or ""),
                source_node=str(row["source_node"] or ""),
                relationship=str(row["relationship"] or ""),
                target_node=str(row["target_node"] or ""),
                confidence=float(row["confidence"] or 0),
                statement=f"{row['source_node']} --{row['relationship']}--> {row['target_node']}",
                quote=_compact(str(row["summary"] or "")),
            )
            for row in rows
        ]

    def _event_facts(
        self,
        session: Any,
        terms: list[str],
        anchors: list[str],
        week: str | None,
    ) -> list[GraphFact]:
        rows = session.run(
            """
            MATCH (article:Article)-[:HAS_EVENT]->(ev:Event)
            WHERE ($week IS NULL OR article.week = $week)
              AND (
                size($anchors) = 0 OR any(term IN $anchors WHERE
                  toLower(ev.event_summary) CONTAINS term OR toLower(article.title) CONTAINS term
                )
              )
              AND (
                size($anchors) > 0 OR size($terms) = 0 OR any(term IN $terms WHERE
                  toLower(ev.event_summary) CONTAINS term OR toLower(article.title) CONTAINS term
                )
              )
            RETURN article.id AS article_id, article.title AS title, article.source AS source,
                   article.published_date AS published_date, ev.event_date AS event_date,
                   ev.event_summary AS event_summary
            ORDER BY article.published_date DESC, ev.importance_score DESC
            LIMIT 16
            """,
            terms=_fold_terms(terms),
            anchors=_fold_terms(anchors),
            week=week,
        ).data()
        return [
            GraphFact(
                kind="event",
                article_id=str(row["article_id"] or ""),
                title=str(row["title"] or ""),
                source=str(row["source"] or ""),
                published_date=str(row["published_date"] or ""),
                statement=f"{row['event_date']}: {row['event_summary']}",
                quote=_compact(str(row["event_summary"] or "")),
            )
            for row in rows
        ]

    def _narrative_facts(
        self,
        session: Any,
        terms: list[str],
        anchors: list[str],
        week: str | None,
    ) -> list[GraphFact]:
        rows = session.run(
            """
            MATCH (article:Article)-[:HAS_NARRATIVE]->(n:Narrative)
            WHERE ($week IS NULL OR article.week = $week)
              AND (
                size($anchors) = 0 OR any(term IN $anchors WHERE
                  toLower(n.name) CONTAINS term OR toLower(n.thesis) CONTAINS term
                  OR toLower(article.title) CONTAINS term
                )
              )
              AND (
                size($anchors) > 0 OR size($terms) = 0 OR any(term IN $terms WHERE
                  toLower(n.name) CONTAINS term OR toLower(n.thesis) CONTAINS term
                  OR toLower(article.title) CONTAINS term
                )
              )
            RETURN article.id AS article_id, article.title AS title, article.source AS source,
                   article.published_date AS published_date, n.name AS name, n.status AS status,
                   n.thesis AS thesis
            ORDER BY article.published_date DESC, n.importance_score DESC
            LIMIT 16
            """,
            terms=_fold_terms(terms),
            anchors=_fold_terms(anchors),
            week=week,
        ).data()
        return [
            GraphFact(
                kind="narrative",
                article_id=str(row["article_id"] or ""),
                title=str(row["title"] or ""),
                source=str(row["source"] or ""),
                published_date=str(row["published_date"] or ""),
                statement=f"{row['name']} ({row['status']}): {row['thesis']}",
                quote=_compact(str(row["thesis"] or "")),
            )
            for row in rows
        ]

    def _chunk_facts(
        self,
        session: Any,
        terms: list[str],
        anchors: list[str],
        query_vector: list[float] | None,
        week: str | None,
    ) -> list[GraphFact]:
        facts = self._chunk_text_facts(session, terms, anchors, week)
        if query_vector is not None:
            try:
                facts.extend(self._chunk_vector_facts(session, query_vector, terms, anchors, week))
            except Exception:
                pass
        return _dedupe_facts(facts)

    def _chunk_vector_facts(
        self,
        session: Any,
        vector: list[float],
        terms: list[str],
        anchors: list[str],
        week: str | None,
    ) -> list[GraphFact]:
        rows = session.run(
            """
            CALL db.index.vector.queryNodes('chunk_embedding', 12, $embedding)
            YIELD node, score
            MATCH (article:Article)-[:HAS_CHUNK]->(node)
            WHERE $week IS NULL OR article.week = $week
              AND (
                size($anchors) = 0 OR any(term IN $anchors WHERE
                  toLower(node.text) CONTAINS term OR toLower(article.title) CONTAINS term
                )
              )
            RETURN article.id AS article_id, article.title AS title, article.source AS source,
                   article.published_date AS published_date, node.text AS text, score
            ORDER BY score DESC
            LIMIT 12
            """,
            embedding=vector,
            anchors=_fold_terms(anchors),
            week=week,
        ).data()
        return [_chunk_row_to_fact(row, terms) for row in rows]

    def _chunk_text_facts(
        self,
        session: Any,
        terms: list[str],
        anchors: list[str],
        week: str | None,
    ) -> list[GraphFact]:
        match_terms = anchors or terms
        if not match_terms:
            return []
        rows = session.run(
            """
            MATCH (article:Article)-[:HAS_CHUNK]->(chunk:Chunk)
            WHERE ($week IS NULL OR article.week = $week)
              AND any(term IN $terms WHERE
                toLower(chunk.text) CONTAINS term OR toLower(article.title) CONTAINS term
              )
            RETURN article.id AS article_id, article.title AS title, article.source AS source,
                   article.published_date AS published_date, chunk.text AS text, 1.0 AS score
            ORDER BY article.published_date DESC, chunk.chunk_index ASC
            LIMIT 12
            """,
            terms=_fold_terms(match_terms),
            week=week,
        ).data()
        return [_chunk_row_to_fact(row, match_terms) for row in rows]

    def _image_facts(
        self,
        session: Any,
        terms: list[str],
        anchors: list[str],
        query_vector: list[float] | None,
        week: str | None,
    ) -> list[GraphFact]:
        facts = self._image_text_facts(session, terms, anchors, week)
        if query_vector is not None:
            try:
                facts.extend(self._image_vector_facts(session, query_vector, week))
            except Exception:
                pass
        return _dedupe_facts(facts)

    def _image_vector_facts(self, session: Any, vector: list[float], week: str | None) -> list[GraphFact]:
        rows = session.run(
            """
            CALL db.index.vector.queryNodes('image_embedding', 8, $embedding)
            YIELD node, score
            MATCH (article:Article)-[:HAS_IMAGE]->(node)
            WHERE $week IS NULL OR article.week = $week
            RETURN article.id AS article_id, article.title AS title, article.source AS source,
                   article.published_date AS published_date, node.caption_hint AS caption_hint,
                   node.source_path AS source_path, score
            ORDER BY score DESC
            LIMIT 8
            """,
            embedding=vector,
            week=week,
        ).data()
        return [_image_row_to_fact(row) for row in rows]

    def _image_text_facts(
        self,
        session: Any,
        terms: list[str],
        anchors: list[str],
        week: str | None,
    ) -> list[GraphFact]:
        match_terms = anchors or terms
        if not match_terms:
            return []
        rows = session.run(
            """
            MATCH (article:Article)-[:HAS_IMAGE]->(img:ImageAsset)
            WHERE ($week IS NULL OR article.week = $week)
              AND any(term IN $terms WHERE
                toLower(img.caption_hint) CONTAINS term OR toLower(img.media_name) CONTAINS term
                OR toLower(article.title) CONTAINS term
              )
            RETURN article.id AS article_id, article.title AS title, article.source AS source,
                   article.published_date AS published_date, img.caption_hint AS caption_hint,
                   img.source_path AS source_path, 1.0 AS score
            ORDER BY article.published_date DESC, img.image_index ASC
            LIMIT 8
            """,
            terms=_fold_terms(match_terms),
            week=week,
        ).data()
        return [_image_row_to_fact(row) for row in rows]


def _chunk_row_to_fact(row: dict[str, Any], terms: list[str]) -> GraphFact:
    snippet = _relevant_text(str(row["text"] or ""), terms, limit=1000)
    return GraphFact(
        kind="chunk",
        article_id=str(row["article_id"] or ""),
        title=str(row["title"] or ""),
        source=str(row["source"] or ""),
        published_date=str(row["published_date"] or ""),
        statement=snippet,
        quote=snippet,
        confidence=float(row.get("score") or 0),
    )


def _image_row_to_fact(row: dict[str, Any]) -> GraphFact:
    caption = str(row["caption_hint"] or row["source_path"] or "")
    return GraphFact(
        kind="image",
        article_id=str(row["article_id"] or ""),
        title=str(row["title"] or ""),
        source=str(row["source"] or ""),
        published_date=str(row["published_date"] or ""),
        statement=_compact(caption, limit=700),
        quote=_compact(caption, limit=700),
        confidence=float(row.get("score") or 0),
    )


def _dedupe_facts(facts: list[GraphFact]) -> list[GraphFact]:
    seen: set[tuple[str, str, str]] = set()
    output: list[GraphFact] = []
    for fact in facts:
        key = (fact.kind, fact.article_id, fact.statement)
        if key in seen:
            continue
        output.append(fact)
        seen.add(key)
    return output


def _fold_terms(terms: list[str]) -> list[str]:
    return [term.casefold() for term in terms if term]


def _content_terms(terms: list[str]) -> list[str]:
    valuation_keys = {_fold(term) for term in VALUATION_TERMS}
    output: list[str] = []
    for term in terms:
        folded = _fold(term).strip()
        if not folded or len(folded) < 3:
            continue
        if folded in STOPWORDS or folded in valuation_keys:
            continue
        output.append(term)
    return _unique(output)


def _relevant_text(text: str, terms: list[str], *, limit: int) -> str:
    units = [unit.strip() for unit in re.split(r"(?<=[.!?])\s+", " ".join(text.split())) if unit.strip()]
    content_terms = _content_terms(terms)
    if content_terms:
        matches = [unit for unit in units if _contains_any(unit, content_terms)]
        if matches:
            return _compact(" ".join(matches[:4]), limit=limit)
    return _compact(text, limit=limit)
