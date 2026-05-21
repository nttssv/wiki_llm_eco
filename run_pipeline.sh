#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

extractor="llm"
mode="docker"
neo4j_write="true"
neo4j_embed_assets="false"
python_bin="python"

if [[ -x "$SCRIPT_DIR/.venv/bin/python" ]]; then
  python_bin="$SCRIPT_DIR/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  python_bin="python3"
fi

week="$("$python_bin" - <<'PY'
from datetime import date

from src.date_utils import week_end_for_date

print(week_end_for_date(date.today()).isoformat())
PY
)"

usage() {
  cat <<'EOF'
Usage:
  ./run_pipeline.sh [--week YYYY-MM-DD] [--extractor mock|llm] [--mode local|docker] [--neo4j-write|--no-neo4j-write] [--neo4j-embed-assets]

Examples:
  ./run_pipeline.sh
  ./run_pipeline.sh --week 2026-04-26
  ./run_pipeline.sh --week 2026-04-26 --extractor llm --mode local
  ./run_pipeline.sh --no-neo4j-write
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --week)
      week="${2:-}"
      shift 2
      ;;
    --extractor)
      extractor="${2:-}"
      shift 2
      ;;
    --mode)
      mode="${2:-}"
      shift 2
      ;;
    --neo4j-write)
      neo4j_write="true"
      shift
      ;;
    --no-neo4j-write)
      neo4j_write="false"
      neo4j_embed_assets="false"
      shift
      ;;
    --neo4j-embed-assets)
      neo4j_write="true"
      neo4j_embed_assets="true"
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage
      exit 1
      ;;
  esac
done

if [[ -z "$week" ]]; then
  echo "Week must not be empty." >&2
  exit 1
fi

if [[ "$extractor" != "mock" && "$extractor" != "llm" ]]; then
  echo "Extractor must be 'mock' or 'llm'." >&2
  exit 1
fi

if [[ "$mode" == "local" ]]; then
  command=("$python_bin" -m src.process_new_docs --week "$week" --extractor "$extractor")
  if [[ "$neo4j_write" == "true" ]]; then
    command+=(--neo4j-write)
  else
    command+=(--no-neo4j-write)
  fi
  if [[ "$neo4j_embed_assets" == "true" ]]; then
    command+=(--neo4j-embed-assets)
  fi
  "${command[@]}"
elif [[ "$mode" == "docker" ]]; then
  command=(docker compose run --rm narrative_agent python -m src.process_new_docs --week "$week" --extractor "$extractor")
  if [[ "$neo4j_write" == "true" ]]; then
    command+=(--neo4j-write)
  else
    command+=(--no-neo4j-write)
  fi
  if [[ "$neo4j_embed_assets" == "true" ]]; then
    command+=(--neo4j-embed-assets)
  fi
  "${command[@]}"
else
  echo "Mode must be 'local' or 'docker'." >&2
  exit 1
fi
