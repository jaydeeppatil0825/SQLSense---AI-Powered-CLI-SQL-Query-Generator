"""
core/intent_builder.py
======================
Schema-agnostic intent builder for the query pipeline.

This module understands the shape of a user question without deciding
runtime tables, columns, formulas, or business mappings.
"""

from __future__ import annotations

import re
from calendar import monthrange
from datetime import date, timedelta
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

_DISPLAY_VERBS = (
    "show",
    "list",
    "display",
    "get",
    "fetch",
    "give",
    "provide",
    "return",
    "view",
    # Existing accepted wording.
    "see",
    "find",
    "tell",
)
_RELATIONSHIP_CONNECTORS = (
    "together with",
    "associated with",
    "belonging to",
    "connected to",
    "combined with",
    "along with",
    "and their",
    "linked to",
    "related to",
    "including",
    "with",
    "plus",
)
_DESCRIPTIVE_FILLERS = (
    "information",
    "attributes",
    "attribute",
    "overview",
    "details",
    "detail",
    "records",
    "record",
    "profile",
    "fields",
    "field",
    "data",
    "info",
)


def _phrase_group_pattern(phrases: tuple[str, ...]) -> str:
    return "|".join(re.escape(phrase).replace(r"\ ", r"\s+") for phrase in phrases)


_LEADING_ACTION_RE = re.compile(
    rf"^\s*(?:{_phrase_group_pattern(_DISPLAY_VERBS)})(?:\s+me)?\b\s*",
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
_SORTED_BY_RE = re.compile(
    r"\b(?:sort(?:ed)?|order(?:ed)?)\s+by\s+(.+?)(?=\s+limit\b|$)",
    re.IGNORECASE,
)
_RANKING_BY_RE = re.compile(
    r"^\s*(top|highest|largest|maximum|bottom|lowest|smallest|minimum)"
    r"(?:\s+(\d+))?\s+(.+?)\s+by\s+(.+?)"
    r"(?=\s+(?:where|having|group(?:ed)?\s+by|sort(?:ed)?\s+by|order(?:ed)?\s+by|limit|from)\b|$)",
    re.IGNORECASE,
)
_BY_RE = re.compile(r"\s+by\s+", re.IGNORECASE)
_PER_RE = re.compile(r"\s+per\s+", re.IGNORECASE)
_WISE_RE = re.compile(r"\b[a-z0-9_ ]+\s+wise\b", re.IGNORECASE)
_FROM_RE = re.compile(r"\s+from\s+(.+)$", re.IGNORECASE)
_IN_RE = re.compile(r"\s+in\s+(.+)$", re.IGNORECASE)
_FOR_RE = re.compile(r"\s+for\s+(.+)$", re.IGNORECASE)
_WHERE_RE = re.compile(
    r"\s+where\s+(.+?)(?=\s+(?:having|group(?:ed)?\s+by|sort(?:ed)?|order(?:ed)?|limit\s+\d+|from)\b|$)",
    re.IGNORECASE,
)
_FILTER_RE = re.compile(
    r"\s+filter(?:ed)?(?:\s+by)?\s+(.+?)(?=\s+(?:having|group(?:ed)?\s+by|sort(?:ed)?|order(?:ed)?|limit\s+\d+|from)\b|$)",
    re.IGNORECASE,
)
_GROUP_BY_RE = re.compile(
    r"\s+group(?:ed)?\s+by\s+(.+?)(?=\s+(?:having|sort(?:ed)?|order(?:ed)?|limit|from)\b|$)",
    re.IGNORECASE,
)
_HAVING_RE = re.compile(
    r"\s+having\s+(.+?)(?=\s+(?:from|sort(?:ed)?|order(?:ed)?|limit\s+\d+)\b|$)",
    re.IGNORECASE,
)
_WITH_RE = re.compile(
    r"\s+with\s+(.+?)(?=\s+(?:from|where|having|group(?:ed)?\s+by|sort(?:ed)?\s+by|order(?:ed)?\s+by|limit\s+\d+)\b|$)",
    re.IGNORECASE,
)
_JOIN_LOOKUP_CONNECTOR_RE = re.compile(
    rf"\s+(?:{_phrase_group_pattern(_RELATIONSHIP_CONNECTORS)})\s+(.+)$",
    re.IGNORECASE,
)
_JOIN_LOOKUP_DESCRIPTIVE_RE = re.compile(
    rf"\s+(?:{_phrase_group_pattern(_DESCRIPTIVE_FILLERS)})$",
    re.IGNORECASE,
)
_INTERVAL_BOUNDARY = r"(?=\s+(?:by|per|each|group(?:ed)?\s+by|sorted|ordered|where|with|for|from|limit\s+\d+)\b|$)"
_BETWEEN_RE = re.compile(r"\bbetween\s+(.+?)\s+and\s+(.+?)" + _INTERVAL_BOUNDARY, re.IGNORECASE)
_BEFORE_RE = re.compile(r"\bbefore\s+(.+?)" + _INTERVAL_BOUNDARY, re.IGNORECASE)
_AFTER_RE = re.compile(r"\bafter\s+(.+?)" + _INTERVAL_BOUNDARY, re.IGNORECASE)
_ON_DATE_RE = re.compile(r"\bon\s+(\d{4}-\d{2}-\d{2})\b", re.IGNORECASE)
_IN_MONTH_YEAR_RE = re.compile(r"\bin\s+([a-z]+)\s+(20\d{2})\b", re.IGNORECASE)
_IN_YEAR_RE = re.compile(r"\bin\s+(20\d{2})\b", re.IGNORECASE)
_IN_MONTH_RE = re.compile(r"\bin\s+([a-z]+)\b", re.IGNORECASE)
_RELATIVE_DAYS_RE = re.compile(r"\blast\s+(\d+)\s+days?\b", re.IGNORECASE)
_GREATER_THAN_RE = re.compile(r"\bgreater\s+than\s+(.+?)(?=\s+(?:by|per|each|group(?:ed)?\s+by|sorted|ordered|$))", re.IGNORECASE)
_LESS_THAN_RE = re.compile(r"\bless\s+than\s+(.+?)(?=\s+(?:by|per|each|group(?:ed)?\s+by|sorted|ordered|$))", re.IGNORECASE)
_COMPARE_RE = re.compile(r"\b(?:vs|versus|compare|comparison)\b", re.IGNORECASE)
_AGGREGATE_SUM_RE = re.compile(r"\b(?:total|sum)\b", re.IGNORECASE)
_AGGREGATE_AVG_RE = re.compile(r"\b(?:average|avg)\b", re.IGNORECASE)
_AGGREGATE_MAX_RE = re.compile(r"\b(?:maximum|max|highest)\b", re.IGNORECASE)
_AGGREGATE_MIN_RE = re.compile(r"\b(?:minimum|min|lowest)\b", re.IGNORECASE)
_UNSAFE_RE = re.compile(r"\b(delete|update|insert|drop|alter|truncate)\b", re.IGNORECASE)
_STOPWORD_RE = re.compile(
    rf"^(?:{_phrase_group_pattern(_DISPLAY_VERBS)}|me|all|the|a|an|of|for|to|with|by|from|in|where|per|each|group|filter)$",
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
_IMPLICIT_SUM_METRIC_NOUNS = {"sale", "sales", "revenue", "revenues"}
_AGGREGATE_KEYWORD_MAP = {
    "total": "sum",
    "sum": "sum",
    "average": "avg",
    "minimum": "min",
    "maximum": "max",
    "count": "count",
}
_RANKING_KEYWORD_MAP = {
    "top": "desc",
    "highest": "desc",
    "bottom": "asc",
    "lowest": "asc",
    "first": "asc",
}
_OPERATOR_KEYWORD_MAP = {
    "greater than": "gt",
    "more than": "gt",
    "above": "gt",
    "over": "gt",
    "less than": "lt",
    "below": "lt",
    "under": "lt",
    "at least": "gte",
    "at most": "lte",
    "between": "between",
    "contains": "contains",
    "equal": "eq",
    "equals": "eq",
    "not": "negation",
    "null": "null_check",
}
_GROUPING_MARKER_MAP = {
    "group by": "group_by",
    "by": "by",
    "per": "per",
}
_FILTER_MARKER_MAP = {
    "where": "where",
    "for": "for",
    "with status": "with_status",
    "is": "is",
}
_JOIN_DETAIL_MARKER_MAP = {
    "with": "with",
    "details": "details",
}
_MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}


def build_intent(question: str, ai_backend: str = "local", today: date | None = None) -> Dict[str, Any]:
    """
    Return structured, schema-agnostic intent for the question.

    AI is intentionally not used here.
    Runtime intent building must remain deterministic.
    The ai_backend parameter is kept temporarily for backward compatibility.
    """
    normalized_question, _ = normalize_question(question)
    intent = _build_fallback_intent(normalized_question)
    intent = _normalize_simple_target_entity_usage(intent, normalized_question)
    return _apply_intent_contract(intent, normalized_question, today=today)


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


def _apply_intent_contract(intent: Dict[str, Any], question: str, *, today: date | None = None) -> Dict[str, Any]:
    """Attach additive, versioned diagnostics without changing legacy fields."""
    normalized = dict(intent or {})
    structured_filters = _extract_structured_filters(question, today=today)
    structured_intervals = [
        dict(entry) for entry in structured_filters
        if entry.get("filter_kind") == "date_interval"
    ]
    interval_raw_phrases = [
        str(entry.get("raw_phrase") or "").strip()
        for entry in structured_intervals
        if str(entry.get("raw_phrase") or "").strip()
    ]
    if interval_raw_phrases:
        _scrub_interval_role_phrases(normalized, question, interval_raw_phrases)
    structured_having = _extract_structured_having(question)
    keyword_markers = _extract_keyword_markers(question)
    intent_type = str(normalized.get("intent_type") or "unknown").strip().lower()
    confidence_reasons = ["deterministic_pattern_match"]
    missing_phrases: list[str] = []
    ambiguous_phrases: list[str] = []
    unsupported_constructs: list[str] = []
    ranking_diagnostics = dict(normalized.get("ranking_diagnostics") or {})
    join_lookup_request = dict(normalized.get("join_lookup_request") or {})
    if normalized.get("requested_sort") and not ranking_diagnostics.get("requested"):
        ranking_diagnostics = {
            "requested": True,
            "mode_hint": (
                "grouped_aggregate"
                if normalized.get("needs_grouping") and normalized.get("aggregate_function")
                else "ordered_list"
            ),
            "direction_source": "explicit_order_by",
            "limit_source": "explicit" if normalized.get("limit") is not None else "not_requested",
            "issues": [],
        }

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
    if structured_intervals:
        confidence_reasons.append("explicit_interval_filter")
    if structured_having:
        confidence_reasons.append("explicit_having_clause")
    if normalized.get("grouping_phrase"):
        confidence_reasons.append("explicit_grouping_phrase")
    if normalized.get("source_scope"):
        confidence_reasons.append("explicit_source_scope")

    if not normalized.get("unsafe"):
        if (
            intent_type in {"list", "count", "sorted_list"}
            and not normalized.get("target_entity_phrase")
            and not join_lookup_request.get("requested")
        ):
            missing_phrases.append("target_entity_phrase")
        if (
            intent_type in {"aggregate", "ranking", "grouped_summary"}
            and str(normalized.get("aggregate_function") or "").strip().lower() != "count"
            and not normalized.get("metric_phrase")
        ):
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
    if any(entry.get("operator") == "unknown" for entry in structured_having):
        unsupported_constructs.append("unparsed_having_expression")
    if any(not entry.get("aggregate_function") for entry in structured_having):
        unsupported_constructs.append("having_aggregate_missing")
    if normalized.get("having_metric_conflict"):
        unsupported_constructs.append("multi_metric_having_not_supported")
    if normalized.get("having_aggregate_conflict"):
        unsupported_constructs.append("multi_aggregate_having_not_supported")
    if len([part for part in str(question or "").split(";") if part.strip()]) > 1:
        unsupported_constructs.append("multiple_statements")

    normalized["intent_contract_version"] = INTENT_CONTRACT_VERSION
    normalized["keyword_markers"] = keyword_markers
    normalized["structured_filters"] = structured_filters
    normalized["structured_intervals"] = structured_intervals
    normalized["structured_having"] = structured_having
    normalized["requested_having"] = [
        str(entry.get("raw_phrase") or "").strip()
        for entry in structured_having
        if str(entry.get("raw_phrase") or "").strip()
    ]
    normalized["having_phrase"] = normalized["requested_having"][0] if normalized["requested_having"] else ""
    normalized["confidence_reasons"] = _merge_unique(confidence_reasons)
    normalized["missing_phrases"] = _merge_unique(missing_phrases)
    normalized["unsupported_constructs"] = _merge_unique(unsupported_constructs)
    normalized["parse_diagnostics"] = {
        "missing_phrases": list(normalized["missing_phrases"]),
        "ambiguous_phrases": _merge_unique(ambiguous_phrases),
        "unsupported_constructs": list(normalized["unsupported_constructs"]),
        "has_issues": bool(missing_phrases or ambiguous_phrases or unsupported_constructs),
    }
    normalized["ranking_diagnostics"] = {
        "requested": bool(ranking_diagnostics.get("requested")),
        "mode_hint": ranking_diagnostics.get("mode_hint"),
        "direction_source": ranking_diagnostics.get("direction_source"),
        "limit_source": ranking_diagnostics.get("limit_source") or "not_requested",
        "issues": _merge_unique(ranking_diagnostics.get("issues") or []),
    }
    normalized["join_lookup_request"] = {
        "requested": bool(join_lookup_request.get("requested")),
        "base_entity_phrase": _clean_scalar(join_lookup_request.get("base_entity_phrase")),
        "related_request_phrase": _clean_scalar(join_lookup_request.get("related_request_phrase")),
        "requested_output_fields": _clean_list(join_lookup_request.get("requested_output_fields")),
        "projection_mode": _clean_scalar(join_lookup_request.get("projection_mode")),
    }
    normalized["requested_output_fields"] = list(
        normalized["join_lookup_request"]["requested_output_fields"]
    )
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
            "join_lookup_request": _empty_join_lookup_request(),
            "requested_output_fields": [],
            "source": "fallback",
        }

    body = _strip_leading_action(normalized_question)
    aggregate_function = None
    limit = extract_requested_limit(normalized_question)
    intent_type = "list"
    business_operation = "browse"
    requested_metrics: list[str] = []
    requested_dimensions: list[str] = []
    structured_having = _extract_structured_having(body)
    requested_filters = _extract_requested_filters(body)
    requested_sort: dict[str, Any] = {}
    source_scope = _extract_source_scope(body)
    having_metric_conflict = False
    having_aggregate_conflict = False
    ranking_request = _extract_ranking_request(normalized_question)
    ranking_diagnostics = {
        "requested": bool(ranking_request),
        "mode_hint": ranking_request.get("mode_hint") if ranking_request else None,
        "direction_source": ranking_request.get("direction_source") if ranking_request else None,
        "limit_source": ranking_request.get("limit_source") if ranking_request else "not_requested",
        "issues": [],
    }

    sort_match = _SORTED_BY_RE.search(normalized_question)
    requested_sort = _extract_requested_sort(normalized_question)

    body_without_rank = re.sub(r"\b(?:top|bottom|lowest|first|limit)\s+\d+\b", "", body, flags=re.IGNORECASE).strip()
    body_without_latest = re.sub(r"\b(?:latest|recent|newest|oldest)\b", "", body_without_rank, flags=re.IGNORECASE).strip()
    body_without_sort = re.sub(r"\b(?:sort(?:ed)?|order(?:ed)?)\s+by\s+.+$", "", body_without_latest, flags=re.IGNORECASE).strip()
    body_without_filters = _remove_filter_clauses(body_without_sort)
    body_without_scope = _remove_source_scope(body_without_filters)
    aggregate_function = _detect_aggregate_function(body_without_filters)
    join_lookup_request = _extract_join_lookup_request(body_without_scope)
    if (
        aggregate_function
        and join_lookup_request.get("projection_mode") == "explicit_fields_only"
        and not re.match(
            r"^\s*(?:total|sum|average|avg|mean|count|maximum|max|minimum|min)\b",
            body_without_scope,
            re.IGNORECASE,
        )
    ):
        aggregate_function = None

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
    elif ranking_request or _TOP_RE.search(normalized_question) or _BOTTOM_RE.search(normalized_question) or _FIRST_RE.search(normalized_question):
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

    if (
        join_lookup_request.get("projection_mode") == "explicit_fields_only"
        and intent_type != "list"
    ):
        join_lookup_request = _empty_join_lookup_request()
    elif (
        join_lookup_request.get("projection_mode") == "explicit_fields_only"
        and source_scope
    ):
        join_lookup_request["base_entity_phrase"] = source_scope[0]

    if ranking_request:
        limit = int(ranking_request["limit"])
        aggregate_function = ranking_request.get("aggregate_function")
        requested_sort = {
            "direction": str(ranking_request["direction"]),
            "terms": str(ranking_request["target_phrase"]),
        }
    elif limit is None:
        top_match = _TOP_RE.search(normalized_question) or _BOTTOM_RE.search(normalized_question) or _FIRST_RE.search(normalized_question) or _LIMIT_RE.search(normalized_question)
        if top_match:
            limit = int(top_match.group(1))

    by_parts = _extract_grouping_parts(body_without_scope)
    count_entity_phrase = ""
    if ranking_request:
        requested_metrics = [str(ranking_request["metric_phrase"])]
        requested_dimensions = (
            [str(ranking_request["entity_phrase"])]
            if ranking_request.get("mode_hint") == "grouped_aggregate"
            else []
        )
    elif intent_type == "sorted_list":
        primary_phrase = _cleanup_phrase(body_without_scope)
        if primary_phrase:
            requested_dimensions = [primary_phrase]
    elif by_parts:
        left, right = by_parts
        left = _cleanup_phrase(left)
        right = _cleanup_phrase(right)
        if intent_type == "count":
            count_entity_phrase = _cleanup_phrase(_COUNT_RE.sub("", left).strip())
            count_entity_phrase = re.sub(r"^of\s+", "", count_entity_phrase, flags=re.IGNORECASE).strip()
        if intent_type == "ranking":
            if left:
                requested_dimensions = [left]
            if right:
                requested_metrics = [right]
        else:
            if intent_type in {"list", "filter"} and aggregate_function is None:
                aggregate_function = _detect_implicit_grouped_sum_metric(left)
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
            elif intent_type != "filter" and not requested_metrics and primary_phrase:
                requested_metrics = [primary_phrase]

    output_aggregate_function = _aggregate_function_before_having(body)
    output_metric_phrase = requested_metrics[0] if output_aggregate_function and requested_metrics else ""
    if structured_having:
        having = structured_having[0]
        having_function = str(having.get("aggregate_function") or "").strip().lower()
        having_metric = str(having.get("metric_phrase") or "").strip()
        if having_function:
            if output_aggregate_function and output_aggregate_function != having_function:
                having_aggregate_conflict = True
            if (
                output_metric_phrase
                and having_metric
                and _cleanup_phrase(output_metric_phrase).lower() != _cleanup_phrase(having_metric).lower()
            ):
                having_metric_conflict = True
            aggregate_function = having_function
            intent_type = "grouped_summary"
            business_operation = "summarize"
            if having_function == "count":
                requested_metrics = []
            elif having_metric:
                requested_metrics = [having_metric]
            if not _has_explicit_grouping_marker(body) or not requested_dimensions:
                implicit_dimension = _extract_implicit_having_dimension(body)
                if implicit_dimension:
                    requested_dimensions = [implicit_dimension]

    if (_TOP_RE.search(normalized_question) or _FIRST_RE.search(normalized_question)) and not requested_sort:
        requested_sort = {"direction": "desc", "terms": requested_metrics[0] if requested_metrics else "ranking"}
    if _BOTTOM_RE.search(normalized_question) and not requested_sort:
        requested_sort = {"direction": "asc", "terms": requested_metrics[0] if requested_metrics else "ranking"}

    needs_grouping = bool(
        ranking_request.get("mode_hint") == "grouped_aggregate"
        or (
            requested_dimensions
            and (
                (requested_metrics and _BY_RE.search(body_without_sort))
                or structured_having
                or (aggregate_function == "count" and _has_explicit_grouping_marker(body_without_sort))
            )
        )
    )
    needs_aggregation = bool(
        ranking_request.get("mode_hint") == "grouped_aggregate"
        or intent_type in {"count", "aggregate", "grouped_summary", "comparison"}
        or (requested_metrics and aggregate_function)
    )
    needs_join = "likely" if needs_grouping and requested_metrics and requested_dimensions else False
    if join_lookup_request["requested"]:
        needs_join = True

    raw_business_terms = _extract_raw_business_terms(
        normalized_question,
        requested_metrics=requested_metrics,
        requested_dimensions=requested_dimensions,
        requested_filters=requested_filters,
        requested_sort=requested_sort,
        source_scope=source_scope,
    )
    raw_business_terms = _merge_unique(
        raw_business_terms,
        join_lookup_request.get("base_entity_phrase"),
        join_lookup_request.get("related_request_phrase"),
        join_lookup_request.get("requested_output_fields") or [],
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
    if count_entity_phrase:
        target_entity_phrase = count_entity_phrase
    elif ranking_request:
        target_entity_phrase = str(ranking_request["entity_phrase"])
    elif join_lookup_request["requested"]:
        target_entity_phrase = str(join_lookup_request.get("base_entity_phrase") or "")
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
        "structured_having": structured_having,
        "requested_having": [
            str(entry.get("raw_phrase") or "").strip()
            for entry in structured_having
            if str(entry.get("raw_phrase") or "").strip()
        ],
        "having_phrase": str(structured_having[0].get("raw_phrase") or "") if structured_having else "",
        "having_metric_conflict": having_metric_conflict,
        "having_aggregate_conflict": having_aggregate_conflict,
        "grouping_phrase": grouping_phrase,
        "ranking_phrase": ranking_phrase,
        "limit_phrase": limit_phrase,
        "ranking_diagnostics": ranking_diagnostics,
        "join_lookup_request": join_lookup_request,
        "requested_output_fields": list(join_lookup_request.get("requested_output_fields") or []),
        "source": "fallback",
    }, normalized_question)


def _has_explicit_grouping_marker(question: str) -> bool:
    ranking = _extract_ranking_request(question)
    if ranking:
        return ranking.get("mode_hint") == "grouped_aggregate"
    without_order_by = _SORTED_BY_RE.sub("", str(question or ""))
    return bool(
        _GROUP_BY_RE.search(without_order_by)
        or _BY_RE.search(without_order_by)
        or _PER_RE.search(without_order_by)
        or _WISE_RE.search(without_order_by)
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


def _tokenize(value: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", str(value or "").lower().replace("_", " "))


def _strip_interval_phrases(value: str, raw_phrases: list[str]) -> str:
    cleaned = str(value or "")
    for raw in sorted((phrase for phrase in raw_phrases if phrase), key=len, reverse=True):
        cleaned = re.sub(re.escape(raw), " ", cleaned, flags=re.IGNORECASE)
        if raw.lower().startswith("in "):
            cleaned = re.sub(re.escape(raw[3:]), " ", cleaned, flags=re.IGNORECASE)
    cleaned = _cleanup_phrase(cleaned)
    cleaned = re.sub(r"\b(?:where|from|for|in|with)\s*$", "", cleaned, flags=re.IGNORECASE)
    return _cleanup_phrase(cleaned)


def _clean_interval_list(values: Any, raw_phrases: list[str]) -> list[str]:
    return [
        cleaned
        for cleaned in (_strip_interval_phrases(str(value), raw_phrases) for value in (values or []))
        if cleaned
    ]


def _scrub_interval_role_phrases(
    normalized: dict[str, Any],
    question: str,
    raw_phrases: list[str],
) -> None:
    """Keep date intervals out of generic metric/dimension/table/filter roles."""
    for key in ("requested_metrics", "requested_dimensions", "source_scope", "raw_business_terms"):
        normalized[key] = _merge_unique(_clean_interval_list(normalized.get(key) or [], raw_phrases))

    for key in ("metric_phrase", "grouping_phrase", "ranking_phrase", "source_scope_phrase", "filter_phrase"):
        if normalized.get(key):
            normalized[key] = _strip_interval_phrases(str(normalized.get(key) or ""), raw_phrases)

    requested_sort = dict(normalized.get("requested_sort") or {})
    if requested_sort.get("terms"):
        requested_sort["terms"] = _strip_interval_phrases(str(requested_sort.get("terms") or ""), raw_phrases)
        if not requested_sort["terms"]:
            requested_sort = {}
    normalized["requested_sort"] = requested_sort

    target = _strip_interval_phrases(str(normalized.get("target_entity_phrase") or ""), raw_phrases)
    if not target:
        stripped_question = _strip_interval_phrases(question, raw_phrases)
        target = _cleanup_phrase(_strip_leading_action(stripped_question))
        target = _remove_source_scope(target)
        target = re.sub(r"\s+(?:where|from|for|in|with)\s*$", "", target, flags=re.IGNORECASE)
        target = _cleanup_phrase(target)
    normalized["target_entity_phrase"] = target

    normalized["requested_filters"] = _merge_unique(
        _clean_interval_list(normalized.get("requested_filters") or [], raw_phrases),
        [
            str(entry.get("raw_phrase") or "").strip()
            for entry in (normalized.get("structured_filters") or [])
            if isinstance(entry, dict)
            and entry.get("filter_kind") != "date_interval"
            and str(entry.get("raw_phrase") or "").strip()
        ],
    )


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
    matches: list[tuple[int, str]] = []
    for aggregate_function, pattern in (
        ("avg", _AGGREGATE_AVG_RE),
        ("sum", _AGGREGATE_SUM_RE),
        ("max", _AGGREGATE_MAX_RE),
        ("min", _AGGREGATE_MIN_RE),
    ):
        match = pattern.search(normalized)
        if match:
            matches.append((match.start(), aggregate_function))
    return min(matches, default=(0, None), key=lambda item: item[0])[1]


def _detect_implicit_grouped_sum_metric(segment: str) -> str | None:
    tokens = [token for token in re.split(r"[^a-z0-9_]+", _cleanup_phrase(segment).lower()) if token]
    return "sum" if any(token in _IMPLICIT_SUM_METRIC_NOUNS for token in tokens) else None


def _detect_ranking_aggregate_function(target_phrase: str) -> str | None:
    """Require an explicit aggregate verb in a grouped ranking target."""
    match = re.match(
        r"^\s*(total|sum|average|avg|mean|count|maximum|max|minimum|min)\b",
        str(target_phrase or ""),
        re.IGNORECASE,
    )
    if not match and re.search(r"\bcount\s*$", str(target_phrase or ""), re.IGNORECASE):
        return "count"
    if not match:
        return None
    token = match.group(1).lower()
    if token == "total":
        return "sum"
    if token in {"average", "avg", "mean"}:
        return "avg"
    if token in {"maximum", "max"}:
        return "max"
    if token in {"minimum", "min"}:
        return "min"
    return token


def _metric_is_generic_phrase(metric_phrase: str) -> bool:
    tokens = [token for token in re.split(r"[^a-z0-9_]+", _clean_scalar(metric_phrase).lower()) if token]
    if not tokens:
        return False
    return len(tokens) == 1 and tokens[0] in _GENERIC_METRIC_TERMS


def _extract_ranking_request(question: str) -> dict[str, Any]:
    body = _strip_leading_action(question)
    match = _RANKING_BY_RE.search(body)
    if not match:
        return {}

    keyword = match.group(1).lower()
    explicit_limit = int(match.group(2)) if match.group(2) else None
    entity_phrase = _cleanup_phrase(match.group(3))
    target_phrase = _cleanup_phrase(match.group(4))
    direction = "asc" if keyword in {"bottom", "lowest", "smallest", "minimum"} else "desc"
    aggregate_function = _detect_ranking_aggregate_function(target_phrase)
    if (
        explicit_limit is None
        and keyword in {"highest", "largest", "maximum", "lowest", "smallest", "minimum"}
        and aggregate_function is None
    ):
        return {}
    metric_phrase = _metric_phrase_from_segment(target_phrase, aggregate_function)
    if aggregate_function == "sum" and re.match(r"^\s*total\b", target_phrase, re.IGNORECASE):
        metric_phrase = target_phrase
    grouped = bool(aggregate_function)
    return {
        "keyword": keyword,
        "entity_phrase": entity_phrase,
        "target_phrase": target_phrase,
        "metric_phrase": metric_phrase or target_phrase,
        "aggregate_function": aggregate_function,
        "mode_hint": "grouped_aggregate" if grouped else "row",
        "direction": direction,
        "direction_source": "ranking_keyword",
        "limit": explicit_limit if explicit_limit is not None else 50,
        "limit_source": "explicit" if explicit_limit is not None else "default_top_n",
    }


def _empty_join_lookup_request() -> dict[str, Any]:
    return {
        "requested": False,
        "base_entity_phrase": "",
        "related_request_phrase": "",
        "requested_output_fields": [],
        "projection_mode": "",
    }


def _extract_join_lookup_request(body: str) -> dict[str, Any]:
    """Parse schema-agnostic two-table lookup wording without resolving schema."""
    cleaned = _cleanup_phrase(body)
    if not cleaned:
        return _empty_join_lookup_request()

    with_match = _JOIN_LOOKUP_CONNECTOR_RE.search(cleaned)
    if with_match:
        base_phrase = _join_lookup_entity_phrase(cleaned[: with_match.start()])
        related_phrase = _cleanup_phrase(with_match.group(1))
        related_phrase = _cleanup_phrase(
            re.split(r"\s+(?:for|where)\s+", related_phrase, maxsplit=1, flags=re.IGNORECASE)[0]
        )
        if not base_phrase or not related_phrase:
            return _empty_join_lookup_request()
        if _with_phrase_is_row_filter(related_phrase):
            return _empty_join_lookup_request()
        aggregate_condition = bool(
            re.match(r"^(?:sum|total|average|avg|mean|count|minimum|min|maximum|max)\b", related_phrase, re.IGNORECASE)
            and re.search(
                r"\b(?:greater\s+than|more\s+than|less\s+than|at\s+least|at\s+most|above|below|over|under|equals?|=|>|<)\b",
                related_phrase,
                re.IGNORECASE,
            )
        )
        if aggregate_condition:
            return _empty_join_lookup_request()
        broad_related = bool(re.match(r"^their\s+", related_phrase, re.IGNORECASE) or _JOIN_LOOKUP_DESCRIPTIVE_RE.search(related_phrase))
        normalized_related = re.sub(r"^their\s+", "", related_phrase, flags=re.IGNORECASE)
        normalized_related = _join_lookup_entity_phrase(normalized_related)
        return {
            "requested": True,
            "base_entity_phrase": base_phrase,
            "related_request_phrase": normalized_related or related_phrase,
            "requested_output_fields": [] if broad_related else [related_phrase],
            "projection_mode": "broad_related" if broad_related else "base_plus_related_fields",
        }

    parts = _split_requested_output_fields(cleaned)
    if len(parts) >= 2:
        return {
            "requested": True,
            "base_entity_phrase": "",
            "related_request_phrase": "",
            "requested_output_fields": parts,
            "projection_mode": "explicit_fields_only",
        }
    return _empty_join_lookup_request()


def _split_requested_output_fields(text: str) -> list[str]:
    protected = re.sub(r",\s+and\s+", ", ", str(text or ""), flags=re.IGNORECASE)
    return [
        _cleanup_phrase(part)
        for part in re.split(r"\s*,\s*|\s+and\s+", protected, flags=re.IGNORECASE)
        if _cleanup_phrase(part)
    ]


def _join_lookup_entity_phrase(phrase: str) -> str:
    text = _cleanup_phrase(_JOIN_LOOKUP_DESCRIPTIVE_RE.sub("", str(phrase or "")))
    text = re.sub(r"^\s*can\s+you\s+", "", text, flags=re.IGNORECASE)
    text = _LEADING_ACTION_RE.sub("", text)
    while True:
        cleaned = re.sub(r"^\s*(?:me|all|the)\s+", "", text, flags=re.IGNORECASE)
        if cleaned == text:
            return _cleanup_phrase(text)
        text = cleaned


def _extract_requested_sort(question: str) -> dict[str, str]:
    ranking = _extract_ranking_request(question)
    if ranking:
        return {
            "direction": str(ranking["direction"]),
            "terms": str(ranking["target_phrase"]),
        }
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


def _keyword_entries(question: str, mapping: dict[str, str]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for keyword, normalized in mapping.items():
        pattern = r"\b" + re.escape(keyword).replace(r"\ ", r"\s+") + r"\b"
        for match in re.finditer(pattern, question, re.IGNORECASE):
            entries.append(
                {
                    "keyword": _clean_scalar(match.group(0)).lower(),
                    "normalized": normalized,
                    "start": match.start(),
                    "end": match.end(),
                }
            )
    entries.sort(key=lambda entry: (int(entry["start"]), -int(entry["end"])))
    deduped: list[dict[str, Any]] = []
    occupied: set[tuple[int, int, str]] = set()
    for entry in entries:
        key = (int(entry["start"]), int(entry["end"]), str(entry["normalized"]))
        if key in occupied:
            continue
        occupied.add(key)
        deduped.append(entry)
    return deduped


def _extract_keyword_markers(question: str) -> dict[str, list[dict[str, Any]]]:
    """Extract generic NLP grammar markers without assigning schema meaning."""
    return {
        "aggregate": _keyword_entries(question, _AGGREGATE_KEYWORD_MAP),
        "ranking": _keyword_entries(question, _RANKING_KEYWORD_MAP),
        "operator": _keyword_entries(question, _OPERATOR_KEYWORD_MAP),
        "grouping": _keyword_entries(question, _GROUPING_MARKER_MAP),
        "filter": _keyword_entries(question, _FILTER_MARKER_MAP),
        "join_detail": _keyword_entries(question, _JOIN_DETAIL_MARKER_MAP),
    }


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
    if _extract_structured_having(question):
        return []

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

    interval_filters = _extract_interval_filters(question)
    fielded_interval_present = any(
        str(entry.get("date_column_phrase") or "").strip()
        for entry in interval_filters
    )
    if fielded_interval_present:
        filters = [
            phrase for phrase in filters
            if not _is_value_only_interval_phrase(phrase)
        ]

    for entry in interval_filters:
        raw_phrase = str(entry.get("raw_phrase") or "").strip()
        if raw_phrase:
            filters.append(raw_phrase)

    return _merge_unique(filters)


def _extract_filter_text(question: str) -> str:
    for pattern in (_WHERE_RE, _FILTER_RE):
        match = pattern.search(question)
        if match:
            candidate = _cleanup_phrase(match.group(1))
            if _where_clause_is_having(question, match, candidate):
                continue
            return candidate
    with_match = _WITH_RE.search(question)
    if with_match:
        candidate = _cleanup_phrase(with_match.group(1))
        if _with_phrase_is_row_filter(candidate):
            return candidate
    return ""


def _where_clause_is_having(question: str, match: re.Match[str], candidate: str) -> bool:
    if not _parse_having_condition(candidate).get("aggregate_function"):
        return False
    if re.match(
        r"^\s*(?:sum|average|avg|mean|count|maximum|max|minimum|min)\b",
        candidate,
        re.IGNORECASE,
    ):
        return True
    prefix = _cleanup_phrase(question[: match.start()])
    return bool(
        _detect_aggregate_function(prefix)
        or _COUNT_RE.search(prefix)
        or _has_explicit_grouping_marker(prefix)
    )


def _with_phrase_is_row_filter(phrase: str) -> bool:
    if not phrase or _parse_having_condition(phrase).get("aggregate_function"):
        return False
    if re.search(
        r"\b(?:between|contains|equals?|is|not\s+equal|greater\s+than|more\s+than|less\s+than|at\s+least|at\s+most|above|below|over|under|null|=|!=|<>|>=|<=|>|<)\b",
        phrase,
        re.IGNORECASE,
    ):
        return True
    return bool(re.match(r"^\s*status\s+\S+", phrase, re.IGNORECASE))


def _split_filter_phrases(filter_text: str) -> list[tuple[str, str | None]]:
    protected = re.sub(
        r"(\bbetween\s+\S+)\s+and\s+(\S+)",
        lambda match: f"{match.group(1)} __between_and__ {match.group(2)}",
        str(filter_text or ""),
        flags=re.IGNORECASE,
    )
    protected = re.sub(
        r"\b((?:greater|less)\s+than)\s+or\s+(equal\s+to)\b",
        lambda match: f"{match.group(1)} __comparison_or__ {match.group(2)}",
        protected,
        flags=re.IGNORECASE,
    )
    parts = re.split(r"\s+(and|or)\s+", protected, flags=re.IGNORECASE)
    results: list[tuple[str, str | None]] = []
    conjunction: str | None = None
    for index, part in enumerate(parts):
        if index % 2 == 1:
            conjunction = part.lower()
            continue
        phrase = _cleanup_phrase(
            part.replace("__between_and__", "and").replace("__comparison_or__", "or")
        )
        if phrase:
            results.append((phrase, conjunction))
        conjunction = None
    return results


def _extract_structured_filters(question: str, *, today: date | None = None) -> list[dict[str, Any]]:
    filter_text = _extract_filter_text(question)
    interval_filters = _extract_interval_filters(question, today=today)
    interval_by_raw = {
        str(entry.get("raw_phrase") or "").strip().lower(): entry
        for entry in interval_filters
        if str(entry.get("raw_phrase") or "").strip()
    }
    phrases = _split_filter_phrases(filter_text) if filter_text else [
        (phrase, None) for phrase in _extract_requested_filters(question)
    ]
    structured: list[dict[str, Any]] = []
    patterns = (
        (r"^(.+?)\s+between\s+(.+?)\s+and\s+(.+)$", "between"),
        (r"^(.+?)\s+is\s+not\s+null$", "is_not_null"),
        (r"^(.+?)\s+is\s+null$", "is_null"),
        (r"^(.+?)\s+(?:is\s+not|not\s+equals?(?:\s+to)?|!=|<>)\s+(.+)$", "neq"),
        (r"^(.+?)\s+(?:is\s+)?(?:greater\s+than\s+or\s+equal\s+to|at\s+least|>=)\s+(.+)$", "gte"),
        (r"^(.+?)\s+(?:is\s+)?(?:less\s+than\s+or\s+equal\s+to|at\s+most|<=)\s+(.+)$", "lte"),
        (r"^(.+?)\s+(?:is\s+)?(?:greater\s+than|more\s+than|above|over|>)\s+(.+)$", "gt"),
        (r"^(.+?)\s+(?:is\s+)?(?:less\s+than|below|under|<)\s+(.+)$", "lt"),
        (r"^(.+?)\s+(?:is\s+)?before\s+(.+)$", "before"),
        (r"^(.+?)\s+(?:is\s+)?after\s+(.+)$", "after"),
        (r"^(.+?)\s+(?:is\s+on|on)\s+(.+)$", "eq"),
        (r"^(.+?)\s+(?:equals?|is|=)\s+(.+)$", "eq"),
        (r"^(.+?)\s+contains\s+(.+)$", "contains"),
        (r"^(status)\s+(.+)$", "eq"),
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
            if operator in {"is_null", "is_not_null"}:
                entry["values"] = []
                entry["value_phrase"] = ""
                entry["value"] = ""
            elif operator == "between":
                entry["values"] = [_cleanup_phrase(match.group(2)), _cleanup_phrase(match.group(3))]
                entry["value_phrase"] = " and ".join(entry["values"])
                entry["value"] = list(entry["values"])
            else:
                entry["value_phrase"] = _cleanup_phrase(match.group(2))
                entry["values"] = [entry["value_phrase"]] if entry["value_phrase"] else []
                entry["value"] = entry["value_phrase"]
            break
        interval_entry = interval_by_raw.get(str(entry.get("raw_phrase") or "").strip().lower())
        if entry.get("operator") == "unknown" and interval_entry:
            entry = {**dict(interval_entry), "conjunction": conjunction}
        else:
            _apply_interval_metadata(entry, today=today)
        structured.append(entry)
    for interval in interval_filters:
        signature = str(interval.get("raw_phrase") or "").strip().lower()
        if signature and not any(str(entry.get("raw_phrase") or "").strip().lower() == signature for entry in structured):
            structured.append(interval)
    return structured


def _extract_interval_filters(question: str, *, today: date | None = None) -> list[dict[str, Any]]:
    current = today or date.today()
    results: list[dict[str, Any]] = []

    def add(raw: str, operator: str, values: list[str], granularity: str, field_phrase: str = "") -> None:
        if not raw or not values:
            return
        value: Any = list(values) if operator == "between" else values[0]
        results.append(
            {
                "raw_phrase": _cleanup_phrase(raw),
                "field": field_phrase,
                "field_phrase": field_phrase,
                "date_column_phrase": field_phrase,
                "operator": operator,
                "value": value,
                "value_phrase": " and ".join(values),
                "values": list(values),
                "conjunction": None,
                "filter_kind": "date_interval",
                "interval_granularity": granularity,
            }
        )

    fielded_added = False
    for pattern, operator in (
        (r"\b([a-z0-9_ ]+?\s+date)\s+between\s+(\d{4}-\d{2}-\d{2})\s+and\s+(\d{4}-\d{2}-\d{2})\b", "between"),
        (r"\b([a-z0-9_ ]+?\s+date)\s+(?:is\s+)?before\s+(\d{4}-\d{2}-\d{2})\b", "before"),
        (r"\b([a-z0-9_ ]+?\s+date)\s+(?:is\s+)?after\s+(\d{4}-\d{2}-\d{2})\b", "after"),
        (r"\b([a-z0-9_ ]+?\s+date)\s+(?:is\s+on|on)\s+(\d{4}-\d{2}-\d{2})\b", "eq"),
    ):
        if fielded_added:
            break
        match = re.search(pattern, question, re.IGNORECASE)
        if not match:
            continue
        field_phrase = _date_field_phrase(match.group(1))
        if operator == "between":
            values = [match.group(2), match.group(3)]
            raw = f"{field_phrase} between {values[0]} and {values[1]}"
        else:
            values = [match.group(2)]
            raw_operator = "on" if operator == "eq" else operator
            raw = f"{field_phrase} {raw_operator} {values[0]}"
        add(raw, operator, values, "day", field_phrase)
        fielded_added = True

    for pattern, granularity in (
        (r"\b([a-z0-9_ ]+?\s+date)\s+in\s+([a-z]+)\s+(20\d{2})\b", "month"),
        (r"\b([a-z0-9_ ]+?\s+date)\s+in\s+(20\d{2})\b", "year"),
        (r"\b([a-z0-9_ ]+?\s+date)\s+in\s+([a-z]+)\b", "month"),
    ):
        if fielded_added:
            break
        match = re.search(pattern, question, re.IGNORECASE)
        if not match:
            continue
        field_phrase = _date_field_phrase(match.group(1))
        if granularity == "year":
            year = int(match.group(2))
            values = [f"{year}-01-01", f"{year}-12-31"]
            raw = f"{field_phrase} in {year}"
        else:
            month_name = match.group(2).lower()
            if month_name not in _MONTHS:
                add(match.group(0), "unknown", [month_name], "unknown", field_phrase)
                fielded_added = True
                continue
            year = int(match.group(3)) if match.lastindex and match.lastindex >= 3 and match.group(3) else current.year
            values = list(_month_range(year, _MONTHS[month_name]))
            raw = f"{field_phrase} in {month_name} {year}" if match.lastindex and match.lastindex >= 3 and match.group(3) else f"{field_phrase} in {month_name}"
        add(raw, "between", values, granularity, field_phrase)
        fielded_added = True

    for pattern, operator, granularity in (
        (_BEFORE_RE, "before", "day"),
        (_AFTER_RE, "after", "day"),
    ):
        if fielded_added:
            continue
        match = pattern.search(question)
        if match:
            value = _cleanup_phrase(match.group(1))
            if _is_iso_date(value):
                add(match.group(0), operator, [value], granularity)

    match = _BETWEEN_RE.search(question)
    if match and not fielded_added:
        start = _cleanup_phrase(match.group(1))
        end = _cleanup_phrase(match.group(2))
        if _is_iso_date(start) and _is_iso_date(end):
            add(match.group(0), "between", [start, end], "day")
        elif _looks_like_interval_value(start) or _looks_like_interval_value(end):
            add(match.group(0), "unknown", [start, end], "unknown")

    match = _ON_DATE_RE.search(question)
    if match and not fielded_added:
        add(match.group(0), "eq", [match.group(1)], "day")

    match = _IN_MONTH_YEAR_RE.search(question)
    if match and not fielded_added and match.group(1).lower() in _MONTHS:
        start, end = _month_range(int(match.group(2)), _MONTHS[match.group(1).lower()])
        add(match.group(0), "between", [start, end], "month")
    elif match and not fielded_added:
        add(match.group(0), "unknown", [match.group(1), match.group(2)], "unknown")

    match = _IN_YEAR_RE.search(question)
    if match and not fielded_added:
        year = int(match.group(1))
        add(match.group(0), "between", [f"{year}-01-01", f"{year}-12-31"], "year")

    match = _IN_MONTH_RE.search(question)
    if match and not fielded_added and not _IN_MONTH_YEAR_RE.search(question):
        month_name = match.group(1).lower()
        if month_name in _MONTHS:
            start, end = _month_range(current.year, _MONTHS[month_name])
            add(match.group(0), "between", [start, end], "month")

    normalized = _cleanup_phrase(question).lower()
    relative_ranges = {
        "this month": _this_month(current),
        "last month": _last_month(current),
        "this year": (f"{current.year}-01-01", f"{current.year}-12-31"),
        "last year": (f"{current.year - 1}-01-01", f"{current.year - 1}-12-31"),
        "this quarter": _quarter_range(current.year, ((current.month - 1) // 3) + 1),
        "last quarter": _last_quarter(current),
    }
    if not fielded_added:
        relative_pattern = "|".join(re.escape(phrase) for phrase in relative_ranges)
        match = re.search(rf"\b([a-z0-9_ ]+?\s+date)\s+({relative_pattern})\b", question, re.IGNORECASE)
        if match:
            field_phrase = _date_field_phrase(match.group(1))
            phrase = match.group(2).lower()
            start, end = relative_ranges[phrase]
            add(f"{field_phrase} {phrase}", "between", [start, end], phrase.replace(" ", "_"), field_phrase)
            fielded_added = True
    for phrase, (start, end) in relative_ranges.items():
        if not fielded_added and phrase in normalized:
            add(phrase, "between", [start, end], phrase.replace(" ", "_"))

    match = _RELATIVE_DAYS_RE.search(question)
    if match:
        days = int(match.group(1))
        if days > 0:
            start = current - timedelta(days=days - 1)
            add(match.group(0), "between", [start.isoformat(), current.isoformat()], "relative_days")

    return results


def _apply_interval_metadata(entry: dict[str, Any], *, today: date | None = None) -> None:
    operator = str(entry.get("operator") or "").strip().lower()
    values = [str(value).strip() for value in (entry.get("values") or []) if str(value).strip()]
    if operator not in {"before", "after", "between", "eq"}:
        return
    if operator == "between" and len(values) == 2 and all(_is_iso_date(value) for value in values):
        entry["filter_kind"] = "date_interval"
        entry["interval_granularity"] = "day"
    elif operator in {"before", "after", "eq"} and len(values) == 1 and _is_iso_date(values[0]):
        entry["filter_kind"] = "date_interval"
        entry["interval_granularity"] = "day"
    else:
        return
    entry["date_column_phrase"] = str(entry.get("field_phrase") or entry.get("field") or "").strip()


def _is_iso_date(value: str) -> bool:
    try:
        date.fromisoformat(str(value).strip())
    except ValueError:
        return False
    return bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(value).strip()))


def _looks_like_interval_value(value: str) -> bool:
    normalized = _cleanup_phrase(value).lower()
    return bool(
        normalized in _MONTHS
        or re.fullmatch(r"20\d{2}", normalized)
        or re.fullmatch(r"\d{4}-\d{2}-\d{2}", normalized)
    )


def _is_value_only_interval_phrase(value: str) -> bool:
    normalized = _cleanup_phrase(value).lower()
    return bool(
        re.match(r"^(?:before|after|on)\s+\d{4}-\d{2}-\d{2}$", normalized)
        or re.match(r"^between\s+.+?\s+and\s+.+$", normalized)
        or re.match(r"^in\s+(?:[a-z]+\s+)?20\d{2}$", normalized)
        or re.match(r"^last\s+\d+\s+days?$", normalized)
    )


def _date_field_phrase(value: str) -> str:
    tokens = _tokenize(value)
    if "date" not in tokens:
        return _cleanup_phrase(value)
    index = len(tokens) - 1 - list(reversed(tokens)).index("date")
    start = max(0, index - 1)
    return " ".join(tokens[start : index + 1])


def _month_range(year: int, month: int) -> tuple[str, str]:
    return f"{year:04d}-{month:02d}-01", f"{year:04d}-{month:02d}-{monthrange(year, month)[1]:02d}"


def _this_month(today: date) -> tuple[str, str]:
    return _month_range(today.year, today.month)


def _last_month(today: date) -> tuple[str, str]:
    year = today.year if today.month > 1 else today.year - 1
    month = today.month - 1 if today.month > 1 else 12
    return _month_range(year, month)


def _quarter_range(year: int, quarter: int) -> tuple[str, str]:
    start_month = ((quarter - 1) * 3) + 1
    start = f"{year:04d}-{start_month:02d}-01"
    end_month = start_month + 2
    end = f"{year:04d}-{end_month:02d}-{monthrange(year, end_month)[1]:02d}"
    return start, end


def _last_quarter(today: date) -> tuple[str, str]:
    quarter = ((today.month - 1) // 3) + 1
    if quarter == 1:
        return _quarter_range(today.year - 1, 4)
    return _quarter_range(today.year, quarter - 1)


def _extract_structured_having(question: str) -> list[dict[str, Any]]:
    explicit_match = _HAVING_RE.search(question)
    if explicit_match:
        phrase = _cleanup_phrase(explicit_match.group(1))
        return [_parse_having_condition(phrase)] if phrase else []

    where_match = _WHERE_RE.search(question)
    if where_match:
        phrase = _cleanup_phrase(where_match.group(1))
        condition = _parse_having_condition(phrase)
        if condition.get("aggregate_function") and _where_clause_is_having(question, where_match, phrase):
            return [condition]

    with_match = _WITH_RE.search(question)
    if with_match:
        phrase = _cleanup_phrase(with_match.group(1))
        condition = _parse_having_condition(phrase)
        if condition.get("aggregate_function"):
            return [condition]
    return []


def _parse_having_condition(phrase: str) -> dict[str, Any]:
    cleaned = _cleanup_phrase(phrase)
    entry = {
        "raw_phrase": cleaned,
        "aggregate_function": "",
        "metric_phrase": "",
        "operator": "unknown",
        "value": "",
        "value_phrase": "",
        "values": [],
        "conjunction": None,
    }
    if not cleaned:
        return entry

    count_prefix_match = re.match(
        r"^(?P<operator>more\s+than|greater\s+than|above|over|less\s+than|below|under|at\s+least|at\s+most)\s+"
        r"(?P<value>[-+]?\d+(?:\.\d+)?)\s+(?P<entity>.+)$",
        cleaned,
        re.IGNORECASE,
    )
    if count_prefix_match:
        operator_word = count_prefix_match.group("operator").lower()
        entry["aggregate_function"] = "count"
        entry["operator"] = {
            "more than": "gt",
            "greater than": "gt",
            "above": "gt",
            "over": "gt",
            "less than": "lt",
            "below": "lt",
            "under": "lt",
            "at least": "gte",
            "at most": "lte",
        }[operator_word]
        entry["value"] = count_prefix_match.group("value")
        entry["value_phrase"] = count_prefix_match.group("value")
        entry["values"] = [count_prefix_match.group("value")]
        return entry

    operator_patterns = (
        (r"\s+(?:is\s+)?greater\s+than\s+or\s+equal\s+to\s+", "gte"),
        (r"\s+(?:is\s+)?less\s+than\s+or\s+equal\s+to\s+", "lte"),
        (r"\s+(?:is\s+)?more\s+than\s+", "gt"),
        (r"\s+(?:is\s+)?greater\s+than\s+", "gt"),
        (r"\s+(?:is\s+)?above\s+", "gt"),
        (r"\s+(?:is\s+)?over\s+", "gt"),
        (r"\s+(?:is\s+)?below\s+", "lt"),
        (r"\s+(?:is\s+)?under\s+", "lt"),
        (r"\s+(?:is\s+)?less\s+than\s+", "lt"),
        (r"\s+(?:is\s+)?at\s+least\s+", "gte"),
        (r"\s+(?:is\s+)?at\s+most\s+", "lte"),
        (r"\s+(?:is\s+)?not\s+equal(?:s)?(?:\s+to)?\s+", "neq"),
        (r"\s+(?:equals?|is)\s+", "eq"),
        (r"\s*(>=)\s*", "gte"),
        (r"\s*(<=)\s*", "lte"),
        (r"\s*(!=|<>)\s*", "neq"),
        (r"\s*(>)\s*", "gt"),
        (r"\s*(<)\s*", "lt"),
        (r"\s*(=)\s*", "eq"),
    )
    left = ""
    right = ""
    for pattern, operator in operator_patterns:
        match = re.search(pattern, cleaned, re.IGNORECASE)
        if not match:
            continue
        left = _cleanup_phrase(cleaned[: match.start()])
        right = _cleanup_phrase(cleaned[match.end() :])
        entry["operator"] = operator
        break
    if not left or not right:
        return entry

    aggregate_match = re.match(
        r"^(sum|total|average|avg|mean|highest|maximum|max|lowest|minimum|min|count)\b\s*(.*)$",
        left,
        re.IGNORECASE,
    )
    if aggregate_match:
        aggregate_word = aggregate_match.group(1).lower()
        entry["aggregate_function"] = {
            "total": "sum",
            "average": "avg",
            "mean": "avg",
            "highest": "max",
            "maximum": "max",
            "lowest": "min",
            "minimum": "min",
        }.get(aggregate_word, aggregate_word)
        metric_phrase = _cleanup_phrase(aggregate_match.group(2))
        entry["metric_phrase"] = "" if entry["aggregate_function"] == "count" else metric_phrase
    else:
        entry["metric_phrase"] = left

    entry["value"] = right
    entry["value_phrase"] = right
    entry["values"] = [right]
    return entry


def _extract_implicit_having_dimension(question: str) -> str:
    body = _strip_leading_action(question)
    boundary = re.search(r"\s+(?:from|where|with|having)\b", body, re.IGNORECASE)
    candidate = body[: boundary.start()] if boundary else ""
    candidate = _cleanup_phrase(candidate)
    if not candidate or _detect_aggregate_function(candidate) or _COUNT_RE.search(candidate):
        return ""
    return candidate


def _aggregate_function_before_having(question: str) -> str | None:
    boundary_match = _HAVING_RE.search(question)
    if boundary_match:
        return _detect_aggregate_function(question[: boundary_match.start()])

    where_match = _WHERE_RE.search(question)
    if where_match and _parse_having_condition(_cleanup_phrase(where_match.group(1))).get("aggregate_function"):
        return _detect_aggregate_function(question[: where_match.start()])

    with_match = _WITH_RE.search(question)
    if with_match and _parse_having_condition(_cleanup_phrase(with_match.group(1))).get("aggregate_function"):
        return _detect_aggregate_function(question[: with_match.start()])
    return _detect_aggregate_function(question)


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
        matched_phrase = _cleanup_phrase(match.group(0))
        interval_raws = [
            _cleanup_phrase(entry.get("raw_phrase") or "").lower()
            for entry in _extract_interval_filters(question)
        ]
        normalized_matched = matched_phrase.lower()
        if any(
            raw
            and (
                raw == normalized_matched
                or raw in normalized_matched
                or normalized_matched in raw
            )
            for raw in interval_raws
        ):
            continue
        if (
            _COUNT_RE.search(body)
            or _detect_aggregate_function(prefix)
            or _TOP_RE.search(prefix)
            or _BOTTOM_RE.search(prefix)
            or re.search(r"\s+with\s+", prefix, re.IGNORECASE)
        ):
            return match
    return None


def _scope_parts(question: str) -> Optional[tuple[re.Match[str], str, Optional[re.Match[str]]]]:
    match = _source_scope_match(question)
    if not match:
        return None
    tail = match.group(1)
    split_match = re.search(
        r"\s+(?:where|with|having|filter(?:ed)?(?:\s+by)?|before|after|between|greater\s+than|less\s+than|sort(?:ed)?|order(?:ed)?|by|per|each|group(?:ed)?\s+by)\b",
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
    with_match = _WITH_RE.search(stripped)
    if with_match and _with_phrase_is_row_filter(_cleanup_phrase(with_match.group(1))):
        stripped = _clean_scalar(f"{stripped[: with_match.start()]} {stripped[with_match.end() :]}")
    having_match = _HAVING_RE.search(stripped)
    if having_match:
        stripped = _clean_scalar(f"{stripped[: having_match.start()]} {stripped[having_match.end() :]}")
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
