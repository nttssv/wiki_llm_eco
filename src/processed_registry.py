"""Processed extraction registry backed by JSON artifacts."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from .extract_schema import ArticleExtraction
from .paths import PROCESSED_DIR, PROCESSED_MANIFEST_PATH


def normalize_article_file_key(path_value: str | Path) -> str:
    """Normalize host and Docker paths so the same source DOCX maps to one key."""

    path = Path(str(path_value).strip())
    parts = path.parts
    if "data" in parts:
        data_index = parts.index("data")
        return "/".join(parts[data_index:])
    return path.name


def processed_article_records(processed_dir: Path = PROCESSED_DIR) -> list[dict[str, Any]]:
    """Read article registry records from processed extraction JSON files."""

    records: list[dict[str, Any]] = []
    for processed_path in sorted(processed_dir.glob("article_*.json")):
        payload = json.loads(processed_path.read_text(encoding="utf-8"))
        extraction_payload = payload.get("extraction")
        if not isinstance(extraction_payload, dict):
            raise ValueError(f"Missing extraction object in {processed_path}")

        extraction = ArticleExtraction.model_validate(extraction_payload)
        article_id = str(payload.get("article_id") or processed_path.stem.removeprefix("article_"))
        original_file_path = str(payload.get("original_file_path") or "")
        records.append(
            {
                "id": article_id,
                "source": extraction.source,
                "section": extraction.section,
                "title": extraction.title,
                "subtitle": extraction.subtitle,
                "published_date": extraction.published_date,
                "category": extraction.category,
                "summary": extraction.summary,
                "importance_score": extraction.importance_score,
                "original_file_path": original_file_path,
                "file_key": normalize_article_file_key(original_file_path),
                "processed_json_path": str(processed_path),
                "extractor": str(payload.get("extractor") or ""),
            }
        )

    records.sort(key=lambda row: str(row["title"] or "").casefold())
    records.sort(key=lambda row: str(row["published_date"] or ""), reverse=True)
    return records


def article_ids_by_file_key(processed_dir: Path = PROCESSED_DIR) -> dict[str, str]:
    """Return normalized original-file keys for already processed articles."""

    return {
        str(record["file_key"]): str(record["id"])
        for record in processed_article_records(processed_dir)
        if str(record.get("file_key") or "")
    }


def write_processed_manifest(
    records: list[dict[str, Any]],
    manifest_path: Path = PROCESSED_MANIFEST_PATH,
) -> Path:
    """Write a compact manifest for humans/tools; processed JSON remains source."""

    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema_version": 1,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "article_count": len(records),
        "articles": records,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return manifest_path


def rebuild_processed_manifest(
    processed_dir: Path = PROCESSED_DIR,
    manifest_path: Path = PROCESSED_MANIFEST_PATH,
) -> tuple[Path, list[dict[str, Any]]]:
    """Rebuild the processed manifest from extraction JSON artifacts."""

    records = processed_article_records(processed_dir)
    manifest_path = write_processed_manifest(records, manifest_path)
    return manifest_path, records
