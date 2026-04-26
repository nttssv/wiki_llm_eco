# Narrative Agent

Simple Python project for turning weekly DOCX news articles into structured narrative intelligence assets:

- SQLite records in `data/narrative.db`
- Structured extraction JSON in `data/processed/`
- Weekly graph exports in `data/exports/graph_<week>.json`
- Current graph snapshot in `data/exports/graph_latest.json`
- Backward-compatible graph export in `data/exports/graph.json`
- Markdown wiki pages in `wiki/articles/`
- Markdown index pages in `wiki/`, `wiki/entities/`, and `wiki/narratives/`
- Weekly reports in `reports/`
- Run logs in `logs/extraction.log`

The current extractor is intentionally local-only. It reads each DOCX file, parses lightweight metadata, runs `mock_extract_article(...)`, stores only metadata plus extracted structure, and avoids persisting full copyrighted article text in SQLite.

## Project structure

```text
narrative_agent/
  data/
    exports/
    raw_articles/
    processed/
    narrative.db
    narrative_backup_before_reingest.db
  reports/
  scripts/
    debug_graph_visibility.py
    debug_india_graph.py
  wiki/
    index.md
    articles/
    entities/
      index.md
    narratives/
      index.md
  logs/
  src/
    __init__.py
    config.py
    ingest_docx.py
    extract_schema.py
    db.py
    dashboard.py
    date_utils.py
    extract_article.py
    export_graph.py
    generate_weekly_report.py
    generate_markdown.py
    llm_extract_article.py
    process_new_docs.py
    run_weekly_extraction.py
    validate_database.py
    paths.py
  README.md
  requirements.txt
```

## Install requirements

From the `narrative_agent/` directory:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Environment setup

```bash
cp .env.example .env_local
# add your OPENAI_API_KEY
```

Supported variables:

- `OPENAI_API_KEY`
- `OPENAI_BASE_URL` optional
- `OPENAI_MODEL` default: `gpt-4o-mini`
- `OPENAI_TEMPERATURE` default: `0`
- `OPENAI_MAX_RETRIES` default: `3`

If using OpenRouter, `.env_local` can look like:

```bash
OPENAI_API_KEY=your_key_here
OPENAI_BASE_URL=https://openrouter.ai/api/v1
OPENAI_MODEL=openai/gpt-4o-mini
OPENAI_TEMPERATURE=0
OPENAI_MAX_RETRIES=3
```

## Run extraction

Place DOCX files in `data/raw_articles/`, then run:

```bash
python -m src.run_weekly_extraction --input data/raw_articles --week 2026-04-26
```

Use the LLM extractor with:

```bash
python -m src.run_weekly_extraction --input data/raw_articles --week 2026-04-26 --extractor llm
```

If no API key is available, the pipeline prints:

```text
⚠️ No API key found → falling back to mock extractor
```

The command will:

1. Read all DOCX files in the input folder
2. Extract text and lightweight metadata
3. Generate a deterministic article ID from source, title, and date
4. Run `mock_extract_article(...)`
5. Save structured extraction JSON into `data/processed/`
6. Insert or update SQLite rows idempotently
7. Generate one Markdown page per article in `wiki/articles/`
8. Print a creation summary for articles, entities, events, narratives, and graph edges
9. Refresh the simple wiki index pages

The default extractor is `mock`. When `--extractor llm` is used, the software attempts structured extraction through the OpenAI API and falls back to the mock extractor if the API key is missing or an article-level LLM extraction fails.

## Add new DOCX files

1. Copy one article per `.docx` file into `data/raw_articles/`
2. Re-run the weekly extraction command
3. Existing articles will update in place because the IDs and database writes are deterministic

Office temporary files such as `~$example.docx` are ignored automatically.

## Inspect the SQLite database

Use the SQLite CLI from `narrative_agent/`:

```bash
sqlite3 data/narrative.db
```

Useful queries:

```sql
.tables
SELECT id, source, title, published_date FROM articles;
SELECT name, type FROM entities ORDER BY name;
SELECT source_node, relationship, target_node FROM graph_edges;
```

## Validate database

Run:

```bash
python -m src.validate_database
```

The validator checks:

- required article fields: `title`, `source`, `published_date`, `summary`
- event references to valid `article_id` values
- required graph edge fields: `source_node`, `relationship`, `target_node`
- duplicate article IDs
- empty entity names
- empty narrative theses

It exits with a non-zero status if any validation issues are found.

## Export graph

Run:

```bash
python -m src.export_graph --format json --week 2026-04-26
```

This writes:

- `data/exports/graph_2026-04-26.json`
- `data/exports/graph_latest.json`
- `data/exports/graph.json`

Each node and edge now carries a `week` field so the dashboard can load historical graph snapshots and compare weekly graph evolution.

The export includes:

- graph nodes for articles, entities, themes, and narratives
- direct article context edges for extracted concepts:
  - `article -> entity` via `MENTIONS_ENTITY`
  - `article -> theme` via `HAS_THEME`
  - `article -> narrative` via `HAS_NARRATIVE`
- extracted relationship edges between concepts from `graph_edges`
- canonicalized entity nodes so duplicate labels such as `India` collapse to one node while preserving all linked article evidence

Useful debug command:

```bash
python -m src.export_graph --format json --week 2026-04-26 --debug-node India
```

## Generate weekly report

```bash
python -m src.generate_weekly_report --week 2026-04-26
```

This writes `reports/2026-04-26_weekly.md` and summarizes:

- weekly article volume
- top entities
- top themes
- narratives active in that week
- simple narrative change categories
- key events
- graph relationships supported by articles from that week

If the database is missing, the command prints:

```text
Database not found. Run weekly extraction first.
```

## One-command processing

Run the full ingestion pipeline after dropping in new DOCX files:

```bash
python -m src.process_new_docs --week 2026-04-26 --extractor llm
```

By default, this command only processes DOCX files that are not already recorded in the database.
If you want to rescan everything, use:

```bash
python -m src.process_new_docs --week 2026-04-26 --extractor llm --all-files
```

This runs:

1. weekly extraction
2. database validation
3. graph export
4. weekly report generation

For a shorter command, use the shell wrapper:

```bash
./run_pipeline.sh --week 2026-04-26
```

Defaults:

- `--extractor llm`
- `--mode docker`
- `--week` defaults to today's date if omitted

This wrapper also defaults to only processing new DOCX files that are not already in the database.

Run locally instead:

```bash
./run_pipeline.sh --week 2026-04-26 --mode local
```

## Run full demo

```bash
python -m src.demo
```

Optional arguments:

```bash
python -m src.demo --week 2026-04-26 --extractor llm
```

The demo runner will:

1. run extraction
2. validate the database
3. export the graph
4. generate the weekly report
5. launch the Streamlit dashboard

## Run dashboard

Start the read-only Streamlit UI with:

```bash
streamlit run src/dashboard.py
```

The dashboard reads from:

- `data/narrative.db`
- `data/exports/graph_latest.json`
- `data/exports/graph_<week>.json` when a historical week is selected

The Articles tab includes a two-panel view.
Selecting an article shows structured extraction on the left and a live DOCX preview on the right.
The DOCX preview is rendered from the local file and is not stored in the database.
Preview is truncated for readability.
If the DOCX contains embedded images, they are rendered in the right panel below the text preview.

The Graph View now supports:

- week selector with latest snapshot as default
- compare mode against the previous available week
- change highlighting:
  - new nodes and edges in green
  - removed nodes and edges in red
- graph summary metrics above the canvas
- top emerging entities based on week-over-week degree growth
- direct debug output for searched nodes so visible neighbors and edges can be inspected without leaving Streamlit

If the graph export is missing, the Graph View tab shows:

```text
Graph export not found. Run python -m src.export_graph --format json first.
```

## Docker

Build the image:

```bash
docker compose build
```

Run the dashboard:

```bash
docker compose up
```

Short wrapper:

```bash
./run_dashboard.sh
```

The dashboard will be available at:

```text
http://localhost:8501
```

Process new DOCX files:

1. Put files into `data/raw_articles/`
2. Run:

```bash
docker compose run --rm narrative_agent python -m src.process_new_docs --week 2026-04-26 --extractor llm
```

Run the one-command demo:

```bash
docker compose run --rm --service-ports narrative_agent python -m src.demo --week 2026-04-26 --extractor llm
```

Stop everything:

```bash
docker compose down
```

Notes:

- `.env_local` is loaded through `env_file` and is not baked into the image.
- `data/`, `reports/`, `wiki/`, and `logs/` are mounted as volumes so the database and generated outputs stay persistent on the host.

## View generated markdown files

Open the generated wiki files directly:

- `wiki/index.md`
- `wiki/articles/`
- `wiki/entities/index.md`
- `wiki/narratives/index.md`
- `reports/`

## Recent Changes

Implemented in the current working session:

- refreshed the repository from newly added DOCX articles and rebuilt the weekly wiki/report outputs
- fixed dashboard staleness by keying Streamlit cache reads to file modification state so new ingestions appear without manual code edits
- improved the graph canvas readability with centered fit-on-load behavior, smaller default label treatment, richer hover text, and cleaner controls
- replaced raw HTML text showing up in the graph UI with proper Streamlit-rendered legend and helper content
- fixed graph neighborhood expansion so search/selection shows the chosen node plus all 1-hop neighbors, including article hubs
- made article-to-concept edges visually distinct so article provenance is easier to read in the canvas
- fixed graph data-linking so article context edges are exported explicitly for entities, themes, and narratives
- added graph canonicalization for duplicate entity labels so concept nodes such as `India` collapse to one canonical node
- added graph debug utilities in `scripts/debug_india_graph.py` and `scripts/debug_graph_visibility.py` to validate exported node/edge visibility
- added week-aware graph snapshots:
  - `graph_<week>.json` for historical views
  - `graph_latest.json` for the current dashboard
- added graph comparison mode in Streamlit so weekly node and edge additions/removals can be inspected visually
- preserved previous weekly graph exports instead of overwriting a single graph file every run

What this does not do yet:

- content-hash based reprocessing
- extraction version tracking
- stronger extraction QA for missed or suspicious entities
- automated tests for schema, migrations, or reports

## Next steps

Suggested next improvements, in a practical order:

1. Strengthen extraction QA
   - tighten the LLM prompt so entity types, event dates, and relationship labels are more consistent
   - add a post-processing normalization layer for sources, categories, countries, company names, and narrative labels
   - flag suspicious links where an extracted entity or theme does not appear in the source text
   - add a confidence threshold or review queue for weak extractions

2. Make file ingestion smarter
   - detect changed files by content hash, not just file path
   - store ingestion metadata such as `file_hash`, `processed_at`, and extraction version
   - support reprocessing only files changed since the last run

3. Expand time-aware graph analysis
   - add article-by-article diffs inside a week comparison view
   - support multi-week playback, not only current versus previous
   - add entity/theme/narrative growth charts beside the graph canvas
   - add saved comparison snapshots or exported change reports

4. Strengthen narrative tracking
   - track narrative movement across more than one week window
   - add explicit trend metrics such as week-over-week mention delta
   - detect merges and near-duplicate narratives using normalized names or similarity rules

5. Expand the dashboard
   - add article-to-article similarity or related coverage views
   - add timeline charts for narrative mentions, themes, and entity frequency
   - add filters for importance score, entity type, and narrative change class
   - add a detail panel for selected graph nodes with linked evidence articles

6. Improve data quality controls
   - add automated tests for extraction schema validation, DB migrations, and report generation
   - add tests for graph canonicalization and week-to-week export diffs
   - add stronger validation rules for placeholder metadata such as `Unknown` sources
   - log extraction failures into a separate review file for manual QA

7. Prepare for production use
   - add backup/export scripts for the SQLite database
   - add scheduled runs with cron or GitHub Actions
   - separate dev/demo data from real weekly article data

Good next session starting point for tomorrow:

1. Ingest a second real week so the graph comparison mode has meaningful before/after data.
2. Run the new graph debug scripts against a few high-risk labels such as countries, sectors, and product names.
3. Decide whether the next increment should be:
   - content-hash based ingestion, or
   - extraction QA and normalization.
4. If dashboard work is next, add multi-week trend charts after the graph history model is stable.

## Notes

- Full article text is read only during extraction and is not stored in SQLite.
- The current mock extractor only returns a rich structured response for articles mentioning `Apple`, `Tim Cook`, or `iPhone`.
- Replace `mock_extract_article(...)` in `src/extract_article.py` when you are ready to connect a real model.
