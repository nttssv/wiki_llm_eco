"""One-command demo runner for the Narrative Agent project."""

from __future__ import annotations

import argparse
from datetime import date
import subprocess
import sys

from .config import load_env


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the full Narrative Agent demo workflow.")
    parser.add_argument("--week", default=date.today().isoformat(), help="Week label in YYYY-MM-DD format.")
    parser.add_argument("--extractor", choices=["mock", "llm"], default="llm", help="Extractor mode.")
    parser.add_argument("--input", default="data/raw_articles", help="Folder containing DOCX article files.")
    return parser.parse_args()


def _run(command: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=check, text=True)


def main() -> None:
    load_env()
    args = parse_args()

    print("🚀 Starting Narrative Agent Demo...")

    print("📥 Running extraction...")
    _run(
        [
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
    )
    print("✅ Extraction complete")

    print("🔍 Validating processed registry...")
    validation_result = _run([sys.executable, "-m", "src.validate_processed_registry"], check=False)
    if validation_result.returncode == 0:
        print("✅ Validation passed")
    else:
        print("⚠️ Validation issues found (continuing)")

    print("🧠 Exporting graph...")
    export_result = _run([sys.executable, "-m", "src.export_graph", "--format", "json"], check=False)
    if export_result.returncode != 0:
        print("⚠️ Graph export failed (continuing)")

    print("📊 Generating weekly report...")
    report_result = _run(
        [sys.executable, "-m", "src.generate_weekly_report", "--week", args.week],
        check=False,
    )
    if report_result.returncode != 0:
        print("⚠️ Weekly report generation failed (continuing)")

    print("🌐 Launching dashboard...")
    _run([sys.executable, "-m", "streamlit", "run", "src/dashboard.py"])


if __name__ == "__main__":
    main()
