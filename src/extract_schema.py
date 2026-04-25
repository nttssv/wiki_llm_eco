"""Pydantic models for structured article extraction."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class BaseExtractionModel(BaseModel):
    """Common model configuration for extraction models."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Entity(BaseExtractionModel):
    name: str
    type: str
    role: str
    description: str


class Event(BaseExtractionModel):
    event_date: str
    event_summary: str
    location: str
    importance_score: int = Field(ge=1, le=10)


class Theme(BaseExtractionModel):
    name: str
    description: str


class Narrative(BaseExtractionModel):
    name: str
    thesis: str
    status: Literal[
        "emerging",
        "strengthening",
        "weakening",
        "transitioning",
        "reversed",
        "stable",
    ]
    importance_score: int = Field(ge=1, le=10)


class GraphEdge(BaseExtractionModel):
    source_node: str
    relationship: str
    target_node: str
    confidence: float = Field(ge=0.0, le=1.0)


class ArticleExtraction(BaseExtractionModel):
    source: str
    section: str
    title: str
    subtitle: str
    published_date: str
    category: str
    summary: str
    importance_score: int = Field(ge=1, le=10)
    entities: list[Entity] = Field(default_factory=list)
    events: list[Event] = Field(default_factory=list)
    themes: list[Theme] = Field(default_factory=list)
    narratives: list[Narrative] = Field(default_factory=list)
    graph_edges: list[GraphEdge] = Field(default_factory=list)
