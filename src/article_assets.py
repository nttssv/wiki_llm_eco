"""Article chunk and image asset preparation for GraphRAG indexing."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from zipfile import ZipFile

from .extract_article import stable_id
from .ingest_docx import RawArticleDocument, read_docx_article
from .paths import ARTICLE_IMAGES_DIR


@dataclass(slots=True)
class ArticleChunk:
    id: str
    article_id: str
    chunk_index: int
    text: str
    paragraph_start: int
    paragraph_end: int


@dataclass(slots=True)
class ImageAsset:
    id: str
    article_id: str
    image_index: int
    source_path: str
    media_name: str
    caption_hint: str


def chunk_article(
    article_id: str,
    article: RawArticleDocument,
    *,
    max_chars: int = 1400,
    overlap_paragraphs: int = 1,
) -> list[ArticleChunk]:
    """Build paragraph-boundary chunks from a parsed article."""

    chunks: list[ArticleChunk] = []
    current: list[str] = []
    start_index = 0

    def flush(end_index: int) -> None:
        nonlocal current, start_index
        text = "\n\n".join(current).strip()
        if not text:
            current = []
            start_index = end_index + 1
            return
        chunk_index = len(chunks)
        chunks.append(
            ArticleChunk(
                id=stable_id("chunk", article_id, str(chunk_index), text[:80]),
                article_id=article_id,
                chunk_index=chunk_index,
                text=text,
                paragraph_start=start_index,
                paragraph_end=end_index,
            )
        )
        current = current[-overlap_paragraphs:] if overlap_paragraphs else []
        start_index = max(0, end_index - len(current) + 1)

    for index, paragraph in enumerate(article.paragraphs):
        candidate = "\n\n".join([*current, paragraph]).strip()
        if current and len(candidate) > max_chars:
            flush(index - 1)
        if not current:
            start_index = index
        current.append(paragraph)

    if current:
        flush(len(article.paragraphs) - 1)

    return chunks


def extract_docx_images(
    article_id: str,
    article_path: Path,
    article: RawArticleDocument,
    *,
    output_dir: Path = ARTICLE_IMAGES_DIR,
) -> list[ImageAsset]:
    """Extract DOCX embedded media files and return image asset metadata."""

    image_suffixes = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}
    article_image_dir = output_dir / article_id
    assets: list[ImageAsset] = []
    caption_hint = _caption_hint(article)

    try:
        with ZipFile(article_path) as archive:
            media_names = sorted(
                name
                for name in archive.namelist()
                if name.startswith("word/media/")
                and Path(name).suffix.lower() in image_suffixes
            )
            if media_names:
                article_image_dir.mkdir(parents=True, exist_ok=True)
            for index, media_name in enumerate(media_names):
                suffix = Path(media_name).suffix.lower()
                asset_id = stable_id("image", article_id, str(index), media_name)
                target_path = article_image_dir / f"{asset_id}{suffix}"
                if not target_path.exists():
                    target_path.write_bytes(archive.read(media_name))
                assets.append(
                    ImageAsset(
                        id=asset_id,
                        article_id=article_id,
                        image_index=index,
                        source_path=str(target_path),
                        media_name=media_name,
                        caption_hint=caption_hint,
                    )
                )
    except Exception:
        return []

    return assets


def prepare_article_assets(article_id: str, article_path: Path) -> tuple[list[ArticleChunk], list[ImageAsset]]:
    article = read_docx_article(article_path)
    chunks = chunk_article(article_id, article)
    images = extract_docx_images(article_id, article_path, article)
    return chunks, images


def _caption_hint(article: RawArticleDocument) -> str:
    hints = [
        article.metadata.get("title", ""),
        article.metadata.get("subtitle", ""),
        *article.chart_references[:4],
    ]
    return " ".join(item.strip() for item in hints if item and item.strip())
