"""
core/intent_builder.py
======================
Schema-agnostic intent builder for the query pipeline.

This module understands the shape of a user question without deciding
runtime tables, columns, formulas, or business mappings.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Optional
from utils.logger import get_logger
from query_pipeline.question_normalizer import normalize_question

logger = get_logger()
INTENT_CONTRACT_VERSION = "1.0"
_ALLOWED_INTENT_TYPES = {
    "list",
    "count",
    "aggregate",
    "ranking",
    "grouped_summary",
    "comparison",
    "sorted_list",
    "filter",
    "unsafe",
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
    "block",
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
_BOTTOM_RE = re.compile(r"\b(?:bottom|lowest)\s+(\d+)\b", re.IGNORECASE)
_FIRST_RE = re.compile(r"\bfirst\s+(\d+)\b", re.IGNORECASE)
_LIMIT_RE = re.compile(r"\blimit\s+(\d+)\b", re.IGNORECASE)
_LATEST_RE = re.compile(r"\b(?:latest|recent|newest|oldest)\b", re.IGNORECASE)
_SORTED_BY_RE = re.compile(r"\b(?:sort(?:ed)?|order(?:ed)?)\s+by\s+(.+)$", re.IGNORECASE)
_BY_RE = re.compile(r"\s+by\s+", re.IGNORECASE)
_PER_RE = re.compile(r"\s+per\s+", re.IGNORECASE)
_WISE_RE = re.compile(r"\b[a-z0-9_ ]+\s+wise\b", re.IGNORECASE)
_FROM_RE = re.compile(r"\s+from\s+(.+)$", re.IGNORECASE)
_IN_RE = re.compile(r"\s+in\s+(.+)$", re.IGNORECASE)
_FOR_RE = re.compile(r"\s+for\s+(.+)$", re.IGNORECASE)
_WHERE_RE = re.compile(
    r"\s+where\s+(.+?)(?=\s+(?:group(?:ed)?\s+by|sort(?:ed)?|order(?:ed)?|limit\s+\d+)\b|$)",
    re.IGNORECASE,
)
_FILTER_RE = re.compile(
    r"\s+filter(?:ed)?(?:\s+by)?\s+(.+?)(?=\s+(?:group(?:ed)?\s+by|sort(?:ed)?|order(?:ed)?|limit\s+\d+)\b|$)",
    re.IGNORECASE,
)
_GROUP_BY_RE = re.compile(r"\s+group(?:ed)?\s+by\s+(.+)$", re.IGNORECASE)
_BETWEEN_RE = re.compile(r"\bbetween\s+(.+?)\s+and\s+(.+?)(?=\s+(?:by|per|each|group(?:ed)?\s+by|sorted|ordered|$))", re.IGNORECASE)
_BEFORE_RE = re.compile(r"\bbefore\s+(.+?)(?=\s+(?:by|per|each|group(?:ed)?\s+by|sorted|ordered|$))", re.IGNORECASE)
_AFTER_RE = re.compile(r"\bafter\s+(.+?)(?=\s+(?:by|per|each|group(?:ed)?\s+by|sorted|ordered|$))", re.IGNORECASE)
_GREATER_THAN_RE = re.compile(r"\bgreater\s+than\s+(.+?)(?=\s+(?:by|per|each|group(?:ed)?\s+by|sorted|ordered|$))", re.IGNORECASE)
_LESS_THAN_RE = re.compile(r"\bless\s+than\s+(.+?)(?=\s+(?:by|per|each|group(?:ed)?\s+by|sorted|ordered|$))", re.IGNORECASE)
_COMPARE_RE = re.compile(r"\b(?:vs|versus|compare|comparison)\b", re.IGNORECASE)
_AGGREGATE_SUM_RE = re.compile(r"\b(?:total|sum)\b", re.IGNORECASE)
_AGGREGATE_AVG_RE = re.compile(r"\b(?:average|avg)\b", re.IGNORECASE)
_AGGREGATE_MAX_RE = re.compile(r"\b(?:maximum|max|highest)\b", re.IGNORECASE)
_AGGREGATE_MIN_RE = re.compile(r"\b(?:minimum|min|lowest)\b", re.IGNORECASE)
_UNSAFE_RE = re.compile(r"\b(delete|update|insert|drop|alter|truncate)\b", re.IGNORECASE)
_STOPWORD_RE = re.compile(
    r"^(?:show|list|display|get|fetch|view|see|give|tell|me|all|the|a|an|of|for|to|with|by|from|in|where|per|each|group|filter)$",
    re.IGNORECASE,
)
_GENERIC_METRIC_TERMS = {
    "amount",
    "value",
    "total",
    "metric",
    "number",
    "quantity",
    "qty",
    "count",
}


def build_intent(question: str, ai_backend: str = "local") -> Dict[str, Any]:
    """
    Return structured, schema-agnostic intent for the question.

    AI is intentionally not used here.
    Runtime intent building must remain deterministic.
    The ai_backend parameter is kept temporarily for backward compatibility.
    """
    normalized_question, _ = normalize_question(question)
    intent = _build_fallback_intent(normalized_question)
    intent = _normalize_simple_target_entity_usage(intent, normalized_question)
    return _apply_intent_contract(intent, normalized_question)


def extract_requested_limit(question: str) -> Optional[int]:
    """Extract an explicit row limit from generic query wording."""
    normalized_question, _ = normalize_question(question)
    for pattern in (_TOP_RE, _BOTTOM_RE, _FIRST_RE, _LIMIT_RE):
        match = pattern.search(normalized_question)
        if not match:
            continue
        try:
            value = int(match.group(1))
        except (TypeError, ValueError):
            continue
        if value > 0:
            return value
    return None


def _apply_intent_contract(intent: Dict[str, Any], question: str) -> Dict[str, Any]:
    """Attach additive, versioned diagnostics without changing legacy fields."""
    normalized = dict(intent or {})
    structured_filters = _extract_structured_filters(question)
    intent_type = str(normalized.get("intent_type") or "unknown").strip().lower()
    confidence_reasons = ["deterministic_pattern_match"]
    missing_phrases: list[str] = []
    ambiguous_phrases: list[str] = []
    unsupported_constructs: list[str] = []

    if normalized.get("unsafe"):
        confidence_reasons.append("explicit_unsafe_operation")
    if intent_type == "count":
        confidence_reasons.append("explicit_count_phrase")
    if normalized.get("aggregate_function"):
        confidence_reasons.append("explicit_aggregate_function")
    if normalized.get("limit") is not None:
        confidence_reasons.append("explicit_limit")
    if normalized.get("requested_sort"):
        confidence_reasons.append("explicit_sorting")
    if normalized.get("requested_filters"):
        confidence_reasons.append("explicit_filter_clause")
    if normalized.get("grouping_phrase"):
        confidence_reasons.append("explicit_grouping_phrase")
    if normalized.get("source_scope"):
        confidence_reasons.append("explicit_source_scope")

    if not normalized.get("unsafe"):
        if intent_type in {"list", "count", "sorted_list"} and not normalized.get("target_entity_phrase"):
            missing_phrases.append("target_entity_phrase")
        if intent_type in {"aggregate", "ranking", "grouped_summary"} and not normalized.get("metric_phrase"):
            missing_phrases.append("metric_phrase")
        if intent_type == "grouped_summary" and not normalized.get("grouping_phrase"):
            missing_phrases.append("grouping_phrase")

    if normalized.get("metric_is_generic"):
        ambiguous_phrases.append("generic_metric_phrase")
        confidence_reasons.append("generic_metric_requires_evidence")
    if re.search(r"\b(?:something|anything|everything|stuff|things)\b", question, re.IGNORECASE):
        ambiguous_phrases.append("vague_business_phrase")
        confidence_reasons.append("vague_language")
    if any(entry.get("operator") == "unknown" for entry in structured_filters):
        unsupported_constructs.append("unparsed_filter_expression")
    if len([part for part in str(question or "").split(";") if part.strip()]) > 1:
        unsupported_constructs.append("multiple_statements")

    normalized["intent_contract_version"] = INTENT_CONTRACT_VERSION
    normalized["structured_filters"] = structured_filters
    normalized["confidence_reasons"] = _merge_unique(confidence_reasons)
    normalized["missing_phrases"] = _merge_unique(missing_phrases)
    normalized["unsupported_constructs"] = _merge_unique(unsupported_constructs)
    normalized["parse_diagnostics"] = {
        "missing_phrases": list(normalized["missing_phrases"]),
        "ambiguous_phrases": _merge_unique(ambiguous_phrases),
        "unsupported_constructs": list(normalized["unsupported_constructs"]),
        "has_issues": bool(missing_phrases or ambiguous_phrases or unsupported_constructs),
    }
    normalized["source"] = "deterministic"
    return normalized




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
        "aggregate_function": _clean_scalar(payload.get("aggregate_function")),
        "source_scope": _clean_list(payload.get("source_scope")),
        "limit": _clean_limit(payload.get("limit")),
        "needs_grouping": _coerce_bool(payload.get("needs_grouping")),
        "needs_aggregation": _coerce_bool(payload.get("needs_aggregation")),
        "needs_join": _coerce_join_hint(payload.get("needs_join")),
        "raw_business_terms": _clean_list(payload.get("raw_business_terms")),
        "confidence": _coerce_confidence(payload.get("confidence")),
        "unsafe": _coerce_bool(payload.get("unsafe")),
        "unsafe_operation": _clean_scalar(payload.get("unsafe_operation")),
        "target_entity_phrase": _clean_scalar(payload.get("target_entity_phrase")),
        "metric_phrase": _clean_scalar(payload.get("metric_phrase")),
        "metric_is_generic": _coerce_bool(payload.get("metric_is_generic")),
        "source_scope_phrase": _clean_scalar(payload.get("source_scope_phrase")),
        "filter_phrase": _clean_scalar(payload.get("filter_phrase")),
        "grouping_phrase": _clean_scalar(payload.get("grouping_phrase")),
        "ranking_phrase": _clean_scalar(payload.get("ranking_phrase")),
        "limit_phrase": _clean_scalar(payload.get("limit_phrase")),
    }
    return _normalize_simple_target_entity_usage(sanitized, question)


def _build_fallback_intent(question: str) -> Dict[str, Any]:
    normalized_question, _ = normalize_question(question)
    unsafe_operation = _detect_unsafe_operation(normalized_question)
    if unsafe_operation:
        raw_terms = _extract_raw_business_terms(
            normalized_question,
            requested_metrics=[],
            requested_dimensions=[],
            requested_filters=[],
            requested_sort={},
            source_scope=[],
        )
        return {
            "user_goal": normalized_question,
            "intent_type": "unsafe",
            "business_operation": "block",
            "requested_metrics": [],
            "requested_dimensions": [],
            "requested_filters": [],
            "requested_sort": {},
            "aggregate_function": None,
            "source_scope": [],
            "limit": None,
            "needs_grouping": False,
            "needs_aggregation": False,
            "needs_join": False,
            "raw_business_terms": raw_terms,
            "confidence": 0.99,
            "unsafe": True,
            "unsafe_operation": unsafe_operation,
            "target_entity_phrase": _cleanup_phrase(_UNSAFE_RE.sub("", normalized_question).strip()),
            "metric_phrase": "",
            "metric_is_generic": False,
            "source_scope_phrase": "",
            "filter_phrase": "",
            "grouping_phrase": "",
            "ranking_phrase": "",
            "limit_phrase": "",
            "source": "fallback",
        }

    body = _strip_leading_action(normalized_question)
    aggregate_function = _detect_aggregate_function(normalized_question)
    limit = extract_requested_limit(normalized_question)
    intent_type = "list"
    business_operation = "browse"
    requested_metrics: list[str] = []
    requested_dimensions: list[str] = []
    requested_filters = _extract_requested_filters(body)
    requested_sort: dict[str, Any] = {}
    source_scope = _extract_source_scope(body)

    sort_match = _SORTED_BY_RE.search(normalized_question)
    requested_sort = _extract_requested_sort(normalized_question)

    body_without_rank = re.sub(r"\b(?:top|bottom|lowest|first|limit)\s+\d+\b", "", body, flags=re.IGNORECASE).strip()
    body_without_latest = re.sub(r"\b(?:latest|recent|newest|oldest)\b", "", body_without_rank, flags=re.IGNORECASE).strip()
    body_without_sort = re.sub(r"\b(?:sort(?:ed)?|order(?:ed)?)\s+by\s+.+$", "", body_without_latest, flags=re.IGNORECASE).strip()
    body_without_filters = _remove_filter_clauses(body_without_sort)
    body_without_scope = _remove_source_scope(body_without_filters)

    if _COUNT_RE.search(body):
        intent_type = "count"
        business_operation = "count"
        aggregate_function = "count"
        count_target = _cleanup_phrase(_COUNT_RE.sub("", body).strip())
        count_target = re.sub(r"^of\s+", "", count_target, flags=re.IGNORECASE).strip()
        requested_dimensions = [count_target] if count_target else []
    elif _COMPARE_RE.search(normalized_question):
        intent_type = "comparison"
        business_operation = "compare"
    elif _TOP_RE.search(normalized_question) or _BOTTOM_RE.search(normalized_question) or _FIRST_RE.search(normalized_question):
        intent_type = "ranking"
        business_operation = "rank"
    elif aggregate_function:
        intent_type = "aggregate"
        business_operation = "summarize"
    elif sort_match or _LATEST_RE.search(normalized_question):
        intent_type = "sorted_list"
        business_operation = "sort"
    elif requested_filters:
        intent_type = "filter"

    if limit is None:
        top_match = _TOP_RE.search(normalized_question) or _BOTTOM_RE.search(normalized_question) or _FIRST_RE.search(normalized_question) or _LIMIT_RE.search(normalized_question)
        if top_match:
            limit = int(top_match.group(1))

    by_parts = _extract_grouping_parts(body_without_scope)
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
            metric_phrase = _metric_phrase_from_segment(left, aggregate_function)
            if metric_phrase:
                requested_metrics = [metric_phrase]
            if right:
                requested_dimensions = [right]
        if intent_type in {"list", "aggregate", "filter"}:
            intent_type = "grouped_summary"
            business_operation = "summarize"
        if intent_type == "ranking":
            business_operation = "rank"
    elif not requested_dimensions:
        if intent_type == "aggregate":
            metric_phrase = _metric_phrase_from_segment(body_without_scope, aggregate_function)
            if metric_phrase:
                requested_metrics = [metric_phrase]
        primary_phrase = _cleanup_phrase(body_without_scope)
        if primary_phrase:
            if intent_type in {"list", "count", "sorted_list"}:
                requested_dimensions = [primary_phrase]
            elif not requested_metrics and primary_phrase:
                requested_metrics = [primary_phrase]

    if (_TOP_RE.search(normalized_question) or _FIRST_RE.search(normalized_question)) and not requested_sort:
        requested_sort = {"direction": "desc", "terms": requested_metrics[0] if requested_metrics else "ranking"}
    if _BOTTOM_RE.search(normalized_question) and not requested_sort:
        requested_sort = {"direction": "asc", "terms": requested_metrics[0] if requested_metrics else "ranking"}

    needs_grouping = bool(requested_dimensions and requested_metrics and _BY_RE.search(body_without_sort))
    needs_aggregation = intent_type in {"count", "aggregate", "grouped_summary", "ranking", "comparison"} or bool(
        requested_metrics and (aggregate_function or _BY_RE.search(body_without_sort))
    )
    needs_join = "likely" if needs_grouping and requested_metrics and requested_dimensions else False

    raw_business_terms = _extract_raw_business_terms(
        normalized_question,
        requested_metrics=requested_metrics,
        requested_dimensions=requested_dimensions,
        requested_filters=requested_filters,
        requested_sort=requested_sort,
        source_scope=source_scope,
    )

    user_goal = _build_user_goal(
        normalized_question,
        intent_type=intent_type,
        requested_metrics=requested_metrics,
        requested_dimensions=requested_dimensions,
        requested_filters=requested_filters,
        requested_sort=requested_sort,
        source_scope=source_scope,
        aggregate_function=aggregate_function,
    )

    confidence = 0.58
    if intent_type in {"count", "ranking", "aggregate"}:
        confidence = 0.72
    elif needs_grouping:
        confidence = 0.68
    elif requested_filters or requested_sort:
        confidence = 0.64
    if re.search(r"\b(?:something|anything|everything|stuff|things)\b", normalized_question):
        confidence = min(confidence, 0.42)
    elif intent_type == "list" and len(raw_business_terms) <= 1 and not source_scope and not requested_filters:
        confidence = min(confidence, 0.48)

    target_entity_phrase = _target_entity_phrase(
        intent_type=intent_type,
        requested_dimensions=requested_dimensions,
        source_scope=source_scope,
        body_without_scope=body_without_scope,
        question=normalized_question,
    )
    metric_phrase = requested_metrics[0] if requested_metrics else ""
    grouping_phrase = requested_dimensions[0] if intent_type in {"grouped_summary", "ranking"} and requested_dimensions else ""
    ranking_phrase = ""
    if intent_type == "ranking":
        ranking_phrase = requested_metrics[0] if requested_metrics else (requested_sort.get("terms") or "")
    limit_phrase = _extract_limit_phrase(normalized_question)

    return _normalize_simple_target_entity_usage({
        "user_goal": user_goal,
        "intent_type": intent_type,
        "business_operation": business_operation,
        "requested_metrics": requested_metrics,
        "requested_dimensions": requested_dimensions,
        "requested_filters": requested_filters,
        "requested_sort": requested_sort,
        "aggregate_function": aggregate_function,
        "source_scope": source_scope,
        "limit": limit,
        "needs_grouping": needs_grouping,
        "needs_aggregation": needs_aggregation,
        "needs_join": needs_join,
        "raw_business_terms": raw_business_terms,
        "confidence": confidence,
        "unsafe": False,
        "unsafe_operation": "",
        "target_entity_phrase": target_entity_phrase,
        "metric_phrase": metric_phrase,
        "metric_is_generic": _metric_is_generic_phrase(metric_phrase),
        "source_scope_phrase": source_scope[0] if source_scope else "",
        "filter_phrase": requested_filters[0] if requested_filters else "",
        "grouping_phrase": grouping_phrase,
        "ranking_phrase": ranking_phrase,
        "limit_phrase": limit_phrase,
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
        elif _TOP_RE.search(question) or _BOTTOM_RE.search(question) or _FIRST_RE.search(question):
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
    source_scope: list[str],
    aggregate_function: str | None,
) -> str:
    if intent_type == "count" and requested_dimensions:
        return f"count {requested_dimensions[0]}"
    if intent_type == "ranking" and requested_dimensions and requested_metrics:
        return f"rank {requested_dimensions[0]} by {requested_metrics[0]}"
    if intent_type == "aggregate" and requested_metrics:
        goal = f"show {aggregate_function or 'aggregate'} {requested_metrics[0]}".replace("aggregate ", "")
        if source_scope:
            goal = f"{goal} from {source_scope[0]}"
        if requested_filters:
            goal = f"{goal} filtered by {requested_filters[0]}"
        return goal
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
    source_scope: list[str],
) -> list[str]:
    collected = _merge_unique(
        requested_metrics,
        requested_dimensions,
        requested_filters,
        source_scope,
        [requested_sort.get("terms")] if requested_sort.get("terms") else [],
    )
    if collected:
        token_terms = []
        for token in re.split(r"[^a-z0-9_]+", question.lower()):
            cleaned = token.strip()
            if not cleaned or cleaned.isdigit() or _STOPWORD_RE.match(cleaned):
                continue
            token_terms.append(cleaned)
        return _merge_unique(collected, token_terms)

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


def _detect_unsafe_operation(question: str) -> str:
    match = _UNSAFE_RE.search(question)
    return str(match.group(1) or "").strip().lower() if match else ""


def _detect_aggregate_function(question: str) -> str | None:
    normalized = str(question or "")
    if _AGGREGATE_AVG_RE.search(normalized):
        return "avg"
    if _AGGREGATE_SUM_RE.search(normalized):
        return "sum"
    if _AGGREGATE_MAX_RE.search(normalized):
        return "max"
    if _AGGREGATE_MIN_RE.search(normalized):
        return "min"
    return None


def _metric_is_generic_phrase(metric_phrase: str) -> bool:
    tokens = [token for token in re.split(r"[^a-z0-9_]+", _clean_scalar(metric_phrase).lower()) if token]
    if not tokens:
        return False
    return all(token in _GENERIC_METRIC_TERMS for token in tokens)


def _extract_requested_sort(question: str) -> dict[str, str]:
    match = _SORTED_BY_RE.search(question)
    if match:
        terms = _clean_scalar(match.group(1))
        direction = "asc"
        direction_match = re.search(r"\s+(asc|ascending|desc|descending)\s*$", terms, re.IGNORECASE)
        if direction_match:
            direction = "desc" if direction_match.group(1).lower().startswith("desc") else "asc"
            terms = terms[: direction_match.start()].strip()
        return {"direction": direction, "terms": terms} if terms else {}
    if re.search(r"\b(?:latest|recent|newest)\b", question, re.IGNORECASE):
        return {"direction": "desc", "terms": "latest"}
    if re.search(r"\boldest\b", question, re.IGNORECASE):
        return {"direction": "asc", "terms": "oldest"}
    return {}


def _extract_limit_phrase(question: str) -> str:
    for pattern in (_TOP_RE, _BOTTOM_RE, _FIRST_RE, _LIMIT_RE):
        match = pattern.search(question)
        if match:
            return _clean_scalar(match.group(0))
    return ""


def _target_entity_phrase(
    *,
    intent_type: str,
    requested_dimensions: list[str],
    source_scope: list[str],
    body_without_scope: str,
    question: str,
) -> str:
    if intent_type in {"list", "count", "sorted_list"} and requested_dimensions:
        return requested_dimensions[0]
    if source_scope:
        return source_scope[0]
    if intent_type == "aggregate":
        body_without_metric = re.sub(
            r"\b(?:total|sum|average|avg|maximum|max|highest|minimum|min|lowest)\b",
            "",
            body_without_scope,
            flags=re.IGNORECASE,
        )
        return _cleanup_phrase(body_without_metric)
    return _cleanup_phrase(body_without_scope) or _cleanup_phrase(question)


def _extract_requested_filters(question: str) -> list[str]:
    filter_text = _extract_filter_text(question)
    if filter_text:
        return [phrase for phrase, _ in _split_filter_phrases(filter_text)]

    filters: list[str] = []
    between_match = _BETWEEN_RE.search(question)
    if between_match:
        left = _cleanup_phrase(between_match.group(1))
        right = _cleanup_phrase(between_match.group(2))
        if left and right:
            filters.append(f"between {left} and {right}")

    for pattern, label in (
        (_BEFORE_RE, "before"),
        (_AFTER_RE, "after"),
        (_GREATER_THAN_RE, "greater than"),
        (_LESS_THAN_RE, "less than"),
    ):
        match = pattern.search(question)
        if match:
            value = _cleanup_phrase(match.group(1))
            if value:
                filters.append(f"{label} {value}")

    return _merge_unique(filters)


def _extract_filter_text(question: str) -> str:
    for pattern in (_WHERE_RE, _FILTER_RE):
        match = pattern.search(question)
        if match:
            return _cleanup_phrase(match.group(1))
    return ""


def _split_filter_phrases(filter_text: str) -> list[tuple[str, str | None]]:
    protected = re.sub(
        r"(\bbetween\s+\S+)\s+and\s+(\S+)",
        lambda match: f"{match.group(1)} __between_and__ {match.group(2)}",
        str(filter_text or ""),
        flags=re.IGNORECASE,
    )
    parts = re.split(r"\s+(and|or)\s+", protected, flags=re.IGNORECASE)
    results: list[tuple[str, str | None]] = []
    conjunction: str | None = None
    for index, part in enumerate(parts):
        if index % 2 == 1:
            conjunction = part.lower()
            continue
        phrase = _cleanup_phrase(part.replace("__between_and__", "and"))
        if phrase:
            results.append((phrase, conjunction))
        conjunction = None
    return results


def _extract_structured_filters(question: str) -> list[dict[str, Any]]:
    filter_text = _extract_filter_text(question)
    phrases = _split_filter_phrases(filter_text) if filter_text else [
        (phrase, None) for phrase in _extract_requested_filters(question)
    ]
    structured: list[dict[str, Any]] = []
    patterns = (
        (r"^(.+?)\s+between\s+(.+?)\s+and\s+(.+)$", "between"),
        (r"^(.+?)\s+(?:is\s+not|!=|<>)\s+(.+)$", "neq"),
        (r"^(.+?)\s+(?:greater\s+than\s+or\s+equal\s+to|at\s+least|>=)\s+(.+)$", "gte"),
        (r"^(.+?)\s+(?:less\s+than\s+or\s+equal\s+to|at\s+most|<=)\s+(.+)$", "lte"),
        (r"^(.+?)\s+(?:greater\s+than|>)\s+(.+)$", "gt"),
        (r"^(.+?)\s+(?:less\s+than|<)\s+(.+)$", "lt"),
        (r"^(.+?)\s+(?:equals?|is|=)\s+(.+)$", "eq"),
        (r"^(.+?)\s+before\s+(.+)$", "before"),
        (r"^(.+?)\s+after\s+(.+)$", "after"),
        (r"^(.+?)\s+contains\s+(.+)$", "contains"),
    )
    for phrase, conjunction in phrases:
        entry = {
            "raw_phrase": phrase,
            "field": "",
            "field_phrase": "",
            "operator": "unknown",
            "value": "",
            "value_phrase": "",
            "values": [],
            "conjunction": conjunction,
        }
        for pattern, operator in patterns:
            match = re.match(pattern, phrase, re.IGNORECASE)
            if not match:
                continue
            entry["field_phrase"] = _cleanup_phrase(match.group(1))
            entry["field"] = entry["field_phrase"]
            entry["operator"] = operator
            if operator == "between":
                entry["values"] = [_cleanup_phrase(match.group(2)), _cleanup_phrase(match.group(3))]
                entry["value_phrase"] = " and ".join(entry["values"])
                entry["value"] = list(entry["values"])
            else:
                entry["value_phrase"] = _cleanup_phrase(match.group(2))
                entry["values"] = [entry["value_phrase"]] if entry["value_phrase"] else []
                entry["value"] = entry["value_phrase"]
            break
        structured.append(entry)
    return structured


def _source_scope_match(question: str) -> Optional[re.Match[str]]:
    from_match = _FROM_RE.search(question)
    if from_match:
        return from_match
    for pattern in (_IN_RE, _FOR_RE):
        match = pattern.search(question)
        if not match:
            continue
        prefix = question[: match.start()].strip()
        body = _strip_leading_action(prefix)
        if _COUNT_RE.search(body) or _detect_aggregate_function(prefix) or _TOP_RE.search(prefix) or _BOTTOM_RE.search(prefix):
            return match
    return None


def _scope_parts(question: str) -> Optional[tuple[re.Match[str], str, Optional[re.Match[str]]]]:
    match = _source_scope_match(question)
    if not match:
        return None
    tail = match.group(1)
    split_match = re.search(
        r"\s+(?:where|filter(?:ed)?(?:\s+by)?|before|after|between|greater\s+than|less\s+than|sort(?:ed)?|order(?:ed)?|by|per|each|group(?:ed)?\s+by)\b",
        tail,
        flags=re.IGNORECASE,
    )
    return match, tail, split_match


def _extract_source_scope(question: str) -> list[str]:
    parts = _scope_parts(question)
    if not parts:
        return []
    _, tail, split_match = parts
    scope = tail[: split_match.start()] if split_match else tail
    cleaned = _cleanup_phrase(scope)
    return [cleaned] if cleaned else []


def _remove_source_scope(question: str) -> str:
    parts = _scope_parts(question)
    if not parts:
        return question.strip()
    match, tail, split_match = parts
    prefix = question[: match.start()].strip()
    if not split_match:
        return prefix

    suffix = tail[split_match.start() :].strip()
    return _clean_scalar(f"{prefix} {suffix}")


def _remove_filter_clauses(question: str) -> str:
    stripped = question
    for pattern in (_WHERE_RE, _FILTER_RE):
        match = pattern.search(stripped)
        if not match:
            continue
        stripped = _clean_scalar(f"{stripped[: match.start()]} {stripped[match.end() :]}")
    for pattern in (_BETWEEN_RE, _BEFORE_RE, _AFTER_RE, _GREATER_THAN_RE, _LESS_THAN_RE):
        match = pattern.search(stripped)
        if match:
            stripped = stripped[: match.start()].strip()
    return stripped


def _extract_grouping_parts(question: str) -> Optional[tuple[str, str]]:
    group_match = _GROUP_BY_RE.search(question)
    if group_match:
        left = question[: group_match.start()].strip()
        right = _cleanup_phrase(group_match.group(1))
        if left and right:
            return left, right
    for pattern in (_BY_RE, _PER_RE):
        parts = _split_once(question, pattern)
        if parts:
            return parts
    each_match = re.search(r"\s+each\s+(.+)$", question, re.IGNORECASE)
    if each_match:
        left = question[: each_match.start()].strip()
        right = _cleanup_phrase(each_match.group(1))
        if left and right:
            return left, right
    return None


def _metric_phrase_from_segment(segment: str, aggregate_function: str | None) -> str:
    metric = _cleanup_phrase(segment)
    if not metric:
        return ""
    if aggregate_function:
        metric = re.sub(
            r"^(?:total|sum|average|avg|maximum|max|highest|minimum|min|lowest)\s+",
            "",
            metric,
            flags=re.IGNORECASE,
        ).strip()
    return _cleanup_phrase(metric)


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
