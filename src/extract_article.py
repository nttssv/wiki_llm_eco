"""Structured extraction logic for articles."""

from __future__ import annotations

from pathlib import Path
import hashlib
import re

from .extract_schema import ArticleExtraction, Entity, Event, GraphEdge, Narrative, Theme


def _normalise_for_hash(value: str) -> str:
    return " ".join(value.strip().lower().split())


def stable_id(prefix: str, *parts: str, length: int = 16) -> str:
    """Create deterministic IDs from human-readable inputs."""

    normalized = "||".join(_normalise_for_hash(part) for part in parts)
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:length]
    return f"{prefix}_{digest}"


def generate_article_id(source: str, title: str, published_date: str) -> str:
    """Generate the article primary key from source, title, and date."""

    return stable_id("article", source or "unknown", title or "untitled", published_date or "undated")


def slugify(value: str) -> str:
    """Create a filesystem-safe slug."""

    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "article"


def markdown_filename(article_id: str, title: str) -> str:
    """Build a stable markdown filename for wiki pages."""

    return f"{slugify(title)}-{article_id[-8:]}.md"


def processed_json_path_name(article_id: str) -> str:
    return f"{article_id}.json"


def mock_extract_article(text: str, metadata: dict[str, str]) -> ArticleExtraction:
    """Return a structured extraction without calling an external LLM."""

    combined_text = f"{metadata.get('title', '')}\n{metadata.get('subtitle', '')}\n{text}"

    if any(keyword in combined_text for keyword in ("Apple", "Tim Cook", "iPhone")):
        published_date = metadata.get("published_date", "")
        return ArticleExtraction(
            source=metadata.get("source", "Unknown"),
            section=metadata.get("section", ""),
            title=metadata.get("title", Path(metadata.get("original_file_path", "Untitled")).stem),
            subtitle=metadata.get("subtitle", ""),
            published_date=published_date,
            category=metadata.get("category", "Technology"),
            summary=(
                "Apple's iPhone-centered operating model is facing simultaneous pressure from "
                "AI disruption, geopolitical tension, and the need to diversify manufacturing."
            ),
            importance_score=9,
            entities=[
                Entity(
                    name="Apple",
                    type="company",
                    role="primary subject",
                    description="Global technology company whose iPhone franchise anchors the article.",
                ),
                Entity(
                    name="Tim Cook",
                    type="person",
                    role="leader",
                    description="Apple chief executive associated with the company's global supply-chain era.",
                ),
                Entity(
                    name="John Ternus",
                    type="person",
                    role="potential successor",
                    description="Apple executive discussed as a potential future leader.",
                ),
                Entity(
                    name="China",
                    type="location",
                    role="manufacturing base",
                    description="Critical production hub in Apple's current supply chain.",
                ),
                Entity(
                    name="India",
                    type="location",
                    role="expansion base",
                    description="Growing alternative manufacturing base for Apple's production footprint.",
                ),
                Entity(
                    name="iPhone",
                    type="product",
                    role="core revenue engine",
                    description="Apple's flagship hardware platform and operational dependency.",
                ),
                Entity(
                    name="AI",
                    type="technology",
                    role="disruptive force",
                    description="Technological shift challenging the durability of the smartphone model.",
                ),
            ],
            events=[
                Event(
                    event_date=published_date or "",
                    event_summary="Apple's global manufacturing strategy is reassessed amid geopolitical fragmentation.",
                    location="China and India",
                    importance_score=8,
                ),
                Event(
                    event_date=published_date or "",
                    event_summary="AI is framed as a strategic challenge to Apple's smartphone-led model.",
                    location="Global",
                    importance_score=9,
                ),
            ],
            themes=[
                Theme(
                    name="globalisation",
                    description="Cross-border production and market integration that shaped Apple's scale model.",
                ),
                Theme(
                    name="supply chains",
                    description="Operational dependence on complex international manufacturing networks.",
                ),
                Theme(
                    name="trade wars",
                    description="Geopolitical conflict that increases costs and fragility across production networks.",
                ),
            ],
            narratives=[
                Narrative(
                    name="Apple's globalised iPhone model under pressure",
                    thesis=(
                        "Apple's globalised iPhone model is under pressure from AI and "
                        "geopolitical fragmentation."
                    ),
                    status="strengthening",
                    importance_score=9,
                )
            ],
            graph_edges=[
                GraphEdge(source_node="Apple", relationship="LED_BY", target_node="Tim Cook", confidence=0.98),
                GraphEdge(
                    source_node="Apple",
                    relationship="SUCCESSOR",
                    target_node="John Ternus",
                    confidence=0.72,
                ),
                GraphEdge(
                    source_node="Apple",
                    relationship="DEPENDS_ON",
                    target_node="iPhone",
                    confidence=0.95,
                ),
                GraphEdge(
                    source_node="Apple",
                    relationship="MANUFACTURES_IN",
                    target_node="China",
                    confidence=0.94,
                ),
                GraphEdge(
                    source_node="Apple",
                    relationship="EXPANDS_TO",
                    target_node="India",
                    confidence=0.91,
                ),
                GraphEdge(
                    source_node="AI",
                    relationship="CHALLENGES",
                    target_node="Apple smartphone model",
                    confidence=0.89,
                ),
                GraphEdge(
                    source_node="Trade wars",
                    relationship="PRESSURE_ON",
                    target_node="Apple supply chain",
                    confidence=0.9,
                ),
            ],
        )

    return ArticleExtraction(
        source=metadata.get("source", "Unknown"),
        section=metadata.get("section", ""),
        title=metadata.get("title", "Untitled"),
        subtitle=metadata.get("subtitle", ""),
        published_date=metadata.get("published_date", ""),
        category=metadata.get("category", "General"),
        summary="No mock extraction pattern matched this article. Replace mock extraction with an LLM-backed extractor.",
        importance_score=3,
        entities=[],
        events=[],
        themes=[],
        narratives=[],
        graph_edges=[],
    )

