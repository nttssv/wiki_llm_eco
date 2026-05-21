# Narrative Agent

Narrative Agent ingests weekly DOCX articles, extracts structured events,
entities, narratives, relationships, chunks, and image captions, stores the
canonical graph in Neo4j, and exposes a Streamlit dashboard for article review
and GraphRAG chat.

The current architecture is Neo4j-first. Processed extraction JSON files are
the article registry and recovery source; there is no SQLite runtime database.

## Architecture

- `data/raw_articles/`: source DOCX files.
- `data/processed/article_*.json`: structured extraction artifacts.
- `data/processed_manifest.json`: generated registry over processed artifacts.
- Neo4j: canonical graph, relationships, events, claims, chunks, image metadata,
  and optional embeddings.
- `wiki/`, `reports/`, `data/exports/`: generated markdown reports and graph
  snapshots.
- `src/dashboard.py`: Streamlit dashboard and GraphRAG chat UI.

Key modules:

- `src/run_weekly_extraction.py`: extract DOCX files, write processed JSON, and
  optionally write directly to Neo4j.
- `src/processed_registry.py`: manifest-backed article registry.
- `src/validate_processed_registry.py`: processed artifact validation.
- `src/neo4j_store.py`: Neo4j schema and upsert logic.
- `src/neo4j_rag.py` and `src/graph_rag.py`: GraphRAG retrieval and synthesis.
- `src/export_graph.py`: Neo4j graph export wrapper.
- `src/generate_weekly_report.py`: Neo4j weekly report wrapper.
- `scripts/validate_neo4j.py`: Neo4j count and integrity validation.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env_local
```

Configure `.env_local`:

```bash
OPENAI_API_KEY=...
OPENAI_MODEL=gpt-4o-mini
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=narrativegraph
NEO4J_DATABASE=neo4j
```

## Run With Docker

Start Neo4j and the dashboard:

```bash
docker compose --profile neo4j up -d neo4j
docker compose up -d --build narrative_agent
```

Open the dashboard at [http://localhost:8501](http://localhost:8501).

Run the full pipeline in Docker:

```bash
./run_pipeline.sh --week 2026-05-03 --mode docker --extractor llm
```

By default, the pipeline only processes DOCX files that do not already exist in
the processed registry. It still rebuilds the processed manifest, validates
Neo4j, exports the graph snapshot, and regenerates the weekly report.

## Run Locally

```bash
source .venv/bin/activate
./run_pipeline.sh --week 2026-05-03 --mode local --extractor llm
```

To run the lower-level extraction directly:

```bash
python -m src.run_weekly_extraction \
  --input data/raw_articles \
  --week 2026-05-03 \
  --extractor llm \
  --new-only \
  --neo4j-write
```

Use `--neo4j-embed-assets` when you want to embed article chunks and image
caption hints during extraction.

## Validation

Validate processed extraction artifacts:

```bash
python -m src.validate_processed_registry
```

Validate Neo4j counts and graph integrity:

```bash
python scripts/validate_neo4j.py
```

Current expected validation shape:

- processed registry: required article metadata, duplicate article ids, and
  duplicate source file keys.
- Neo4j: article count parity with processed JSON, relationship evidence links,
  claim/article links, narrative/article links, entity canonical keys, chunks,
  images, and week coverage.

## Latest Verified Test Run

Last verified: 2026-05-04.

Commands run:

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

.venv/bin/python -m src.validate_processed_registry
find data -maxdepth 2 \( -iname '*sqlite*' -o -name '*.db' \) -print
docker compose build narrative_agent
docker compose up -d narrative_agent
./run_pipeline.sh --week 2026-05-03 --mode docker --extractor llm
docker compose exec -T narrative_agent python scripts/evaluate_graphrag.py
docker compose exec -T narrative_agent python -m src.graph_rag \
  "openAI có thử advertising sử dụng chatbot?" --no-llm --backend neo4j
docker compose exec -T narrative_agent python -m src.export_graph --format json --week 2026-05-03
docker compose exec -T narrative_agent python -m src.generate_weekly_report --week 2026-05-03
curl -sS -I http://localhost:8501
```

Verified results:

- Local compile passed.
- Processed registry validation passed with 0 issues.
- No `.db` or `sqlite` files remain under `data/`.
- Docker image rebuilt and dashboard container restarted.
- Pipeline completed with 38 already-processed articles skipped and 0 newly
  processed articles.
- Neo4j validation passed: 38 articles, 252 entities, 74 narratives, 154 graph
  edges, 372 claims, 257 chunks, 57 images, and 3 weeks.
- Graph export wrote `data/exports/graph_2026-05-03.json` and
  `data/exports/graph_latest.json`.
- Weekly report wrote `reports/2026-05-03_weekly.md`.
- GraphRAG eval cases passed.
- CLI smoke query returned `OpenAI --EXPANDS_TO--> Advertising`.
- Dashboard returned HTTP 200, showed `Primary graph source: Neo4j`, and Graph
  View loaded week `2026-05-03`.

## Exports And Reports

Export a graph snapshot from Neo4j:

```bash
python -m src.export_graph --format json --week 2026-05-03
```

Generate the weekly report from Neo4j:

```bash
python -m src.generate_weekly_report --week 2026-05-03
```

Generated outputs:

- `data/exports/graph_<week>.json`
- `data/exports/graph_latest.json`
- `reports/<week>_weekly.md`
- `wiki/index.md`
- `wiki/entities/index.md`
- `wiki/narratives/index.md`

## GraphRAG Chat

Ask the graph from the CLI:

```bash
python -m src.graph_rag "openAI có thử advertising sử dụng chatbot?" --backend neo4j
```

Run deterministic retrieval evaluation:

```bash
python scripts/evaluate_graphrag.py
```

The dashboard chat has two modes:

- Fast answer: retrieve graph facts, article summaries, chunks, and citations,
  then synthesize a concise answer.
- Deep answer with embeddings: expand retrieval over embedded chunks/images and
  give the LLM more article context for a fuller synthesis.

## Dashboard

```bash
streamlit run src/dashboard.py
```

Dashboard tabs:

- Ask Graph: chat with Neo4j-backed GraphRAG.
- Overview: filtered article and narrative summary.
- Articles: extraction review plus DOCX preview.
- Narratives, Entities, Narrative Trends: graph-derived tables.
- Graph View: week-scoped relationship graph from Neo4j, with JSON snapshot
  fallback for graph exports.

If Neo4j is unavailable, the dashboard can still show article records from
`data/processed/article_*.json`; graph chat and graph-derived views require
Neo4j.

## Neo4j Backup And Maintenance

Backup:

```bash
scripts/backup_neo4j.sh
```

Restore:

```bash
scripts/restore_neo4j.sh path/to/backup.dump
```

Canonicalize entities after large ingestion changes:

```bash
python scripts/canonicalize_neo4j_entities.py
python scripts/validate_neo4j.py
```

## Data Handling

- Full DOCX text is read during extraction.
- Structured extraction artifacts are stored in `data/processed`.
- Article chunks and image captions can be written to Neo4j for RAG.
- The processed JSON artifacts are the rollback/rebuild source for article
  metadata and extraction payloads.
- The project no longer creates or reads `data/narrative.db`.
