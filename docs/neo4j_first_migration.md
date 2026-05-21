# Neo4j-First Migration Status

Status date: 2026-05-04.

The migration is complete for runtime code. The application now uses processed
JSON artifacts as the article registry and Neo4j as the canonical graph store.
The local `data/narrative.db` file and legacy SQLite graph modules/scripts have
been removed.

## Current Architecture

1. DOCX files live in `data/raw_articles/`.
2. Extraction writes `data/processed/article_*.json`.
3. `src.processed_registry` rebuilds `data/processed_manifest.json` from those
   JSON artifacts.
4. Extraction writes graph structures directly to Neo4j when `--neo4j-write` is
   enabled.
5. Dashboard graph views, trends, reports, exports, and GraphRAG read from
   Neo4j.
6. Dashboard article fallback reads processed JSON only if Neo4j is unavailable.

## Completed Changes

- Removed `DB_PATH` from `src/paths.py`.
- Removed the SQLite persistence layer and legacy graph helpers:
  - `src/db.py`
  - `src/sqlite_legacy_graph.py`
  - `src/sqlite_legacy_graph_schema.py`
  - `src/sqlite_legacy_graph_write.py`
  - `src/validate_database.py`
  - `src/narrative_tracking.py`
- Removed legacy migration scripts:
  - `scripts/sync_neo4j.py`
  - `scripts/rebuild_sqlite_legacy_graph.py`
  - `scripts/drop_sqlite_legacy_graph.py`
- Replaced article registry reads with `src/processed_registry.py`.
- Added `src/validate_processed_registry.py`.
- Converted `run_pipeline.sh` and `src/process_new_docs.py` to validate
  processed JSON plus Neo4j only.
- Converted `src.export_graph` and `src.generate_weekly_report` to Neo4j
  wrappers.
- Moved weekly report markdown helpers into `src/weekly_report.py`.
- Converted dashboard fallback from local database reads to processed JSON.
- Removed non-Neo4j GraphRAG backend selection.
- Removed `data/narrative.db`.
- Removed local SQLite backup `.db` files from `data/` and `data/backups/`.

## Verified Checks

Local compile:

```bash
.venv/bin/python -m py_compile \
  src/processed_registry.py \
  src/run_weekly_extraction.py \
  src/process_new_docs.py \
  src/validate_processed_registry.py \
  scripts/validate_neo4j.py \
  src/neo4j_store.py \
  src/export_graph.py \
  src/export_graph_neo4j.py \
  src/generate_weekly_report.py \
  src/generate_weekly_report_neo4j.py \
  src/weekly_report.py \
  src/dashboard.py \
  src/graph_rag.py \
  src/neo4j_rag.py \
  scripts/evaluate_graphrag.py \
  src/demo.py
```

Processed registry:

```bash
.venv/bin/python -m src.validate_processed_registry
find data -maxdepth 2 \( -iname '*sqlite*' -o -name '*.db' \) -print
```

Result:

- passed
- issues found: 0
- no `.db` or `sqlite` files found under `data/`

Docker app:

```bash
docker compose build narrative_agent
docker compose up -d narrative_agent
docker compose ps
```

Result:

- `narrative_agent` rebuilt and restarted.
- `narrative_neo4j` was healthy.

Docker pipeline:

```bash
./run_pipeline.sh --week 2026-05-03 --mode docker --extractor llm
```

Result:

- articles processed: 0
- articles skipped: 38
- processed registry validation: passed
- Neo4j validation: passed
- Neo4j articles: 38
- entities: 252
- narratives: 74
- graph edges: 154
- claims: 372
- chunks: 257
- images: 57
- weeks: 3
- exported `data/exports/graph_2026-05-03.json`
- exported `data/exports/graph_latest.json`
- regenerated `reports/2026-05-03_weekly.md`

GraphRAG eval:

```bash
docker compose exec -T narrative_agent python scripts/evaluate_graphrag.py
```

Result:

- `openai_recent_developments_valuation`: passed
- `openai_chatbot_advertising`: passed

Dashboard smoke:

- `http://localhost:8501` returned HTTP 200.
- Sidebar showed `Primary graph source: Neo4j`.
- Ask Graph backend selector showed `Neo4j`.
- Ask Graph submitted `openAI có thử advertising sử dụng chatbot?`.
- Answer included `OpenAI --EXPANDS_TO--> Advertising`.
- Graph View loaded week `2026-05-03`.

## Operational Notes

- Processed JSON is now the only local registry/rebuild source.
- Neo4j must be available for graph chat, trends, graph relationships, exports,
  and reports.
- The dashboard can still show article rows from processed JSON if Neo4j is
  temporarily unavailable.
- Do not reintroduce a second graph store unless there is a clear rollback need;
  it creates count drift and retrieval ambiguity.
