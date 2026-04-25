#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

week="$(date +%F)"
extractor="llm"
mode="docker"

usage() {
  cat <<'EOF'
Usage:
  ./run_pipeline.sh [--week YYYY-MM-DD] [--extractor mock|llm] [--mode local|docker]

Examples:
  ./run_pipeline.sh
  ./run_pipeline.sh --week 2026-04-26
  ./run_pipeline.sh --week 2026-04-26
  ./run_pipeline.sh --week 2026-04-26 --extractor llm --mode local
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
  python -m src.process_new_docs --week "$week" --extractor "$extractor"
elif [[ "$mode" == "docker" ]]; then
  docker compose run --rm narrative_agent python -m src.process_new_docs --week "$week" --extractor "$extractor"
else
  echo "Mode must be 'local' or 'docker'." >&2
  exit 1
fi
