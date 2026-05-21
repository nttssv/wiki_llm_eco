"""CLI entry point for weekly narrative extraction."""

from __future__ import annotations

from dataclasses import dataclass
import argparse
import json
import logging
from pathlib import Path
from typing import Iterable

from .config import get_openai_api_key, load_env
from .extract_article import generate_article_id, processed_json_path_name, mock_extract_article
from .generate_markdown import write_article_markdown, write_wiki_indexes
from .ingest_docx import read_docx_article
from .llm_extract_article import extract_article_with_llm
from .neo4j_store import fetch_wiki_index_rows_from_neo4j, upsert_extraction_to_neo4j
from .paths import (
    LOG_PATH,
    PROCESSED_DIR,
    WIKI_ARTICLES_DIR,
    WIKI_DIR,
    WIKI_ENTITIES_DIR,
    WIKI_NARRATIVES_DIR,
    resolve_project_path,
)
from .processed_registry import (
    article_ids_by_file_key,
    normalize_article_file_key,
    rebuild_processed_manifest,
)


@dataclass(slots=True)
class RunSummary:
    articles_processed: int = 0
    articles_skipped: int = 0


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
    parser.add_argument(
        "--neo4j-write",
        action="store_true",
        help="Also write each extraction directly into Neo4j canonical graph.",
    )
    parser.add_argument(
        "--neo4j-embed-assets",
        action="store_true",
        help="When using --neo4j-write, also embed extracted chunks and image caption hints.",
    )
    return parser.parse_args()


def main() -> None:
    load_env()
    args = parse_args()
    input_dir = resolve_project_path(args.input)

    configure_logging(LOG_PATH)
    logging.info(
        "Starting weekly extraction for week=%s input=%s extractor=%s neo4j_write=%s",
        args.week,
        input_dir,
        args.extractor,
        args.neo4j_write,
    )

    if not input_dir.exists():
        raise FileNotFoundError(f"Input folder does not exist: {input_dir}")

    use_llm = args.extractor == "llm"
    has_api_key = get_openai_api_key() is not None
    if use_llm and not has_api_key:
        warning_message = "⚠️ No API key found → falling back to mock extractor"
        print(warning_message)
        logging.warning("No API key found. Falling back to mock extractor.")

    summary = RunSummary()
    existing_article_ids_by_file_key = article_ids_by_file_key()
    existing_file_keys = set(existing_article_ids_by_file_key) if args.new_only else set()

    for docx_path in iter_docx_files(input_dir):
        file_key = normalize_article_file_key(docx_path)
        if args.new_only and file_key in existing_file_keys:
            summary.articles_skipped += 1
            logging.info("Skipping already processed file=%s", docx_path.name)
            continue

        resolved_docx_path = docx_path.resolve()
        raw_article = read_docx_article(resolved_docx_path)
        raw_article.metadata["original_file_path"] = str(resolved_docx_path)

        article_id = existing_article_ids_by_file_key.get(file_key) or generate_article_id(
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
            "original_file_path": str(resolved_docx_path),
            "chart_references": raw_article.chart_references,
            "extractor": extractor_used,
            "extraction": extraction.model_dump(),
        }

        json_path = save_extraction_json(PROCESSED_DIR, article_id, json_payload)
        if args.neo4j_write:
            neo4j_stats = upsert_extraction_to_neo4j(
                article_id,
                extraction,
                resolved_docx_path,
                args.week,
                embed_assets=args.neo4j_embed_assets,
            )
            logging.info(
                "Neo4j direct write article_id=%s claims=%s chunks=%s images=%s embeddings=%s",
                article_id,
                neo4j_stats.claims,
                neo4j_stats.chunks,
                neo4j_stats.images,
                neo4j_stats.embeddings,
            )
        markdown_path = write_article_markdown(WIKI_ARTICLES_DIR, article_id, extraction)

        summary.articles_processed += 1

        logging.info(
            "Processed article_id=%s file=%s extractor=%s json=%s markdown=%s",
            article_id,
            docx_path.name,
            extractor_used,
            json_path.name,
            markdown_path.name,
        )

    manifest_path, article_rows = rebuild_processed_manifest()
    if args.neo4j_write:
        entity_rows, narrative_rows = fetch_wiki_index_rows_from_neo4j()
        wiki_index_source = "Neo4j"
    else:
        entity_rows, narrative_rows = [], []
        wiki_index_source = "processed registry"

    wiki_index_path, entities_index_path, narratives_index_path = write_wiki_indexes(
        WIKI_DIR,
        WIKI_ENTITIES_DIR,
        WIKI_NARRATIVES_DIR,
        article_rows,
        entity_rows,
        narrative_rows,
    )
    logging.info(
        "Updated manifest=%s and wiki indexes from %s root=%s entities=%s narratives=%s",
        manifest_path.name,
        wiki_index_source,
        wiki_index_path.name,
        entities_index_path.name,
        narratives_index_path.name,
    )

    print(f"articles processed: {summary.articles_processed}")
    print(f"articles skipped: {summary.articles_skipped}")


if __name__ == "__main__":
    main()
