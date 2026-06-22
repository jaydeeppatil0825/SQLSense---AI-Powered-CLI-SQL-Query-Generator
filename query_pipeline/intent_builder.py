"""
core/intent_builder.py
======================
Schema-agnostic intent builder for the query pipeline.

This module understands the shape of a user question without deciding
runtime tables, columns, formulas, or business mappings.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional

from core.ai_backend_service import call_ai_backend
from utils.logger import get_logger
from query_pipeline.question_normalizer import normalize_question
from sql_pipeline.sql_validator import extract_requested_limit

logger = get_logger()
_ALLOWED_INTENT_TYPES = {
    "list",
    "count",
    "ranking",
    "grouped_summary",
    "comparison",
    "sorted_list",
    "filter",
    "unknown",
}
_ALLOWED_BUSINESS_OPERATIONS = {
    "browse",
    "count",
    "rank",
    "summarize",
    "compare",
    "sort",
    "analyze",
}

_LEADING_ACTION_RE = re.compile(
    r"^\s*(?:show|list|display|get|fetch|view|see|give|tell(?:\s+me)?|find)\b\s*",
    re.IGNORECASE,
)
_COUNT_RE = re.compile(
    r"^\s*(?:count\b|how\s+many\b|number\s+of\b)",
    re.IGNORECASE,
)
_TOP_RE = re.compile(r"\btop\s+(\d+)\b", re.IGNORECASE)
_FIRST_RE = re.compile(r"\bfirst\s+(\d+)\b", re.IGNORECASE)
_LIMIT_RE = re.compile(r"\blimit\s+(\d+)\b", re.IGNORECASE)
_LATEST_RE = re.compile(r"\b(?:latest|recent|newest|oldest)\b", re.IGNORECASE)
_SORTED_BY_RE = re.compile(r"\b(?:sorted|ordered)\s+by\s+(.+)$", re.IGNORECASE)
_BY_RE = re.compile(r"\s+by\s+", re.IGNORECASE)
_PER_RE = re.compile(r"\s+per\s+", re.IGNORECASE)
_WISE_RE = re.compile(r"\b[a-z0-9_ ]+\s+wise\b", re.IGNORECASE)
_FROM_RE = re.compile(r"\s+from\s+(.+)$", re.IGNORECASE)
_IN_RE = re.compile(r"\s+in\s+(.+)$", re.IGNORECASE)
_WHERE_RE = re.compile(r"\s+where\s+(.+)$", re.IGNORECASE)
_COMPARE_RE = re.compile(r"\b(?:vs|versus|compare|comparison)\b", re.IGNORECASE)
_STOPWORD_RE = re.compile(
    r"^(?:show|list|display|get|fetch|view|see|give|tell|me|all|the|a|an|of|for|to|with|by|from|in|where)$",
    re.IGNORECASE,
)


def build_intent(question: str, ai_backend: str = "local") -> Dict[str, Any]:
    """Return structured, schema-agnostic intent for the question."""
    normalized_question, _ = normalize_question(question)
    fallback = _build_fallback_intent(normalized_question)

    ai_intent = _build_ai_intent(normalized_question, ai_backend=ai_backend)
    if not ai_intent:
        return fallback

    merged = dict(fallback)
    for key, value in ai_intent.items():
        if key not in merged:
            continue
        if value in (None, "", [], {}):
            continue
        merged[key] = value

    merged["raw_business_terms"] = _merge_unique(
        ai_intent.get("raw_business_terms"),
        fallback.get("raw_business_terms"),
    )
    merged["requested_metrics"] = _merge_unique(
        ai_intent.get("requested_metrics"),
        fallback.get("requested_metrics"),
    )
    merged["requested_dimensions"] = _merge_unique(
        ai_intent.get("requested_dimensions"),
        fallback.get("requested_dimensions"),
    )
    merged["requested_filters"] = _merge_unique(
        ai_intent.get("requested_filters"),
        fallback.get("requested_filters"),
    )
    merged["needs_grouping"] = bool(ai_intent.get("needs_grouping")) or bool(fallback.get("needs_grouping"))
    merged["needs_aggregation"] = bool(ai_intent.get("needs_aggregation")) or bool(fallback.get("needs_aggregation"))
    merged["needs_join"] = _merge_join_hint(ai_intent.get("needs_join"), fallback.get("needs_join"))
    merged["limit"] = merged.get("limit") or fallback.get("limit")
    merged["confidence"] = max(
        float(fallback.get("confidence") or 0.0),
        float(merged.get("confidence") or 0.0),
    )
    merged = _normalize_simple_target_entity_usage(merged, normalized_question)
    merged["source"] = "ai" if ai_intent.get("source") == "ai" else fallback.get("source", "fallback")
    return merged


def _build_ai_intent(question: str, *, ai_backend: str) -> Optional[Dict[str, Any]]:
    messages = [
        {
            "role": "system",
            "content": (
                "You analyze natural-language data questions. "
                "Return JSON only. "
                "Do not choose database tables, columns, formulas, aliases, or SQL. "
                "Only describe the request shape and preserve business phrases from the question."
            ),
        },
        {
            "role": "user",
            "content": (
                "Analyze this user question and return JSON with exactly these keys: "
                "user_goal, intent_type, business_operation, requested_metrics, requested_dimensions, "
                "requested_filters, requested_sort, limit, needs_grouping, needs_aggregation, needs_join, "
                "raw_business_terms, confidence. "
                "Rules: keep business terms from the question, do not map them to schema terms, "
                "do not invent formulas, do not invent tables or columns, "
                "use generic values like list/count/ranking/grouped_summary/comparison/filter/sort where suitable. "
                f"Question: {question}"
            ),
        },
    ]

    try:
        raw_response = call_ai_backend(
            messages,
            backend=ai_backend,
            temperature=0,
            max_tokens=500,
        )
    except Exception as exc:
        logger.debug(f"Intent builder AI fallback activated: {exc}")
        return None

    return _parse_ai_intent_response(raw_response, question)


def _parse_ai_intent_response(raw_response: str, question: str) -> Optional[Dict[str, Any]]:
    text = str(raw_response or "").strip()
    if not text:
        return None

    parsed = _extract_json_object(text)
    if not isinstance(parsed, dict):
        return None

    normalized_question, _ = normalize_question(question)
    sanitized = _sanitize_intent(parsed, normalized_question)
    if not sanitized:
        return None
    sanitized["source"] = "ai"
    return sanitized


def _extract_json_object(text: str) -> Optional[dict[str, Any]]:
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _sanitize_intent(payload: Dict[str, Any], question: str) -> Dict[str, Any]:
    intent_type = _normalize_intent_type(payload.get("intent_type"))
    business_operation = _normalize_business_operation(payload.get("business_operation"))
    user_goal = _clean_scalar(payload.get("user_goal")) or question
    requested_sort = payload.get("requested_sort")
    if not isinstance(requested_sort, dict):
        requested_sort = {}

    sanitized = {
        "user_goal": user_goal,
        "intent_type": intent_type or "unknown",
        "business_operation": business_operation or "analyze",
        "requested_metrics": _clean_list(payload.get("requested_metrics")),
        "requested_dimensions": _clean_list(payload.get("requested_dimensions")),
        "requested_filters": _clean_list(payload.get("requested_filters")),
        "requested_sort": {
            key: _clean_scalar(value)
            for key, value in requested_sort.items()
            if _clean_scalar(value)
        },
        "limit": _clean_limit(payload.get("limit")),
        "needs_grouping": _coerce_bool(payload.get("needs_grouping")),
        "needs_aggregation": _coerce_bool(payload.get("needs_aggregation")),
        "needs_join": _coerce_join_hint(payload.get("needs_join")),
        "raw_business_terms": _clean_list(payload.get("raw_business_terms")),
        "confidence": _coerce_confidence(payload.get("confidence")),
    }
    return _normalize_simple_target_entity_usage(sanitized, question)


def _build_fallback_intent(question: str) -> Dict[str, Any]:
    normalized_question, _ = normalize_question(question)
    body = _strip_leading_action(normalized_question)
    limit = extract_requested_limit(normalized_question)
    intent_type = "list"
    business_operation = "browse"
    requested_metrics: list[str] = []
    requested_dimensions: list[str] = []
    requested_filters: list[str] = []
    requested_sort: dict[str, Any] = {}

    sort_match = _SORTED_BY_RE.search(normalized_question)
    if sort_match:
        requested_sort = {"direction": "asc", "terms": sort_match.group(1).strip()}

    if _COUNT_RE.search(normalized_question):
        intent_type = "count"
        business_operation = "count"
        requested_dimensions = [_cleanup_phrase(_COUNT_RE.sub("", normalized_question).strip())]
    elif _COMPARE_RE.search(normalized_question):
        intent_type = "comparison"
        business_operation = "compare"
    elif _TOP_RE.search(normalized_question) or _FIRST_RE.search(normalized_question):
        intent_type = "ranking"
        business_operation = "rank"
    elif sort_match or _LATEST_RE.search(normalized_question):
        intent_type = "sorted_list"
        business_operation = "sort"

    if limit is None:
        top_match = _TOP_RE.search(normalized_question) or _FIRST_RE.search(normalized_question) or _LIMIT_RE.search(normalized_question)
        if top_match:
            limit = int(top_match.group(1))

    body_without_rank = re.sub(r"\b(?:top|first|limit)\s+\d+\b", "", body, flags=re.IGNORECASE).strip()
    body_without_latest = re.sub(r"\b(?:latest|recent|newest|oldest)\b", "", body_without_rank, flags=re.IGNORECASE).strip()
    body_without_sort = re.sub(r"\b(?:sorted|ordered)\s+by\s+.+$", "", body_without_latest, flags=re.IGNORECASE).strip()

    by_parts = _split_once(body_without_sort, _BY_RE)
    if by_parts:
        left, right = by_parts
        left = _cleanup_phrase(left)
        right = _cleanup_phrase(right)
        if intent_type == "ranking":
            if left:
                requested_dimensions = [left]
            if right:
                requested_metrics = [right]
        else:
            if left:
                requested_metrics = [left]
            if right:
                requested_dimensions = [right]
        if intent_type == "list":
            intent_type = "grouped_summary"
            business_operation = "summarize"
        if intent_type == "ranking":
            business_operation = "rank"
    elif not requested_dimensions:
        primary_phrase = _cleanup_phrase(body_without_sort)
        if primary_phrase:
            requested_dimensions = [primary_phrase]

    if not requested_filters:
        filter_match = _WHERE_RE.search(body) or _FROM_RE.search(body) or _IN_RE.search(body)
        if filter_match:
            filter_phrase = _cleanup_phrase(filter_match.group(1))
            if filter_phrase:
                requested_filters = [filter_phrase]

    if _LATEST_RE.search(normalized_question) and not requested_sort:
        requested_sort = {"direction": "desc", "terms": "latest"}
    if _TOP_RE.search(normalized_question) and not requested_sort:
        requested_sort = {"direction": "desc", "terms": "ranking"}

    needs_grouping = bool(requested_dimensions and requested_metrics and _BY_RE.search(body_without_sort))
    needs_aggregation = intent_type in {"count", "grouped_summary", "ranking", "comparison"} or bool(
        requested_metrics and _BY_RE.search(body_without_sort)
    )
    needs_join = "likely" if needs_grouping and requested_metrics and requested_dimensions else False

    raw_business_terms = _extract_raw_business_terms(
        normalized_question,
        requested_metrics=requested_metrics,
        requested_dimensions=requested_dimensions,
        requested_filters=requested_filters,
        requested_sort=requested_sort,
    )

    user_goal = _build_user_goal(
        normalized_question,
        intent_type=intent_type,
        requested_metrics=requested_metrics,
        requested_dimensions=requested_dimensions,
        requested_filters=requested_filters,
        requested_sort=requested_sort,
    )

    confidence = 0.58
    if intent_type in {"count", "ranking"}:
        confidence = 0.72
    elif needs_grouping:
        confidence = 0.68
    elif requested_filters or requested_sort:
        confidence = 0.64

    return _normalize_simple_target_entity_usage({
        "user_goal": user_goal,
        "intent_type": intent_type,
        "business_operation": business_operation,
        "requested_metrics": requested_metrics,
        "requested_dimensions": requested_dimensions,
        "requested_filters": requested_filters,
        "requested_sort": requested_sort,
        "limit": limit,
        "needs_grouping": needs_grouping,
        "needs_aggregation": needs_aggregation,
        "needs_join": needs_join,
        "raw_business_terms": raw_business_terms,
        "confidence": confidence,
        "source": "fallback",
    }, normalized_question)


def _has_explicit_grouping_marker(question: str) -> bool:
    return bool(
        _BY_RE.search(question)
        or _PER_RE.search(question)
        or _WISE_RE.search(question)
    )


def _normalize_simple_target_entity_usage(intent: Dict[str, Any], question: str) -> Dict[str, Any]:
    """Keep simple list/count target entities in raw_business_terms, not grouping dimensions."""
    normalized = dict(intent or {})
    intent_type = str(normalized.get("intent_type") or "").strip().lower()
    requested_metrics = list(normalized.get("requested_metrics") or [])
    requested_sort = dict(normalized.get("requested_sort") or {})

    if intent_type == "unknown":
        if _COUNT_RE.search(question):
            normalized["intent_type"] = "count"
            normalized["business_operation"] = "count"
        elif _TOP_RE.search(question) or _FIRST_RE.search(question):
            normalized["intent_type"] = "ranking"
            normalized["business_operation"] = "rank"
        elif _LATEST_RE.search(question) or requested_sort:
            normalized["intent_type"] = "sorted_list"
            normalized["business_operation"] = "sort"
        else:
            normalized["intent_type"] = "list"
            normalized["business_operation"] = "browse"
        intent_type = str(normalized.get("intent_type") or "").strip().lower()

    if intent_type == "count":
        normalized["requested_metrics"] = []
        requested_metrics = []

    if (
        intent_type in {"list", "count", "sorted_list"}
        and not _has_explicit_grouping_marker(question)
        and not requested_metrics
    ):
        normalized["requested_dimensions"] = []
        normalized["needs_grouping"] = False
        normalized["needs_join"] = False
        if intent_type == "count":
            normalized["business_operation"] = "count"
            normalized["needs_aggregation"] = True
        elif intent_type == "sorted_list":
            normalized["business_operation"] = "sort"
            normalized["needs_aggregation"] = False
        else:
            normalized["business_operation"] = "browse"
            normalized["needs_aggregation"] = False

    return normalized


def _build_user_goal(
    question: str,
    *,
    intent_type: str,
    requested_metrics: list[str],
    requested_dimensions: list[str],
    requested_filters: list[str],
    requested_sort: dict[str, Any],
) -> str:
    if intent_type == "count" and requested_dimensions:
        return f"count {requested_dimensions[0]}"
    if intent_type == "ranking" and requested_dimensions and requested_metrics:
        return f"rank {requested_dimensions[0]} by {requested_metrics[0]}"
    if requested_metrics and requested_dimensions:
        return f"show {requested_metrics[0]} grouped by {requested_dimensions[0]}"
    if requested_dimensions:
        goal = f"show {requested_dimensions[0]}"
        if requested_filters:
            goal = f"{goal} filtered by {requested_filters[0]}"
        if requested_sort.get("terms"):
            goal = f"{goal} sorted by {requested_sort['terms']}"
        return goal
    return question


def _extract_raw_business_terms(
    question: str,
    *,
    requested_metrics: list[str],
    requested_dimensions: list[str],
    requested_filters: list[str],
    requested_sort: dict[str, Any],
) -> list[str]:
    collected = _merge_unique(
        requested_metrics,
        requested_dimensions,
        requested_filters,
        [requested_sort.get("terms")] if requested_sort.get("terms") else [],
    )
    if collected:
        return collected

    terms = []
    for token in re.split(r"[^a-z0-9_]+", question.lower()):
        cleaned = token.strip()
        if not cleaned or cleaned.isdigit() or _STOPWORD_RE.match(cleaned):
            continue
        terms.append(cleaned)
    return _merge_unique(terms)


def _strip_leading_action(question: str) -> str:
    stripped = _LEADING_ACTION_RE.sub("", str(question or "").strip())
    return stripped.strip() or str(question or "").strip()


def _split_once(value: str, pattern: re.Pattern[str]) -> Optional[tuple[str, str]]:
    parts = pattern.split(value, maxsplit=1)
    if len(parts) != 2:
        return None
    return parts[0], parts[1]


def _cleanup_phrase(value: str) -> str:
    phrase = re.sub(r"\s+", " ", str(value or "")).strip(" ,.;:")
    phrase = re.sub(r"^(?:all|the)\s+", "", phrase, flags=re.IGNORECASE)
    return phrase.strip()


def _clean_scalar(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _clean_list(value: Any) -> list[str]:
    if isinstance(value, str):
        candidates = [value]
    elif isinstance(value, list):
        candidates = value
    else:
        return []
    cleaned = []
    for item in candidates:
        text = _clean_scalar(item)
        if text:
            cleaned.append(text)
    return _merge_unique(cleaned)


def _merge_unique(*values: Any) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for value in values:
        if isinstance(value, str):
            iterable = [value]
        else:
            iterable = list(value or [])
        for item in iterable:
            text = _clean_scalar(item)
            key = text.lower()
            if not text or key in seen:
                continue
            seen.add(key)
            merged.append(text)
    return merged


def _clean_limit(value: Any) -> Optional[int]:
    if value in (None, "", False):
        return None
    try:
        limit = int(value)
    except (TypeError, ValueError):
        return None
    return limit if limit > 0 else None


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "yes", "1", "likely"}
    return bool(value)


def _coerce_join_hint(value: Any) -> str | bool:
    if isinstance(value, str):
        cleaned = value.strip().lower()
        if cleaned in {"likely", "yes", "true"}:
            return "likely" if cleaned == "likely" else True
        if cleaned in {"no", "false"}:
            return False
        return value.strip()
    return bool(value)


def _merge_join_hint(primary: Any, fallback: Any) -> str | bool:
    primary_hint = _coerce_join_hint(primary)
    fallback_hint = _coerce_join_hint(fallback)
    if primary_hint == "likely" or fallback_hint == "likely":
        return "likely"
    return bool(primary_hint or fallback_hint)


def _coerce_confidence(value: Any) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(confidence, 1.0))


def _normalize_intent_type(value: Any) -> str:
    text = _clean_scalar(value).lower().replace(" ", "_").replace("-", "_")
    if text in _ALLOWED_INTENT_TYPES:
        return text
    return ""


def _normalize_business_operation(value: Any) -> str:
    text = _clean_scalar(value).lower().replace(" ", "_").replace("-", "_")
    if text in _ALLOWED_BUSINESS_OPERATIONS:
        return text
    return ""
