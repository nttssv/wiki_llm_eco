"""CLI entry point for weekly narrative extraction."""

from __future__ import annotations

from dataclasses import dataclass
import argparse
import json
import logging
from pathlib import Path
from typing import Iterable

from .config import get_openai_api_key, load_env
from .db import NarrativeDatabase, PersistenceStats
from .extract_article import generate_article_id, processed_json_path_name, mock_extract_article
from .generate_markdown import write_article_markdown, write_wiki_indexes
from .ingest_docx import read_docx_article
from .llm_extract_article import extract_article_with_llm
from .paths import (
    DB_PATH,
    LOG_PATH,
    PROCESSED_DIR,
    WIKI_ARTICLES_DIR,
    WIKI_DIR,
    WIKI_ENTITIES_DIR,
    WIKI_NARRATIVES_DIR,
    resolve_project_path,
)


@dataclass(slots=True)
class RunSummary:
    articles_processed: int = 0
    articles_skipped: int = 0
    entities_created: int = 0
    events_created: int = 0
    narratives_created: int = 0
    graph_edges_created: int = 0

    def update(self, stats: PersistenceStats) -> None:
        self.entities_created += stats.entities_created
        self.events_created += stats.events_created
        self.narratives_created += stats.narratives_created
        self.graph_edges_created += stats.graph_edges_created


def configure_logging(log_path: Path) -> None:
    """Configure file logging for extraction runs."""

    log_path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        handlers=[
            logging.FileHandler(log_path, encoding="utf-8"),
            logging.StreamHandler(),
        ],
        force=True,
    )


def iter_docx_files(input_dir: Path) -> Iterable[Path]:
    """Yield valid DOCX files, skipping Office temporary files."""

    for path in sorted(input_dir.glob("*.docx")):
        if path.name.startswith("~$"):
            continue
        yield path


def save_extraction_json(output_dir: Path, article_id: str, payload: dict[str, object]) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / processed_json_path_name(article_id)
    output_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return output_path


def article_file_key(path_value: str | Path) -> str:
    """Normalize file paths so host and Docker mounts map to the same article key."""

    path = Path(str(path_value).strip())
    parts = path.parts
    if "data" in parts:
        data_index = parts.index("data")
        return "/".join(parts[data_index:])
    return path.name


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run weekly narrative extraction over DOCX articles.")
    parser.add_argument("--input", required=True, help="Folder containing DOCX article files.")
    parser.add_argument("--week", required=True, help="Processing week label, for example 2026-04-26.")
    parser.add_argument("--extractor", choices=["mock", "llm"], default="mock", help="Extractor mode.")
    parser.add_argument(
        "--new-only",
        action="store_true",
        help="Only process DOCX files not already recorded in the database.",
    )
    return parser.parse_args()


def main() -> None:
    load_env()
    args = parse_args()
    input_dir = resolve_project_path(args.input)

    configure_logging(LOG_PATH)
    logging.info(
        "Starting weekly extraction for week=%s input=%s extractor=%s",
        args.week,
        input_dir,
        args.extractor,
    )

    if not input_dir.exists():
        raise FileNotFoundError(f"Input folder does not exist: {input_dir}")

    use_llm = args.extractor == "llm"
    has_api_key = get_openai_api_key() is not None
    if use_llm and not has_api_key:
        warning_message = "⚠️ No API key found → falling back to mock extractor"
        print(warning_message)
        logging.warning("No API key found. Falling back to mock extractor.")

    database = NarrativeDatabase(DB_PATH)
    database.initialize()
    summary = RunSummary()
    existing_file_keys = (
        {article_file_key(file_path) for file_path in database.fetch_article_file_paths()}
        if args.new_only
        else set()
    )

    try:
        for docx_path in iter_docx_files(input_dir):
            if args.new_only and article_file_key(docx_path) in existing_file_keys:
                summary.articles_skipped += 1
                logging.info("Skipping already processed file=%s", docx_path.name)
                continue

            raw_article = read_docx_article(docx_path)
            raw_article.metadata["original_file_path"] = str(docx_path)

            article_id = generate_article_id(
                raw_article.metadata.get("source", ""),
                raw_article.metadata.get("title", ""),
                raw_article.metadata.get("published_date", ""),
            )

            extraction = mock_extract_article(raw_article.body_text, raw_article.metadata)
            extractor_used = "mock"
            if use_llm and has_api_key:
                try:
                    extraction = extract_article_with_llm(raw_article.body_text, raw_article.metadata)
                    extractor_used = "llm"
                except Exception:
                    logging.exception(
                        "LLM extraction failed for file=%s article_id=%s. Falling back to mock extractor.",
                        docx_path.name,
                        article_id,
                    )

            json_payload = {
                "article_id": article_id,
                "original_file_path": str(docx_path),
                "chart_references": raw_article.chart_references,
                "extractor": extractor_used,
                "extraction": extraction.model_dump(),
            }

            json_path = save_extraction_json(PROCESSED_DIR, article_id, json_payload)
            stats = database.upsert_extraction(article_id, extraction, docx_path, args.week)
            markdown_path = write_article_markdown(WIKI_ARTICLES_DIR, article_id, extraction)

            summary.articles_processed += 1
            summary.update(stats)

            logging.info(
                "Processed article_id=%s file=%s extractor=%s json=%s markdown=%s",
                article_id,
                docx_path.name,
                extractor_used,
                json_path.name,
                markdown_path.name,
            )

        wiki_index_path, entities_index_path, narratives_index_path = write_wiki_indexes(
            WIKI_DIR,
            WIKI_ENTITIES_DIR,
            WIKI_NARRATIVES_DIR,
            database.fetch_articles(),
            database.fetch_entities(),
            database.fetch_narratives(),
        )
        logging.info(
            "Updated wiki indexes root=%s entities=%s narratives=%s",
            wiki_index_path.name,
            entities_index_path.name,
            narratives_index_path.name,
        )
    finally:
        database.close()

    print(f"articles processed: {summary.articles_processed}")
    print(f"articles skipped: {summary.articles_skipped}")
    print(f"entities created: {summary.entities_created}")
    print(f"events created: {summary.events_created}")
    print(f"narratives created: {summary.narratives_created}")
    print(f"graph edges created: {summary.graph_edges_created}")


if __name__ == "__main__":
    main()
