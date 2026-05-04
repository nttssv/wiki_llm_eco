#!/usr/bin/env python
"""Sync the SQLite narrative store into Neo4j for GraphRAG."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.neo4j_store import sync_sqlite_to_neo4j  # noqa: E402
from src.paths import DB_PATH  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync SQLite narrative data into Neo4j.")
    parser.add_argument("--database", default=str(DB_PATH), help="SQLite database path.")
    parser.add_argument("--clear", action="store_true", help="Clear existing Neo4j nodes before sync.")
    parser.add_argument("--embed", action="store_true", help="Create OpenAI embeddings for chunks and image captions.")
    args = parser.parse_args()

    stats = sync_sqlite_to_neo4j(Path(args.database), clear=args.clear, embed=args.embed)
    print(json.dumps(asdict(stats), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
