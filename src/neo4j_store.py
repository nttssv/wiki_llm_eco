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
from .db import NarrativeDatabase
from .embeddings import embed_texts, embeddings_enabled
from .extract_article import stable_id
from .paths import DB_PATH, PROJECT_ROOT, RAW_ARTICLES_DIR


@dataclass(slots=True)
class Neo4jSyncStats:
    articles: int = 0
    entities: int = 0
    themes: int = 0
    narratives: int = 0
    events: int = 0
    graph_edges: int = 0
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
    )


def neo4j_is_available() -> bool:
    try:
        with neo4j_driver() as driver:
            driver.verify_connectivity()
        return True
    except Exception:
        return False


def ensure_schema(driver: Any) -> None:
    statements = [
        "CREATE CONSTRAINT article_id IF NOT EXISTS FOR (n:Article) REQUIRE n.id IS UNIQUE",
        "CREATE CONSTRAINT entity_id IF NOT EXISTS FOR (n:Entity) REQUIRE n.id IS UNIQUE",
        "CREATE CONSTRAINT theme_id IF NOT EXISTS FOR (n:Theme) REQUIRE n.id IS UNIQUE",
        "CREATE CONSTRAINT narrative_id IF NOT EXISTS FOR (n:Narrative) REQUIRE n.id IS UNIQUE",
        "CREATE CONSTRAINT event_id IF NOT EXISTS FOR (n:Event) REQUIRE n.id IS UNIQUE",
        "CREATE CONSTRAINT chunk_id IF NOT EXISTS FOR (n:Chunk) REQUIRE n.id IS UNIQUE",
        "CREATE CONSTRAINT image_id IF NOT EXISTS FOR (n:ImageAsset) REQUIRE n.id IS UNIQUE",
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


def sync_sqlite_to_neo4j(
    database_path: Path = DB_PATH,
    *,
    clear: bool = False,
    embed: bool = False,
) -> Neo4jSyncStats:
    """Sync SQLite graph, article chunks, and image assets into Neo4j."""

    database = NarrativeDatabase(database_path)
    stats = Neo4jSyncStats()
    try:
        with neo4j_driver() as driver:
            ensure_schema(driver)
            with driver.session(database=get_neo4j_database()) as session:
                if clear:
                    session.run("MATCH (n) DETACH DELETE n").consume()

                _sync_articles(session, database, stats)
                entity_lookup = _sync_entities(session, database, stats)
                _sync_themes(session, database, stats)
                _sync_narratives(session, database, stats)
                _sync_article_links(session, database)
                _sync_events(session, database, stats)
                _sync_graph_edges(session, database, entity_lookup, stats)
                _sync_article_assets(session, database, entity_lookup, stats, embed=embed)
    finally:
        database.close()

    return stats


def _run_unwind(session: Any, query: str, rows: list[dict[str, Any]]) -> None:
    if rows:
        session.run(query, rows=rows).consume()


def _sync_articles(session: Any, database: NarrativeDatabase, stats: Neo4jSyncStats) -> None:
    rows = []
    for row in database.fetch_articles():
        published_date = str(row["published_date"] or "")
        rows.append(
            {
                "id": str(row["id"]),
                "source": str(row["source"] or ""),
                "section": str(row["section"] or ""),
                "title": str(row["title"] or ""),
                "subtitle": str(row["subtitle"] or ""),
                "published_date": published_date,
                "week": week_label_for_published_date(published_date) or "",
                "category": str(row["category"] or ""),
                "summary": str(row["summary"] or ""),
                "importance_score": int(row["importance_score"] or 0),
                "original_file_path": str(row["original_file_path"] or ""),
            }
        )
    _run_unwind(
        session,
        """
        UNWIND $rows AS row
        MERGE (a:Article {id: row.id})
        SET a += row
        """,
        rows,
    )
    stats.articles = len(rows)


def _sync_entities(session: Any, database: NarrativeDatabase, stats: Neo4jSyncStats) -> dict[str, str]:
    rows = [
        {
            "id": str(row["id"]),
            "name": str(row["name"] or ""),
            "type": str(row["type"] or ""),
            "description": str(row["description"] or ""),
            "canonical_key": _key(str(row["name"] or "")),
        }
        for row in database.fetch_entities()
    ]
    _run_unwind(
        session,
        """
        UNWIND $rows AS row
        MERGE (e:Entity {id: row.id})
        SET e += row
        """,
        rows,
    )
    stats.entities = len(rows)
    return {row["canonical_key"]: row["id"] for row in rows if row["canonical_key"]}


def _sync_themes(session: Any, database: NarrativeDatabase, stats: Neo4jSyncStats) -> None:
    rows = [
        {"id": str(row["id"]), "name": str(row["name"] or ""), "description": str(row["description"] or "")}
        for row in database.fetch_themes()
    ]
    _run_unwind(
        session,
        """
        UNWIND $rows AS row
        MERGE (t:Theme {id: row.id})
        SET t += row
        """,
        rows,
    )
    stats.themes = len(rows)


def _sync_narratives(session: Any, database: NarrativeDatabase, stats: Neo4jSyncStats) -> None:
    rows = [
        {
            "id": str(row["id"]),
            "name": str(row["name"] or ""),
            "thesis": str(row["thesis"] or ""),
            "status": str(row["status"] or ""),
            "importance_score": int(row["importance_score"] or 0),
            "first_seen_date": str(row["first_seen_date"] or ""),
            "last_seen_date": str(row["last_seen_date"] or ""),
            "mention_count": int(row["mention_count"] or 0),
        }
        for row in database.fetch_narratives()
    ]
    _run_unwind(
        session,
        """
        UNWIND $rows AS row
        MERGE (n:Narrative {id: row.id})
        SET n += row
        """,
        rows,
    )
    stats.narratives = len(rows)


def _sync_article_links(session: Any, database: NarrativeDatabase) -> None:
    _run_unwind(
        session,
        """
        UNWIND $rows AS row
        MATCH (a:Article {id: row.article_id})
        MATCH (e:Entity {id: row.entity_id})
        MERGE (a)-[r:MENTIONS]->(e)
        SET r.role = row.role
        """,
        [
            {
                "article_id": str(row["article_id"]),
                "entity_id": str(row["entity_id"]),
                "role": str(row["role"] or ""),
            }
            for row in database.fetch_article_entities()
        ],
    )
    _run_unwind(
        session,
        """
        UNWIND $rows AS row
        MATCH (a:Article {id: row.article_id})
        MATCH (t:Theme {id: row.theme_id})
        MERGE (a)-[:HAS_THEME]->(t)
        """,
        [{"article_id": str(row["article_id"]), "theme_id": str(row["theme_id"])} for row in database.fetch_article_themes()],
    )
    _run_unwind(
        session,
        """
        UNWIND $rows AS row
        MATCH (a:Article {id: row.article_id})
        MATCH (n:Narrative {id: row.narrative_id})
        MERGE (a)-[:HAS_NARRATIVE]->(n)
        """,
        [
            {"article_id": str(row["article_id"]), "narrative_id": str(row["narrative_id"])}
            for row in database.fetch_article_narratives()
        ],
    )


def _sync_events(session: Any, database: NarrativeDatabase, stats: Neo4jSyncStats) -> None:
    rows = [
        {
            "id": str(row["id"]),
            "article_id": str(row["article_id"]),
            "event_date": str(row["event_date"] or ""),
            "event_summary": str(row["event_summary"] or ""),
            "location": str(row["location"] or ""),
            "importance_score": int(row["importance_score"] or 0),
        }
        for row in database.fetch_all(
            """
            SELECT id, article_id, event_date, event_summary, location, importance_score
            FROM events
            """
        )
    ]
    _run_unwind(
        session,
        """
        UNWIND $rows AS row
        MATCH (a:Article {id: row.article_id})
        MERGE (ev:Event {id: row.id})
        SET ev += row
        MERGE (a)-[:HAS_EVENT]->(ev)
        """,
        rows,
    )
    stats.events = len(rows)


def _sync_graph_edges(
    session: Any,
    database: NarrativeDatabase,
    entity_lookup: dict[str, str],
    stats: Neo4jSyncStats,
) -> None:
    rows: list[dict[str, Any]] = []
    inferred_entities: dict[str, dict[str, str]] = {}
    for row in database.fetch_graph_edges():
        source_name = str(row["source_node"] or "")
        target_name = str(row["target_node"] or "")
        source_id = entity_lookup.get(_key(source_name)) or stable_id("entity", source_name)
        target_id = entity_lookup.get(_key(target_name)) or stable_id("entity", target_name)
        for entity_id, name in ((source_id, source_name), (target_id, target_name)):
            if entity_id not in inferred_entities:
                inferred_entities[entity_id] = {
                    "id": entity_id,
                    "name": name,
                    "type": "inferred",
                    "description": "",
                    "canonical_key": _key(name),
                }
        rows.append(
            {
                "id": str(row["id"]),
                "source_id": source_id,
                "target_id": target_id,
                "source_name": source_name,
                "target_name": target_name,
                "relationship": str(row["relationship"] or ""),
                "evidence_article_id": str(row["evidence_article_id"] or ""),
                "confidence": float(row["confidence"] or 0),
            }
        )

    _run_unwind(
        session,
        """
        UNWIND $rows AS row
        MERGE (e:Entity {id: row.id})
        ON CREATE SET e.name = row.name, e.type = row.type, e.description = row.description, e.canonical_key = row.canonical_key
        """,
        list(inferred_entities.values()),
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
        rows,
    )
    stats.graph_edges = len(rows)


def _sync_article_assets(
    session: Any,
    database: NarrativeDatabase,
    entity_lookup: dict[str, str],
    stats: Neo4jSyncStats,
    *,
    embed: bool,
) -> None:
    article_entities: dict[str, list[dict[str, str]]] = {}
    for row in database.fetch_article_entities():
        article_entities.setdefault(str(row["article_id"]), []).append(
            {"id": str(row["entity_id"]), "name": str(row["entity_name"] or "")}
        )

    chunk_rows: list[dict[str, Any]] = []
    image_rows: list[dict[str, Any]] = []
    chunk_mentions: list[dict[str, str]] = []

    for row in database.fetch_articles():
        article_id = str(row["id"])
        source_path = _resolve_article_path(str(row["original_file_path"] or ""))
        if source_path is None:
            continue
        try:
            chunks, images = prepare_article_assets(article_id, source_path)
        except Exception:
            stats.asset_errors += 1
            continue
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
            for entity in article_entities.get(article_id, []):
                if entity["name"] and _key(entity["name"]) in folded_text:
                    chunk_mentions.append({"chunk_id": chunk.id, "entity_id": entity["id"]})
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

    if embed and embeddings_enabled():
        _attach_embeddings(chunk_rows, "text", stats)
        _attach_embeddings(image_rows, "caption_hint", stats)

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
    stats.chunks = len(chunk_rows)
    stats.images = len(image_rows)


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
