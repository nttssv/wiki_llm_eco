#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

backup_dir="data/backups/neo4j"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
backup_file=""

usage() {
  cat <<'EOF'
Usage:
  scripts/backup_neo4j.sh [--output path/to/backup.tar.gz]

Creates an offline Neo4j data backup by stopping the Neo4j container briefly,
archiving data/neo4j, then starting Neo4j again.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --output)
      backup_file="${2:-}"
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

if [[ ! -d "data/neo4j" ]]; then
  echo "Neo4j data directory not found: data/neo4j" >&2
  exit 1
fi

if [[ -z "$backup_file" ]]; then
  mkdir -p "$backup_dir"
  backup_file="$backup_dir/neo4j_${timestamp}.tar.gz"
else
  mkdir -p "$(dirname "$backup_file")"
fi

restart_neo4j() {
  docker compose --profile neo4j up -d neo4j >/dev/null
}

trap restart_neo4j EXIT

echo "Stopping Neo4j for a consistent offline backup..."
docker compose stop neo4j >/dev/null

echo "Writing backup: $backup_file"
tar -C data -czf "$backup_file" neo4j

echo "Backup written: $backup_file"
