"""Graph-first RAG over the narrative SQLite store.

The LLM is used for intent parsing and answer synthesis. Relationship lookup
stays deterministic and evidence-backed so the model cannot invent graph facts.
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
import re
import sqlite3
from typing import Any, Literal, TypedDict
import unicodedata

from pydantic import BaseModel, Field

try:  # LangChain prompt templates keep LLM inputs explicit and reusable.
    from langchain_core.prompts import ChatPromptTemplate
except Exception:  # pragma: no cover - optional until dependencies are installed.
    ChatPromptTemplate = None  # type: ignore[assignment]

from .config import (
    get_openai_api_key,
    get_openai_base_url,
    get_openai_max_retries,
    get_openai_max_output_tokens,
    get_openai_model,
    get_openai_temperature,
    get_openai_timeout_seconds,
    load_env,
)
from .date_utils import week_label_for_published_date
from .ingest_docx import read_docx_article
from .paths import DB_PATH, PROJECT_ROOT, RAW_ARTICLES_DIR

try:  # LangGraph is the preferred orchestration layer.
    from langgraph.graph import END, StateGraph
except Exception:  # pragma: no cover - fallback keeps local scripts usable before install.
    END = "__end__"
    StateGraph = None  # type: ignore[assignment]


RELATIONSHIP_TYPES = (
    "LED_BY",
    "SUCCESSOR",
    "DEPENDS_ON",
    "MANUFACTURES_IN",
    "EXPANDS_TO",
    "CHALLENGES",
    "PRESSURE_ON",
    "SUPPORTS",
    "AFFECTS",
    "RELATED_TO",
)
VALUATION_TERMS = (
    "valuation",
    "valued",
    "value",
    "market value",
    "funding",
    "raises",
    "raise",
    "định giá",
    "gia tri",
    "$",
    "bn",
    "trn",
    "billion",
    "trillion",
)
STOPWORDS = {
    "co",
    "có",
    "nhung",
    "những",
    "buoc",
    "bước",
    "tien",
    "tiến",
    "moi",
    "mới",
    "gan",
    "gần",
    "day",
    "đây",
    "dinh",
    "định",
    "gia",
    "giá",
    "bao",
    "nhieu",
    "nhiêu",
    "what",
    "new",
    "recent",
    "recently",
    "latest",
    "tuc",
    "tức",
    "nao",
    "nào",
    "nhat",
    "nhất",
    "cho",
    "minh",
    "mình",
    "ve",
    "về",
    "thong",
    "thông",
    "tin",
    "bai",
    "bài",
    "bao",
    "báo",
    "su",
    "sự",
    "kien",
    "kiện",
    "xau",
    "xâu",
    "chuoi",
    "chuỗi",
    "lai",
    "lại",
    "relationship",
    "relationships",
    "chinh",
    "chính",
}
MONEY_PATTERN = re.compile(
    r"(?i)(?:\$|US\$)\s?[\d,.]+(?:\s?(?:bn|trn|billion|trillion|m|million))?"
    r"|[\d,.]+\s?(?:bn|trn|billion|trillion)"
)


def _openai_client() -> Any:
    from openai import OpenAI

    return OpenAI(
        api_key=get_openai_api_key(),
        base_url=get_openai_base_url(),
        timeout=get_openai_timeout_seconds(),
        max_retries=0,
    )


class QueryIntent(BaseModel):
    """Structured user intent for graph retrieval."""

    entities: list[str] = Field(default_factory=list)
    relationship_types: list[str] = Field(default_factory=list)
    ask_for: list[Literal["developments", "valuation", "relationships", "summary", "chain"]] = Field(
        default_factory=list
    )
    search_terms: list[str] = Field(default_factory=list)
    time_scope: Literal["latest", "all", "week"] = "latest"
    max_hops: int = Field(default=2, ge=1, le=3)


class Citation(BaseModel):
    article_id: str
    title: str
    source: str = ""
    published_date: str = ""
    quote: str = ""


class GraphFact(BaseModel):
    kind: Literal["edge", "event", "narrative", "article", "snippet", "chunk", "image"]
    article_id: str
    title: str
    published_date: str = ""
    source: str = ""
    source_node: str = ""
    relationship: str = ""
    target_node: str = ""
    confidence: float | None = None
    statement: str
    quote: str = ""


class GraphRAGResult(BaseModel):
    question: str
    answer: str
    intent: QueryIntent
    facts: list[GraphFact]
    citations: list[Citation]
    evaluation: dict[str, Any]
    llm_used: bool = False
    warnings: list[str] = Field(default_factory=list)


class GraphRAGState(TypedDict, total=False):
    question: str
    week: str | None
    use_llm: bool
    intent: QueryIntent
    facts: list[GraphFact]
    citations: list[Citation]
    answer: str
    evaluation: dict[str, Any]
    warnings: list[str]
    llm_used: bool
    deep_search: bool


def _fold(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(character for character in text if not unicodedata.combining(character))
    return text.replace("đ", "d").replace("Đ", "d").lower()


def _compact(value: str, limit: int = 700) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "..."


def _contains_any(value: str, terms: list[str]) -> bool:
    folded = _fold(value)
    return any(_fold(term) in folded for term in terms if term)


def _query_entity_terms(intent: QueryIntent) -> list[str]:
    valuation_keys = {_fold(term) for term in VALUATION_TERMS}
    terms: list[str] = []
    for term in [*intent.entities, *intent.search_terms]:
        folded = _fold(term).strip()
        if not folded or len(folded) < 3:
            continue
        if folded in STOPWORDS or folded in valuation_keys:
            continue
        terms.append(term)
    return _unique(terms)


def _anchor_terms(intent: QueryIntent, resolved_terms: list[str]) -> list[str]:
    query_terms = _query_entity_terms(intent)
    if not query_terms:
        return []

    anchored: list[str] = []
    folded_query_terms = [_fold(term) for term in query_terms]
    for term in resolved_terms:
        folded_term = _fold(term)
        if any(query_term in folded_term or folded_term in query_term for query_term in folded_query_terms):
            anchored.append(term)
    return _unique(anchored or query_terms)


def _evidence_units(value: str) -> list[str]:
    text = " ".join(str(value or "").split())
    return [unit.strip() for unit in re.split(r"(?<=[.!?])\s+", text) if unit.strip()]


def _has_query_specific_explicit_valuation(statement: str, intent: QueryIntent) -> bool:
    explicit_terms = ["valuation", "valued", "định giá"]
    if not MONEY_PATTERN.search(statement) or not _contains_any(statement, explicit_terms):
        return False

    entity_terms = _query_entity_terms(intent)
    if not entity_terms:
        return True

    for unit in _evidence_units(statement):
        if (
            MONEY_PATTERN.search(unit)
            and _contains_any(unit, explicit_terms)
            and _contains_any(unit, entity_terms)
        ):
            return True
    return False


def _timeline_sort_key(fact: GraphFact) -> tuple[str, str]:
    date_match = re.search(r"\b(20\d{2})(?:-(\d{2})-(\d{2}))?\b", fact.statement)
    if date_match:
        year, month, day = date_match.group(1), date_match.group(2) or "00", date_match.group(3) or "00"
        return (f"{year}-{month}-{day}", fact.published_date)
    return (fact.published_date, fact.title)


def _synthesis_facts(facts: list[GraphFact], *, deep_search: bool = False) -> list[GraphFact]:
    selected: list[GraphFact] = []
    limits = (
        {
            "article": 6,
            "event": 10,
            "edge": 8,
            "narrative": 5,
            "snippet": 8,
            "chunk": 10,
            "image": 4,
        }
        if deep_search
        else {
            "article": 4,
            "event": 7,
            "edge": 6,
            "narrative": 4,
            "snippet": 4,
            "chunk": 4,
            "image": 2,
        }
    )
    for kind, limit in limits.items():
        selected.extend([fact for fact in facts if fact.kind == kind][:limit])

    output: list[GraphFact] = []
    seen: set[tuple[str, str, str, str]] = set()
    for fact in selected:
        key = (fact.kind, fact.article_id, fact.relationship, fact.statement)
        if key in seen:
            continue
        output.append(fact)
        seen.add(key)
    return output[:34] if deep_search else output[:20]


def _fact_payload(fact: GraphFact) -> dict[str, Any]:
    data = fact.model_dump()
    statement_limit = 900 if fact.kind == "article" else 550
    data["statement"] = _compact(fact.statement, limit=statement_limit)
    data["quote"] = _compact(fact.quote, limit=350)
    return data


def _llm_messages(system_prompt: str, user_payload: str) -> list[dict[str, str]]:
    if ChatPromptTemplate is None:
        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_payload},
        ]

    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", "{system_prompt}"),
            ("human", "{user_payload}"),
        ]
    )
    messages = prompt.format_messages(system_prompt=system_prompt, user_payload=user_payload)
    roles = {"system": "system", "human": "user", "ai": "assistant"}
    return [
        {
            "role": roles.get(str(message.type), str(message.type)),
            "content": str(message.content),
        }
        for message in messages
    ]


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        clean = str(value or "").strip()
        key = _fold(clean)
        if clean and key not in seen:
            output.append(clean)
            seen.add(key)
    return output


def _week_for_row(row: sqlite3.Row) -> str | None:
    return week_label_for_published_date(str(row["published_date"] or ""))


def _resolve_article_path(path_value: str) -> Path | None:
    path = Path(str(path_value or ""))
    candidates: list[Path] = []
    if path.is_absolute():
        candidates.append(path)

    parts = path.parts
    if "data" in parts:
        data_index = parts.index("data")
        candidates.append(PROJECT_ROOT / Path(*parts[data_index:]))

    if path.name:
        candidates.append(RAW_ARTICLES_DIR / path.name)

    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


class GraphRAGService:
    """Run GraphRAG queries against a narrative SQLite database."""

    def __init__(self, database_path: Path = DB_PATH) -> None:
        self.database_path = database_path
        self._workflow = self._build_workflow()

    def answer_question(
        self,
        question: str,
        week: str | None = None,
        use_llm: bool = True,
        deep_search: bool = False,
    ) -> GraphRAGResult:
        state: GraphRAGState = {
            "question": question,
            "week": None if week in {"", "All"} else week,
            "use_llm": use_llm,
            "deep_search": deep_search,
            "warnings": [],
            "llm_used": False,
        }
        if self._workflow is not None:
            final_state = self._workflow.invoke(state)
        else:
            final_state = self._run_sequential(state)

        return GraphRAGResult(
            question=question,
            answer=str(final_state.get("answer", "")),
            intent=final_state["intent"],
            facts=final_state.get("facts", []),
            citations=final_state.get("citations", []),
            evaluation=final_state.get("evaluation", {}),
            llm_used=bool(final_state.get("llm_used", False)),
            warnings=final_state.get("warnings", []),
        )

    def _build_workflow(self) -> Any | None:
        if StateGraph is None:
            return None

        workflow = StateGraph(GraphRAGState)
        workflow.add_node("parse_intent", self._parse_intent_node)
        workflow.add_node("retrieve_graph", self._retrieve_graph_node)
        workflow.add_node("synthesize_answer", self._synthesize_answer_node)
        workflow.add_node("evaluate_answer", self._evaluate_answer_node)
        workflow.set_entry_point("parse_intent")
        workflow.add_edge("parse_intent", "retrieve_graph")
        workflow.add_edge("retrieve_graph", "synthesize_answer")
        workflow.add_edge("synthesize_answer", "evaluate_answer")
        workflow.add_edge("evaluate_answer", END)
        return workflow.compile()

    def _run_sequential(self, state: GraphRAGState) -> GraphRAGState:
        for step in (
            self._parse_intent_node,
            self._retrieve_graph_node,
            self._synthesize_answer_node,
            self._evaluate_answer_node,
        ):
            state.update(step(state))
        return state

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(f"file:{self.database_path}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        return connection

    def _parse_intent_node(self, state: GraphRAGState) -> GraphRAGState:
        question = state["question"]
        intent = self._heuristic_intent(question)
        warnings = list(state.get("warnings", []))

        # Keep pre-retrieval intent parsing deterministic. LLM work happens after
        # graph/text retrieval so interactive queries only pay for one model call.
        return {"intent": intent, "warnings": warnings}

    def _heuristic_intent(self, question: str) -> QueryIntent:
        folded = _fold(question)
        is_latest_query = any(
            term in folded
            for term in ("gan day", "latest", "recent", "moi nhat", "newest")
        )
        ask_for: list[str] = []
        if any(term in folded for term in ("dinh gia", "valuation", "valued", "bao nhieu", "gia tri")):
            ask_for.append("valuation")
        if any(term in folded for term in ("buoc tien", "moi", "gan day", "latest", "recent")):
            ask_for.append("developments")
        if any(term in folded for term in ("lien quan", "relationship", "relation", "quan he")):
            ask_for.append("relationships")
        if any(
            term in folded
            for term in (
                "xau chuoi",
                "sau chuoi",
                "lien ket",
                "nguyen nhan",
                "he qua",
                "vi sao",
                "tai sao",
                "so what",
                "implication",
            )
        ):
            ask_for.append("chain")
        if not ask_for:
            ask_for.append("summary")

        relationship_types = [
            relationship
            for relationship in RELATIONSHIP_TYPES
            if relationship.lower() in question.lower()
        ]
        search_terms = [
            token
            for token in re.findall(r"[\w$.-]+", question, flags=re.UNICODE)
            if len(_fold(token)) > 2 and _fold(token) not in STOPWORDS
        ]
        return QueryIntent(
            entities=[],
            relationship_types=relationship_types,
            ask_for=ask_for,  # type: ignore[arg-type]
            search_terms=search_terms[:8],
            time_scope="latest" if is_latest_query else "all",
        )

    def _parse_intent_with_llm(self, question: str) -> QueryIntent:
        load_env()
        client = _openai_client()
        system_prompt = (
            "Extract graph query intent from the user question. "
            "Return only entities explicitly named or clearly requested. "
            "Do not answer the question."
        )
        response = client.responses.parse(
            model=get_openai_model(),
            temperature=0,
            input=_llm_messages(system_prompt, question),
            text_format=QueryIntent,
        )
        if response.output_parsed is not None:
            return response.output_parsed
        return QueryIntent.model_validate_json(response.output_text)

    def _retrieve_graph_node(self, state: GraphRAGState) -> GraphRAGState:
        intent = state["intent"]
        with self._connect() as connection:
            selected_week = state.get("week") or self._default_week(connection, intent)
            article_rows = connection.execute(
                """
                SELECT id, source, title, published_date, category, summary, original_file_path
                FROM articles
                ORDER BY published_date DESC, title ASC
                """
            ).fetchall()
            article_haystacks = self._build_article_haystacks(connection, article_rows)
            labels = self._known_graph_labels(connection)
            terms = self._resolve_terms(intent, labels)
            anchors = _anchor_terms(intent, terms)
            related_article_ids = self._match_article_ids(
                article_rows,
                article_haystacks,
                anchors or terms,
                selected_week,
            )
            facts = self._collect_facts(
                connection,
                article_rows,
                related_article_ids,
                terms,
                anchors,
                intent,
                selected_week,
            )
            citations = self._build_citations(facts)

        return {"facts": facts, "citations": citations}

    def _default_week(self, connection: sqlite3.Connection, intent: QueryIntent) -> str | None:
        if intent.time_scope != "latest":
            return None
        weeks = sorted(
            {
                week
                for row in connection.execute("SELECT published_date FROM articles").fetchall()
                for week in [week_label_for_published_date(str(row["published_date"] or ""))]
                if week is not None
            }
        )
        return weeks[-1] if weeks else None

    def _known_graph_labels(self, connection: sqlite3.Connection) -> list[str]:
        labels = [
            str(row["name"])
            for row in connection.execute("SELECT name FROM entities").fetchall()
        ]
        for row in connection.execute(
            """
            SELECT source_node AS label FROM graph_edges
            UNION
            SELECT target_node AS label FROM graph_edges
            """
        ).fetchall():
            labels.append(str(row["label"]))
        return _unique(labels)

    def _resolve_terms(self, intent: QueryIntent, labels: list[str]) -> list[str]:
        requested = _unique([*intent.entities, *intent.search_terms])
        resolved: list[str] = []
        requested_folded = [_fold(term) for term in requested]
        for label in labels:
            folded_label = _fold(label)
            if len(folded_label) <= 2:
                continue
            if any(term and (term in folded_label or folded_label in term) for term in requested_folded):
                resolved.append(label)

        if not resolved:
            resolved.extend(requested)
        return _unique(resolved)

    def _build_article_haystacks(
        self,
        connection: sqlite3.Connection,
        article_rows: list[sqlite3.Row],
    ) -> dict[str, str]:
        haystacks = {
            str(row["id"]): " ".join(
                str(row[key] or "")
                for key in ("title", "source", "category", "summary")
            )
            for row in article_rows
        }
        link_queries = (
            """
            SELECT ae.article_id, e.name, e.type, e.description, ae.role
            FROM article_entities AS ae
            JOIN entities AS e ON e.id = ae.entity_id
            """,
            """
            SELECT an.article_id, n.name, n.thesis, n.status
            FROM article_narratives AS an
            JOIN narratives AS n ON n.id = an.narrative_id
            """,
            """
            SELECT at.article_id, t.name, t.description
            FROM article_themes AS at
            JOIN themes AS t ON t.id = at.theme_id
            """,
            """
            SELECT article_id, event_date, event_summary, location
            FROM events
            """,
            """
            SELECT evidence_article_id AS article_id, source_node, relationship, target_node
            FROM graph_edges
            """,
        )
        for query in link_queries:
            for row in connection.execute(query).fetchall():
                article_id = str(row["article_id"] or "")
                if article_id not in haystacks:
                    continue
                haystacks[article_id] += " " + " ".join(str(value or "") for value in row)
        return haystacks

    def _match_article_ids(
        self,
        article_rows: list[sqlite3.Row],
        article_haystacks: dict[str, str],
        terms: list[str],
        week: str | None,
    ) -> set[str]:
        matched: set[str] = set()
        for row in article_rows:
            article_id = str(row["id"])
            if week is not None and _week_for_row(row) != week:
                continue
            if not terms or _contains_any(article_haystacks.get(article_id, ""), terms):
                matched.add(article_id)
        return matched

    def _collect_facts(
        self,
        connection: sqlite3.Connection,
        article_rows: list[sqlite3.Row],
        related_article_ids: set[str],
        terms: list[str],
        anchors: list[str],
        intent: QueryIntent,
        week: str | None,
    ) -> list[GraphFact]:
        article_by_id = {str(row["id"]): row for row in article_rows}
        facts: list[GraphFact] = []
        match_terms = anchors or terms

        for row in connection.execute(
            """
            SELECT ge.id, ge.source_node, ge.relationship, ge.target_node, ge.confidence,
                   a.id AS article_id, a.title, a.source, a.published_date, a.summary
            FROM graph_edges AS ge
            JOIN articles AS a ON a.id = ge.evidence_article_id
            ORDER BY a.published_date DESC, ge.confidence DESC
            """
        ).fetchall():
            article_id = str(row["article_id"])
            edge_text = f"{row['source_node']} {row['target_node']}"
            direct_match = _contains_any(edge_text, match_terms)
            if match_terms and not direct_match:
                continue
            if intent.relationship_types and str(row["relationship"] or "") not in intent.relationship_types:
                continue
            if not match_terms and article_id not in related_article_ids:
                continue
            if week is not None and week_label_for_published_date(str(row["published_date"] or "")) != week:
                continue
            facts.append(
                GraphFact(
                    kind="edge",
                    article_id=article_id,
                    title=str(row["title"] or ""),
                    source=str(row["source"] or ""),
                    published_date=str(row["published_date"] or ""),
                    source_node=str(row["source_node"] or ""),
                    relationship=str(row["relationship"] or ""),
                    target_node=str(row["target_node"] or ""),
                    confidence=float(row["confidence"] or 0),
                    statement=(
                        f"{row['source_node']} --{row['relationship']}--> {row['target_node']}"
                    ),
                    quote=_compact(str(row["summary"] or "")),
                )
            )

        for row in connection.execute(
            """
            SELECT ev.article_id, ev.event_date, ev.event_summary, ev.location, ev.importance_score,
                   a.title, a.source, a.published_date
            FROM events AS ev
            JOIN articles AS a ON a.id = ev.article_id
            ORDER BY a.published_date DESC, ev.importance_score DESC
            """
        ).fetchall():
            article_id = str(row["article_id"])
            event_match = _contains_any(f"{row['event_summary']} {row['title']}", match_terms)
            if match_terms and not event_match:
                continue
            if not match_terms and article_id not in related_article_ids:
                continue
            if week is not None and week_label_for_published_date(str(row["published_date"] or "")) != week:
                continue
            facts.append(
                GraphFact(
                    kind="event",
                    article_id=article_id,
                    title=str(row["title"] or ""),
                    source=str(row["source"] or ""),
                    published_date=str(row["published_date"] or ""),
                    statement=f"{row['event_date']}: {row['event_summary']}",
                    quote=_compact(str(row["event_summary"] or "")),
                )
            )

        for row in connection.execute(
            """
            SELECT an.article_id, n.name, n.thesis, n.status, n.importance_score,
                   a.title, a.source, a.published_date
            FROM article_narratives AS an
            JOIN narratives AS n ON n.id = an.narrative_id
            JOIN articles AS a ON a.id = an.article_id
            ORDER BY a.published_date DESC, n.importance_score DESC
            """
        ).fetchall():
            article_id = str(row["article_id"])
            if article_id not in related_article_ids and not _contains_any(
                f"{row['name']} {row['thesis']} {row['title']}",
                match_terms,
            ):
                continue
            if week is not None and week_label_for_published_date(str(row["published_date"] or "")) != week:
                continue
            facts.append(
                GraphFact(
                    kind="narrative",
                    article_id=article_id,
                    title=str(row["title"] or ""),
                    source=str(row["source"] or ""),
                    published_date=str(row["published_date"] or ""),
                    statement=f"{row['name']} ({row['status']}): {row['thesis']}",
                    quote=_compact(str(row["thesis"] or "")),
                )
            )

        for article_id in sorted(related_article_ids):
            row = article_by_id.get(article_id)
            if row is None:
                continue
            facts.append(
                GraphFact(
                    kind="article",
                    article_id=article_id,
                    title=str(row["title"] or ""),
                    source=str(row["source"] or ""),
                    published_date=str(row["published_date"] or ""),
                    statement=_compact(str(row["summary"] or "")),
                    quote=_compact(str(row["summary"] or "")),
                )
            )
            for snippet in self._source_snippets(row, match_terms, intent):
                facts.append(
                    GraphFact(
                        kind="snippet",
                        article_id=article_id,
                        title=str(row["title"] or ""),
                        source=str(row["source"] or ""),
                        published_date=str(row["published_date"] or ""),
                        statement=snippet,
                        quote=snippet,
                    )
                )

        return self._rank_facts(facts, intent)[:24]

    def _source_snippets(self, article_row: sqlite3.Row, terms: list[str], intent: QueryIntent) -> list[str]:
        path = _resolve_article_path(str(article_row["original_file_path"] or ""))
        if path is None:
            return []

        try:
            article = read_docx_article(path)
        except Exception:
            return []

        if "valuation" in intent.ask_for:
            base_terms = terms or intent.search_terms or intent.entities
            snippet_terms = list(VALUATION_TERMS)
        else:
            base_terms = []
            snippet_terms = list(terms)

        snippets: list[str] = []
        for paragraph in article.paragraphs:
            if base_terms and not _contains_any(paragraph, base_terms):
                continue
            if not _contains_any(paragraph, snippet_terms):
                continue
            snippets.append(_compact(paragraph, limit=900))
            if len(snippets) >= 5:
                break
        return snippets

    def _rank_facts(self, facts: list[GraphFact], intent: QueryIntent) -> list[GraphFact]:
        def score(fact: GraphFact) -> tuple[int, float, str]:
            kind_weight = {
                "edge": 6,
                "event": 5,
                "chunk": 4,
                "snippet": 4,
                "article": 4,
                "image": 3,
                "narrative": 2,
            }[fact.kind]
            valuation_boost = 3 if "valuation" in intent.ask_for and (
                MONEY_PATTERN.search(fact.statement) or _contains_any(fact.statement, list(VALUATION_TERMS))
            ) else 0
            article_boost = 2 if fact.kind == "article" and (
                "developments" in intent.ask_for or "summary" in intent.ask_for
            ) else 0
            confidence = fact.confidence if fact.confidence is not None else 0
            return (kind_weight + valuation_boost + article_boost, confidence, fact.published_date)

        deduped: list[GraphFact] = []
        seen: set[tuple[str, str, str, str]] = set()
        for fact in sorted(facts, key=score, reverse=True):
            key = (fact.kind, fact.article_id, fact.statement, fact.relationship)
            if key in seen:
                continue
            deduped.append(fact)
            seen.add(key)
        return deduped

    def _build_citations(self, facts: list[GraphFact]) -> list[Citation]:
        citations_by_article: dict[str, Citation] = {}
        for fact in facts:
            if fact.article_id in citations_by_article:
                continue
            citations_by_article[fact.article_id] = Citation(
                article_id=fact.article_id,
                title=fact.title,
                source=fact.source,
                published_date=fact.published_date,
                quote=fact.quote,
            )
        return list(citations_by_article.values())

    def _synthesize_answer_node(self, state: GraphRAGState) -> GraphRAGState:
        facts = state.get("facts", [])
        citations = state.get("citations", [])
        warnings = list(state.get("warnings", []))
        llm_used = False

        if state.get("use_llm", True) and facts and get_openai_api_key() is not None:
            try:
                answer = self._synthesize_with_llm(
                    state["question"],
                    state["intent"],
                    facts,
                    citations,
                    deep_search=bool(state.get("deep_search", False)),
                )
                llm_used = True
                return {"answer": answer, "warnings": warnings, "llm_used": llm_used}
            except Exception as exc:
                logging.warning("GraphRAG answer synthesis failed: %s", exc)
                warnings.append("LLM answer synthesis failed; used deterministic answer.")

        answer = self._deterministic_answer(state["question"], state["intent"], facts, citations)
        return {"answer": answer, "warnings": warnings, "llm_used": llm_used}

    def _synthesize_with_llm(
        self,
        question: str,
        intent: QueryIntent,
        facts: list[GraphFact],
        citations: list[Citation],
        *,
        deep_search: bool = False,
    ) -> str:
        client = _openai_client()
        synthesis_facts = _synthesis_facts(facts, deep_search=deep_search)
        payload = {
            "question": question,
            "intent": intent.model_dump(),
            "facts": [_fact_payload(fact) for fact in synthesis_facts],
            "citations": [
                {
                    "article_id": citation.article_id,
                    "title": citation.title,
                    "source": citation.source,
                    "published_date": citation.published_date,
                }
                for citation in citations[:8]
            ],
        }
        system_prompt = (
            "You answer in Vietnamese using only the supplied graph facts and evidence. "
            "Act like an analyst connecting events, not a database dumping rows. "
            "First answer the user's direct question in 2-4 concise bullets under 'Trả lời trực tiếp'. "
            "For latest-news questions, prioritize the newest relevant Article and Chunk facts before older timeline facts. "
            "If the newest relevant article is about a lawsuit, dispute, trial, investigation, outage, IPO, partnership change, or funding event, mention that plainly. "
            "Then add a section named 'Sâu chuỗi sự kiện' that explains the timeline and how one fact leads to the next. "
            "Then add 'Luận điểm từ graph' for the strategic interpretation backed by relationships and narratives. "
            "Use 'Evidence hỗ trợ' only for the most important quotes or relationships, not every retrieved fact. "
            "End with 'Điểm chưa đủ evidence' when the payload lacks an explicit answer, especially valuation. "
            "Never create a relationship, valuation, event, or citation that is not in the payload. "
            "If the evidence does not contain an explicit valuation for the requested entity, say that clearly. "
            "Cite article titles and dates inline."
        )
        if deep_search:
            system_prompt += (
                " This is a deep answer mode using embedding-expanded context. "
                "Read the Article and Chunk facts together, synthesize across related articles, "
                "and explain the conclusion more thoroughly while staying grounded in the supplied facts."
            )
        last_error: Exception | None = None
        for _ in range(get_openai_max_retries()):
            try:
                response = client.responses.create(
                    model=get_openai_model(),
                    temperature=get_openai_temperature(),
                    max_output_tokens=get_openai_max_output_tokens(),
                    input=_llm_messages(system_prompt, json.dumps(payload, ensure_ascii=False)),
                )
                return str(response.output_text).strip()
            except Exception as exc:
                last_error = exc
        raise RuntimeError(last_error or "LLM synthesis failed")

    def _deterministic_answer(
        self,
        question: str,
        intent: QueryIntent,
        facts: list[GraphFact],
        citations: list[Citation],
    ) -> str:
        if not facts:
            return "Mình không tìm thấy fact nào trong graph hiện tại khớp câu hỏi này."

        edge_facts = [fact for fact in facts if fact.kind == "edge"]
        article_facts = sorted(
            [fact for fact in facts if fact.kind == "article"],
            key=lambda fact: (fact.published_date, fact.title),
            reverse=True,
        )
        event_facts = sorted(
            [fact for fact in facts if fact.kind == "event"],
            key=_timeline_sort_key,
        )
        snippet_facts = [fact for fact in facts if fact.kind in {"snippet", "chunk", "image"}]
        narrative_facts = [fact for fact in facts if fact.kind == "narrative"]
        lines = ["Dựa trên graph/text đã retrieve, câu trả lời trực tiếp là:"]

        if article_facts:
            lines.append("\nTrả lời trực tiếp:")
            for fact in article_facts[:3]:
                lines.append(f"- {fact.title} ({fact.published_date}): {fact.statement}")

        if event_facts:
            lines.append("\nSâu chuỗi sự kiện:")
            for index, fact in enumerate(event_facts[:6], start=1):
                lines.append(f"{index}. {fact.statement} ({fact.title}, {fact.published_date}).")

        interpretation_lines: list[str] = []
        if event_facts and edge_facts:
            interpretation_lines.append(
                "Các event cho biết diễn biến theo thời gian; các relationship cho biết cơ chế nối các event đó lại với nhau."
            )
        if edge_facts:
            backbone = "; ".join(fact.statement for fact in edge_facts[:4])
            interpretation_lines.append(f"Backbone quan hệ nổi bật: {backbone}.")
        if narrative_facts:
            narratives = "; ".join(fact.statement for fact in narrative_facts[:3])
            interpretation_lines.append(f"Narrative layer: {narratives}.")
        if interpretation_lines:
            lines.append("\nLuận điểm từ graph:")
            for line in interpretation_lines:
                lines.append(f"- {line}")

        if edge_facts:
            lines.append("\nRelationship hỗ trợ:")
            for fact in edge_facts[:5]:
                lines.append(
                    f"- {fact.statement} "
                    f"(confidence {fact.confidence:.2f}, {fact.title}, {fact.published_date})."
                )

        general_snippets = [fact for fact in snippet_facts if "valuation" not in intent.ask_for]
        if general_snippets:
            lines.append("\nEvidence hỗ trợ:")
            for fact in general_snippets[:2]:
                prefix = "Image/caption" if fact.kind == "image" else "Text"
                lines.append(f"- {prefix}: {fact.statement} ({fact.title}, {fact.published_date}).")

        if "valuation" in intent.ask_for:
            valuation_lines = [
                fact for fact in [*snippet_facts, *event_facts]
                if MONEY_PATTERN.search(fact.statement) or _contains_any(fact.statement, list(VALUATION_TERMS))
            ]
            explicit_valuation_lines = [
                fact for fact in valuation_lines
                if _has_query_specific_explicit_valuation(fact.statement, intent)
            ]
            lines.append("\nVề định giá:")
            if explicit_valuation_lines:
                for fact in explicit_valuation_lines[:4]:
                    lines.append(f"- Evidence: {fact.statement} ({fact.title}, {fact.published_date}).")
            else:
                lines.append("- Không thấy con số định giá cụ thể cho entity được hỏi trong graph/evidence hiện có.")
                related_amounts = [
                    fact for fact in valuation_lines
                    if MONEY_PATTERN.search(fact.statement)
                    and not _contains_any(fact.statement, ["valuation", "valued", "định giá"])
                ]
                for fact in related_amounts[:3]:
                    lines.append(f"- Có con số liên quan nhưng không phải định giá rõ ràng: {fact.statement} ({fact.title}, {fact.published_date}).")

        if "valuation" in intent.ask_for and not any(
            _has_query_specific_explicit_valuation(fact.statement, intent)
            for fact in [*snippet_facts, *event_facts]
        ):
            lines.append("\nĐiểm chưa đủ evidence:")
            lines.append("- Graph hiện có có funding/partnership/IPO signals, nhưng chưa có valuation trực tiếp cho entity được hỏi.")

        if citations:
            citation_text = "; ".join(
                f"{citation.title} ({citation.published_date})"
                for citation in citations[:6]
            )
            lines.append(f"\nNguồn: {citation_text}.")

        return "\n".join(lines)

    def _evaluate_answer_node(self, state: GraphRAGState) -> GraphRAGState:
        facts = state.get("facts", [])
        citations = state.get("citations", [])
        edge_facts = [fact for fact in facts if fact.kind == "edge"]
        evaluation = {
            "fact_count": len(facts),
            "edge_fact_count": len(edge_facts),
            "citation_count": len(citations),
            "all_edge_facts_have_evidence": all(bool(fact.article_id) for fact in edge_facts),
            "valuation_evidence_count": sum(
                1
                for fact in facts
                if MONEY_PATTERN.search(fact.statement) or _contains_any(fact.statement, list(VALUATION_TERMS))
            ),
            "llm_used": bool(state.get("llm_used", False)),
            "deep_search": bool(state.get("deep_search", False)),
        }
        return {"evaluation": evaluation}


def answer_question(
    question: str,
    week: str | None = None,
    use_llm: bool = True,
    deep_search: bool = False,
    backend: Literal["sqlite", "neo4j", "auto"] = "sqlite",
) -> GraphRAGResult:
    if backend in {"neo4j", "auto"}:
        try:
            from .neo4j_rag import Neo4jGraphRAGService

            return Neo4jGraphRAGService().answer_question(
                question=question,
                week=week,
                use_llm=use_llm,
                deep_search=deep_search,
            )
        except Exception as exc:
            if backend == "neo4j":
                raise
            logging.warning("Neo4j GraphRAG unavailable; falling back to SQLite: %s", exc)
    return GraphRAGService().answer_question(
        question=question,
        week=week,
        use_llm=use_llm,
        deep_search=deep_search,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Ask the narrative graph with GraphRAG.")
    parser.add_argument("question", help="Question to ask.")
    parser.add_argument("--week", help="Optional week label, for example 2026-05-03.")
    parser.add_argument("--no-llm", action="store_true", help="Disable LLM synthesis for deterministic evaluation.")
    parser.add_argument("--deep", action="store_true", help="Use embedding-expanded retrieval for a deeper answer.")
    parser.add_argument("--backend", choices=["sqlite", "neo4j", "auto"], default="sqlite")
    args = parser.parse_args()

    result = answer_question(
        args.question,
        week=args.week,
        use_llm=not args.no_llm,
        deep_search=args.deep,
        backend=args.backend,
    )
    print(result.answer)
    print("\n--- Evaluation ---")
    print(json.dumps(result.evaluation, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
