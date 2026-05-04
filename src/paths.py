"""Shared project paths and path helpers."""

from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"
RAW_ARTICLES_DIR = DATA_DIR / "raw_articles"
PROCESSED_DIR = DATA_DIR / "processed"
EXPORTS_DIR = DATA_DIR / "exports"
ARTICLE_IMAGES_DIR = DATA_DIR / "article_images"
DB_PATH = DATA_DIR / "narrative.db"
REPORTS_DIR = PROJECT_ROOT / "reports"
WIKI_DIR = PROJECT_ROOT / "wiki"
WIKI_ARTICLES_DIR = WIKI_DIR / "articles"
WIKI_ENTITIES_DIR = WIKI_DIR / "entities"
WIKI_NARRATIVES_DIR = WIKI_DIR / "narratives"
LOG_PATH = PROJECT_ROOT / "logs" / "extraction.log"


def resolve_project_path(path_value: str) -> Path:
    """Resolve a project-relative or absolute path."""

    path = Path(path_value)
    if path.is_absolute():
        return path
    return PROJECT_ROOT / path
