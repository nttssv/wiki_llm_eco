"""SQLite persistence layer for structured narrative data."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from typing import Any

from .date_utils import week_label_for_published_date
from .extract_article import stable_id
from .extract_schema import ArticleExtraction, Entity, Event, GraphEdge, Narrative, Theme


def normalize_article_file_key(path_value: str | Path) -> str:
    """Normalize file paths so host and Docker mounts map to the same article key."""

    path = Path(str(path_value).strip())
    parts = path.parts
    if "data" in parts:
        data_index = parts.index("data")
        return "/".join(parts[data_index:])
    return path.name


@dataclass(slots=True)
class PersistenceStats:
    entities_created: int = 0
    events_created: int = 0
    narratives_created: int = 0
    graph_edges_created: int = 0


class NarrativeDatabase:
    """Handle schema creation and idempotent upserts."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(database_path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")

    def close(self) -> None:
        self.connection.close()

    def initialize(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS articles (
                id TEXT PRIMARY KEY,
                source TEXT,
                section TEXT,
                title TEXT,
                subtitle TEXT,
                published_date TEXT,
                category TEXT,
                summary TEXT,
                importance_score INTEGER,
                original_file_path TEXT,
                created_at TEXT
            );

            CREATE TABLE IF NOT EXISTS entities (
                id TEXT PRIMARY KEY,
                name TEXT,
                type TEXT,
                description TEXT
            );

            CREATE TABLE IF NOT EXISTS events (
                id TEXT PRIMARY KEY,
                article_id TEXT,
                event_date TEXT,
                event_summary TEXT,
                location TEXT,
                importance_score INTEGER,
                FOREIGN KEY(article_id) REFERENCES articles(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS themes (
                id TEXT PRIMARY KEY,
                name TEXT,
                description TEXT
            );

            CREATE TABLE IF NOT EXISTS narratives (
                id TEXT PRIMARY KEY,
                name TEXT,
                thesis TEXT,
                status TEXT,
                importance_score INTEGER,
                first_seen_date TEXT,
                last_seen_date TEXT,
                mention_count INTEGER DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS article_entities (
                article_id TEXT,
                entity_id TEXT,
                role TEXT,
                PRIMARY KEY(article_id, entity_id),
                FOREIGN KEY(article_id) REFERENCES articles(id) ON DELETE CASCADE,
                FOREIGN KEY(entity_id) REFERENCES entities(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS article_themes (
                article_id TEXT,
                theme_id TEXT,
                PRIMARY KEY(article_id, theme_id),
                FOREIGN KEY(article_id) REFERENCES articles(id) ON DELETE CASCADE,
                FOREIGN KEY(theme_id) REFERENCES themes(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS article_narratives (
                article_id TEXT,
                narrative_id TEXT,
                PRIMARY KEY(article_id, narrative_id),
                FOREIGN KEY(article_id) REFERENCES articles(id) ON DELETE CASCADE,
                FOREIGN KEY(narrative_id) REFERENCES narratives(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS graph_edges (
                id TEXT PRIMARY KEY,
                source_node TEXT,
                relationship TEXT,
                target_node TEXT,
                evidence_article_id TEXT,
                confidence REAL,
                FOREIGN KEY(evidence_article_id) REFERENCES articles(id) ON DELETE CASCADE
            );
            """
        )
        self._migrate_narratives_table()
        self.connection.commit()

    def _migrate_narratives_table(self) -> None:
        """Add narrative tracking columns if needed and backfill existing data."""

        columns = {
            str(row["name"])
            for row in self.connection.execute("PRAGMA table_info(narratives)").fetchall()
        }
        if "first_seen_date" not in columns:
            self.connection.execute("ALTER TABLE narratives ADD COLUMN first_seen_date TEXT")
        if "last_seen_date" not in columns:
            self.connection.execute("ALTER TABLE narratives ADD COLUMN last_seen_date TEXT")
        if "mention_count" not in columns:
            self.connection.execute("ALTER TABLE narratives ADD COLUMN mention_count INTEGER DEFAULT 1")

        narrative_rows = self.connection.execute("SELECT id FROM narratives").fetchall()
        for narrative_row in narrative_rows:
            narrative_id = str(narrative_row["id"])
            linked_rows = self.connection.execute(
                """
                SELECT DISTINCT an.article_id, a.published_date
                FROM article_narratives AS an
                JOIN articles AS a ON a.id = an.article_id
                WHERE an.narrative_id = ?
                """,
                (narrative_id,),
            ).fetchall()
            week_labels = sorted(
                {
                    week_label
                    for row in linked_rows
                    for week_label in [week_label_for_published_date(str(row["published_date"] or ""))]
                    if week_label is not None
                }
            )
            mention_count = len({str(row["article_id"]) for row in linked_rows}) or 1
            first_seen_date = week_labels[0] if week_labels else ""
            last_seen_date = week_labels[-1] if week_labels else ""
            self.connection.execute(
                """
                UPDATE narratives
                SET first_seen_date = ?,
                    last_seen_date = ?,
                    mention_count = ?
                WHERE id = ?
                """,
                (first_seen_date, last_seen_date, mention_count, narrative_id),
            )

    def _exists(self, table_name: str, row_id: str) -> bool:
        result = self.connection.execute(
            f"SELECT 1 FROM {table_name} WHERE id = ? LIMIT 1",
            (row_id,),
        ).fetchone()
        return result is not None

    def fetch_all(self, query: str, parameters: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        """Run a read query and return all rows."""

        return self.connection.execute(query, parameters).fetchall()

    def fetch_articles(self) -> list[sqlite3.Row]:
        return self.fetch_all(
            """
            SELECT id, source, section, title, subtitle, published_date, category, summary,
                   importance_score, original_file_path, created_at
            FROM articles
            ORDER BY published_date DESC, title ASC
            """
        )

    def fetch_article_file_paths(self) -> list[str]:
        return [
            str(row["original_file_path"] or "")
            for row in self.fetch_all(
                """
                SELECT original_file_path
                FROM articles
                WHERE COALESCE(original_file_path, '') != ''
                """
            )
        ]

    def fetch_article_ids_by_file_key(self) -> dict[str, str]:
        rows = self.fetch_all(
            """
            SELECT id, original_file_path
            FROM articles
            WHERE COALESCE(original_file_path, '') != ''
            """
        )
        return {
            normalize_article_file_key(str(row["original_file_path"] or "")): str(row["id"])
            for row in rows
        }

    def fetch_entities(self) -> list[sqlite3.Row]:
        return self.fetch_all(
            """
            SELECT id, name, type, description
            FROM entities
            ORDER BY name ASC
            """
        )

    def fetch_themes(self) -> list[sqlite3.Row]:
        return self.fetch_all(
            """
            SELECT id, name, description
            FROM themes
            ORDER BY name ASC
            """
        )

    def fetch_narratives(self) -> list[sqlite3.Row]:
        return self.fetch_all(
            """
            SELECT id, name, thesis, status, importance_score, first_seen_date, last_seen_date, mention_count
            FROM narratives
            ORDER BY mention_count DESC, importance_score DESC, name ASC
            """
        )

    def fetch_graph_edges(self) -> list[sqlite3.Row]:
        return self.fetch_all(
            """
            SELECT id, source_node, relationship, target_node, evidence_article_id, confidence
            FROM graph_edges
            ORDER BY relationship ASC, source_node ASC, target_node ASC
            """
        )

    def fetch_article_entities(self) -> list[sqlite3.Row]:
        return self.fetch_all(
            """
            SELECT ae.article_id, ae.entity_id, ae.role, e.name AS entity_name
            FROM article_entities AS ae
            JOIN entities AS e ON e.id = ae.entity_id
            ORDER BY ae.article_id ASC, e.name ASC
            """
        )

    def fetch_article_themes(self) -> list[sqlite3.Row]:
        return self.fetch_all(
            """
            SELECT at.article_id, at.theme_id, t.name AS theme_name
            FROM article_themes AS at
            JOIN themes AS t ON t.id = at.theme_id
            ORDER BY at.article_id ASC, t.name ASC
            """
        )

    def fetch_article_narratives(self) -> list[sqlite3.Row]:
        return self.fetch_all(
            """
            SELECT an.article_id, an.narrative_id, n.name AS narrative_name
            FROM article_narratives AS an
            JOIN narratives AS n ON n.id = an.narrative_id
            ORDER BY an.article_id ASC, n.name ASC
            """
        )

    def upsert_extraction(
        self,
        article_id: str,
        extraction: ArticleExtraction,
        original_file_path: Path,
        current_week: str,
    ) -> PersistenceStats:
        stats = PersistenceStats()
        timestamp = datetime.now(timezone.utc).isoformat()
        entity_ids_for_article: set[str] = set()
        event_ids_for_article: set[str] = set()
        theme_ids_for_article: set[str] = set()
        narrative_ids_for_article: set[str] = set()
        graph_edge_ids_for_article: set[str] = set()

        self.connection.execute(
            """
            INSERT INTO articles (
                id, source, section, title, subtitle, published_date, category,
                summary, importance_score, original_file_path, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                source = excluded.source,
                section = excluded.section,
                title = excluded.title,
                subtitle = excluded.subtitle,
                published_date = excluded.published_date,
                category = excluded.category,
                summary = excluded.summary,
                importance_score = excluded.importance_score,
                original_file_path = excluded.original_file_path
            """,
            (
                article_id,
                extraction.source,
                extraction.section,
                extraction.title,
                extraction.subtitle,
                extraction.published_date,
                extraction.category,
                extraction.summary,
                extraction.importance_score,
                str(original_file_path),
                timestamp,
            ),
        )

        for entity in extraction.entities:
            entity_id, created = self._upsert_entity(entity)
            stats.entities_created += int(created)
            entity_ids_for_article.add(entity_id)
            self.connection.execute(
                """
                INSERT INTO article_entities (article_id, entity_id, role)
                VALUES (?, ?, ?)
                ON CONFLICT(article_id, entity_id) DO UPDATE SET role = excluded.role
                """,
                (article_id, entity_id, entity.role),
            )

        for event in extraction.events:
            event_id, created = self._upsert_event(article_id, event)
            stats.events_created += int(created)
            event_ids_for_article.add(event_id)

        for theme in extraction.themes:
            theme_id = self._upsert_theme(theme)
            theme_ids_for_article.add(theme_id)
            self.connection.execute(
                """
                INSERT OR IGNORE INTO article_themes (article_id, theme_id)
                VALUES (?, ?)
                """,
                (article_id, theme_id),
            )

        for narrative in extraction.narratives:
            narrative_id, created = self._upsert_narrative(narrative, article_id, current_week)
            stats.narratives_created += int(created)
            narrative_ids_for_article.add(narrative_id)

        for edge in extraction.graph_edges:
            edge_id, created = self._upsert_graph_edge(article_id, edge)
            stats.graph_edges_created += int(created)
            graph_edge_ids_for_article.add(edge_id)

        self._delete_missing_article_links("article_entities", "entity_id", article_id, entity_ids_for_article)
        self._delete_missing_article_links("article_themes", "theme_id", article_id, theme_ids_for_article)
        self._delete_missing_article_links(
            "article_narratives",
            "narrative_id",
            article_id,
            narrative_ids_for_article,
        )
        self._delete_missing_article_rows("events", "article_id", article_id, event_ids_for_article)
        self._delete_missing_article_rows(
            "graph_edges",
            "evidence_article_id",
            article_id,
            graph_edge_ids_for_article,
        )
        self._refresh_narrative_aggregates()

        self.connection.commit()
        return stats

    def _delete_missing_article_links(
        self,
        table_name: str,
        value_column: str,
        article_id: str,
        keep_ids: set[str],
    ) -> None:
        if keep_ids:
            placeholders = ", ".join("?" for _ in keep_ids)
            parameters = (article_id, *sorted(keep_ids))
            self.connection.execute(
                f"""
                DELETE FROM {table_name}
                WHERE article_id = ?
                  AND {value_column} NOT IN ({placeholders})
                """,
                parameters,
            )
            return

        self.connection.execute(
            f"DELETE FROM {table_name} WHERE article_id = ?",
            (article_id,),
        )

    def _delete_missing_article_rows(
        self,
        table_name: str,
        article_column: str,
        article_id: str,
        keep_ids: set[str],
    ) -> None:
        if keep_ids:
            placeholders = ", ".join("?" for _ in keep_ids)
            parameters = (article_id, *sorted(keep_ids))
            self.connection.execute(
                f"""
                DELETE FROM {table_name}
                WHERE {article_column} = ?
                  AND id NOT IN ({placeholders})
                """,
                parameters,
            )
            return

        self.connection.execute(
            f"DELETE FROM {table_name} WHERE {article_column} = ?",
            (article_id,),
        )

    def _refresh_narrative_aggregates(self) -> None:
        narrative_rows = self.connection.execute(
            """
            SELECT id
            FROM narratives
            WHERE id IN (SELECT DISTINCT narrative_id FROM article_narratives)
            """
        ).fetchall()

        self.connection.execute(
            """
            DELETE FROM narratives
            WHERE id NOT IN (SELECT DISTINCT narrative_id FROM article_narratives)
            """
        )

        for narrative_row in narrative_rows:
            narrative_id = str(narrative_row["id"])
            linked_rows = self.connection.execute(
                """
                SELECT DISTINCT an.article_id, a.published_date
                FROM article_narratives AS an
                JOIN articles AS a ON a.id = an.article_id
                WHERE an.narrative_id = ?
                """,
                (narrative_id,),
            ).fetchall()
            week_labels = sorted(
                {
                    week_label
                    for row in linked_rows
                    for week_label in [week_label_for_published_date(str(row["published_date"] or ""))]
                    if week_label is not None
                }
            )
            mention_count = len({str(row["article_id"]) for row in linked_rows}) or 1
            first_seen_date = week_labels[0] if week_labels else ""
            last_seen_date = week_labels[-1] if week_labels else ""
            self.connection.execute(
                """
                UPDATE narratives
                SET first_seen_date = ?,
                    last_seen_date = ?,
                    mention_count = ?
                WHERE id = ?
                """,
                (first_seen_date, last_seen_date, mention_count, narrative_id),
            )

    def _upsert_entity(self, entity: Entity) -> tuple[str, bool]:
        entity_id = stable_id("entity", entity.type, entity.name)
        created = not self._exists("entities", entity_id)
        self.connection.execute(
            """
            INSERT INTO entities (id, name, type, description)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name = excluded.name,
                type = excluded.type,
                description = excluded.description
            """,
            (entity_id, entity.name, entity.type, entity.description),
        )
        return entity_id, created

    def _upsert_event(self, article_id: str, event: Event) -> tuple[str, bool]:
        event_id = stable_id(
            "event",
            article_id,
            event.event_date,
            event.event_summary,
            event.location,
        )
        created = not self._exists("events", event_id)
        self.connection.execute(
            """
            INSERT INTO events (id, article_id, event_date, event_summary, location, importance_score)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                article_id = excluded.article_id,
                event_date = excluded.event_date,
                event_summary = excluded.event_summary,
                location = excluded.location,
                importance_score = excluded.importance_score
            """,
            (
                event_id,
                article_id,
                event.event_date,
                event.event_summary,
                event.location,
                event.importance_score,
            ),
        )
        return event_id, created

    def _upsert_theme(self, theme: Theme) -> str:
        theme_id = stable_id("theme", theme.name)
        self.connection.execute(
            """
            INSERT INTO themes (id, name, description)
            VALUES (?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name = excluded.name,
                description = excluded.description
            """,
            (theme_id, theme.name, theme.description),
        )
        return theme_id

    def _upsert_narrative(self, narrative: Narrative, article_id: str, current_week: str) -> tuple[str, bool]:
        narrative_id = stable_id("narrative", narrative.name)
        created = not self._exists("narratives", narrative_id)
        link_exists = self.connection.execute(
            """
            SELECT 1
            FROM article_narratives
            WHERE article_id = ? AND narrative_id = ?
            LIMIT 1
            """,
            (article_id, narrative_id),
        ).fetchone() is not None

        if created:
            self.connection.execute(
                """
                INSERT INTO narratives (
                    id, name, thesis, status, importance_score, first_seen_date, last_seen_date, mention_count
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    narrative_id,
                    narrative.name.strip(),
                    narrative.thesis,
                    narrative.status,
                    narrative.importance_score,
                    current_week,
                    current_week,
                    1,
                ),
            )
        elif link_exists:
            self.connection.execute(
                """
                UPDATE narratives
                SET name = ?,
                    thesis = ?,
                    status = ?,
                    importance_score = ?
                WHERE id = ?
                """,
                (
                    narrative.name.strip(),
                    narrative.thesis,
                    narrative.status,
                    narrative.importance_score,
                    narrative_id,
                ),
            )
        else:
            self.connection.execute(
                """
                UPDATE narratives
                SET name = ?,
                    thesis = ?,
                    status = ?,
                    importance_score = ?,
                    first_seen_date = CASE
                        WHEN COALESCE(first_seen_date, '') = '' OR first_seen_date > ?
                        THEN ?
                        ELSE first_seen_date
                    END,
                    last_seen_date = CASE
                        WHEN COALESCE(last_seen_date, '') = '' OR last_seen_date < ?
                        THEN ?
                        ELSE last_seen_date
                    END,
                    mention_count = COALESCE(mention_count, 0) + 1
                WHERE id = ?
                """,
                (
                    narrative.name.strip(),
                    narrative.thesis,
                    narrative.status,
                    narrative.importance_score,
                    current_week,
                    current_week,
                    current_week,
                    current_week,
                    narrative_id,
                ),
            )

        self.connection.execute(
            """
            INSERT OR IGNORE INTO article_narratives (article_id, narrative_id)
            VALUES (?, ?)
            """,
            (article_id, narrative_id),
        )
        return narrative_id, created

    def _upsert_graph_edge(self, article_id: str, edge: GraphEdge) -> tuple[str, bool]:
        edge_id = stable_id(
            "edge",
            edge.source_node,
            edge.relationship,
            edge.target_node,
            article_id,
        )
        created = not self._exists("graph_edges", edge_id)
        self.connection.execute(
            """
            INSERT INTO graph_edges (id, source_node, relationship, target_node, evidence_article_id, confidence)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                source_node = excluded.source_node,
                relationship = excluded.relationship,
                target_node = excluded.target_node,
                evidence_article_id = excluded.evidence_article_id,
                confidence = excluded.confidence
            """,
            (
                edge_id,
                edge.source_node,
                edge.relationship,
                edge.target_node,
                article_id,
                edge.confidence,
            ),
        )
        return edge_id, created
