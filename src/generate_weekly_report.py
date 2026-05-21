"""Generate a markdown weekly narrative report from Neo4j."""

from __future__ import annotations

import argparse
import logging

from .generate_weekly_report_neo4j import generate_report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a Neo4j-backed weekly markdown narrative report.")
    parser.add_argument("--week", required=True, help="Week-ending date in YYYY-MM-DD format.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
    report_path = generate_report(args.week)
    logging.info("Generated Neo4j weekly report: %s", report_path)
    print(f"neo4j weekly report written: {report_path}")


if __name__ == "__main__":
    main()
