"""Neo4j graph and vector-store sync for GraphRAG."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .article_assets import prepare_article_assets
from .config import (
    get_embedding_dimensions,
    get_neo4j_database,
    get_neo4j_password,
    get_neo4j_uri,
    get_neo4j_user,
)
from .date_utils import week_label_for_published_date
from .embeddings import embed_texts, embeddings_enabled
from .extract_article import stable_id
from .extract_schema import ArticleExtraction
from .paths import PROJECT_ROOT, RAW_ARTICLES_DIR


@dataclass(slots=True)
class Neo4jSyncStats:
    articles: int = 0
    entities: int = 0
    themes: int = 0
    narratives: int = 0
    events: int = 0
    graph_edges: int = 0
    claims: int = 0
    chunks: int = 0
    images: int = 0
    embeddings: int = 0
    asset_errors: int = 0


def neo4j_driver() -> Any:
    password = get_neo4j_password()
    if not password:
        raise RuntimeError("NEO4J_PASSWORD is required for Neo4j GraphRAG.")

    from neo4j import GraphDatabase

    return GraphDatabase.driver(
        get_neo4j_uri(),
        auth=(get_neo4j_user(), password),
        connection_timeout=5.0,
        connection_acquisition_timeout=5.0,
    )


def neo4j_is_available() -> bool:
    try:
        with neo4j_driver() as driver:
            driver.verify_connectivity()
        return True
    except Exception:
        return False


def fetch_wiki_index_rows_from_neo4j() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return entity and narrative rows for wiki indexes from Neo4j."""

    with neo4j_driver() as driver:
        driver.verify_connectivity()
        with driver.session(database=get_neo4j_database()) as session:
            entity_rows = session.run(
                """
                MATCH (e:Entity)
                RETURN
                    e.id AS id,
                    coalesce(e.name, '') AS name,
                    coalesce(e.type, '') AS type,
                    coalesce(e.description, '') AS description
                ORDER BY name ASC
                """
            ).data()
            narrative_rows = session.run(
                """
                MATCH (n:Narrative)
                OPTIONAL MATCH (a:Article)-[:HAS_NARRATIVE]->(n)
                RETURN
                    n.id AS id,
                    coalesce(n.name, '') AS name,
                    coalesce(n.thesis, '') AS thesis,
                    coalesce(n.status, '') AS status,
                    coalesce(n.importance_score, 0) AS importance_score,
                    coalesce(n.first_seen_date, '') AS first_seen_date,
                    coalesce(n.last_seen_date, '') AS last_seen_date,
                    count(DISTINCT a) AS mention_count
                ORDER BY mention_count DESC, importance_score DESC, name ASC
                """
            ).data()
    return entity_rows, narrative_rows


def ensure_schema(driver: Any) -> None:
    statements = [
        "CREATE CONSTRAINT article_id IF NOT EXISTS FOR (n:Article) REQUIRE n.id IS UNIQUE",
        "CREATE CONSTRAINT entity_id IF NOT EXISTS FOR (n:Entity) REQUIRE n.id IS UNIQUE",
        "CREATE CONSTRAINT theme_id IF NOT EXISTS FOR (n:Theme) REQUIRE n.id IS UNIQUE",
        "CREATE CONSTRAINT narrative_id IF NOT EXISTS FOR (n:Narrative) REQUIRE n.id IS UNIQUE",
        "CREATE CONSTRAINT event_id IF NOT EXISTS FOR (n:Event) REQUIRE n.id IS UNIQUE",
        "CREATE CONSTRAINT claim_id IF NOT EXISTS FOR (n:Claim) REQUIRE n.id IS UNIQUE",
        "CREATE CONSTRAINT week_label IF NOT EXISTS FOR (n:Week) REQUIRE n.label IS UNIQUE",
        "CREATE CONSTRAINT chunk_id IF NOT EXISTS FOR (n:Chunk) REQUIRE n.id IS UNIQUE",
        "CREATE CONSTRAINT image_id IF NOT EXISTS FOR (n:ImageAsset) REQUIRE n.id IS UNIQUE",
        "CREATE FULLTEXT INDEX article_text IF NOT EXISTS FOR (n:Article) ON EACH [n.title, n.subtitle, n.summary]",
        "CREATE FULLTEXT INDEX claim_text IF NOT EXISTS FOR (n:Claim) ON EACH [n.text]",
        "CREATE FULLTEXT INDEX chunk_text IF NOT EXISTS FOR (n:Chunk) ON EACH [n.text]",
        "CREATE FULLTEXT INDEX image_text IF NOT EXISTS FOR (n:ImageAsset) ON EACH [n.caption_hint, n.media_name]",
        (
            "CREATE VECTOR INDEX chunk_embedding IF NOT EXISTS FOR (n:Chunk) ON (n.embedding) "
            f"OPTIONS {{indexConfig: {{`vector.dimensions`: {get_embedding_dimensions()}, "
            "`vector.similarity_function`: 'cosine'}}"
        ),
        (
            "CREATE VECTOR INDEX image_embedding IF NOT EXISTS FOR (n:ImageAsset) ON (n.embedding) "
            f"OPTIONS {{indexConfig: {{`vector.dimensions`: {get_embedding_dimensions()}, "
            "`vector.similarity_function`: 'cosine'}}"
        ),
    ]
    with driver.session(database=get_neo4j_database()) as session:
        for statement in statements:
            session.run(statement).consume()


def upsert_extraction_to_neo4j(
    article_id: str,
    extraction: ArticleExtraction,
    original_file_path: Path,
    current_week: str,
    *,
    embed_assets: bool = False,
) -> Neo4jSyncStats:
    """Write one structured extraction directly into canonical Neo4j graph."""

    stats = Neo4jSyncStats(articles=1)
    published_date = extraction.published_date
    week = week_label_for_published_date(published_date) or current_week
    article_row = {
        "id": article_id,
        "source": extraction.source,
        "section": extraction.section,
        "title": extraction.title,
        "subtitle": extraction.subtitle,
        "published_date": published_date,
        "week": week,
        "category": extraction.category,
        "summary": extraction.summary,
        "importance_score": extraction.importance_score,
        "original_file_path": str(original_file_path),
    }

    entity_rows: list[dict[str, Any]] = []
    article_entity_rows: list[dict[str, str]] = []
    entity_lookup: dict[str, str] = {}
    for entity in extraction.entities:
        entity_id = canonical_entity_id(entity.name)
        key = _key(entity.name)
        entity_lookup[key] = entity_id
        entity_rows.append(
            {
                "id": entity_id,
                "name": entity.name,
                "type": entity.type,
                "description": entity.description,
                "canonical_key": key,
            }
        )
        article_entity_rows.append({"article_id": article_id, "entity_id": entity_id, "role": entity.role})

    event_rows = [
        {
            "id": stable_id("event", article_id, event.event_date, event.event_summary, event.location),
            "article_id": article_id,
            "event_date": event.event_date,
            "event_summary": event.event_summary,
            "location": event.location,
            "importance_score": event.importance_score,
        }
        for event in extraction.events
    ]
    theme_rows = [
        {"id": stable_id("theme", theme.name), "name": theme.name, "description": theme.description}
        for theme in extraction.themes
    ]
    article_theme_rows = [{"article_id": article_id, "theme_id": row["id"]} for row in theme_rows]
    narrative_rows = [
        {
            "id": stable_id("narrative", narrative.name),
            "name": narrative.name.strip(),
            "thesis": narrative.thesis,
            "status": narrative.status,
            "importance_score": narrative.importance_score,
            "first_seen_date": current_week,
            "last_seen_date": current_week,
            "mention_count": 1,
        }
        for narrative in extraction.narratives
    ]
    article_narrative_rows = [{"article_id": article_id, "narrative_id": row["id"]} for row in narrative_rows]

    inferred_entities: dict[str, dict[str, Any]] = {}
    edge_rows: list[dict[str, Any]] = []
    for edge in extraction.graph_edges:
        source_id = entity_lookup.get(_key(edge.source_node)) or canonical_entity_id(edge.source_node)
        target_id = entity_lookup.get(_key(edge.target_node)) or canonical_entity_id(edge.target_node)
        for entity_id, name in ((source_id, edge.source_node), (target_id, edge.target_node)):
            if entity_id not in inferred_entities:
                inferred_entities[entity_id] = {
                    "id": entity_id,
                    "name": name,
                    "type": "inferred",
                    "description": "",
                    "canonical_key": _key(name),
                }
        edge_rows.append(
            {
                "id": stable_id("edge", edge.source_node, edge.relationship, edge.target_node, article_id),
                "source_id": source_id,
                "target_id": target_id,
                "source_name": edge.source_node,
                "target_name": edge.target_node,
                "relationship": edge.relationship,
                "evidence_article_id": article_id,
                "confidence": edge.confidence,
            }
        )

    claim_rows, claim_about_rows = _claim_rows_for_extraction(
        article_id,
        extraction,
        event_rows,
        edge_rows,
        article_narrative_rows,
    )

    chunk_rows: list[dict[str, Any]] = []
    image_rows: list[dict[str, Any]] = []
    chunk_mentions: list[dict[str, str]] = []
    try:
        chunks, images = prepare_article_assets(article_id, original_file_path)
        for chunk in chunks:
            chunk_rows.append(
                {
                    "id": chunk.id,
                    "article_id": article_id,
                    "chunk_index": chunk.chunk_index,
                    "text": chunk.text,
                    "paragraph_start": chunk.paragraph_start,
                    "paragraph_end": chunk.paragraph_end,
                }
            )
            folded_text = _key(chunk.text)
            for entity in extraction.entities:
                entity_id = entity_lookup.get(_key(entity.name))
                if entity_id and _key(entity.name) in folded_text:
                    chunk_mentions.append({"chunk_id": chunk.id, "entity_id": entity_id})
        for image in images:
            image_rows.append(
                {
                    "id": image.id,
                    "article_id": article_id,
                    "image_index": image.image_index,
                    "source_path": image.source_path,
                    "media_name": image.media_name,
                    "caption_hint": image.caption_hint,
                }
            )
    except Exception:
        stats.asset_errors += 1

    if embed_assets and embeddings_enabled():
        _attach_embeddings(chunk_rows, "text", stats)
        _attach_embeddings(image_rows, "caption_hint", stats)

    with neo4j_driver() as driver:
        ensure_schema(driver)
        with driver.session(database=get_neo4j_database()) as session:
            _delete_article_scope(session, article_id)
            _write_article_scope(
                session,
                article_row,
                [*entity_rows, *inferred_entities.values()],
                article_entity_rows,
                event_rows,
                theme_rows,
                article_theme_rows,
                narrative_rows,
                article_narrative_rows,
                edge_rows,
                claim_rows,
                claim_about_rows,
                chunk_rows,
                image_rows,
                chunk_mentions,
            )

    stats.entities = len(entity_rows) + len(inferred_entities)
    stats.events = len(event_rows)
    stats.themes = len(theme_rows)
    stats.narratives = len(narrative_rows)
    stats.graph_edges = len(edge_rows)
    stats.claims = len(claim_rows)
    stats.chunks = len(chunk_rows)
    stats.images = len(image_rows)
    return stats


def _run_unwind(session: Any, query: str, rows: list[dict[str, Any]]) -> None:
    if rows:
        session.run(query, rows=rows).consume()


def _delete_article_scope(session: Any, article_id: str) -> None:
    delete_queries = [
        """
        MATCH (a:Article {id: $article_id})-[r:MENTIONS|HAS_THEME|HAS_NARRATIVE|IN_WEEK]->()
        DELETE r
        """,
        """
        MATCH (a:Article {id: $article_id})-[:HAS_EVENT]->(ev:Event)
        DETACH DELETE ev
        """,
        """
        MATCH (a:Article {id: $article_id})-[:HAS_CHUNK]->(chunk:Chunk)
        DETACH DELETE chunk
        """,
        """
        MATCH (a:Article {id: $article_id})-[:HAS_IMAGE]->(img:ImageAsset)
        DETACH DELETE img
        """,
        """
        MATCH (a:Article {id: $article_id})-[:SUPPORTS_CLAIM]->(claim:Claim)
        DETACH DELETE claim
        """,
        """
        MATCH ()-[rel:RELATES_TO {evidence_article_id: $article_id}]->()
        DELETE rel
        """,
    ]
    for query in delete_queries:
        session.run(query, article_id=article_id).consume()


def _write_article_scope(
    session: Any,
    article_row: dict[str, Any],
    entity_rows: list[dict[str, Any]],
    article_entity_rows: list[dict[str, str]],
    event_rows: list[dict[str, Any]],
    theme_rows: list[dict[str, Any]],
    article_theme_rows: list[dict[str, str]],
    narrative_rows: list[dict[str, Any]],
    article_narrative_rows: list[dict[str, str]],
    edge_rows: list[dict[str, Any]],
    claim_rows: list[dict[str, Any]],
    claim_about_rows: list[dict[str, str]],
    chunk_rows: list[dict[str, Any]],
    image_rows: list[dict[str, Any]],
    chunk_mentions: list[dict[str, str]],
) -> None:
    session.run(
        """
        MERGE (a:Article {id: $row.id})
        SET a += $row
        WITH a, $row AS row
        MERGE (w:Week {label: row.week})
        MERGE (a)-[:IN_WEEK]->(w)
        """,
        row=article_row,
    ).consume()
    _run_unwind(
        session,
        """
        UNWIND $rows AS row
        MERGE (e:Entity {id: row.id})
        SET e += row
        """,
        entity_rows,
    )
    _run_unwind(
        session,
        """
        UNWIND $rows AS row
        MATCH (a:Article {id: row.article_id})
        MATCH (e:Entity {id: row.entity_id})
        MERGE (a)-[r:MENTIONS]->(e)
        SET r.role = row.role
        """,
        article_entity_rows,
    )
    _run_unwind(
        session,
        """
        UNWIND $rows AS row
        MATCH (a:Article {id: row.article_id})
        MERGE (ev:Event {id: row.id})
        SET ev += row
        MERGE (a)-[:HAS_EVENT]->(ev)
        """,
        event_rows,
    )
    _run_unwind(
        session,
        """
        UNWIND $rows AS row
        MERGE (t:Theme {id: row.id})
        SET t += row
        """,
        theme_rows,
    )
    _run_unwind(
        session,
        """
        UNWIND $rows AS row
        MATCH (a:Article {id: row.article_id})
        MATCH (t:Theme {id: row.theme_id})
        MERGE (a)-[:HAS_THEME]->(t)
        """,
        article_theme_rows,
    )
    _run_unwind(
        session,
        """
        UNWIND $rows AS row
        MERGE (n:Narrative {id: row.id})
        ON CREATE SET n += row
        ON MATCH SET
            n.name = row.name,
            n.thesis = row.thesis,
            n.status = row.status,
            n.importance_score = row.importance_score,
            n.last_seen_date = CASE
                WHEN coalesce(n.last_seen_date, '') = '' OR n.last_seen_date < row.last_seen_date
                THEN row.last_seen_date
                ELSE n.last_seen_date
            END,
            n.first_seen_date = CASE
                WHEN coalesce(n.first_seen_date, '') = '' OR n.first_seen_date > row.first_seen_date
                THEN row.first_seen_date
                ELSE n.first_seen_date
            END
        """,
        narrative_rows,
    )
    _run_unwind(
        session,
        """
        UNWIND $rows AS row
        MATCH (a:Article {id: row.article_id})
        MATCH (n:Narrative {id: row.narrative_id})
        MERGE (a)-[:HAS_NARRATIVE]->(n)
        """,
        article_narrative_rows,
    )
    _run_unwind(
        session,
        """
        UNWIND $rows AS row
        MATCH (source:Entity {id: row.source_id})
        MATCH (target:Entity {id: row.target_id})
        MATCH (article:Article {id: row.evidence_article_id})
        MERGE (source)-[rel:RELATES_TO {id: row.id}]->(target)
        SET rel.relationship = row.relationship,
            rel.confidence = row.confidence,
            rel.evidence_article_id = row.evidence_article_id
        MERGE (article)-[:EVIDENCES]->(source)
        MERGE (article)-[:EVIDENCES]->(target)
        """,
        edge_rows,
    )
    _run_unwind(
        session,
        """
        UNWIND $rows AS row
        MATCH (article:Article {id: row.article_id})
        MERGE (claim:Claim {id: row.id})
        SET claim += row
        MERGE (article)-[:SUPPORTS_CLAIM]->(claim)
        """,
        claim_rows,
    )
    _run_unwind(
        session,
        """
        UNWIND $rows AS row
        MATCH (claim:Claim {id: row.claim_id})
        MATCH (entity:Entity {id: row.entity_id})
        MERGE (claim)-[:ABOUT]->(entity)
        """,
        claim_about_rows,
    )
    _run_unwind(
        session,
        """
        UNWIND $rows AS row
        MATCH (a:Article {id: row.article_id})
        MERGE (c:Chunk {id: row.id})
        SET c.id = row.id,
            c.article_id = row.article_id,
            c.chunk_index = row.chunk_index,
            c.text = row.text,
            c.paragraph_start = row.paragraph_start,
            c.paragraph_end = row.paragraph_end
        FOREACH (_ IN CASE WHEN row.embedding IS NULL THEN [] ELSE [1] END | SET c.embedding = row.embedding)
        MERGE (a)-[:HAS_CHUNK]->(c)
        """,
        _rows_with_embedding_key(chunk_rows),
    )
    _run_unwind(
        session,
        """
        UNWIND $rows AS row
        MATCH (a:Article {id: row.article_id})
        MERGE (img:ImageAsset {id: row.id})
        SET img.id = row.id,
            img.article_id = row.article_id,
            img.image_index = row.image_index,
            img.source_path = row.source_path,
            img.media_name = row.media_name,
            img.caption_hint = row.caption_hint
        FOREACH (_ IN CASE WHEN row.embedding IS NULL THEN [] ELSE [1] END | SET img.embedding = row.embedding)
        MERGE (a)-[:HAS_IMAGE]->(img)
        """,
        _rows_with_embedding_key(image_rows),
    )
    _run_unwind(
        session,
        """
        UNWIND $rows AS row
        MATCH (c:Chunk {id: row.chunk_id})
        MATCH (e:Entity {id: row.entity_id})
        MERGE (c)-[:MENTIONS]->(e)
        """,
        chunk_mentions,
    )


def _claim_rows_for_extraction(
    article_id: str,
    extraction: ArticleExtraction,
    event_rows: list[dict[str, Any]],
    edge_rows: list[dict[str, Any]],
    article_narrative_rows: list[dict[str, str]],
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    rows: list[dict[str, Any]] = []
    about_rows: list[dict[str, str]] = []
    article_entity_ids = [
        canonical_entity_id(entity.name)
        for entity in extraction.entities
    ]

    if extraction.summary.strip():
        claim_id = stable_id("claim", "article_summary", article_id, extraction.summary[:120])
        rows.append(
            {
                "id": claim_id,
                "article_id": article_id,
                "type": "article_summary",
                "text": extraction.summary,
                "confidence": 1.0,
                "source": extraction.source,
                "published_date": extraction.published_date,
            }
        )
        about_rows.extend({"claim_id": claim_id, "entity_id": entity_id} for entity_id in article_entity_ids)

    for event_row in event_rows:
        claim_id = stable_id("claim", "event", event_row["id"])
        rows.append(
            {
                "id": claim_id,
                "article_id": article_id,
                "type": "event",
                "text": f"{event_row['event_date']}: {event_row['event_summary']}",
                "confidence": min(1.0, max(0.0, float(event_row["importance_score"] or 0) / 10)),
                "source": extraction.source,
                "published_date": extraction.published_date,
            }
        )
        text_key = _key(event_row["event_summary"])
        for entity in extraction.entities:
            if _key(entity.name) in text_key:
                about_rows.append({"claim_id": claim_id, "entity_id": canonical_entity_id(entity.name)})

    for edge_row in edge_rows:
        claim_id = stable_id("claim", "relationship", edge_row["id"])
        rows.append(
            {
                "id": claim_id,
                "article_id": article_id,
                "type": "relationship",
                "text": f"{edge_row['source_name']} --{edge_row['relationship']}--> {edge_row['target_name']}",
                "confidence": float(edge_row["confidence"] or 0),
                "source": extraction.source,
                "published_date": extraction.published_date,
            }
        )
        about_rows.append({"claim_id": claim_id, "entity_id": edge_row["source_id"]})
        about_rows.append({"claim_id": claim_id, "entity_id": edge_row["target_id"]})

    narrative_by_id = {stable_id("narrative", narrative.name): narrative for narrative in extraction.narratives}
    for narrative_link in article_narrative_rows:
        narrative = narrative_by_id.get(narrative_link["narrative_id"])
        if narrative is None:
            continue
        claim_id = stable_id("claim", "narrative", article_id, narrative_link["narrative_id"])
        rows.append(
            {
                "id": claim_id,
                "article_id": article_id,
                "type": "narrative",
                "text": f"{narrative.name} ({narrative.status}): {narrative.thesis}",
                "confidence": min(1.0, max(0.0, float(narrative.importance_score or 0) / 10)),
                "source": extraction.source,
                "published_date": extraction.published_date,
            }
        )

    return rows, about_rows


def _attach_embeddings(rows: list[dict[str, Any]], text_key: str, stats: Neo4jSyncStats) -> None:
    batch_size = 64
    for start in range(0, len(rows), batch_size):
        batch = rows[start : start + batch_size]
        vectors = embed_texts(row.get(text_key, "") for row in batch)
        for row, vector in zip(batch, vectors, strict=True):
            row["embedding"] = vector
            stats.embeddings += 1


def _rows_with_embedding_key(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{**row, "embedding": row.get("embedding")} for row in rows]


def _resolve_article_path(path_value: str) -> Path | None:
    path = Path(str(path_value or ""))
    candidates: list[Path] = []
    if path.is_absolute():
        candidates.append(path)
    parts = path.parts
    if "data" in parts:
        data_index = parts.index("data")
        candidates.append(PROJECT_ROOT / Path(*parts[data_index:]))
    if path.name:
        candidates.append(RAW_ARTICLES_DIR / path.name)
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def _key(value: str) -> str:
    return " ".join(str(value or "").casefold().split())


def canonical_entity_id(name: str) -> str:
    """Return the canonical Neo4j entity id for a display name."""

    key = _key(name)
    return stable_id("entity", key or str(name or "").strip())
