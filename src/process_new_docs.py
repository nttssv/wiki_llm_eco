"""Run the full narrative processing pipeline for newly added documents."""

from __future__ import annotations

import argparse
import subprocess
import sys

from .config import load_env


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Process new DOCX files through the full pipeline.")
    parser.add_argument("--week", required=True, help="Processing week label, for example 2026-04-26.")
    parser.add_argument("--extractor", choices=["mock", "llm"], default="mock", help="Extractor mode.")
    parser.add_argument("--input", default="data/raw_articles", help="Folder containing DOCX article files.")
    parser.add_argument(
        "--all-files",
        action="store_true",
        help="Process all DOCX files, including ones already recorded in the database.",
    )
    neo4j_group = parser.add_mutually_exclusive_group()
    neo4j_group.add_argument(
        "--neo4j-write",
        dest="neo4j_write",
        action="store_true",
        default=True,
        help="Write new extractions directly into Neo4j canonical graph. This is the default.",
    )
    neo4j_group.add_argument(
        "--no-neo4j-write",
        dest="neo4j_write",
        action="store_false",
        help="Skip direct Neo4j writes and Neo4j validation.",
    )
    parser.add_argument(
        "--neo4j-embed-assets",
        action="store_true",
        help="When using --neo4j-write, also embed chunks and image caption hints.",
    )
    return parser.parse_args()


def _run_step(command: list[str]) -> None:
    subprocess.run(command, check=True)


def main() -> None:
    load_env()
    args = parse_args()
    if args.neo4j_embed_assets and not args.neo4j_write:
        raise SystemExit("--neo4j-embed-assets requires Neo4j writes. Remove --no-neo4j-write.")

    extraction_command = [
        sys.executable,
        "-m",
        "src.run_weekly_extraction",
        "--input",
        args.input,
        "--week",
        args.week,
        "--extractor",
        args.extractor,
    ]
    if not args.all_files:
        extraction_command.append("--new-only")
    if args.neo4j_write:
        extraction_command.append("--neo4j-write")
    if args.neo4j_embed_assets:
        extraction_command.append("--neo4j-embed-assets")

    _run_step(extraction_command)
    _run_step([sys.executable, "-m", "src.validate_processed_registry"])
    if args.neo4j_write:
        _run_step([sys.executable, "scripts/validate_neo4j.py"])
        _run_step([sys.executable, "-m", "src.export_graph", "--format", "json", "--week", args.week])
        _run_step([sys.executable, "-m", "src.generate_weekly_report", "--week", args.week])


if __name__ == "__main__":
    main()
