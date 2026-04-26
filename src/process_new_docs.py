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
    return parser.parse_args()


def _run_step(command: list[str]) -> None:
    subprocess.run(command, check=True)


def main() -> None:
    load_env()
    args = parse_args()

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

    _run_step(extraction_command)
    _run_step([sys.executable, "-m", "src.validate_database"])
    _run_step([sys.executable, "-m", "src.export_graph", "--format", "json", "--week", args.week])
    _run_step([sys.executable, "-m", "src.generate_weekly_report", "--week", args.week])


if __name__ == "__main__":
    main()
