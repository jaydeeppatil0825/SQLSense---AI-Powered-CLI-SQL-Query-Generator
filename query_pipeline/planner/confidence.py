"""Confidence and ambiguity scoring helpers for the deterministic planner."""

from __future__ import annotations

from typing import Any
import re


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip().lower())


def _compute_intent_confidence(question: str, intent: dict[str, Any]) -> float:
    """Compute confidence score for the intent based on question clarity."""
    normalized = _normalize(question)
    confidence = 0.5  # Base confidence

    # Higher confidence if question has clear intent type
    intent_type = intent.get("intent_type", "")
    if intent_type in {"count", "total", "average", "top_n"}:
        confidence += 0.2

    # Higher confidence if question has explicit metrics
    if intent.get("requested_metrics"):
        confidence += 0.15

    # Higher confidence if question has explicit dimensions
    if intent.get("requested_dimensions"):
        confidence += 0.1

    # Higher confidence if question has explicit limit
    if intent.get("limit"):
        confidence += 0.1

    # Lower confidence if question is very short
    if len(normalized.split()) < 3:
        confidence -= 0.1  # Reduced penalty from 0.2 to 0.1

    # Lower confidence if question contains vague terms
    vague_terms = {"something", "anything", "everything", "all", "stuff", "things"}
    if any(term in normalized for term in vague_terms):
        confidence -= 0.15

    # Lower confidence if question has no business terms
    if not intent.get("raw_business_terms"):
        confidence -= 0.1

    # Boost confidence for simple, clear questions
    if intent_type == "list" and len(intent.get("raw_business_terms", [])) >= 1:
        confidence += 0.1

    if intent_type == "count" and len(intent.get("raw_business_terms", [])) >= 1:
        confidence += 0.1

    # Clamp confidence between 0.0 and 1.0
    return max(0.0, min(1.0, confidence))


def _has_close_role_ambiguity(candidates: list[dict[str, Any]]) -> bool:
    unique_candidates = []
    seen: set[tuple[str, str]] = set()
    for entry in candidates or []:
        key = (str(entry.get("table") or "").strip(), str(entry.get("column") or "").strip())
        if not key[0] or not key[1] or key in seen:
            continue
        seen.add(key)
        unique_candidates.append(entry)
    if len(unique_candidates) < 2:
        return False
    unique_candidates.sort(
        key=lambda item: (
            -float(item.get("score") or 0.0),
            str(item.get("table") or ""),
            str(item.get("column") or ""),
        )
    )
    top_score = float(unique_candidates[0].get("score") or 0.0)
    second_score = float(unique_candidates[1].get("score") or 0.0)
    return abs(top_score - second_score) < 0.08
