#!/usr/bin/env python
"""Run local GraphRAG retrieval/evidence evaluations.

This evaluator intentionally disables LLM synthesis so CI can verify retrieval
deterministically without an API key.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.graph_rag import answer_question  # noqa: E402


VALUATION_PATTERN = re.compile(r"(?i)\b(valuation|valued|định giá)\b")
MONEY_PATTERN = re.compile(
    r"(?i)(?:\$|US\$)\s?[\d,.]+(?:\s?(?:bn|trn|billion|trillion|m|million))?"
    r"|[\d,.]+\s?(?:bn|trn|billion|trillion)"
)


def _relationship_key(fact: Any) -> tuple[str, str, str]:
    return (fact.source_node, fact.relationship, fact.target_node)


def evaluate_case(case: dict[str, Any], backend: str | None = None) -> dict[str, Any]:
    result = answer_question(
        case["question"],
        week=case.get("week"),
        use_llm=False,
        backend=backend or case.get("backend", "neo4j"),
    )
    relationships = {_relationship_key(fact) for fact in result.facts if fact.kind == "edge"}
    article_ids = {citation.article_id for citation in result.citations}
    statements = "\n".join(fact.statement for fact in result.facts)
    expected_entities = [str(entity).casefold() for entity in case.get("expected_entities", [])]

    missing_relationships = [
        relationship
        for relationship in [tuple(item) for item in case.get("expected_relationships", [])]
        if relationship not in relationships
    ]
    missing_articles = [
        article_id
        for article_id in case.get("expected_article_ids", [])
        if article_id not in article_ids
    ]
    explicit_valuation_found = bool(VALUATION_PATTERN.search(statements) and MONEY_PATTERN.search(statements))
    valuation_ok = not case.get("expected_no_explicit_valuation", False) or not explicit_valuation_found
    off_anchor_edges = [
        _relationship_key(fact)
        for fact in result.facts
        if fact.kind == "edge"
        and expected_entities
        and not any(
            entity in fact.source_node.casefold() or entity in fact.target_node.casefold()
            for entity in expected_entities
        )
    ]
    missing_answer_phrases = [
        phrase
        for phrase in case.get("expected_answer_phrases", [])
        if str(phrase).casefold() not in result.answer.casefold()
    ]

    passed = (
        not missing_relationships
        and not missing_articles
        and valuation_ok
        and not off_anchor_edges
        and not missing_answer_phrases
    )
    return {
        "id": case["id"],
        "passed": passed,
        "fact_count": len(result.facts),
        "citation_count": len(result.citations),
        "missing_relationships": missing_relationships,
        "missing_articles": missing_articles,
        "explicit_valuation_found": explicit_valuation_found,
        "valuation_ok": valuation_ok,
        "off_anchor_edges": off_anchor_edges,
        "missing_answer_phrases": missing_answer_phrases,
        "evaluation": result.evaluation,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate GraphRAG retrieval quality.")
    parser.add_argument(
        "--cases",
        default=str(PROJECT_ROOT / "evals" / "graphrag_cases.json"),
        help="Path to GraphRAG evaluation cases JSON.",
    )
    parser.add_argument(
        "--backend",
        choices=["neo4j", "auto"],
        help="Override backend for all eval cases. Defaults to each case backend, or Neo4j.",
    )
    args = parser.parse_args()

    cases = json.loads(Path(args.cases).read_text(encoding="utf-8"))
    results = [evaluate_case(case, backend=args.backend) for case in cases]
    print(json.dumps(results, indent=2, ensure_ascii=False))

    if not all(result["passed"] for result in results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
