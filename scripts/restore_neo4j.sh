#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

backup_file=""
force="false"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"

usage() {
  cat <<'EOF'
Usage:
  scripts/restore_neo4j.sh --backup path/to/neo4j_backup.tar.gz --force

Restores data/neo4j from a backup created by scripts/backup_neo4j.sh.
This stops the dashboard and Neo4j, moves the current data/neo4j directory to
data/neo4j_before_restore_<timestamp>, extracts the backup, then restarts both
services. --force is required because restore replaces the active graph store.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --backup)
      backup_file="${2:-}"
      shift 2
      ;;
    --force)
      force="true"
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

if [[ -z "$backup_file" ]]; then
  echo "--backup is required." >&2
  usage
  exit 1
fi

if [[ "$force" != "true" ]]; then
  echo "Restore replaces the active Neo4j store. Re-run with --force after confirming the backup path." >&2
  exit 1
fi

if [[ ! -f "$backup_file" ]]; then
  echo "Backup file not found: $backup_file" >&2
  exit 1
fi

if ! tar -tzf "$backup_file" | grep -q '^neo4j/'; then
  echo "Backup does not look like a Neo4j data backup created by scripts/backup_neo4j.sh." >&2
  exit 1
fi

echo "Stopping dashboard and Neo4j..."
docker compose stop narrative_agent neo4j >/dev/null

restore_guard_path="data/neo4j_before_restore_${timestamp}"
if [[ -d "data/neo4j" ]]; then
  echo "Moving current data/neo4j to $restore_guard_path"
  mv data/neo4j "$restore_guard_path"
fi

echo "Restoring Neo4j data from: $backup_file"
mkdir -p data
tar -C data -xzf "$backup_file"

echo "Starting Neo4j and dashboard..."
docker compose --profile neo4j up -d neo4j >/dev/null
docker compose up -d narrative_agent >/dev/null

echo "Restore complete."
echo "Previous store kept at: $restore_guard_path"
