"""
Structured query planning and relevant-table selection.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import date
from typing import Any
import re

from kb_pipeline.schema_facts import (
    column_business_description,
    column_business_terms,
    column_sample_values,
    enrich_knowledge_base_schema_facts,
    resolved_semantic_type,
)
from kb_pipeline.relationship_graph import (
    build_relationship_graph,
    find_safe_direct_join_relationships,
)
from kb_pipeline.vector import VectorRetriever
from utils.logger import get_logger

logger = get_logger()

_UNSAFE_QUERY_RE = re.compile(
    r"\b(insert|update|delete|drop|alter|truncate|create|grant|revoke)\b",
    re.IGNORECASE,
)


_QUESTION_STOP_WORDS = {
    "show",
    "list",
    "display",
    "get",
    "fetch",
    "what",
    "which",
    "where",
    "when",
    "how",
    "many",
    "current",
    "latest",
    "recent",
    "all",
    "records", 
    "record",
    "data",
    "table",
    
}


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip().lower())


def _normalize_identifier(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", _normalize(text)).strip("_")


def _humanize(text: str) -> str:
    return _normalize_identifier(text).replace("_", " ").strip()


def _singularize_token(token: str) -> str:
    if token.endswith("ies") and len(token) > 3:
        return token[:-3] + "y"
    if token.endswith("ses") and len(token) > 3:
        return token[:-2]
    if token.endswith("s") and not token.endswith("ss") and len(token) > 1:
        return token[:-1]
    return token


def _tokenize(text: str) -> list[str]:
    return [token for token in re.split(r"[^a-z0-9]+", _normalize(text)) if token]


def _content_terms(question: str) -> list[str]:
    return [token for token in _tokenize(question) if token not in _QUESTION_STOP_WORDS]


def _aggregate_function_hint(question: str) -> str | None:
    normalized = _normalize(question)
    if re.search(r"\b(average|avg|mean)\b", normalized):
        return "avg"
    if re.search(r"\b(total|sum)\b", normalized):
        return "sum"
    if re.search(r"\b(highest|maximum|max|largest|most)\b", normalized):
        return "max"
    if re.search(r"\b(lowest|minimum|min|smallest|least)\b", normalized):
        return "min"
    return None


def _explicit_metric_candidate_count(
    question: str,
    metric_candidates: list[dict[str, Any]],
) -> int:
    normalized_question = _normalize(question)
    question_tokens = set(_content_terms(question))
    explicit_count = 0
    seen: set[tuple[str, str]] = set()

    for candidate in metric_candidates or []:
        table_name = str(candidate.get("table") or "").strip()
        column_name = str(candidate.get("column") or "").strip()
        if not column_name:
            continue
        signature = (table_name, column_name)
        if signature in seen:
            continue

        matched_terms = [
            str(value).strip().lower()
            for value in (candidate.get("matched_terms") or [])
            if str(value).strip()
        ]
        candidate_tokens = set(_tokenize(column_name))
        explicit = False
        for term in matched_terms:
            term_tokens = set(_tokenize(term))
            if term and term in normalized_question:
                explicit = True
                break
            if term_tokens and term_tokens <= question_tokens:
                explicit = True
                break
        if not explicit and candidate_tokens and candidate_tokens <= question_tokens:
            explicit = True
        if explicit:
            explicit_count += 1
            seen.add(signature)

    return explicit_count


def _question_requests_multiple_metrics(question: str) -> bool:
    normalized_question = _normalize(question)
    return bool(re.search(r"\b(and|,)\b", normalized_question))


def classify_query_shape(
    *,
    question: str,
    intent: dict[str, Any] | None,
    retrieved_context: dict[str, Any] | None,
    plan: dict[str, Any],
    selected_tables: list[dict[str, Any]],
    selected_columns: list[dict[str, Any]],
    metric_candidates: list[dict[str, Any]],
    dimension_candidates: list[dict[str, Any]],
    filter_candidates: list[dict[str, Any]],
    join_paths: list[dict[str, Any]],
    formula_evidence: list[dict[str, Any]],
) -> str:
    """Centralized planner query-shape classifier used before route selection."""
    del retrieved_context, selected_columns  # Reserved for future expansion without changing the contract.

    table_count = len([entry for entry in selected_tables if entry.get("table")])
    requested_dimensions = list((intent or {}).get("requested_dimensions") or plan.get("requested_dimensions") or [])
    requested_metrics = list((intent or {}).get("requested_metrics") or plan.get("requested_metrics") or [])
    requested_filters = list((intent or {}).get("requested_filters") or plan.get("requested_filters") or [])
    has_grouping = bool(plan.get("grouping") or plan.get("dimension") or requested_dimensions)
    has_filters = bool(filter_candidates or plan.get("filters") or plan.get("date_range") or requested_filters)
    has_join_paths = bool(join_paths)
    has_join = has_join_paths or table_count > 1
    intent_type = str((intent or {}).get("intent_type") or "").strip().lower()
    planner_intent = str(plan.get("intent") or "").strip().lower()
    aggregate_hint = _aggregate_function_hint(question)
    is_count = (
        planner_intent == "count"
        or intent_type == "count"
        or str((intent or {}).get("aggregate_function") or "").strip().lower() == "count"
    )
    is_ranking = bool(
        planner_intent == "top_n"
        or intent_type in {"ranking", "sorted_list"}
        or (intent or {}).get("requested_sort")
        or plan.get("sorting")
    )
    has_explicit_rank_count = bool(
        re.search(r"\b(?:top|bottom|lowest|highest|first|last)\s+\d+\b", question, re.IGNORECASE)
    )
    explicit_metric_count = _explicit_metric_candidate_count(question, metric_candidates)
    has_metric = bool(metric_candidates or requested_metrics)

    if bool((intent or {}).get("unsafe")) or _UNSAFE_QUERY_RE.search(question):
        return "blocked_unsafe"
    if explicit_metric_count > 1 and _question_requests_multiple_metrics(question):
        return "multi_metric_aggregate"
    if is_ranking and has_explicit_rank_count:
        return "ranking_query"
    if is_ranking:
        return "ranking_query"
    if has_grouping and (has_metric or is_count):
        return "grouped_aggregate"
    if has_filters:
        return "filtered_query"
    if aggregate_hint and table_count == 1 and not has_grouping and not has_join:
        return "single_table_aggregate"
    if has_join_paths:
        return "joined_lookup"
    if is_count and table_count == 1:
        return "single_table_count"
    if planner_intent == "list" and table_count == 1:
        return "single_table_list"
    return "unknown"


def _extract_limit(question: str) -> int | None:
    match = re.search(
        r"\b(?:top|first|last|latest|recent|limit|show|get|fetch)\s+(\d+)\b"
        r"|\b(\d+)\s+(?:rows?|records?|results?|items?)\b",
        question,
        re.IGNORECASE,
    )
    if match:
        return int(match.group(1) or match.group(2))
    return None


def _detect_sorting(question: str) -> dict[str, str] | None:
    normalized = _normalize(question)

    explicit_match = re.search(r"\b(?:sorted|sort|ordered|order)\s+by\s+([a-z0-9_ ]+)", normalized)
    if explicit_match:
        sort_value = explicit_match.group(1)
        sort_value = re.split(r"\b(?:for|with|where|in|on|from|and|or|limit|top|first|last)\b", sort_value)[0].strip()
        if sort_value:
            return {"direction": "asc", "by": sort_value}

    if re.search(r"\b(?:latest|recent|newest|most recent|last)\b", normalized):
        return {"direction": "desc", "by": "date"}

    if re.search(r"\b(?:oldest|earliest|first)\b", normalized):
        return {"direction": "asc", "by": "date"}
    
    top_n_match = re.search(r"\btop\s+\d+\s+[a-z0-9_ ]+?\s+by\s+([a-z0-9_ ]+)", normalized)
    if top_n_match:
        sort_value = top_n_match.group(1).strip()
        if sort_value:
            return {"direction": "desc", "by": sort_value}

    return None

def _detect_intent(question: str) -> str:
    normalized = _normalize(question)

    if re.search(r"\b(count|how many|number of)\b", normalized):
        return "count"
    
    if re.search(r"\b(average|avg|mean)\b", normalized):
        return "average"
    
    if re.search(r"\b(total|sum)\b", normalized):
        return "total"
    
    if re.search(r"\b(compare|comparison|versus|vs)\b", normalized):
        return "comparison"
    if re.search(r"\b(trend|monthly|by month|per month|by date|over time)\b", normalized):
        return "trend"
    if (
        re.search(r"\b(highest|maximum|max|lowest|minimum|min)\b", normalized)
        and not re.search(r"\b(?:highest|lowest)\s+\d+\b", normalized)
        and " by " not in normalized
    ):
        return "aggregate"
    if re.search(r"\b(highest|largest|lowest|smallest|most|least)\b", normalized):
        return "top_n"
    if re.search(r"\btop\s+\d+\b", normalized):
        if " by " in normalized:
            return "top_n"
        return "list"
    if re.search(r"\b(list|show|display|fetch|get)\b", normalized):
        return "list"
    return "list"


def _extract_business_terms(question: str) -> list[str]:
    """Extract business terms from question, preserving them for context retrieval."""
    normalized = _normalize(question)
    stop_words = {
        "show", "list", "display", "get", "fetch", "what", "which", "where", "when",
        "how", "many", "current", "latest", "recent", "all", "the", "a", "an", "by",
        "per", "for", "with", "from", "in", "on", "at", "to", "and", "or", "top",
        "highest", "largest", "lowest", "smallest", "most", "least", "count", "total",
        "sum", "average", "avg", "mean", "compare", "comparison", "versus", "vs",
        "trend", "monthly", "over", "time", "sorted", "sort", "order", "ordered",
    }
    
    terms = []
    for token in _tokenize(question):
        if token and token not in stop_words and len(token) > 1:
            terms.append(token)
    
    return terms


def _extract_phrase_positions(question: str) -> dict[str, list[str]]:
    """
    Extract phrase positions from question using generic patterns.
    This is schema-agnostic and does not classify terms as metrics/dimensions.
    """
    normalized = _normalize(question)
    phrases = {
        "metric_candidates": [],
        "dimension_candidates": [],
    }
    
    # Extract "top N X by Y" pattern first (takes precedence)
    top_match = re.search(r"\btop\s+\d+\s+([a-z0-9_ ]+?)\s+by\s+([a-z0-9_ ]+)", normalized)
    if top_match:
        dimension_phrase = top_match.group(1).strip()
        metric_phrase = top_match.group(2).strip()
        if dimension_phrase:
            phrases["dimension_candidates"].append(dimension_phrase)
        if metric_phrase:
            phrases["metric_candidates"].append(metric_phrase)
        return phrases  # Return early to avoid duplicate matches
    
    # Extract "X by Y" pattern - X is metric candidate, Y is dimension candidate
    by_match = re.search(r"\b(?:show|list|display|get|fetch|count|total|sum|average|avg|mean)?\s*([a-z0-9_ ]+?)\s+by\s+([a-z0-9_ ]+)", normalized)
    if by_match:
        metric_phrase = by_match.group(1).strip()
        dimension_phrase = by_match.group(2).strip()
        if metric_phrase:
            phrases["metric_candidates"].append(metric_phrase)
        if dimension_phrase:
            phrases["dimension_candidates"].append(dimension_phrase)
    
    # Extract "X wise" pattern
    wise_match = re.search(r"\b([a-z0-9_ ]+?)\s+wise\b", normalized)
    if wise_match:
        dimension_phrase = wise_match.group(1).strip()
        if dimension_phrase:
            phrases["dimension_candidates"].append(dimension_phrase)
    
    return phrases


def _extract_requested_filters(question: str) -> list[str]:
    """Extract filter terms from question in a schema-agnostic way."""
    normalized = _normalize(question)
    filters = []
      
    # Look for preposition patterns that suggest filters
    filter_patterns = [
        r"\b(?:from|in)\s+([a-z][a-z0-9_ ]*)",
        r"\b(?:with|where)\s+([a-z][a-z0-9_ ]*)",
    ]
    
    for pattern in filter_patterns:
        matches = re.findall(pattern, normalized)
        for match in matches:
            # Clean up the match
            clean_match = re.split(r"\b(?:by|and|or|top|sorted|order)\b", match)[0].strip()
            if clean_match and len(clean_match) > 1:
                filters.append(clean_match)
    
    return list(set(filters))

 
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


def build_intent(question: str) -> dict[str, Any]:
    """Backward-compatible delegate to the canonical deterministic intent builder."""
    from query_pipeline.intent_builder import build_intent as _canonical_build_intent

    return _canonical_build_intent(question)



def _detect_dimension(question: str, intent: str | None = None) -> str | None:
    normalized = _normalize(question)
    if str(intent or "") == "top_n":
        top_n_match = re.search(r"\btop\s+\d+\s+([a-z0-9_ ]+?)\s+by\s+([a-z0-9_ ]+)", normalized)
        if top_n_match:
            return top_n_match.group(1).strip() or None
    wise_match = re.search(r"\b([a-z0-9_ ]+?)\s+wise\b", normalized)
    if wise_match:
        tokens = wise_match.group(1).strip().split()
        if tokens:
            return tokens[-1]
    if re.search(r"\b(?:sorted|sort|ordered|order)\s+by\s+", normalized):
        return None
    match = re.search(r"\b(?:by|per)\s+([a-z0-9_ ]+)", normalized)
    if not match:
        return None
    value = match.group(1)
    value = re.split(r"\b(?:for|with|where|in|on|from|and)\b", value)[0].strip()
    return value or None


def _detect_date_range(question: str) -> dict | None:
    normalized = _normalize(question)
    today = date.today()

    if "this month" in normalized or "current month" in normalized:
        start_date = today.replace(day=1)
        if start_date.month == 12:
            end_date = start_date.replace(year=start_date.year + 1, month=1)
        else:
            end_date = start_date.replace(month=start_date.month + 1)
        return {
            "label": "this_month",
            "start": start_date.isoformat(),
            "end_exclusive": end_date.isoformat(),
        }

    if "this year" in normalized or "current year" in normalized:
        return {
            "label": "this_year",
            "start": f"{today.year}-01-01",
            "end_exclusive": f"{today.year + 1}-01-01",
        }

    year_match = re.search(r"\b(?:in|for|during)\s+(20\d{2})\b|\b(20\d{2})\b", normalized)
    if year_match:
        year = int(year_match.group(1) or year_match.group(2))
        return {
            "label": f"year_{year}",
            "start": f"{year}-01-01",
            "end_exclusive": f"{year + 1}-01-01",
        }

    return None





def _normalized_sample_values(column: dict[str, Any]) -> list[tuple[str, str]]:
    values = []
    for raw_value in column_sample_values(column):
        if raw_value is None:
            continue
        normalized = _normalize(str(raw_value))
        if normalized:
            values.append((normalized, str(raw_value)))
    return values


def _column_can_hold_status(column: dict[str, Any]) -> bool:
    return resolved_semantic_type(column) == "status"


def _column_supports_sample_filter(column: dict[str, Any]) -> bool:
    semantic_type = resolved_semantic_type(column)
    if semantic_type in {"status", "name", "text", "code", "reference"}:
        return True

    column_type = _normalize(str(column.get("type", "")))
    return any(token in column_type for token in ("char", "text", "enum"))




def _detect_runtime_filters(question: str, candidate_tables: dict[str, Any]) -> list[dict[str, Any]]:
    normalized_question = _normalize(question)
    question_terms = set(_tokenize(question))
    requested_limit = _extract_limit(question)
    filters: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()

    for table_name, table_data in candidate_tables.items():
        for column in table_data.get("columns", []):
            if not _column_supports_sample_filter(column):
                continue
            column_name = str(column.get("name", ""))
            for normalized_value, raw_value in _normalized_sample_values(column):
                value_terms = set(_tokenize(normalized_value))
                if not value_terms:
                    continue
                if requested_limit is not None and normalized_value == str(requested_limit):
                    continue
                if normalized_value not in normalized_question and not value_terms <= question_terms:
                    continue

                signature = (table_name, column_name, raw_value)
                if signature in seen:
                    continue
                seen.add(signature)
                filters.append(
                    {
                        "type": "status" if _column_can_hold_status(column) else "value",
                        "table": table_name,
                        "column": column_name,
                        "value": raw_value,
                        "term": normalized_value,
                    }
                )

    return filters


def _extract_preposition_filter_value(question: str) -> str | None:
    match = re.search(r"\b(?:from|in)\s+([A-Za-z][A-Za-z0-9 ]*)$", str(question or "").strip(), re.IGNORECASE)
    if not match:
        return None
    value = match.group(1).strip()
    value = re.split(r"\b(?:by|with|where|and|or|order|sorted|latest|top)\b", value)[0].strip()
    if not value or re.fullmatch(r"20\d{2}", value, re.IGNORECASE):
        return None
    return value


def _column_supports_generic_text_filter(column: dict[str, Any]) -> bool:
    semantic_type = resolved_semantic_type(column)
    if semantic_type in {"status", "date", "id", "money", "quantity", "percentage"}:
        return False
    column_type = _normalize(str(column.get("type", "")))
    return any(token in column_type for token in ("char", "text", "enum"))


def _column_metadata_tokens(column: dict[str, Any]) -> set[str]:
    tokens = set(_tokenize(str(column.get("name", ""))))
    tokens.update(_tokenize(column_business_description(column)))
    for term in column_business_terms(column):
        tokens.update(_tokenize(str(term)))
    return tokens


def _generic_filter_column_score(column: dict[str, Any], filter_value: str) -> float:
    score = 0.0
    semantic_type = resolved_semantic_type(column)
    if semantic_type in {"name", "text", "code", "reference"}:
        score += 1.0

    normalized_filter = _normalize(filter_value)
    filter_terms = set(_tokenize(filter_value))
    metadata_overlap = len(filter_terms & _column_metadata_tokens(column))
    if metadata_overlap:
        score += metadata_overlap * 0.45

    for normalized_value, _ in _normalized_sample_values(column):
        if normalized_filter == normalized_value:
            score += 4.0
            break
        if normalized_filter in normalized_value or normalized_value in normalized_filter:
            score += 1.5
            break

    return score


def _detect_generic_value_filters(question: str, candidate_tables: dict[str, Any]) -> list[dict[str, Any]]:
    filter_value = _extract_preposition_filter_value(question)
    if not filter_value:
        return []

    ranked: list[tuple[float, dict[str, Any]]] = []
    for table_name, table_data in candidate_tables.items():
        for column in table_data.get("columns", []):
            if not _column_supports_generic_text_filter(column):
                continue
            score = _generic_filter_column_score(column, filter_value)
            if score <= 0:
                continue
            ranked.append(
                (
                    score,
                    {
                        "type": "value",
                        "table": table_name,
                        "column": str(column.get("name", "")),
                        "value": filter_value,
                        "term": filter_value,
                    },
                )
            )

    ranked.sort(key=lambda item: (-item[0], item[1]["table"], item[1]["column"]))
    if not ranked:
        return []
    top_score = ranked[0][0]
    return [filter_data for score, filter_data in ranked if score == top_score][:1]


def _default_limit_for_intent(intent: str) -> int | None:
    if intent in {"list", "top_n"}:
        return 50
    return None


def _semantic_hints(intent: str, date_range: dict[str, Any] | None, sorting: dict[str, str] | None) -> set[str]:
    hints = set()

    if intent == "trend" or date_range or str((sorting or {}).get("by", "")).lower() == "date":
        hints.add("date")

    return hints


def _primary_metric_hint(semantic_hints: set[str]) -> str | None:
    return "date" if "date" in semantic_hints else None


def _should_preserve_simple_list_metric(plan: dict[str, Any]) -> bool:
    if str(plan.get("intent") or "") != "list":
        return True
    if plan.get("dimension") or plan.get("grouping"):
        return True
    return False


def _is_simple_primary_table_question(plan: dict[str, Any]) -> bool:
    if (
        str(plan.get("intent") or "") in {"count", "total", "average"}
        and not plan.get("dimension")
        and not plan.get("grouping")
    ):
        return True

    return (
        str(plan.get("intent") or "") in {"list", "count"}
        and not plan.get("dimension")
        and not plan.get("grouping")
        and not plan.get("filters")
        and not plan.get("date_range")
        and not (set(plan.get("semantic_hints") or set()) & {"date", "status"})
    )


def _primary_table_for_simple_question_from_entries(
    question: str,
    plan: dict[str, Any],
    table_entries: list[dict[str, Any]],
) -> str | None:
    """Choose one dominant table for simple single-table list/count questions."""
    if not _is_simple_primary_table_question(plan) or not table_entries:
        return None

    ordered = [
        entry for entry in table_entries
        if str(entry.get("table", "")).strip()
    ]
    if not ordered:
        return None

    top_entry = ordered[0]
    top_table = str(top_entry.get("table", "")).strip()
    top_score = float(top_entry.get("score", top_entry.get("confidence", 0.0)) or 0.0)
    second_score = (
        float(ordered[1].get("score", ordered[1].get("confidence", 0.0)) or 0.0)
        if len(ordered) > 1
        else 0.0
    )
    direct_match = _table_name_matches_question(question, top_table)
    second_direct_match = (
        _table_name_matches_question(question, str(ordered[1].get("table", "")).strip())
        if len(ordered) > 1
        else False
    )

    if len(ordered) == 1:
        return top_table if (direct_match or top_score >= 0.6) else None

    if direct_match and not second_direct_match and top_score >= second_score:
        return top_table

    if top_score >= 0.85 and second_score < 0.7:
        return top_table

    if top_score >= second_score + 0.2 and top_score >= 0.65:
        return top_table

    return None


def _table_name_matches_question(question: str, table_name: str) -> bool:
    normalized_question = _normalize(question)
    human_table = _humanize(table_name)
    if human_table and human_table in normalized_question:
        return True

    question_terms = set(_tokenize(question))
    table_terms: set[str] = set()
    for token in _tokenize(table_name):
        table_terms.add(token)
        table_terms.add(_singularize_token(token))

    return bool(table_terms & question_terms)


def _retrieve_with_vector(
    question: str,
    knowledge_base: dict,
    business_glossary: dict | None = None,
    retriever: VectorRetriever | None = None,
) -> dict:
    """Use vector retrieval to find relevant tables, columns, and glossary terms."""
    try:
        active_retriever = retriever
        if active_retriever is None:
            return {
                "table_names": [],
                "tables": [],
                "columns": [],
                "glossary_terms": [],
                "relationships": [],
                "semantic_descriptions": [],
                "profiling_hints": [],
                "retriever_status": {},
                "used_vector": False,
                "error": "vector retriever unavailable",
            }

        return {
            "table_names": active_retriever.get_relevant_tables(question, top_k=5),
            "tables": active_retriever.get_relevant_table_details(question, top_k=5),
            "columns": active_retriever.get_relevant_columns(question, top_k=10),
            "glossary_terms": active_retriever.get_relevant_glossary_terms(question, top_k=5),
            "relationships": active_retriever.get_relevant_relationships(question, top_k=5),
            "semantic_descriptions": active_retriever.get_relevant_semantic_descriptions(question, top_k=8),
            "profiling_hints": active_retriever.get_relevant_profiling_hints(question, top_k=8),
            "retriever_status": active_retriever.get_status(),
            "used_vector": True,
        }
    except Exception as exc:
        logger.warning(f"Vector retrieval error: {exc}")
        return {
            "table_names": [],
            "tables": [],
            "columns": [],
            "glossary_terms": [],
            "relationships": [],
            "semantic_descriptions": [],
            "profiling_hints": [],
            "retriever_status": {},
            "used_vector": False,
            "error": str(exc),
        }


def _glossary_matches(question: str, glossary: dict | None) -> list[tuple[str, dict[str, Any]]]:
    if not glossary:
        return []

    normalized_question = _normalize(question)
    question_terms = set(_content_terms(question))
    matches = []
    for term, term_data in glossary.items():
        normalized_term = _normalize(term)
        term_tokens = set(_tokenize(term))
        primary_terms = list(term_data.get("primary_terms", []) or term_data.get("business_terms", []) or [])
        alias_tokens = {
            token
            for alias in primary_terms
            for token in _tokenize(alias)
        }
        if normalized_term and normalized_term in normalized_question:
            matches.append((term, term_data))
            continue
        if term_tokens and term_tokens <= question_terms:
            matches.append((term, term_data))
            continue
        if alias_tokens and alias_tokens & question_terms:
            matches.append((term, term_data))
    return matches


def _glossary_alias_hits_question(question: str, term: str, term_data: dict[str, Any]) -> bool:
    normalized_question = _normalize(question)
    question_terms = set(_content_terms(question))
    normalized_term = _normalize(term)
    term_tokens = set(_tokenize(term))

    if normalized_term and normalized_term in normalized_question:
        return True
    if term_tokens and term_tokens <= question_terms:
        return True

    for alias in (term_data.get("primary_terms", []) or term_data.get("business_terms", []) or []):
        normalized_alias = _normalize(alias)
        alias_tokens = set(_tokenize(alias))
        if normalized_alias and normalized_alias in normalized_question:
            return True
        if alias_tokens and alias_tokens <= question_terms:
            return True

    return False


def _glossary_mapped_tables(term_data: dict[str, Any]) -> set[str]:
    return {
        str(mapping.get("table", "")).strip()
        for mapping in term_data.get("mapped_columns", []) or []
        if str(mapping.get("table", "")).strip()
    }


def _has_strong_glossary_table_match(
    question: str,
    table_name: str,
    glossary_matches: list[tuple[str, dict[str, Any]]],
) -> bool:
    for term, term_data in glossary_matches:
        mapped_tables = _glossary_mapped_tables(term_data)
        if table_name not in mapped_tables:
            continue
        if len(mapped_tables) != 1:
            continue
        if _glossary_alias_hits_question(question, term, term_data):
            return True
    return False


def _enriched_kb(knowledge_base: dict) -> dict:
    if not knowledge_base:
        return {}
    return enrich_knowledge_base_schema_facts(
        deepcopy(knowledge_base),
        infer_relationships=False,
    )


def _question_text_for_table(table_name: str, table_data: dict) -> str:
    pieces = [
        table_name,
        _humanize(table_name),
        str(table_data.get("business_purpose", "")),
        str(table_data.get("business_description", "")),
    ]
    for column in table_data.get("columns", []):
        pieces.append(str(column.get("name", "")))
        pieces.append(column_business_description(column))
        pieces.extend(str(term) for term in column_business_terms(column))
    return " ".join(piece for piece in pieces if piece)


def _table_score(
    plan: dict[str, Any],
    table_name: str,
    table_data: dict[str, Any],
    glossary_matches: list[tuple[str, dict[str, Any]]],
    vector_results: dict[str, Any] | None,
) -> tuple[float, list[str]]:
    score = 0.0
    reasons: list[str] = []
    normalized_question = _normalize(plan.get("question", ""))
    question_terms = set(plan.get("question_terms", []))
    table_text = _question_text_for_table(table_name, table_data).lower()
    table_tokens = set(_tokenize(table_text))

    overlap = len(question_terms & table_tokens)
    if overlap:
        score += overlap * 0.45
        reasons.append(f"matched {overlap} question term(s) in table metadata")

    human_table = _humanize(table_name)
    if _table_name_matches_question(plan.get("question", ""), table_name):
        score += 1.6
        reasons.append("table name matches the question directly")

    for semantic_hint in plan.get("semantic_hints", set()):
        if any(resolved_semantic_type(column) == semantic_hint for column in table_data.get("columns", [])):
            score += 0.8
            reasons.append(f"contains {semantic_hint} column(s)")

    dimension = str(plan.get("dimension") or "").strip()
    if dimension:
        dimension_tokens = set(_tokenize(dimension))
        if dimension_tokens & table_tokens:
            score += 0.9
            reasons.append(f"dimension '{dimension}' matched table metadata")

    if plan.get("date_range") and any(resolved_semantic_type(column) == "date" for column in table_data.get("columns", [])):
        score += 0.6
        reasons.append("date filter needs a date column")

    if any(filter_data.get("type") == "status" for filter_data in plan.get("filters", [])):
        if any(resolved_semantic_type(column) == "status" for column in table_data.get("columns", [])):
            score += 0.6
            reasons.append("status filter needs a status column")

    for term, term_data in glossary_matches:
        direct_term_match = _glossary_alias_hits_question(plan.get("question", ""), term, term_data)
        mapped_tables = _glossary_mapped_tables(term_data)
        mapped_table_count = len(mapped_tables)
        glossary_boost = 1.1 if direct_term_match else 0.35
        if _is_simple_primary_table_question(plan):
            if direct_term_match and mapped_table_count == 1:
                glossary_boost = 1.35
            elif direct_term_match and mapped_table_count == 2:
                glossary_boost = 0.55
            elif direct_term_match:
                glossary_boost = 0.2
            else:
                glossary_boost = 0.1
        for mapping in term_data.get("mapped_columns", []):
            if mapping.get("table") == table_name:
                score += glossary_boost
                reasons.append(f"glossary term '{term}' mapped to this table")
                break

    if vector_results:
        vector_table_names = set(vector_results.get("table_names") or [])
        if table_name in vector_table_names:
            score += 1.8
            reasons.append("vector retrieval nominated this table")
        vector_columns = vector_results.get("columns") or []
        column_matches = [col for col in vector_columns if col.get("table_name") == table_name]
        if column_matches:
            score += min(1.2, 0.4 * len(column_matches))
            reasons.append("vector retrieval nominated columns in this table")
        for term_meta in vector_results.get("glossary_terms") or []:
            if table_name in (term_meta.get("table_names") or []):
                score += 0.8
                reasons.append("vector glossary retrieval matched this table")
                break

    return score, reasons


def _column_score(
    plan: dict[str, Any],
    table_name: str,
    column: dict[str, Any],
    glossary_matches: list[tuple[str, dict[str, Any]]],
    vector_results: dict[str, Any] | None,
) -> tuple[float, list[str]]:
    score = 0.0
    reasons: list[str] = []
    column_name = str(column.get("name", "")).lower()
    semantic_type = resolved_semantic_type(column)
    question_terms = set(plan.get("question_terms", []))
    column_tokens = set(_tokenize(column_name))
    column_tokens.update(_tokenize(column_business_description(column)))
    for term in column_business_terms(column):
        column_tokens.update(_tokenize(term))

    overlap = len(question_terms & column_tokens)
    if overlap:
        score += overlap * 0.55
        reasons.append(f"matched {overlap} question term(s)")

    if semantic_type in plan.get("semantic_hints", set()):
        score += 1.1
        reasons.append(f"semantic type matched '{semantic_type}'")

    dimension = str(plan.get("dimension") or "").strip()
    if dimension:
        dimension_tokens = set(_tokenize(dimension))
        table_tokens = set(_tokenize(table_name))
        if dimension_tokens & column_tokens:
            score += 1.0
            reasons.append(f"dimension '{dimension}' matched this column")
        if dimension_tokens & table_tokens and semantic_type in {"name", "text", "code", "reference"}:
            score += 1.4
            reasons.append("display-style column matched the grouping table")
        if dimension_tokens & table_tokens and column_tokens & {"name", "label", "title", "display", "segment", "code"}:
            score += 0.6
            reasons.append("column looks suitable as a grouping label")

    if semantic_type == "date" and plan.get("date_range"):
        score += 1.0
        reasons.append("date filter needs a date column")

    if semantic_type == "status" and any(filter_data.get("type") == "status" for filter_data in plan.get("filters", [])):
        score += 1.0
        reasons.append("status filter needs a status column")

    for term, term_data in glossary_matches:
        for mapping in term_data.get("mapped_columns", []):
            if mapping.get("table") == table_name and mapping.get("column") == column.get("name"):
                score += 1.2
                reasons.append(f"glossary term '{term}' mapped to this column")
                break

    if vector_results:
        for vector_column in vector_results.get("columns") or []:
            if vector_column.get("table_name") == table_name and vector_column.get("column_name") == column.get("name"):
                score += 1.4
                reasons.append("vector retrieval nominated this column")
                break

    return score, reasons


def _select_columns_for_table(
    plan: dict[str, Any],
    table_name: str,
    table_data: dict[str, Any],
    glossary_matches: list[tuple[str, dict[str, Any]]],
    vector_results: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    scored_columns: list[tuple[str, float, list[str], str]] = []
    for column in table_data.get("columns", []):
        score, reasons = _column_score(plan, table_name, column, glossary_matches, vector_results)
        if score <= 0:
            continue
        scored_columns.append(
            (
                str(column.get("name", "")),
                score,
                reasons,
                resolved_semantic_type(column),
                str(column.get("semantic_type", "unknown")).strip().lower() or "unknown",
            )
        )

    scored_columns.sort(key=lambda item: (-item[1], item[0]))
    selected_columns = []
    for column_name, score, reasons, semantic_type, core_semantic_type in scored_columns[:6]:
        selected_columns.append(
            {
                "column": column_name,
                "semantic_type": semantic_type,
                "core_semantic_type": core_semantic_type,
                "confidence": round(min(max(score / 3.0, 0.45), 0.99), 2),
                "reason": "; ".join(dict.fromkeys(reasons)),
            }
        )
    return selected_columns


def _preferred_primary_tables_for_simple_question(
    plan: dict[str, Any],
    scored_tables: list[tuple[str, float, list[str]]],
    glossary_matches: list[tuple[str, dict[str, Any]]],
    vector_results: dict[str, Any] | None,
) -> list[str] | None:
    if not _is_simple_primary_table_question(plan) or not scored_tables:
        return None

    top_table, top_score, _ = scored_tables[0]
    second_table = scored_tables[1][0] if len(scored_tables) > 1 else None
    second_score = scored_tables[1][1] if len(scored_tables) > 1 else 0.0
    question = str(plan.get("question") or "")
    direct_match_tables = [
        table_name
        for table_name, _, _ in scored_tables
        if _table_name_matches_question(question, table_name)
    ]
    if len(direct_match_tables) == 1:
        return [direct_match_tables[0]]

    has_direct_match = _table_name_matches_question(question, top_table)
    vector_table_names = list((vector_results or {}).get("table_names") or [])
    has_top_vector_match = bool(vector_table_names and vector_table_names[0] == top_table)
    has_strong_glossary_match = _has_strong_glossary_table_match(question, top_table, glossary_matches)
    second_has_strong_glossary_match = bool(
        second_table
        and _has_strong_glossary_table_match(question, second_table, glossary_matches)
    )

    if len(scored_tables) == 1 and (has_direct_match or has_top_vector_match or has_strong_glossary_match):
        return [top_table]

    if second_score <= 0:
        return [top_table] if (has_direct_match or has_top_vector_match or has_strong_glossary_match) else None

    if str(plan.get("intent") or "") in {"total", "average"} and top_score > second_score:
        return [top_table]

    if (has_direct_match or has_top_vector_match) and top_score >= (second_score * 1.5):
        return [top_table]

    if has_strong_glossary_match and not second_has_strong_glossary_match and top_score > second_score:
        return [top_table]

    if (has_top_vector_match or has_strong_glossary_match) and top_score >= second_score + 0.35:
        return [top_table]

    return None


def _expand_selected_tables(
    knowledge_base: dict,
    selected_names: list[str],
    dimension: str | None,
    plan: dict[str, Any] | None = None,
) -> list[str]:
    selected = list(selected_names)
    if plan and _is_simple_primary_table_question(plan):
        return selected

    dimension_tokens = set(_tokenize(dimension or ""))

    for table_name in list(selected_names):
        table_data = knowledge_base.get(table_name, {})
        for relationship in table_data.get("relationships", []):
            target_table = relationship.get("from_table") if relationship.get("direction") == "incoming" else relationship.get("to_table")
            if target_table not in knowledge_base or target_table in selected:
                continue

            target_tokens = set(_tokenize(target_table))
            target_tokens.update(knowledge_base[target_table].get("table_tokens", []))
            if dimension_tokens and dimension_tokens & target_tokens:
                selected.append(target_table)
            elif relationship.get("confidence", 0) >= 0.92 and len(selected) < 4:
                selected.append(target_table)

    return selected


def _candidate_tables_for_filters(
    knowledge_base: dict[str, Any],
    scored_tables: list[tuple[str, float, list[str]]],
    vector_results: dict[str, Any] | None,
) -> dict[str, Any]:
    candidate_names: list[str] = []
    for table_name, _, _ in scored_tables[:3]:
        if table_name in knowledge_base and table_name not in candidate_names:
            candidate_names.append(table_name)
    for table_name in list((vector_results or {}).get("table_names") or [])[:3]:
        if table_name in knowledge_base and table_name not in candidate_names:
            candidate_names.append(table_name)
    if not candidate_names:
        candidate_names = list(knowledge_base.keys())[:3]
    return {table_name: knowledge_base[table_name] for table_name in candidate_names}


def _build_selected_table_entries(
    knowledge_base: dict,
    scored_tables: list[tuple[str, float, list[str]]],
    plan: dict[str, Any],
    glossary_matches: list[tuple[str, dict[str, Any]]],
    vector_results: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    if not scored_tables:
        return []

    top_tables = []
    preferred_primary_tables = _preferred_primary_tables_for_simple_question(
        plan,
        scored_tables,
        glossary_matches,
        vector_results,
    )
    if preferred_primary_tables:
        top_tables = [
            entry for entry in scored_tables
            if entry[0] in preferred_primary_tables
        ]

    if not top_tables:
        top_tables = [entry for entry in scored_tables if entry[1] > 0.6][:3]
    if not top_tables:
        top_tables = scored_tables[:3]

    selected_names = _expand_selected_tables(
        knowledge_base,
        [entry[0] for entry in top_tables],
        plan.get("dimension"),
        plan=plan,
    )
    entries = []
    score_lookup = {table_name: (score, reasons) for table_name, score, reasons in scored_tables}

    for table_name in selected_names:
        score, reasons = score_lookup.get(table_name, (0.75, ["selected as a relationship bridge"]))
        selected_columns = _select_columns_for_table(
            plan,
            table_name,
            knowledge_base.get(table_name, {}),
            glossary_matches,
            vector_results,
        )
        entries.append(
            {
                "table": table_name,
                "confidence": round(min(max(score / 4.0, 0.55), 0.99), 2),
                "reason": "; ".join(dict.fromkeys(reasons)) or "selected from semantic and vector context",
                "selected_columns": selected_columns,
            }
        )

    return entries


def _infer_metric_from_selected_columns(
    plan: dict[str, Any],
    selected_tables: list[dict[str, Any]],
) -> str | None:
    current_metric = plan.get("metric")
    if plan.get("intent") not in {"total", "average", "top_n", "trend", "comparison"}:
        return current_metric

    ranked_semantics: list[tuple[float, str]] = []
    for table_entry in selected_tables:
        for column_entry in table_entry.get("selected_columns", []):
            semantic_type = str(column_entry.get("semantic_type", "")).lower()
            if semantic_type not in {"money", "quantity", "percentage", "date", "status"}:
                continue
            ranked_semantics.append((float(column_entry.get("confidence") or 0.0), semantic_type))

    ranked_semantics.sort(key=lambda item: (-item[0], item[1]))
    for _, semantic_type in ranked_semantics:
        if semantic_type in {"money", "quantity", "percentage"}:
            return semantic_type

    return current_metric


def _infer_metric_from_glossary_matches(
    glossary_matches: list[tuple[str, dict[str, Any]]],
    knowledge_base: dict[str, Any],
) -> str | None:
    ranked_semantics: list[tuple[float, str]] = []
    confidence_rank = {"high": 1.0, "medium": 0.75, "low": 0.5}

    for _, term_data in glossary_matches:
        for mapping in term_data.get("mapped_columns", []):
            table_name = str(mapping.get("table", "") or "")
            column_name = str(mapping.get("column", "") or "")
            if not table_name or not column_name:
                continue

            semantic_type = ""
            for column in knowledge_base.get(table_name, {}).get("columns", []):
                if str(column.get("name", "")) == column_name:
                    semantic_type = resolved_semantic_type(column)
                    break

            if semantic_type not in {"money", "quantity", "percentage"}:
                continue

            confidence = confidence_rank.get(str(mapping.get("confidence", "")).lower(), 0.6)
            ranked_semantics.append((confidence, semantic_type))

    ranked_semantics.sort(key=lambda item: (-item[0], item[1]))
    return ranked_semantics[0][1] if ranked_semantics else None


def _build_fk_relationship_graph(knowledge_base: dict) -> dict:
    """Build a graph of FK relationships between tables for join path computation."""
    graph: dict[str, dict[str, list[str]]] = {}
    
    for table_name, table_data in knowledge_base.items():
        if table_name not in graph:
            graph[table_name] = {"outgoing": [], "incoming": []}
        
        for fk in table_data.get("foreign_keys", []):
            from_table = fk.get("from_table") or table_name
            to_table = fk.get("to_table") or fk.get("referenced_table")
            from_column = fk.get("column")
            to_column = fk.get("referenced_column")
            
            if from_table and to_table and from_table in knowledge_base and to_table in knowledge_base:
                # Outgoing relationship: from_table -> to_table
                if from_table not in graph:
                    graph[from_table] = {"outgoing": [], "incoming": []}
                if to_table not in graph:
                    graph[to_table] = {"outgoing": [], "incoming": []}
                
                graph[from_table]["outgoing"].append({
                    "from_table": from_table,
                    "to_table": to_table,
                    "from_column": from_column,
                    "to_column": to_column,
                })
                graph[to_table]["incoming"].append({
                    "from_table": from_table,
                    "from_column": from_column,
                    "to_column": to_column,
                })
    
    return graph


def _find_shortest_path(graph: dict, start: str, end: str, max_depth: int = 5) -> list[dict] | None:
    """Find shortest path between two tables using BFS."""
    from collections import deque
    
    if start not in graph or end not in graph:
        return None
    
    if start == end:
        return []
    
    queue = deque([(start, [])])
    visited = {start}
    
    while queue and len(queue[0][1]) < max_depth:
        current, path = queue.popleft()
        
        # Check outgoing edges
        for edge in graph[current].get("outgoing", []):
            next_table = edge["to_table"]
            path_edge = {
                "from_table": current,
                "from_column": edge["from_column"],
                "to_table": next_table,
                "to_column": edge["to_column"],
                "join_condition": f"{current}.{edge['from_column']} = {next_table}.{edge['to_column']}",
            }
            if next_table == end:
                return path + [path_edge]
            if next_table not in visited:
                visited.add(next_table)
                queue.append((next_table, path + [path_edge]))
        
        # Check incoming edges
        for edge in graph[current].get("incoming", []):
            next_table = edge["from_table"]
            path_edge = {
                "from_table": current,
                "from_column": edge["to_column"],
                "to_table": next_table,
                "to_column": edge["from_column"],
                "join_condition": f"{current}.{edge['to_column']} = {next_table}.{edge['from_column']}",
            }
            if next_table == end:
                return path + [path_edge]
            if next_table not in visited:
                visited.add(next_table)
                queue.append((next_table, path + [path_edge]))
    
    return None


def _compute_join_paths(selected_tables: list[str], knowledge_base: dict) -> list[dict]:
    """Compute join paths between all selected tables using FK relationships."""
    graph = _build_fk_relationship_graph(knowledge_base)
    join_paths = []
    
    # Find paths between all pairs of selected tables
    for i, table_a in enumerate(selected_tables):
        for table_b in selected_tables[i+1:]:
            path = _find_shortest_path(graph, table_a, table_b)
            if path:
                join_paths.append({
                    "from_table": table_a,
                    "to_table": table_b,
                    "path": path,
                    "length": len(path),
                })
    
    return join_paths


def _tables_from_join_paths(join_paths: list[dict]) -> list[str]:
    """Return all tables required by computed FK paths in encounter order."""
    table_names: list[str] = []
    for join_path in join_paths:
        for candidate in (join_path.get("from_table"), join_path.get("to_table")):
            if candidate and candidate not in table_names:
                table_names.append(candidate)
        for edge in join_path.get("path", []):
            for candidate in (edge.get("from_table"), edge.get("to_table")):
                if candidate and candidate not in table_names:
                    table_names.append(candidate)
    return table_names


def _join_columns_for_table(table_name: str, join_paths: list[dict]) -> set[str]:
    join_columns: set[str] = set()
    for join_path in join_paths:
        for edge in join_path.get("path", []):
            if edge.get("from_table") == table_name and edge.get("from_column"):
                join_columns.add(str(edge["from_column"]))
            if edge.get("to_table") == table_name and edge.get("to_column"):
                join_columns.add(str(edge["to_column"]))
    return join_columns


def _selected_join_columns_for_table(table_name: str, table_data: dict[str, Any], join_paths: list[dict]) -> list[dict[str, Any]]:
    selected_columns = []
    needed_columns = _join_columns_for_table(table_name, join_paths)
    if not needed_columns:
        return selected_columns

    columns_by_name = {
        str(column.get("name", "")): column
        for column in table_data.get("columns", [])
        if column.get("name")
    }
    for column_name in sorted(needed_columns):
        column = columns_by_name.get(column_name, {})
        selected_columns.append(
            {
                "column": column_name,
                "semantic_type": resolved_semantic_type(column, fallback="id"),
                "core_semantic_type": str(column.get("semantic_type", "id")).strip().lower() or "id",
                "confidence": 0.82,
                "reason": "required by computed FK join path",
            }
        )
    return selected_columns


def _promote_join_path_tables(
    selected_names: list[str],
    selected_tables: list[dict[str, Any]],
    knowledge_base: dict[str, Any],
    plan: dict[str, Any],
    join_paths: list[dict],
) -> tuple[list[str], list[dict[str, Any]]]:
    """Promote bridge tables that are required by FK join paths into AI context."""
    if plan and _is_simple_primary_table_question(plan):
        return selected_names, selected_tables

    promoted_names = list(selected_names)
    promoted_entries = list(selected_tables)
    existing = set(promoted_names)
    score_by_table = {
        entry.get("table"): entry
        for entry in promoted_entries
        if entry.get("table")
    }

    for table_name in _tables_from_join_paths(join_paths):
        if table_name not in knowledge_base:
            continue
        if table_name not in existing:
            existing.add(table_name)
            promoted_names.append(table_name)
        if table_name not in score_by_table:
            table_data = knowledge_base.get(table_name, {})
            entry = {
                "table": table_name,
                "confidence": 0.76,
                "reason": "promoted because it is required by a computed FK join path",
                "selected_columns": _selected_join_columns_for_table(table_name, table_data, join_paths),
            }
            promoted_entries.append(entry)
            score_by_table[table_name] = entry
            continue

        existing_columns = {
            column_entry.get("column")
            for column_entry in score_by_table[table_name].setdefault("selected_columns", [])
        }
        for column_entry in _selected_join_columns_for_table(table_name, knowledge_base.get(table_name, {}), join_paths):
            if column_entry.get("column") not in existing_columns:
                score_by_table[table_name]["selected_columns"].append(column_entry)
                existing_columns.add(column_entry.get("column"))

    return promoted_names, promoted_entries


def _add_missing_tables_for_columns(
    selected_tables: list[str],
    selected_columns: list[dict],
    knowledge_base: dict,
) -> tuple[list[str], list[dict]]:
    """Add tables to selected_tables if selected columns belong to tables not in selected_tables."""
    column_tables = {col["table"] for col in selected_columns}
    missing_tables = column_tables - set(selected_tables)
    
    if missing_tables:
        for table in missing_tables:
            if table in knowledge_base:
                selected_tables.append(table)
    
    return selected_tables, selected_columns


def _find_bridge_tables(
    selected_tables: list[str],
    knowledge_base: dict,
    max_bridges: int = 3,
) -> list[str]:
    """Find bridge tables that connect disconnected selected tables."""
    graph = _build_fk_relationship_graph(knowledge_base)
    bridge_tables = []
    
    # Check if selected tables are connected
    if len(selected_tables) < 2:
        return bridge_tables
    
    # Build set of all reachable tables from first selected table using BFS
    start_table = selected_tables[0]
    reachable = {start_table}
    queue = [start_table]
    
    while queue:
        current = queue.pop(0)
        for edge in graph.get(current, {}).get("outgoing", []):
            if edge["to_table"] not in reachable:
                reachable.add(edge["to_table"])
                queue.append(edge["to_table"])
        for edge in graph.get(current, {}).get("incoming", []):
            if edge["from_table"] not in reachable:
                reachable.add(edge["from_table"])
                queue.append(edge["from_table"])
    
    # Find selected tables not reachable from start
    disconnected = [t for t in selected_tables if t not in reachable]
    
    if not disconnected:
        return bridge_tables
    
    # Find bridge tables to connect disconnected tables
    for disconnected_table in disconnected[:max_bridges]:
        path = _find_shortest_path(graph, start_table, disconnected_table, max_depth=5)
        if path and len(path) > 0:
            # Add intermediate tables as bridges (exclude the final target table)
            for edge in path[:-1]:  # Exclude final edge
                bridge = edge["to_table"]
                if bridge not in selected_tables and bridge not in bridge_tables and bridge in knowledge_base:
                    bridge_tables.append(bridge)
    
    return bridge_tables


def _planner_intent_from_structured_intent(intent: dict[str, Any] | None) -> str:
    intent_type = str((intent or {}).get("intent_type") or "").strip().lower()
    aggregate_function = str((intent or {}).get("aggregate_function") or "").strip().lower()
    if intent_type == "count":
        return "count"
    if intent_type == "aggregate":
        if aggregate_function == "avg":
            return "average"
        if aggregate_function == "sum":
            return "total"
        return "aggregate"
    if intent_type == "ranking":
        return "top_n"
    if intent_type == "comparison":
        return "comparison"
    if intent_type in {"grouped_summary", "sorted_list", "filter"}:
        return "list"
    return "list"


def _structured_dimension(intent: dict[str, Any] | None) -> str | None:
    requested_dimensions = list((intent or {}).get("requested_dimensions") or [])
    if not requested_dimensions:
        return None
    return str(requested_dimensions[0]).strip() or None


def _structured_sorting(intent: dict[str, Any] | None, planner_intent: str) -> dict[str, str] | None:
    requested_sort = dict((intent or {}).get("requested_sort") or {})
    sort_terms = str(requested_sort.get("terms") or "").strip()
    direction = str(requested_sort.get("direction") or "").strip().lower() or "asc"
    if sort_terms:
        return {"direction": direction, "by": sort_terms}
    if planner_intent == "top_n":
        return {"direction": "desc", "by": "metric"}
    return None


def _structured_filter_entries(
    filter_candidates: list[dict[str, Any]],
    requested_filters: list[str],
    structured_filters: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    filters: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    clauses = [dict(entry) for entry in (structured_filters or []) if isinstance(entry, dict)]
    if not clauses:
        clauses = [
            {
                "raw_phrase": str(term).strip(),
                "field": "",
                "operator": "unknown",
                "value": str(term).strip(),
                "conjunction": None if index == 0 else "and",
            }
            for index, term in enumerate(requested_filters)
            if str(term).strip()
        ]

    for index, clause in enumerate(clauses):
        ranked_candidates = sorted(
            (
                (_filter_field_match_score(entry, clause), entry)
                for entry in filter_candidates
            ),
            key=lambda item: (
                -item[0],
                str(item[1].get("table") or ""),
                str(item[1].get("column") or ""),
            ),
        )
        if not ranked_candidates or ranked_candidates[0][0] <= 0:
            continue
        entry = ranked_candidates[0][1]
        table_name = str(entry.get("table", "")).strip()
        column_name = str(entry.get("column", "")).strip()
        if not table_name or not column_name:
            continue
        matched_terms = list(entry.get("matched_terms") or [])
        raw_phrase = str(clause.get("raw_phrase") or "").strip()
        value = clause.get("value", clause.get("value_phrase", ""))
        term = str(matched_terms[0] if matched_terms else raw_phrase or value).strip()
        signature = (table_name, column_name, raw_phrase or str(value) or term)
        if signature in seen:
            continue
        seen.add(signature)
        filters.append(
            {
                "type": "value",
                "table": table_name,
                "column": column_name,
                "value": value,
                "term": raw_phrase or term,
                "operator": str(clause.get("operator") or "unknown"),
                "field_phrase": str(clause.get("field") or clause.get("field_phrase") or ""),
                "value_phrase": clause.get("value", clause.get("value_phrase", "")),
                "conjunction": clause.get("conjunction"),
                "raw_phrase": raw_phrase,
                "evidence_score": float(entry.get("score") or 0.0),
            }
        )
    return filters[:4]


def _filter_field_match_score(entry: dict[str, Any], clause: dict[str, Any]) -> float:
    field_phrase = str(clause.get("field_phrase") or clause.get("field") or "").strip()
    if not field_phrase:
        return float(entry.get("score") or 0.0)
    normalized_field = _normalize(field_phrase)
    field_tokens = {_singularize_token(token) for token in _tokenize(field_phrase)}
    column_text = str(entry.get("column") or "").replace("_", " ")
    normalized_column = _normalize(column_text)
    column_tokens = {_singularize_token(token) for token in _tokenize(column_text)}
    qualified_tokens = {
        _singularize_token(token)
        for token in _tokenize(f"{entry.get('table') or ''} {column_text}")
    }
    lexical_score = 0.0
    if normalized_column == normalized_field:
        lexical_score = 1.0
    elif field_tokens and field_tokens <= column_tokens:
        lexical_score = 0.9
    elif field_tokens and field_tokens <= qualified_tokens:
        lexical_score = 0.88
    elif field_tokens:
        lexical_score = (len(field_tokens & qualified_tokens) / len(field_tokens)) * 0.7

    for text in [str(value) for value in (entry.get("matched_terms") or [])]:
        normalized_text = _normalize(text)
        text_tokens = set(_tokenize(text))
        if not normalized_text or not text_tokens:
            continue
        if normalized_text == normalized_field:
            lexical_score = max(lexical_score, 0.82)
        elif field_tokens and field_tokens <= text_tokens:
            lexical_score = max(lexical_score, 0.75)
        elif field_tokens:
            overlap = len(field_tokens & text_tokens) / len(field_tokens)
            lexical_score = max(lexical_score, overlap * 0.6)
    evidence_score = min(float(entry.get("score") or 0.0), 1.0)
    return round((lexical_score * 0.9) + (evidence_score * 0.1), 4) if lexical_score else 0.0


def _has_structured_filter_ambiguity(
    filter_candidates: list[dict[str, Any]],
    structured_filters: list[dict[str, Any]],
) -> bool:
    for clause in structured_filters:
        ranked: list[tuple[float, str, str]] = []
        seen: set[tuple[str, str]] = set()
        for candidate in filter_candidates:
            table_name = str(candidate.get("table") or "").strip()
            column_name = str(candidate.get("column") or "").strip()
            signature = (table_name, column_name)
            if not table_name or not column_name or signature in seen:
                continue
            seen.add(signature)
            score = _filter_field_match_score(candidate, clause)
            if score > 0:
                ranked.append((score, table_name, column_name))
        ranked.sort(key=lambda item: (-item[0], item[1], item[2]))
        if len(ranked) >= 2 and abs(ranked[0][0] - ranked[1][0]) < 0.08:
            return True
    return False


def _role_candidate_match_score(phrase: str, entry: dict[str, Any]) -> float:
    phrase_tokens = {_singularize_token(token) for token in _tokenize(phrase)}
    column_name = str(entry.get("column") or "")
    column_tokens = {_singularize_token(token) for token in _tokenize(column_name)}
    qualified_tokens = {
        _singularize_token(token)
        for token in _tokenize(f"{entry.get('table') or ''} {column_name}")
    }
    if not phrase_tokens or not column_tokens:
        return 0.0
    if phrase_tokens == qualified_tokens:
        lexical_score = 1.0
    elif phrase_tokens == column_tokens:
        lexical_score = 0.98
    elif phrase_tokens <= column_tokens:
        lexical_score = 0.9
    elif phrase_tokens <= qualified_tokens:
        lexical_score = 0.86
    else:
        lexical_score = (len(phrase_tokens & qualified_tokens) / len(phrase_tokens)) * 0.6
    for term in entry.get("matched_terms") or []:
        term_tokens = {_singularize_token(token) for token in _tokenize(str(term))}
        if term_tokens == phrase_tokens:
            lexical_score = max(lexical_score, 0.94)
        elif phrase_tokens <= term_tokens:
            lexical_score = max(lexical_score, 0.82)
    return round(lexical_score, 4)


def _resolve_role_candidate(
    phrase: str,
    candidates: list[dict[str, Any]],
    *,
    allowed_tables: set[str] | None = None,
) -> tuple[list[dict[str, Any]], str]:
    exact_matches: list[dict[str, Any]] = []
    exact_seen: set[tuple[str, str]] = set()
    normalized_phrase = _humanize(phrase)
    phrase_tokens = {_singularize_token(token) for token in _tokenize(phrase)}
    generic_single_token = len(phrase_tokens) == 1 and next(iter(phrase_tokens), "") in _GENERIC_ROLE_TERMS
    for candidate in candidates or []:
        table_name = str(candidate.get("table") or "").strip()
        column_name = str(candidate.get("column") or "").strip()
        signature = (table_name, column_name)
        if not all(signature) or signature in exact_seen:
            continue
        if allowed_tables is not None and table_name not in allowed_tables:
            continue
        column_tokens = {_singularize_token(token) for token in _tokenize(column_name)}
        table_tokens = {_singularize_token(token) for token in _tokenize(table_name)}
        exact_term_tokens = [
            {_singularize_token(token) for token in _tokenize(str(term))}
            for term in candidate.get("matched_terms") or []
            if _tokenize(str(term))
        ]
        is_exact_match = (
            _humanize(column_name) == normalized_phrase
            or (phrase_tokens and phrase_tokens == column_tokens)
            or (phrase_tokens and phrase_tokens == table_tokens | column_tokens)
            or (
                not generic_single_token
                and (
                    any(phrase_tokens == tokens for tokens in exact_term_tokens)
                    or any(phrase_tokens == table_tokens | tokens for tokens in exact_term_tokens)
                )
            )
        )
        if not is_exact_match:
            continue
        exact_seen.add(signature)
        exact_matches.append(dict(candidate))
    if len(exact_matches) == 1:
        return exact_matches, "resolved"
    if len(exact_matches) > 1:
        return [], "ambiguous"

    ranked: list[tuple[float, dict[str, Any]]] = []
    seen: set[tuple[str, str]] = set()
    for candidate in candidates or []:
        table_name = str(candidate.get("table") or "").strip()
        column_name = str(candidate.get("column") or "").strip()
        signature = (table_name, column_name)
        if not all(signature) or signature in seen:
            continue
        if allowed_tables is not None and table_name not in allowed_tables:
            continue
        seen.add(signature)
        score = _role_candidate_match_score(phrase, candidate)
        if score > 0:
            ranked.append((score, dict(candidate)))
    ranked.sort(
        key=lambda item: (
            -item[0],
            -float(item[1].get("score") or 0.0),
            str(item[1].get("table") or ""),
            str(item[1].get("column") or ""),
        )
    )
    if not ranked or ranked[0][0] < 0.7:
        return [], "missing"
    if len(ranked) > 1:
        exact_schema_match = ranked[0][0] >= 0.98 and ranked[1][0] < 0.98
        if not exact_schema_match and abs(ranked[0][0] - ranked[1][0]) < 0.08:
            return [], "ambiguous"
    return [ranked[0][1]], "resolved"


def _merge_candidate_columns(*groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for group in groups:
        for entry in group:
            table_name = str(entry.get("table", "")).strip()
            column_name = str(entry.get("column", "")).strip()
            if not table_name or not column_name:
                continue
            key = (table_name, column_name)
            existing = merged.get(key)
            if not existing:
                merged[key] = dict(entry)
                continue
            existing["score"] = max(float(existing.get("score") or 0.0), float(entry.get("score") or 0.0))
            existing["matched_terms"] = list(dict.fromkeys(list(existing.get("matched_terms") or []) + list(entry.get("matched_terms") or [])))
            existing["is_measure"] = bool(existing.get("is_measure")) or bool(entry.get("is_measure"))
            existing["is_dimension"] = bool(existing.get("is_dimension")) or bool(entry.get("is_dimension"))
            existing["source"] = existing.get("source") if existing.get("source") == "vector" else entry.get("source", existing.get("source"))
    results = list(merged.values())
    results.sort(key=lambda item: (-float(item.get("score") or 0.0), str(item.get("table") or ""), str(item.get("column") or "")))
    return results


def _column_selection_from_retrieved_context(
    table_name: str,
    merged_columns: list[dict[str, Any]],
    join_paths: list[dict],
) -> list[dict[str, Any]]:
    selected = []
    for entry in merged_columns:
        if str(entry.get("table") or "") != table_name:
            continue
        selected.append(
            {
                "column": str(entry.get("column") or ""),
                "semantic_type": str(entry.get("semantic_type") or "unknown"),
                "core_semantic_type": str(entry.get("core_semantic_type") or entry.get("semantic_type") or "unknown"),
                "column_type": str(entry.get("column_type") or entry.get("type") or ""),
                "nullable": entry.get("nullable"),
                "confidence": round(min(max(float(entry.get("score") or 0.0), 0.45), 0.99), 2),
                "reason": "; ".join(
                    filter(
                        None,
                        [
                            f"retrieved from {entry.get('source')}" if entry.get("source") else "",
                            ", ".join(entry.get("matched_terms") or []),
                        ],
                    )
                ).strip("; "),
            }
        )
    existing = {entry.get("column") for entry in selected}
    for join_column in _selected_join_columns_for_table(table_name, {}, join_paths):
        if join_column.get("column") in existing:
            continue
        selected.append(join_column)
        existing.add(join_column.get("column"))
    selected.sort(key=lambda item: (-float(item.get("confidence") or 0.0), str(item.get("column") or "")))
    return selected[:8]


def _table_entries_from_retrieved_context(
    matched_tables: list[dict[str, Any]],
    merged_columns: list[dict[str, Any]],
    join_paths: list[dict],
    selected_table_names: list[str] | None = None,
) -> list[dict[str, Any]]:
    selected_table_names = list(selected_table_names or [])
    table_lookup = {
        str(entry.get("table", "")).strip(): dict(entry)
        for entry in matched_tables
        if str(entry.get("table", "")).strip()
    }
    entries = []
    ordered_table_names = selected_table_names or list(table_lookup.keys())
    for table_name in ordered_table_names:
        entry = table_lookup.get(table_name, {"table": table_name, "score": 0.55, "matched_terms": [], "source": "retrieved_context"})
        if not table_name:
            continue
        matched_terms = ", ".join(entry.get("matched_terms") or [])
        reason_parts = [f"retrieved from {entry.get('source')}"] if entry.get("source") else []
        if matched_terms:
            reason_parts.append(matched_terms)
        entries.append(
            {
                "table": table_name,
                "confidence": round(min(max(float(entry.get("score") or 0.0), 0.4), 0.99), 2),
                "reason": "; ".join(reason_parts) or "selected from retrieved context",
                "selected_columns": _column_selection_from_retrieved_context(table_name, merged_columns, join_paths),
            }
        )
    return entries


def _tables_from_column_candidates(candidates: list[dict[str, Any]]) -> list[str]:
    tables: list[str] = []
    for entry in candidates:
        table_name = str(entry.get("table", "")).strip()
        if table_name and table_name not in tables:
            tables.append(table_name)
    return tables


def _evidence_schema_slice(
    selected_table_names: list[str],
    selected_columns: list[dict[str, Any]],
    matched_relationships: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build a compatibility schema slice only from normalized runtime evidence."""
    schema = {
        table_name: {
            "columns": [],
            "primary_keys": [],
            "foreign_keys": [],
            "relationships": [],
        }
        for table_name in selected_table_names
        if table_name
    }
    seen_columns: set[tuple[str, str]] = set()
    for entry in selected_columns:
        table_name = str(entry.get("table") or "").strip()
        column_name = str(entry.get("column") or "").strip()
        key = (table_name, column_name)
        if not all(key) or key in seen_columns or table_name not in schema:
            continue
        seen_columns.add(key)
        schema[table_name]["columns"].append(
            {
                "name": column_name,
                "type": str(entry.get("column_type") or ""),
                "nullable": entry.get("nullable"),
                "semantic_type": str(entry.get("semantic_type") or "unknown"),
            }
        )
    for relationship in matched_relationships:
        from_table = str(relationship.get("from_table") or "").strip()
        to_table = str(relationship.get("to_table") or "").strip()
        if from_table not in schema or to_table not in schema:
            continue
        schema[from_table]["relationships"].append(dict(relationship))
    return schema


def _relationship_graph_from_evidence(
    selected_table_names: list[str],
    matched_relationships: list[dict[str, Any]],
) -> dict[str, Any]:
    graph = {
        table_name: {"outgoing": [], "incoming": []}
        for table_name in selected_table_names
        if table_name
    }
    for relationship in matched_relationships:
        from_table = str(relationship.get("from_table") or "").strip()
        to_table = str(relationship.get("to_table") or "").strip()
        if from_table not in graph or to_table not in graph:
            continue
        edge = {
            "from_table": from_table,
            "from_column": relationship.get("from_column"),
            "to_table": to_table,
            "to_column": relationship.get("to_column"),
            "source": relationship.get("relationship_source") or relationship.get("evidence_source") or relationship.get("source"),
            "confidence": relationship.get("confidence") or relationship.get("score"),
        }
        graph[from_table]["outgoing"].append(edge)
        graph[to_table]["incoming"].append(edge)
    return graph


def _build_query_context_from_retrieved_context(
    question: str,
    normalized_question: str,
    intent: dict[str, Any],
    retrieved_context: dict[str, Any],
    knowledge_base: dict[str, Any],
) -> dict:
    planner_intent = _planner_intent_from_structured_intent(intent)
    dimension = _structured_dimension(intent)
    sorting = _structured_sorting(intent, planner_intent)
    limit = (intent or {}).get("limit")
    requested_metrics = list((intent or {}).get("requested_metrics") or [])
    requested_dimensions = list((intent or {}).get("requested_dimensions") or [])
    requested_filters = list((intent or {}).get("requested_filters") or [])
    intent_type = str((intent or {}).get("intent_type") or "").strip().lower()
    aggregate_function = str((intent or {}).get("aggregate_function") or "").strip().lower()
    ranking_mode_hint = str(
        ((intent or {}).get("ranking_diagnostics") or {}).get("mode_hint") or ""
    ).strip()
    requires_metric_evidence = aggregate_function != "count" and bool(
        (intent or {}).get("needs_aggregation")
        or aggregate_function
        or intent_type in {"aggregate", "grouped_summary"}
        or (intent_type == "ranking" and ranking_mode_hint == "grouped_aggregate")
    )
    required_metric_phrases = requested_metrics if requires_metric_evidence else []

    matched_tables = [
        dict(entry)
        for entry in (retrieved_context.get("matched_tables") or [])
        if str(entry.get("table", "")).strip()
    ]
    matched_columns = [
        dict(entry)
        for entry in (retrieved_context.get("matched_columns") or [])
        if str(entry.get("table", "")).strip()
    ]
    measure_candidates = [
        _ensure_candidate_reason(dict(entry), role="metric")
        for entry in (retrieved_context.get("measure_candidates") or [])
        if str(entry.get("table", "")).strip()
    ]
    dimension_candidates = [
        _ensure_candidate_reason(dict(entry), role="dimension")
        for entry in (retrieved_context.get("dimension_candidates") or [])
        if str(entry.get("table", "")).strip()
    ]
    filter_candidates = [
        _ensure_candidate_reason(dict(entry), role="filter")
        for entry in (retrieved_context.get("filter_candidates") or [])
        if str(entry.get("table", "")).strip()
    ]
    join_paths = [
        dict(path)
        for path in (retrieved_context.get("possible_join_paths") or [])
        if str(path.get("from_table", "")).strip() and str(path.get("to_table", "")).strip()
    ]
    matched_relationships = [
        dict(entry)
        for entry in (retrieved_context.get("matched_relationships") or [])
        if str(entry.get("from_table", "")).strip() and str(entry.get("to_table", "")).strip()
    ]

    merged_columns = _merge_candidate_columns(matched_columns, measure_candidates, dimension_candidates, filter_candidates)

    selected_table_names: list[str] = []
    for table_name in _tables_from_column_candidates(dimension_candidates + measure_candidates + filter_candidates):
        if table_name not in selected_table_names:
            selected_table_names.append(table_name)
    for entry in matched_tables:
        table_name = str(entry.get("table", "")).strip()
        if table_name and table_name not in selected_table_names:
            selected_table_names.append(table_name)

    simple_primary_table = _primary_table_for_simple_question_from_entries(
        question,
        {"intent": planner_intent, "dimension": dimension, "grouping": [dimension] if dimension else [], "filters": requested_filters},
        matched_tables,
    )
    if simple_primary_table:
        selected_table_names = [simple_primary_table]
        join_paths = []
    else:
        for table_name in _tables_from_join_paths(join_paths):
            if table_name not in selected_table_names:
                selected_table_names.append(table_name)

    selected_tables = _table_entries_from_retrieved_context(
        [entry for entry in matched_tables if entry.get("table") in selected_table_names],
        merged_columns,
        join_paths,
        selected_table_names=selected_table_names,
    )
    selected_columns = [
        {"table": entry["table"], **column_entry}
        for entry in selected_tables
        for column_entry in entry.get("selected_columns", [])
    ]
    effective_measure_candidates = list(measure_candidates or []) or _fallback_metric_candidates_from_selected_columns(
        selected_columns,
        question,
    )
    filters = _structured_filter_entries(
        filter_candidates,
        requested_filters,
        list((intent or {}).get("structured_filters") or []),
    )
    confidence = float(retrieved_context.get("confidence") or 0.0)
    if not selected_table_names:
        confidence = min(confidence, 0.35)
    elif len(selected_table_names) == 1 and len(selected_columns) >= 1:
        confidence = max(confidence, 0.72)

    plan = {
        "question": question,
        "intent": planner_intent,
        "metric": None,
        "dimension": dimension,
        "filters": filters,
        "date_range": None,
        "grouping": [value for value in requested_dimensions if str(value or "").strip()],
        "sorting": sorting,
        "limit": limit,
        "question_terms": list(retrieved_context.get("query_terms") or _content_terms(question)),
        "semantic_hints": set(),
        "matched_glossary_terms": [
            str(entry.get("term", "")).strip()
            for entry in (retrieved_context.get("matched_glossary_terms") or [])
            if str(entry.get("term", "")).strip()
        ],
        "requested_metrics": requested_metrics,
        "requested_dimensions": requested_dimensions,
        "requested_filters": requested_filters,
        "unresolved_metrics": required_metric_phrases if required_metric_phrases and not effective_measure_candidates else [],
        "evidence_sources": list(retrieved_context.get("retrieval_sources") or []),
    }

    reduced_kb = _evidence_schema_slice(selected_table_names, selected_columns, matched_relationships)
    warnings = []
    if confidence < 0.5:
        warnings.append("Retrieved context is weak; planner confidence is low.")
    if required_metric_phrases and not effective_measure_candidates:
        warnings.append("Requested metric remains unresolved in dynamic context.")

    legacy_query_shape = _derive_complex_query_shape(
        plan,
        intent,
        selected_tables,
        effective_measure_candidates,
        dimension_candidates,
        filters,
        join_paths,
        retrieved_context.get("formula_evidence") or [],
    )

    missing_evidence_flags = _detect_missing_evidence(
        plan,
        selected_tables,
        selected_columns,
        join_paths,
        required_metric_phrases,
        requested_dimensions,
        requested_filters,
        effective_measure_candidates,
        dimension_candidates,
        filters,
        retrieved_context.get("formula_evidence") or [],
        legacy_query_shape,
    )
    if ranking_mode_hint == "row":
        missing_evidence_flags["missing_metric"] = False
        missing_evidence_flags["missing_formula_evidence"] = False
    intent_missing_phrases = set((intent or {}).get("missing_phrases") or [])
    if "target_entity_phrase" in intent_missing_phrases:
        missing_evidence_flags["missing_table"] = True
    if "metric_phrase" in intent_missing_phrases:
        missing_evidence_flags["missing_metric"] = True
    if "grouping_phrase" in intent_missing_phrases:
        missing_evidence_flags["missing_dimension"] = True
    if (intent or {}).get("unsupported_constructs"):
        missing_evidence_flags["unsupported_intent"] = True

    legacy_route_recommendation = _compute_route_recommendation(
        plan,
        missing_evidence_flags,
        confidence,
        selected_tables,
        legacy_query_shape,
    )

    complex_sql_plan = _build_complex_sql_plan(
        intent,
        plan,
        selected_tables,
        selected_columns,
        effective_measure_candidates,
        dimension_candidates,
        filters,
        join_paths,
        retrieved_context.get("formula_evidence") or [],
        missing_evidence_flags,
        legacy_route_recommendation,
        legacy_query_shape,
    )

    debug_trace_details = _build_debug_trace(
        question,
        plan,
        selected_tables,
        selected_columns,
        join_paths,
        missing_evidence_flags,
        confidence,
        legacy_route_recommendation,
    )

    normalized_result = _normalize_planner_output(
        question=question,
        normalized_question=normalized_question,
        intent=intent,
        retrieved_context=retrieved_context,
        plan=plan,
        selected_tables=selected_tables,
        selected_columns=selected_columns,
        selected_table_names=selected_table_names,
        selected_knowledge_base=reduced_kb,
        knowledge_base={},
        warnings=warnings,
        confidence=confidence,
        vector_results={},
        vector_used=False,
        join_paths=join_paths,
        fk_relationships=_relationship_graph_from_evidence(selected_table_names, matched_relationships),
        matched_relationships=matched_relationships,
        measure_candidates=effective_measure_candidates,
        dimension_candidates=dimension_candidates,
        filter_candidates=filter_candidates,
        formula_evidence=list(retrieved_context.get("formula_evidence") or []),
        missing_evidence_flags=missing_evidence_flags,
        complex_sql_plan=complex_sql_plan,
        legacy_route_recommendation=legacy_route_recommendation,
        debug_trace_details=debug_trace_details,
    )
    normalized_result = _apply_implicit_sample_filter_contract(normalized_result, knowledge_base)
    joined_aggregate_result = _apply_joined_aggregate_contract(normalized_result, knowledge_base)
    if joined_aggregate_result is not normalized_result:
        return joined_aggregate_result
    return _apply_join_lookup_contract(normalized_result, knowledge_base)


_JOIN_DECISION_NODES = (
    "unsafe_check",
    "table_scope",
    "requested_fields",
    "join_need",
    "relationship_graph_lookup",
    "safe_join_path",
    "ambiguity_check",
    "where",
    "selected_output_columns",
    "route",
)


def _join_failure_context(
    context: dict[str, Any],
    *,
    blocked_node: str,
    reason: str,
    query_shape: str = "joined_lookup",
    resolved_nodes: set[str] | None = None,
) -> dict[str, Any]:
    resolved = set(resolved_nodes or set())
    decision_path = []
    for node_name in _JOIN_DECISION_NODES:
        if node_name in resolved:
            status = "resolved"
            node_reason = f"{node_name.replace('_', ' ')} resolved"
        elif node_name == blocked_node:
            status = "blocked"
            node_reason = reason
        else:
            status = "not_required" if node_name == "where" else "blocked"
            node_reason = "not evaluated because an earlier join decision was blocked"
        decision_path.append({"node": node_name, "status": status, "reason": node_reason})

    failed = dict(context)
    failed.update(
        {
            "query_shape": query_shape,
            "route": "cannot_plan_safely",
            "route_recommendation": "cannot_plan_safely",
            "route_reason": reason,
            "planner_reason": reason,
            "can_plan": False,
            "selected_join_path": None,
            "selected_output_columns": [],
            "clause_plan": {
                "clause_shape": "joined_lookup",
                "selected_join_path": None,
                "limit": failed.get("limit"),
                "requires": {
                    "aggregate": False,
                    "metric": False,
                    "dimension": False,
                    "where": bool((failed.get("intent") or {}).get("structured_filters")),
                    "having": False,
                    "order_by": False,
                    "limit": True,
                    "join": True,
                    "requested_fields": True,
                    "selected_output_columns": True,
                },
                "decision_path": decision_path,
            },
        }
    )
    failed["missing_evidence"] = list(dict.fromkeys([*(failed.get("missing_evidence") or []), blocked_node]))
    return failed


def _table_phrase_score(phrase: str, table_name: str) -> float:
    phrase_tokens = {_singularize_token(token) for token in _tokenize(phrase)}
    table_tokens = {_singularize_token(token) for token in _tokenize(table_name)}
    if not phrase_tokens or not table_tokens:
        return 0.0
    if phrase_tokens == table_tokens:
        return 1.0
    if phrase_tokens <= table_tokens or table_tokens <= phrase_tokens:
        return 0.82
    return round((len(phrase_tokens & table_tokens) / len(phrase_tokens)) * 0.6, 4)


def _resolve_join_table(
    phrase: str,
    knowledge_base: dict[str, Any],
    retrieved_tables: list[dict[str, Any]] | None = None,
) -> tuple[str | None, str]:
    requested_tokens = {_singularize_token(token) for token in _tokenize(phrase)}
    retrieval_scores: dict[str, float] = {}
    for candidate in retrieved_tables or []:
        table_name = str(candidate.get("table") or "")
        candidate_terms = [table_name, *(candidate.get("matched_terms") or [])]
        if any(
            {_singularize_token(token) for token in _tokenize(term)} == requested_tokens
            for term in candidate_terms
            if _tokenize(term)
        ):
            retrieval_scores[table_name] = max(
                retrieval_scores.get(table_name, 0.0),
                min(float(candidate.get("score") or 0.0), 0.96),
            )
    ranked = sorted(
        (
            (max(_table_phrase_score(phrase, table_name), retrieval_scores.get(table_name, 0.0)), table_name)
            for table_name in knowledge_base
        ),
        key=lambda item: (-item[0], item[1]),
    )
    ranked = [item for item in ranked if item[0] > 0]
    if not ranked:
        return None, "missing"
    if len(ranked) > 1 and abs(ranked[0][0] - ranked[1][0]) < 0.08:
        return None, "ambiguous"
    return ranked[0][1], "resolved"


def _column_phrase_score(
    phrase: str,
    table_name: str,
    column: dict[str, Any],
    retrieved_candidates: list[dict[str, Any]],
) -> float:
    phrase_tokens = {_singularize_token(token) for token in _tokenize(phrase)}
    column_name = str(column.get("name") or "")
    column_tokens = {_singularize_token(token) for token in _tokenize(column_name)}
    qualified_tokens = {
        _singularize_token(token) for token in _tokenize(f"{table_name} {column_name}")
    }
    if not phrase_tokens or not column_tokens:
        return 0.0
    score = 0.0
    if phrase_tokens == qualified_tokens:
        score = 1.0
    elif phrase_tokens == column_tokens:
        score = 0.9
    elif phrase_tokens <= qualified_tokens:
        score = 0.78
    elif phrase_tokens & qualified_tokens:
        score = (len(phrase_tokens & qualified_tokens) / len(phrase_tokens)) * 0.55

    for candidate in retrieved_candidates:
        if (
            str(candidate.get("table") or "") != table_name
            or str(candidate.get("column") or "") != column_name
        ):
            continue
        candidate_texts = [column_name, *(candidate.get("matched_terms") or [])]
        candidate_token_sets = [
            {_singularize_token(token) for token in _tokenize(text)}
            for text in candidate_texts
            if _tokenize(text)
        ]
        if any(tokens == phrase_tokens for tokens in candidate_token_sets):
            score = max(score, 0.78)
        elif any(phrase_tokens <= tokens for tokens in candidate_token_sets):
            score = max(score, 0.72)
    return round(score, 4)


def _resolve_join_output_field(
    phrase: str,
    knowledge_base: dict[str, Any],
    retrieved_candidates: list[dict[str, Any]],
    allowed_tables: set[str] | None = None,
) -> tuple[dict[str, Any] | None, str]:
    ranked = []
    for table_name, table_data in knowledge_base.items():
        if allowed_tables is not None and table_name not in allowed_tables:
            continue
        for column in table_data.get("columns", []) or []:
            column_name = str(column.get("name") or "")
            if not column_name:
                continue
            score = _column_phrase_score(phrase, table_name, column, retrieved_candidates)
            if score > 0:
                ranked.append((score, table_name, column_name))
    ranked.sort(key=lambda item: (-item[0], item[1], item[2]))
    if not ranked or ranked[0][0] < 0.7:
        return None, "missing"
    if len(ranked) > 1 and abs(ranked[0][0] - ranked[1][0]) < 0.08:
        return None, "ambiguous"
    score, table_name, column_name = ranked[0]
    return {
        "table": table_name,
        "column": column_name,
        "score": score,
        "source": "schema_and_retrieval_evidence",
    }, "resolved"


def _qualified_output(table_name: str, column_name: str, source: str) -> dict[str, Any]:
    return {
        "table": table_name,
        "column": column_name,
        "expression": f"{table_name}.{column_name}",
        "alias": f"{table_name}__{column_name}",
        "source": source,
    }


def _all_table_outputs(table_name: str, knowledge_base: dict[str, Any], source: str) -> list[dict[str, Any]]:
    return [
        _qualified_output(table_name, str(column.get("name") or ""), source)
        for column in knowledge_base.get(table_name, {}).get("columns", []) or []
        if str(column.get("name") or "")
    ]


_JOINED_AGGREGATE_DECISION_NODES = (
    "unsafe_check",
    "table_scope",
    "query_shape",
    "aggregate",
    "metric",
    "dimension",
    "join_need",
    "relationship_graph_lookup",
    "safe_join_path",
    "ambiguity_check",
    "where",
    "having",
    "order_by",
    "limit",
    "clause_shape",
    "route",
)

_EXPLICIT_UNSUPPORTED_JOIN_RE = re.compile(
    r"\b(?:left|right|full|cross|natural)(?:\s+outer)?\s+join\b|\bjoin\s+using\b|\bvia\b",
    re.IGNORECASE,
)

_GENERIC_ROLE_TERMS = {
    "amount",
    "value",
    "status",
    "type",
    "category",
    "total",
}


def _joined_aggregate_failure_context(
    context: dict[str, Any],
    *,
    blocked_node: str,
    reason: str,
    resolved_nodes: set[str] | None = None,
) -> dict[str, Any]:
    resolved = set(resolved_nodes or set())
    decision_path = []
    for node_name in _JOINED_AGGREGATE_DECISION_NODES:
        if node_name in resolved:
            status = "resolved"
            node_reason = f"{node_name.replace('_', ' ')} resolved"
        elif node_name == blocked_node:
            status = "blocked"
            node_reason = reason
        elif node_name in {"where", "having", "order_by", "limit"}:
            status = "not_required"
            node_reason = "clause was not evaluated because an earlier decision was blocked"
        else:
            status = "blocked"
            node_reason = "not evaluated because an earlier joined aggregate decision was blocked"
        decision_path.append({"node": node_name, "status": status, "reason": node_reason})

    failed = dict(context)
    failed.update(
        {
            "query_shape": "joined_aggregate",
            "route": "cannot_plan_safely",
            "route_recommendation": "cannot_plan_safely",
            "route_reason": reason,
            "planner_reason": reason,
            "can_plan": False,
            "selected_join_path": None,
            "selected_relationship_path": None,
            "selected_output_columns": [],
            "clause_plan": {
                "clause_shape": "unsupported",
                "selected_join_path": None,
                "selected_order_by": {},
                "limit": failed.get("limit"),
                "requires": {
                    "aggregate": True,
                    "metric": True,
                    "dimension": True,
                    "where": bool((failed.get("intent") or {}).get("structured_filters")),
                    "having": bool((failed.get("intent") or {}).get("structured_having")),
                    "order_by": bool((failed.get("intent") or {}).get("requested_sort")),
                    "limit": (failed.get("intent") or {}).get("limit") is not None,
                    "join": True,
                },
                "decision_path": decision_path,
            },
        }
    )
    failed["missing_evidence"] = list(
        dict.fromkeys([*(failed.get("missing_evidence") or []), blocked_node])
    )
    failed["ambiguities"] = list(
        dict.fromkeys([*(failed.get("ambiguities") or []), blocked_node])
    )
    return failed


def _joined_aggregate_filter_contract(
    intent: dict[str, Any],
    filter_candidates: list[dict[str, Any]],
    allowed_tables: set[str],
) -> tuple[list[dict[str, Any]], str]:
    structured_filters = [
        dict(entry)
        for entry in (intent.get("structured_filters") or [])
        if isinstance(entry, dict)
    ]
    if not structured_filters:
        return [], ""

    selected: list[dict[str, Any]] = []
    for clause in structured_filters:
        field_phrase = str(clause.get("field_phrase") or clause.get("field") or "").strip()
        resolved, status = _resolve_role_candidate(
            field_phrase,
            filter_candidates,
            allowed_tables=allowed_tables,
        )
        if status != "resolved" or len(resolved) != 1:
            return [], f"joined WHERE field evidence is {status}"
        candidate = dict(resolved[0])
        candidate.update(
            {
                "field_phrase": field_phrase,
                "raw_phrase": str(clause.get("raw_phrase") or ""),
                "operator": str(clause.get("operator") or ""),
                "value": clause.get("value"),
                "value_phrase": clause.get("value_phrase"),
                "values": list(clause.get("values") or []),
                "conjunction": clause.get("conjunction"),
            }
        )
        selected.append(candidate)
    return selected, ""


def _sample_value_matches(value: str, sample: Any) -> bool:
    return _humanize(str(value or "")) == _humanize(str(sample or ""))


def _build_sample_value_filter(
    *,
    value_phrase: str,
    knowledge_base: dict[str, Any],
    allowed_tables: set[str],
    owner_table: str | None = None,
    source: str,
) -> tuple[dict[str, Any] | None, str]:
    value_phrase = str(value_phrase or "").strip()
    if not value_phrase:
        return None, "missing"
    matches: list[dict[str, Any]] = []
    tables = {owner_table} if owner_table else set(allowed_tables)
    for table_name in tables:
        if table_name not in allowed_tables:
            continue
        for column in knowledge_base.get(table_name, {}).get("columns", []) or []:
            column_name = str(column.get("name") or "").strip()
            if not column_name:
                continue
            matched_sample = None
            for sample in column_sample_values(column):
                if _sample_value_matches(value_phrase, sample):
                    matched_sample = sample
                    break
            if matched_sample is None:
                continue
            matches.append(
                {
                    "table": table_name,
                    "column": column_name,
                    "field_phrase": column_name,
                    "raw_phrase": value_phrase,
                    "operator": "eq",
                    "value": matched_sample,
                    "value_phrase": value_phrase,
                    "values": [matched_sample],
                    "conjunction": "",
                    "source": source,
                }
            )
    if len(matches) == 1:
        return matches[0], "resolved"
    if len(matches) > 1:
        return None, "ambiguous"
    return None, "missing"


def _source_scope_as_filter(
    source_phrase: str,
    knowledge_base: dict[str, Any],
    allowed_tables: set[str],
) -> tuple[dict[str, Any] | None, str]:
    phrase_tokens = {_singularize_token(token) for token in _tokenize(source_phrase)}
    owner_matches: list[tuple[float, str, set[str]]] = []
    for table_name in allowed_tables:
        table_tokens = {_singularize_token(token) for token in _tokenize(table_name)}
        if not table_tokens or not table_tokens <= phrase_tokens:
            continue
        score = _table_phrase_score(source_phrase, table_name)
        if score > 0:
            owner_matches.append((score, table_name, table_tokens))
    owner_matches.sort(key=lambda item: (-item[0], item[1]))
    if not owner_matches:
        return None, "missing"
    if len(owner_matches) > 1 and abs(owner_matches[0][0] - owner_matches[1][0]) < 0.08:
        return None, "ambiguous"
    _, owner_table, owner_tokens = owner_matches[0]
    value_tokens = [token for token in _tokenize(source_phrase) if _singularize_token(token) not in owner_tokens]
    value_phrase = " ".join(value_tokens).strip()
    return _build_sample_value_filter(
        value_phrase=value_phrase,
        knowledge_base=knowledge_base,
        allowed_tables=allowed_tables,
        owner_table=owner_table,
        source="source_scope_value_filter",
    )


def _apply_implicit_sample_filter_contract(
    context: dict[str, Any],
    knowledge_base: dict[str, Any],
) -> dict[str, Any]:
    intent = context.get("intent") if isinstance(context.get("intent"), dict) else {}
    if str(context.get("query_shape") or "") != "single_table_list":
        return context
    if intent.get("structured_filters") or intent.get("requested_filters") or context.get("selected_filters"):
        return context
    if str(intent.get("intent_type") or "").strip().lower() not in {"list", "filter"}:
        return context
    selected_table_names = [
        str(value).strip()
        for value in (context.get("selected_table_names") or [])
        if str(value).strip()
    ]
    if len(selected_table_names) != 1:
        return context
    phrase = str(intent.get("target_entity_phrase") or "").strip()
    if not phrase:
        return context
    implicit_filter, status = _source_scope_as_filter(
        phrase,
        knowledge_base,
        {selected_table_names[0]},
    )
    if status != "resolved" or implicit_filter is None:
        return context

    planned = dict(context)
    planned_intent = dict(intent)
    planned_intent["structured_filters"] = []
    planned_intent["requested_filters"] = [
        str(implicit_filter.get("raw_phrase") or implicit_filter.get("value_phrase") or "").strip()
    ]
    planned.update(
        {
            "intent": planned_intent,
            "query_shape": "filtered_query",
            "selected_filters": [implicit_filter],
            "filter_candidates": _merge_candidate_columns(
                [implicit_filter],
                [entry for entry in (context.get("filter_candidates") or []) if isinstance(entry, dict)],
            ),
            "selected_columns": _merge_candidate_columns(
                [entry for entry in (context.get("selected_columns") or []) if isinstance(entry, dict)],
                [implicit_filter],
            ),
            "required_evidence": ["selected_table", "filter_candidate"],
        }
    )
    planned["plan"] = {**dict(planned.get("plan") or {}), "filters": [implicit_filter]}
    clause_plan = dict(planned.get("clause_plan") or {})
    clause_plan["clause_shape"] = "where_only"
    requires = dict(clause_plan.get("requires") or {})
    requires["where"] = True
    clause_plan["requires"] = requires
    decision_path = []
    for entry in clause_plan.get("decision_path") or []:
        node = dict(entry)
        if node.get("node") == "query_shape":
            node["reason"] = "resolved clause shape 'where_only'"
        elif node.get("node") == "where":
            node["status"] = "resolved"
            node["reason"] = "row-level sample value filter resolved from KB profile evidence"
        elif node.get("node") == "clause_shape":
            node["reason"] = "final clause shape 'where_only' is complete"
        decision_path.append(node)
    clause_plan["decision_path"] = decision_path
    planned["clause_plan"] = clause_plan
    return planned


def _resolve_metric_with_modifier(
    metric_phrase: str,
    metric_candidates: list[dict[str, Any]],
) -> tuple[dict[str, Any] | None, str | None, str]:
    tokens = _tokenize(metric_phrase)
    if len(tokens) < 2:
        return None, None, "missing"
    for split_at in range(1, len(tokens)):
        modifier_phrase = " ".join(tokens[:split_at]).strip()
        residual_phrase = " ".join(tokens[split_at:]).strip()
        resolved, status = _resolve_role_candidate(residual_phrase, metric_candidates)
        if status == "resolved" and len(resolved) == 1:
            return dict(resolved[0]), modifier_phrase, "resolved"
        if status == "ambiguous":
            return None, None, "ambiguous"
    return None, None, "missing"


def _resolve_owned_monetary_metric_from_schema(
    metric_phrase: str,
    knowledge_base: dict[str, Any],
) -> tuple[dict[str, Any] | None, str]:
    phrase_tokens = {_singularize_token(token) for token in _tokenize(metric_phrase)}
    if len(phrase_tokens) < 2:
        return None, "missing"
    owner_matches: list[tuple[float, str]] = []
    for table_name in knowledge_base:
        table_tokens = {_singularize_token(token) for token in _tokenize(table_name)}
        if not table_tokens:
            continue
        overlap = phrase_tokens & table_tokens
        if not overlap:
            continue
        score = len(overlap) / max(len(table_tokens), 1)
        if score > 0:
            owner_matches.append((score, table_name))
    owner_matches.sort(key=lambda item: (-item[0], item[1]))
    if not owner_matches:
        return None, "missing"
    if len(owner_matches) > 1 and abs(owner_matches[0][0] - owner_matches[1][0]) < 0.08:
        return None, "ambiguous"
    owner_table = owner_matches[0][1]
    monetary_candidates: list[dict[str, Any]] = []
    for column in knowledge_base.get(owner_table, {}).get("columns", []) or []:
        column_name = str(column.get("name") or "").strip()
        if not column_name:
            continue
        semantic_type = str(column.get("semantic_type") or "").strip().lower()
        data_type = str(column.get("type") or "").strip().lower()
        planner_roles = column.get("planner_roles") if isinstance(column.get("planner_roles"), dict) else {}
        is_measure = bool(
            column.get("is_measure")
            or planner_roles.get("measure_candidate")
            or semantic_type in {"money", "numeric_candidate", "quantity", "percentage"}
        )
        is_monetary = semantic_type == "money" or any(
            token in data_type for token in ("decimal", "numeric", "money")
        )
        if not is_measure or not is_monetary:
            continue
        monetary_candidates.append(
            {
                "table": owner_table,
                "column": column_name,
                "semantic_type": semantic_type,
                "core_semantic_type": semantic_type,
                "data_type": data_type,
                "is_measure": True,
                "is_dimension": False,
                "is_date": False,
                "score": 0.74,
                "matched_terms": [metric_phrase],
                "evidence_sources": ["schema_owner_phrase", "kb_numeric_profile"],
                "source": "kb_schema_profile",
                "reason": "unique owner-table monetary measure resolved from schema/profile evidence",
            }
        )
    if len(monetary_candidates) == 1:
        return monetary_candidates[0], "resolved"
    if len(monetary_candidates) > 1:
        return None, "ambiguous"
    return None, "missing"


def _resolve_entity_display_dimension(
    entity_phrase: str,
    dimension_candidates: list[dict[str, Any]],
    knowledge_base: dict[str, Any],
) -> tuple[list[dict[str, Any]], str]:
    table_name, table_status = _resolve_join_table(entity_phrase, knowledge_base, [])
    if table_status != "resolved" or not table_name:
        return [], table_status
    display_candidates: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for candidate in dimension_candidates:
        if str(candidate.get("table") or "") != table_name:
            continue
        column_name = str(candidate.get("column") or "").strip()
        if not column_name:
            continue
        semantic_type = str(candidate.get("semantic_type") or "").strip().lower()
        column_tokens = {_singularize_token(token) for token in _tokenize(column_name)}
        is_display = semantic_type == "name" or "name" in column_tokens
        if not is_display:
            continue
        signature = (table_name, column_name)
        if signature in seen:
            continue
        seen.add(signature)
        display_candidates.append(dict(candidate))
    if len(display_candidates) == 1:
        return display_candidates, "resolved"
    if len(display_candidates) > 1:
        return [], "ambiguous"
    return [], "missing"


def _resolve_count_base_table(
    source_phrase: str,
    dimension_table: str,
    knowledge_base: dict[str, Any],
) -> tuple[str | None, str]:
    explicit_base, base_status = _resolve_join_table(source_phrase, knowledge_base, [])
    if base_status == "resolved" and explicit_base:
        return explicit_base, "resolved"
    if base_status != "ambiguous":
        return explicit_base, base_status

    ranked = sorted(
        (
            (_table_phrase_score(source_phrase, table_name), table_name)
            for table_name in knowledge_base
            if table_name != dimension_table
        ),
        key=lambda item: (-item[0], item[1]),
    )
    ranked = [item for item in ranked if item[0] > 0]
    if not ranked:
        return None, "missing"
    top_score = ranked[0][0]
    tied_candidates = [table_name for score, table_name in ranked if abs(score - top_score) < 0.08]
    graph = build_relationship_graph(knowledge_base, infer_relationships=False)
    graph_backed = [
        table_name
        for table_name in tied_candidates
        if len(find_safe_direct_join_relationships(graph, table_name, dimension_table)) == 1
    ]
    if len(graph_backed) == 1:
        return graph_backed[0], "resolved"
    if len(graph_backed) > 1:
        return None, "ambiguous"
    return None, "ambiguous"


def _apply_joined_aggregate_contract(
    context: dict[str, Any],
    knowledge_base: dict[str, Any],
) -> dict[str, Any]:
    intent = context.get("intent") if isinstance(context.get("intent"), dict) else {}
    if intent.get("unsafe"):
        return context

    intent_type = str(intent.get("intent_type") or "").strip().lower()
    requested_dimensions = [
        str(value).strip() for value in (intent.get("requested_dimensions") or []) if str(value).strip()
    ]
    requested_metrics = [
        str(value).strip() for value in (intent.get("requested_metrics") or []) if str(value).strip()
    ]
    ranking_candidate = intent_type == "ranking" and bool(intent.get("metric_phrase"))
    grouped_candidate = bool(
        intent.get("needs_grouping")
        and (intent.get("needs_aggregation") or intent.get("aggregate_function"))
        and requested_dimensions
    )
    if not grouped_candidate and not ranking_candidate:
        return context
    if str(context.get("query_shape") or "") == "multi_metric_aggregate":
        return context

    question = str(context.get("normalized_question") or context.get("plan", {}).get("question") or "")
    if _EXPLICIT_UNSUPPORTED_JOIN_RE.search(question):
        return _joined_aggregate_failure_context(
            context,
            blocked_node="query_shape",
            reason="explicit non-INNER or multi-hop join wording is not supported",
            resolved_nodes={"unsafe_check", "table_scope"},
        )
    if context.get("formula_evidence"):
        return _joined_aggregate_failure_context(
            context,
            blocked_node="query_shape",
            reason="formulas are not supported for deterministic joined aggregates",
            resolved_nodes={"unsafe_check", "table_scope"},
        )
    if intent.get("having_metric_conflict") or intent.get("having_aggregate_conflict"):
        return _joined_aggregate_failure_context(
            context,
            blocked_node="having",
            reason="HAVING must use the selected output aggregate and metric",
            resolved_nodes=set(_JOINED_AGGREGATE_DECISION_NODES[:11]),
        )

    metric_phrase = str(intent.get("metric_phrase") or next(iter(requested_metrics), "")).strip()
    dimension_phrase = str(
        next(iter(requested_dimensions), "")
        or (intent.get("target_entity_phrase") if ranking_candidate else "")
        or ""
    ).strip()
    if re.search(r"\b(?:and|,)\b", metric_phrase, re.IGNORECASE):
        return _joined_aggregate_failure_context(
            context,
            blocked_node="metric",
            reason="joined aggregates support exactly one metric",
            resolved_nodes={"unsafe_check", "table_scope", "query_shape", "aggregate"},
        )
    if re.search(r"\b(?:and|,)\b", dimension_phrase, re.IGNORECASE):
        return _joined_aggregate_failure_context(
            context,
            blocked_node="dimension",
            reason="joined aggregates support exactly one grouping dimension",
            resolved_nodes={"unsafe_check", "table_scope", "query_shape", "aggregate", "metric"},
        )

    aggregate_function = str(intent.get("aggregate_function") or "").strip().lower()
    if not aggregate_function and ranking_candidate and _normalize(metric_phrase).startswith("total "):
        aggregate_function = "sum"
    if aggregate_function not in {"count", "sum", "avg", "min", "max"}:
        return context

    retrieved = context.get("retrieved_context") if isinstance(context.get("retrieved_context"), dict) else {}
    metric_candidates = [
        dict(entry)
        for entry in (retrieved.get("measure_candidates") or context.get("metric_candidates") or [])
        if isinstance(entry, dict)
    ]
    dimension_candidates = [
        dict(entry)
        for entry in (retrieved.get("dimension_candidates") or context.get("dimension_candidates") or [])
        if isinstance(entry, dict)
    ]
    filter_candidates = [
        dict(entry)
        for entry in (retrieved.get("filter_candidates") or context.get("filter_candidates") or [])
        if isinstance(entry, dict)
    ]
    metric: dict[str, Any] | None = None
    modifier_filter_phrase: str | None = None
    if aggregate_function != "count":
        resolved_metrics, metric_status = _resolve_role_candidate(metric_phrase, metric_candidates)
        if metric_status != "resolved" or len(resolved_metrics) != 1:
            modifier_metric, modifier_phrase, modifier_status = _resolve_metric_with_modifier(
                metric_phrase,
                metric_candidates,
            )
            if modifier_status == "resolved" and modifier_metric is not None:
                metric = modifier_metric
                aggregate_words = {"total", "sum", "average", "avg", "mean", "maximum", "max", "minimum", "min"}
                if _normalize(modifier_phrase or "") not in aggregate_words:
                    modifier_filter_phrase = modifier_phrase
            else:
                schema_metric, schema_metric_status = _resolve_owned_monetary_metric_from_schema(
                    metric_phrase,
                    knowledge_base,
                )
                if schema_metric_status == "resolved" and schema_metric is not None:
                    metric = schema_metric
                else:
                    return _joined_aggregate_failure_context(
                        context,
                        blocked_node="metric",
                        reason=f"joined aggregate metric evidence is {schema_metric_status if metric_status == 'missing' else metric_status}",
                        resolved_nodes={"unsafe_check", "table_scope", "query_shape", "aggregate"},
                    )
        else:
            metric = dict(resolved_metrics[0])

    resolved_dimensions, dimension_status = _resolve_role_candidate(
        dimension_phrase,
        dimension_candidates,
    )
    if dimension_status != "resolved" or len(resolved_dimensions) != 1:
        display_dimensions, display_status = _resolve_entity_display_dimension(
            dimension_phrase,
            dimension_candidates,
            knowledge_base,
        )
        if display_status == "resolved" and len(display_dimensions) == 1:
            resolved_dimensions = display_dimensions
        else:
            if dimension_status == "missing" and display_status == "missing":
                return context
            return _joined_aggregate_failure_context(
                context,
                blocked_node="dimension",
                reason=f"joined aggregate dimension evidence is {display_status if dimension_status == 'missing' else dimension_status}",
                resolved_nodes={"unsafe_check", "table_scope", "query_shape", "aggregate", "metric"},
            )
    dimension = dict(resolved_dimensions[0])
    dimension_table = str(dimension.get("table") or "").strip()
    dimension_column = str(dimension.get("column") or "").strip()

    source_phrase = str(next(iter(intent.get("source_scope") or []), "")).strip()
    if aggregate_function == "count" and not source_phrase:
        source_phrase = str(intent.get("target_entity_phrase") or "").strip()
    base_table = str((metric or {}).get("table") or "").strip()
    deferred_source_filter_phrase: str | None = None
    if source_phrase:
        if aggregate_function == "count":
            explicit_base, base_status = _resolve_count_base_table(
                source_phrase,
                dimension_table,
                knowledge_base,
            )
        else:
            explicit_base, base_status = _resolve_join_table(source_phrase, knowledge_base, [])
        if base_status != "resolved" or explicit_base is None:
            return _joined_aggregate_failure_context(
                context,
                blocked_node="table_scope",
                reason=f"joined aggregate base table evidence is {base_status}",
                resolved_nodes={"unsafe_check"},
            )
        if base_table and base_table != explicit_base:
            deferred_source_filter_phrase = source_phrase
        else:
            base_table = explicit_base
    if not base_table:
        return context if ranking_candidate else _joined_aggregate_failure_context(
            context,
            blocked_node="table_scope",
            reason="joined aggregate base table evidence is missing",
            resolved_nodes={"unsafe_check"},
        )
    if base_table == dimension_table:
        return context

    allowed_tables = {base_table, dimension_table}
    selected_filters, filter_reason = _joined_aggregate_filter_contract(
        intent,
        filter_candidates,
        allowed_tables,
    )
    if filter_reason:
        return _joined_aggregate_failure_context(
            context,
            blocked_node="where",
            reason=filter_reason,
            resolved_nodes={
                "unsafe_check", "table_scope", "query_shape", "aggregate", "metric", "dimension",
                "join_need", "relationship_graph_lookup", "safe_join_path", "ambiguity_check",
            },
        )
    filter_tables = {str(entry.get("table") or "") for entry in selected_filters}
    if not filter_tables <= allowed_tables:
        return _joined_aggregate_failure_context(
            context,
            blocked_node="table_scope",
            reason="joined aggregate filters require a third table",
            resolved_nodes={"unsafe_check"},
        )
    for implicit_filter_phrase, implicit_source in (
        (modifier_filter_phrase, "metric_modifier_value_filter"),
        (deferred_source_filter_phrase, "source_scope_value_filter"),
    ):
        if not implicit_filter_phrase:
            continue
        if implicit_source == "source_scope_value_filter":
            implicit_filter, implicit_status = _source_scope_as_filter(
                implicit_filter_phrase,
                knowledge_base,
                allowed_tables,
            )
        else:
            implicit_filter, implicit_status = _build_sample_value_filter(
                value_phrase=implicit_filter_phrase,
                knowledge_base=knowledge_base,
                allowed_tables=allowed_tables,
                owner_table=base_table,
                source=implicit_source,
            )
        if implicit_status != "resolved" or implicit_filter is None:
            return _joined_aggregate_failure_context(
                context,
                blocked_node="where",
                reason=f"joined WHERE modifier evidence is {implicit_status}",
                resolved_nodes={
                    "unsafe_check", "table_scope", "query_shape", "aggregate", "metric", "dimension",
                    "join_need", "relationship_graph_lookup", "safe_join_path", "ambiguity_check",
                },
            )
        selected_filters.append(implicit_filter)

    graph = build_relationship_graph(knowledge_base, infer_relationships=False)
    graph_edges = find_safe_direct_join_relationships(graph, base_table, dimension_table)
    if not graph_edges:
        return _joined_aggregate_failure_context(
            context,
            blocked_node="safe_join_path",
            reason="no safe direct Relationship Graph edge exists between metric and dimension tables",
            resolved_nodes={
                "unsafe_check", "table_scope", "query_shape", "aggregate", "metric", "dimension",
                "join_need", "relationship_graph_lookup",
            },
        )
    if len(graph_edges) != 1:
        return _joined_aggregate_failure_context(
            context,
            blocked_node="ambiguity_check",
            reason="multiple distinct safe Relationship Graph edges exist between metric and dimension tables",
            resolved_nodes={
                "unsafe_check", "table_scope", "query_shape", "aggregate", "metric", "dimension",
                "join_need", "relationship_graph_lookup", "safe_join_path",
            },
        )
    edge = dict(graph_edges[0])

    structured_having = [
        dict(entry) for entry in (intent.get("structured_having") or []) if isinstance(entry, dict)
    ]
    selected_having: list[dict[str, Any]] = []
    if structured_having:
        if len(structured_having) != 1:
            return _joined_aggregate_failure_context(
                context,
                blocked_node="having",
                reason="joined aggregates support exactly one HAVING predicate",
                resolved_nodes=set(_JOINED_AGGREGATE_DECISION_NODES[:11]),
            )
        condition = dict(structured_having[0])
        having_function = str(condition.get("aggregate_function") or "").strip().lower()
        having_metric = str(condition.get("metric_phrase") or "").strip()
        if having_function != aggregate_function:
            return _joined_aggregate_failure_context(
                context,
                blocked_node="having",
                reason="HAVING aggregate does not match the selected output aggregate",
                resolved_nodes=set(_JOINED_AGGREGATE_DECISION_NODES[:11]),
            )
        if aggregate_function != "count" and _humanize(having_metric) != _humanize(metric_phrase):
            return _joined_aggregate_failure_context(
                context,
                blocked_node="having",
                reason="HAVING metric does not match the selected output metric",
                resolved_nodes=set(_JOINED_AGGREGATE_DECISION_NODES[:11]),
            )
        condition["table"] = base_table
        condition["column"] = "" if aggregate_function == "count" else str(metric.get("column") or "")
        selected_having = [condition]

    selected_order_by = None
    requested_sort = dict(intent.get("requested_sort") or {})
    if requested_sort:
        direction = str(requested_sort.get("direction") or "").strip().lower()
        if direction not in {"asc", "desc"}:
            return _joined_aggregate_failure_context(
                context,
                blocked_node="order_by",
                reason="joined aggregate ORDER BY direction is invalid",
                resolved_nodes=set(_JOINED_AGGREGATE_DECISION_NODES[:12]),
            )
        selected_order_by = {
            "target_type": "aggregate_expression",
            "table": base_table,
            "column": "" if aggregate_function == "count" else str(metric.get("column") or ""),
            "aggregate_function": aggregate_function,
            "direction": direction,
            "source": "selected_joined_aggregate",
        }

    limit = intent.get("limit")
    if limit is not None and (
        isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000
    ):
        return _joined_aggregate_failure_context(
            context,
            blocked_node="limit",
            reason="joined aggregate LIMIT must be between 1 and 1000",
            resolved_nodes=set(_JOINED_AGGREGATE_DECISION_NODES[:13]),
        )
    if not requested_sort:
        limit = None

    selected_join_path = {
        "base_table": base_table,
        "joined_tables": [dimension_table],
        "edges": [edge],
        "path_source": "relationship_graph",
        "ambiguity_status": "resolved",
    }
    metric_column = "" if aggregate_function == "count" else str(metric.get("column") or "")
    aggregate_expression = (
        "COUNT(*)"
        if aggregate_function == "count"
        else f"{aggregate_function.upper()}({base_table}.{metric_column})"
    )
    aggregate_alias = (
        f"count__{base_table}__rows"
        if aggregate_function == "count"
        else f"{aggregate_function}__{base_table}__{metric_column}"
    )
    selected_output_columns = [
        {
            "kind": "dimension",
            "table": dimension_table,
            "column": dimension_column,
            "expression": f"{dimension_table}.{dimension_column}",
            "alias": f"{dimension_table}__{dimension_column}",
            "source": "selected_joined_aggregate_dimension",
        },
        {
            "kind": "aggregate",
            "table": base_table,
            "column": metric_column,
            "aggregate_function": aggregate_function,
            "expression": aggregate_expression,
            "alias": aggregate_alias,
            "source": "selected_joined_aggregate_metric",
        },
    ]
    has_where = bool(selected_filters)
    has_having = bool(selected_having)
    clause_shape = (
        "where_group_by_having" if has_where and has_having
        else "where_group_by" if has_where
        else "group_by_having" if has_having
        else "group_by"
    )
    resolved_nodes = {
        "unsafe_check", "table_scope", "query_shape", "aggregate", "dimension", "join_need",
        "relationship_graph_lookup", "safe_join_path", "ambiguity_check", "clause_shape", "route",
    }
    if aggregate_function != "count":
        resolved_nodes.add("metric")
    decision_path = []
    for node_name in _JOINED_AGGREGATE_DECISION_NODES:
        if node_name == "metric" and aggregate_function == "count":
            status, reason = "not_required", "COUNT(*) does not require a metric column"
        elif node_name == "where" and not has_where:
            status, reason = "not_required", "no row-level filter was requested"
        elif node_name == "having" and not has_having:
            status, reason = "not_required", "no aggregate filter was requested"
        elif node_name == "order_by" and not selected_order_by:
            status, reason = "not_required", "no aggregate ordering was requested"
        elif node_name == "limit" and limit is None:
            status, reason = "not_required", "no ranking limit was requested"
        else:
            status, reason = "resolved", f"{node_name.replace('_', ' ')} resolved from deterministic evidence"
        decision_path.append({"node": node_name, "status": status, "reason": reason})

    selected_tables = [
        {"table": base_table, "confidence": 1.0, "source": "joined_aggregate_contract"},
        {"table": dimension_table, "confidence": 1.0, "source": "joined_aggregate_contract"},
    ]
    selected_columns = [dimension]
    if metric is not None:
        selected_columns.insert(0, metric)
    selected_columns.extend(selected_filters)
    required_join = (
        f"{edge['from_table']}.{edge['from_column']} = "
        f"{edge['to_table']}.{edge['to_column']}"
    )
    planned = dict(context)
    planned_intent = dict(intent)
    planned_intent["structured_filters"] = [dict(entry) for entry in selected_filters]
    planned_intent["requested_filters"] = [
        str(entry.get("raw_phrase") or entry.get("value_phrase") or entry.get("field_phrase") or "")
        for entry in selected_filters
        if str(entry.get("raw_phrase") or entry.get("value_phrase") or entry.get("field_phrase") or "")
    ]
    planned.update(
        {
            "intent": planned_intent,
            "query_shape": "joined_aggregate",
            "route": "deterministic_sql_required",
            "route_recommendation": "deterministic_sql_required",
            "route_reason": "joined aggregate can be generated from one safe direct Relationship Graph edge",
            "planner_reason": "joined aggregate can be generated from one safe direct Relationship Graph edge",
            "can_plan": True,
            "aggregate_function": aggregate_function,
            "selected_tables": selected_tables,
            "selected_table_names": [base_table, dimension_table],
            "selected_knowledge_base": {
                table_name: deepcopy(knowledge_base[table_name])
                for table_name in (base_table, dimension_table)
            },
            "selected_columns": selected_columns,
            "selected_output_columns": selected_output_columns,
            "selected_metric": metric,
            "metric_candidates": [metric] if metric is not None else [],
            "measure_candidates": [metric] if metric is not None else [],
            "selected_dimensions": [dimension],
            "selected_filters": selected_filters,
            "selected_having": selected_having,
            "selected_order_by": selected_order_by,
            "selected_join_path": selected_join_path,
            "selected_relationship_path": selected_join_path,
            "join_paths": [],
            "required_joins": [required_join],
            "limit": limit,
            "missing_evidence": [],
            "ambiguities": [],
            "ambiguity_details": [],
            "clause_plan": {
                "clause_shape": clause_shape,
                "selected_join_path": selected_join_path,
                "selected_order_by": dict(selected_order_by or {}),
                "limit": limit,
                "requires": {
                    "aggregate": True,
                    "metric": aggregate_function != "count",
                    "dimension": True,
                    "where": has_where,
                    "having": has_having,
                    "order_by": bool(selected_order_by),
                    "limit": limit is not None,
                    "join": True,
                },
                "decision_path": decision_path,
            },
        }
    )
    planned["plan"] = {
        **dict(planned.get("plan") or {}),
        "filters": selected_filters,
        "limit": limit,
        "unresolved_metrics": [],
    }
    planned["complex_sql_plan"] = {
        "query_shape": "joined_aggregate",
        "selected_tables": selected_tables,
        "selected_metric": metric,
        "selected_dimensions": [dimension],
        "selected_output_columns": selected_output_columns,
        "filters": selected_filters,
        "having": selected_having,
        "selected_order_by": dict(selected_order_by or {}),
        "selected_join_path": selected_join_path,
        "required_joins": [required_join],
        "limit": limit,
        "clause_plan": dict(planned["clause_plan"]),
        "route_recommendation": "deterministic_sql_required",
    }
    return planned


def _apply_join_lookup_contract(
    context: dict[str, Any],
    knowledge_base: dict[str, Any],
) -> dict[str, Any]:
    intent = context.get("intent") if isinstance(context.get("intent"), dict) else {}
    if intent.get("unsafe"):
        return context
    lookup = dict(intent.get("join_lookup_request") or {})
    structured_filters = list(intent.get("structured_filters") or [])
    selected_filters = [
        dict(entry) for entry in (context.get("selected_filters") or []) if isinstance(entry, dict)
    ]
    retrieved = context.get("retrieved_context") if isinstance(context.get("retrieved_context"), dict) else {}
    retrieved_columns = [
        dict(entry) for entry in (retrieved.get("matched_columns") or []) if isinstance(entry, dict)
    ]
    retrieved_tables = [
        dict(entry) for entry in (retrieved.get("matched_tables") or []) if isinstance(entry, dict)
    ]

    base_phrase = str(lookup.get("base_entity_phrase") or intent.get("target_entity_phrase") or "").strip()
    explicit_base = None
    base_resolution = "missing"
    if base_phrase:
        explicit_base, base_resolution = _resolve_join_table(
            base_phrase,
            knowledge_base,
            retrieved_tables,
        )

    filter_tables = {
        str(entry.get("table") or "") for entry in selected_filters if str(entry.get("table") or "")
    }
    cross_table_filter = bool(
        str(intent.get("intent_type") or "") == "filter"
        and explicit_base
        and any(table_name != explicit_base for table_name in filter_tables)
    )
    if not lookup.get("requested") and not cross_table_filter:
        return context

    resolved_nodes = {"unsafe_check"}
    if base_phrase and base_resolution != "resolved":
        return _join_failure_context(
            context,
            blocked_node="table_scope",
            reason=f"base table evidence is {base_resolution}",
            resolved_nodes=resolved_nodes,
        )

    if (
        intent.get("needs_aggregation")
        or intent.get("needs_grouping")
        or intent.get("structured_having")
        or intent.get("requested_sort")
        or context.get("formula_evidence")
    ):
        return _join_failure_context(
            context,
            blocked_node="route",
            reason="joined analytics, grouping, HAVING, formulas, and ranking are not supported in Phase 5",
            query_shape="multi_table_aggregate" if intent.get("needs_aggregation") else "ranking_query",
            resolved_nodes=resolved_nodes | {"table_scope", "requested_fields", "join_need"},
        )

    projection_mode = str(lookup.get("projection_mode") or "")
    requested_fields = list(lookup.get("requested_output_fields") or [])
    resolved_fields: list[dict[str, Any]] = []
    for phrase in requested_fields:
        field_table, field_table_status = _resolve_join_table(
            str(phrase),
            knowledge_base,
            [],
        )
        allowed_tables = {field_table} if field_table_status == "resolved" and field_table else None
        field, field_status = _resolve_join_output_field(
            str(phrase),
            knowledge_base,
            retrieved_columns,
            allowed_tables=allowed_tables,
        )
        if field_status != "resolved" or field is None:
            return _join_failure_context(
                context,
                blocked_node="requested_fields",
                reason=f"requested output field '{phrase}' is {field_status}",
                resolved_nodes=resolved_nodes | {"table_scope"},
            )
        resolved_fields.append(field)
    resolved_nodes.update({"table_scope", "requested_fields", "join_need"})

    if projection_mode == "broad_related":
        related_parts = [
            part.strip()
            for part in re.split(r"\band\b", str(lookup.get("related_request_phrase") or ""), flags=re.IGNORECASE)
            if part.strip()
        ]
        if len(related_parts) > 1:
            return _join_failure_context(
                context,
                blocked_node="requested_fields",
                reason="joined lookup requested multiple related entities",
                resolved_nodes=resolved_nodes | {"table_scope"},
            )

    candidate_tables: set[str] = set(filter_tables)
    if explicit_base:
        candidate_tables.add(explicit_base)
    candidate_tables.update(str(entry.get("table") or "") for entry in resolved_fields)
    if projection_mode == "broad_related":
        related_phrase = str(lookup.get("related_request_phrase") or "").strip()
        related_table, related_status = _resolve_join_table(
            related_phrase,
            knowledge_base,
            retrieved_tables,
        )
        if related_status != "resolved" or related_table is None:
            return _join_failure_context(
                context,
                blocked_node="table_scope",
                reason=f"related table evidence is {related_status}",
                resolved_nodes={"unsafe_check", "requested_fields", "join_need"},
            )
        candidate_tables.add(related_table)

    candidate_tables.discard("")
    if len(candidate_tables) != 2:
        return _join_failure_context(
            context,
            blocked_node="table_scope",
            reason="joined lookup requires exactly two uniquely resolved tables",
            resolved_nodes={"unsafe_check", "requested_fields", "join_need"},
        )
    first_table, second_table = sorted(candidate_tables)
    graph = build_relationship_graph(knowledge_base, infer_relationships=False)
    graph_edges = find_safe_direct_join_relationships(graph, first_table, second_table)
    resolved_nodes.add("relationship_graph_lookup")
    if not graph_edges:
        return _join_failure_context(
            context,
            blocked_node="safe_join_path",
            reason="no safe direct Relationship Graph edge exists between the selected tables",
            resolved_nodes=resolved_nodes,
        )
    if len(graph_edges) != 1:
        return _join_failure_context(
            context,
            blocked_node="ambiguity_check",
            reason="multiple distinct safe Relationship Graph edges exist between the selected tables",
            resolved_nodes=resolved_nodes | {"safe_join_path"},
        )
    edge = dict(graph_edges[0])
    resolved_nodes.update({"safe_join_path", "ambiguity_check"})

    if explicit_base:
        base_table = explicit_base
        joined_table = second_table if first_table == explicit_base else first_table
    else:
        base_table = str(edge.get("from_table") or "")
        joined_table = str(edge.get("to_table") or "")
    if {base_table, joined_table} != candidate_tables:
        return _join_failure_context(
            context,
            blocked_node="table_scope",
            reason="Relationship Graph direction does not resolve a unique base orientation",
            resolved_nodes={"unsafe_check", "requested_fields", "join_need"},
        )

    if structured_filters:
        if len(selected_filters) != len(structured_filters) or not filter_tables <= candidate_tables:
            return _join_failure_context(
                context,
                blocked_node="where",
                reason="joined WHERE field evidence is missing or ambiguous",
                resolved_nodes=resolved_nodes,
            )
        resolved_nodes.add("where")

    if projection_mode == "broad_related":
        output_columns = [
            *_all_table_outputs(base_table, knowledge_base, "broad_base_projection"),
            *_all_table_outputs(joined_table, knowledge_base, "broad_related_projection"),
        ]
    elif projection_mode == "base_plus_related_fields":
        if any(str(field.get("table") or "") != joined_table for field in resolved_fields):
            return _join_failure_context(
                context,
                blocked_node="requested_fields",
                reason="explicit related field did not resolve uniquely on the joined table",
                resolved_nodes={"unsafe_check", "table_scope", "join_need"},
            )
        output_columns = _all_table_outputs(base_table, knowledge_base, "base_projection")
        output_columns.extend(
            _qualified_output(joined_table, str(field["column"]), "requested_related_field")
            for field in resolved_fields
        )
    elif projection_mode == "explicit_fields_only":
        output_columns = [
            _qualified_output(str(field["table"]), str(field["column"]), "requested_output_field")
            for field in resolved_fields
        ]
    else:
        output_columns = _all_table_outputs(base_table, knowledge_base, "filtered_base_projection")

    deduped_outputs = []
    seen_outputs: set[tuple[str, str]] = set()
    for output in output_columns:
        signature = (str(output.get("table") or ""), str(output.get("column") or ""))
        if not all(signature) or signature in seen_outputs:
            continue
        seen_outputs.add(signature)
        deduped_outputs.append(output)
    if not deduped_outputs:
        return _join_failure_context(
            context,
            blocked_node="selected_output_columns",
            reason="joined lookup output columns are missing",
            resolved_nodes=resolved_nodes,
        )
    resolved_nodes.add("selected_output_columns")

    raw_limit = intent.get("limit")
    resolved_limit = 50 if raw_limit is None else raw_limit
    if isinstance(resolved_limit, bool) or not isinstance(resolved_limit, int) or not 1 <= resolved_limit <= 1000:
        return _join_failure_context(
            context,
            blocked_node="route",
            reason="joined lookup LIMIT must be between 1 and 1000",
            resolved_nodes=resolved_nodes,
        )

    selected_join_path = {
        "base_table": base_table,
        "joined_tables": [joined_table],
        "edges": [edge],
        "path_source": "relationship_graph",
        "ambiguity_status": "resolved",
    }
    decision_path = [
        {
            "node": node_name,
            "status": "not_required" if node_name == "where" and not structured_filters else "resolved",
            "reason": (
                "no row-level filter was requested"
                if node_name == "where" and not structured_filters
                else f"{node_name.replace('_', ' ')} resolved from deterministic evidence"
            ),
        }
        for node_name in _JOIN_DECISION_NODES
    ]
    selected_tables = []
    for table_name in (base_table, joined_table):
        selected_tables.append(
            {
                "table": table_name,
                "confidence": 1.0,
                "selected_columns": [
                    {"column": output["column"], "confidence": 1.0, "reason": output["source"]}
                    for output in deduped_outputs
                    if output["table"] == table_name
                ],
            }
        )
    required_join = (
        f"{edge['from_table']}.{edge['from_column']} = "
        f"{edge['to_table']}.{edge['to_column']}"
    )
    planned = dict(context)
    planned.update(
        {
            "query_shape": "joined_lookup",
            "route": "deterministic_sql_required",
            "route_recommendation": "deterministic_sql_required",
            "route_reason": "joined lookup can be generated from one safe direct Relationship Graph edge",
            "planner_reason": "joined lookup can be generated from one safe direct Relationship Graph edge",
            "can_plan": True,
            "selected_tables": selected_tables,
            "selected_table_names": [base_table, joined_table],
            "selected_knowledge_base": {
                table_name: deepcopy(knowledge_base[table_name])
                for table_name in (base_table, joined_table)
            },
            "selected_columns": list(deduped_outputs),
            "selected_output_columns": list(deduped_outputs),
            "selected_join_path": selected_join_path,
            "selected_relationship_path": selected_join_path,
            "join_paths": [selected_join_path],
            "required_joins": [required_join],
            "limit": resolved_limit,
            "missing_evidence": [],
            "ambiguities": [],
            "clause_plan": {
                "clause_shape": "joined_lookup",
                "selected_join_path": selected_join_path,
                "limit": resolved_limit,
                "requires": {
                    "aggregate": False,
                    "metric": False,
                    "dimension": False,
                    "where": bool(structured_filters),
                    "having": False,
                    "order_by": False,
                    "limit": True,
                    "join": True,
                    "requested_fields": True,
                    "selected_output_columns": True,
                },
                "decision_path": decision_path,
            },
        }
    )
    planned["plan"] = {
        **dict(planned.get("plan") or {}),
        "limit": resolved_limit,
        "filters": selected_filters,
    }
    planned["complex_sql_plan"] = {
        "query_shape": "joined_lookup",
        "selected_tables": selected_tables,
        "selected_columns": list(deduped_outputs),
        "selected_join_path": selected_join_path,
        "required_joins": [required_join],
        "limit": resolved_limit,
        "route_recommendation": "deterministic_sql_required",
    }
    return planned


def _detect_missing_evidence(
    plan: dict[str, Any],
    selected_tables: list[dict[str, Any]],
    selected_columns: list[dict[str, Any]],
    join_paths: list[dict],
    requested_metrics: list[str],
    requested_dimensions: list[str],
    requested_filters: list[str],
    measure_candidates: list[dict[str, Any]],
    dimension_candidates: list[dict[str, Any]],
    filters: list[dict[str, Any]],
    formula_evidence: list[dict[str, Any]],
    query_shape: str | None,
) -> dict[str, Any]:
    """Detect missing evidence for planning."""
    missing_evidence: dict[str, Any] = {
        "missing_table": False,
        "missing_metric": False,
        "missing_dimension": False,
        "missing_join_path": False,
        "missing_filter_column": False,
        "missing_formula_evidence": False,
    }

    if not selected_tables:
        missing_evidence["missing_table"] = True
    elif (
        query_shape in {"unknown", "single_table_list", "single_table_count", "single_table_aggregate", "filtered_query"}
        and len(selected_tables) != 1
    ):
        missing_evidence["missing_table"] = True
    elif len(selected_tables) > 1 and not join_paths and query_shape == "unknown":
        missing_evidence["missing_table"] = True
    elif len(selected_tables) > 1 and not join_paths and not requested_dimensions and not requested_filters:
        missing_evidence["missing_table"] = True
    elif len(selected_tables) > 1 and not join_paths and requested_metrics and not requested_dimensions:
        missing_evidence["missing_table"] = True

    if requested_metrics and not measure_candidates and not formula_evidence:
        missing_evidence["missing_metric"] = True

    if requested_dimensions and not dimension_candidates:
        missing_evidence["missing_dimension"] = True

    if len(selected_tables) > 1 and not join_paths:
        missing_evidence["missing_join_path"] = True

    if requested_filters and len(filters) < len(requested_filters):
        missing_evidence["missing_filter_column"] = True

    if query_shape == "formula_query" and not formula_evidence:
        missing_evidence["missing_formula_evidence"] = True

    return missing_evidence


def _compute_route_recommendation(
    plan: dict[str, Any],
    missing_evidence: dict[str, Any],
    confidence: float,
    selected_tables: list[dict[str, Any]],
    query_shape: str | None,
) -> str:
    """Compute route recommendation based on evidence strength."""
    intent = str(plan.get("intent") or "").strip().lower()
    has_grouping = bool(plan.get("grouping") or plan.get("dimension"))
    has_formula_gap = bool(missing_evidence.get("missing_formula_evidence"))
    is_complex_shape = bool(query_shape)

    if str(query_shape or "").strip() == "blocked_unsafe":
        return "blocked_unsafe"

    if (
        missing_evidence.get("missing_join_path")
        or missing_evidence.get("missing_formula_evidence")
        or (is_complex_shape and missing_evidence.get("missing_metric"))
        or (is_complex_shape and has_grouping and missing_evidence.get("missing_dimension"))
    ):
        return "cannot_plan_safely"

    if confidence < 0.3:
        return "cannot_plan_safely"

    if not selected_tables:
        return "cannot_plan_safely"

    if (
        is_complex_shape
        or len(selected_tables) > 1
        or has_grouping
        or has_formula_gap
        or missing_evidence.get("missing_metric")
        or intent not in {"list", "count"}
    ):
        return "deterministic_sql_required"

    return "deterministic_sql_required"


def _derive_complex_query_shape(
    plan: dict[str, Any],
    intent: dict[str, Any] | None,
    selected_tables: list[dict[str, Any]],
    measure_candidates: list[dict[str, Any]],
    dimension_candidates: list[dict[str, Any]],
    filters: list[dict[str, Any]],
    join_paths: list[dict[str, Any]],
    formula_evidence: list[dict[str, Any]],
) -> str | None:
    planner_intent = str(plan.get("intent") or "").strip().lower()
    intent_type = str((intent or {}).get("intent_type") or "").strip().lower()
    table_count = len([entry for entry in selected_tables if entry.get("table")])
    has_join = bool(join_paths) or table_count > 1
    has_grouping = bool(plan.get("grouping") or plan.get("dimension") or (intent or {}).get("needs_grouping"))
    has_filters = bool(filters)
    has_formula = bool(formula_evidence)
    has_measure = bool(measure_candidates)
    has_metric_request = bool(plan.get("requested_metrics"))
    has_aggregation = bool(
        planner_intent in {"count", "total", "average", "top_n"}
        or (intent or {}).get("needs_aggregation")
        or has_measure
        or has_formula
    )
    has_ranking = bool(
        planner_intent == "top_n"
        or intent_type == "ranking"
        or (plan.get("limit") is not None and plan.get("sorting"))
    )

    if (
        has_metric_request
        and not has_measure
        and not has_formula
        and (has_grouping or has_ranking or has_join)
    ):
        return "formula_query"
    if has_ranking and (has_grouping or has_aggregation):
        return "ranking_aggregation"
    if has_grouping and (has_measure or has_formula or has_aggregation):
        return "grouped_aggregation"
    if has_filters and not has_grouping and not has_aggregation and not has_join:
        return "filtered_list"
    if has_join:
        return "multi_table_lookup"
    return None
def _required_join_predicates(join_paths: list[dict[str, Any]]) -> list[str]:
    predicates: list[str] = []
    seen: set[str] = set()
    for join_path in join_paths:
        for edge in join_path.get("path", []) or []:
            join_condition = str(edge.get("join_condition") or "").strip()
            if not join_condition:
                from_table = str(edge.get("from_table") or "").strip()
                from_column = str(edge.get("from_column") or "").strip()
                to_table = str(edge.get("to_table") or "").strip()
                to_column = str(edge.get("to_column") or "").strip()
                if from_table and from_column and to_table and to_column:
                    join_condition = f"{from_table}.{from_column} = {to_table}.{to_column}"
            if join_condition and join_condition not in seen:
                seen.add(join_condition)
                predicates.append(join_condition)
    return predicates


def _aggregation_type_for_complex_shape(
    query_shape: str | None,
    plan: dict[str, Any],
    formula_evidence: list[dict[str, Any]],
    measure_candidates: list[dict[str, Any]],
) -> str | None:
    planner_intent = str(plan.get("intent") or "").strip().lower()
    if query_shape == "formula_query":
        return "derived"
    if planner_intent == "count":
        return "count"
    if planner_intent == "average":
        return "average"
    if query_shape in {"grouped_aggregation", "ranking_aggregation"} and (measure_candidates or formula_evidence):
        return "sum"
    return None


def _build_complex_sql_plan(
    intent: dict[str, Any] | None,
    plan: dict[str, Any],
    selected_tables: list[dict[str, Any]],
    selected_columns: list[dict[str, Any]],
    measure_candidates: list[dict[str, Any]],
    dimension_candidates: list[dict[str, Any]],
    filters: list[dict[str, Any]],
    join_paths: list[dict[str, Any]],
    formula_evidence: list[dict[str, Any]],
    missing_evidence: dict[str, Any],
    route_recommendation: str,
    query_shape: str | None,
) -> dict[str, Any] | None:
    if not query_shape:
        return None

    return {
        "query_shape": query_shape,
        "metric_candidates": list(measure_candidates),
        "dimension_candidates": list(dimension_candidates),
        "filter_candidates": list(filters),
        "selected_tables": list(selected_tables),
        "selected_columns": list(selected_columns),
        "join_paths": list(join_paths),
        "required_joins": _required_join_predicates(join_paths),
        "aggregation_type": _aggregation_type_for_complex_shape(
            query_shape,
            plan,
            formula_evidence,
            measure_candidates,
        ),
        "ordering": dict(plan.get("sorting") or (intent or {}).get("requested_sort") or {}),
        "limit": plan.get("limit"),
        "formula_evidence": list(formula_evidence),
        "sql_skeleton_type": query_shape,
        "missing_evidence": {
            key: value
            for key, value in missing_evidence.items()
            if value
        },
        "route_recommendation": route_recommendation,
    }


def _build_debug_trace(
    question: str,
    plan: dict[str, Any],
    selected_tables: list[dict[str, Any]],
    selected_columns: list[dict[str, Any]],
    join_paths: list[dict],
    missing_evidence: dict[str, Any],
    confidence: float,
    route_recommendation: str,
) -> dict[str, Any]:
    """Build debug trace for planning transparency."""
    return {
        "question": question,
        "intent": plan.get("intent"),
        "metric": plan.get("metric"),
        "dimension": plan.get("dimension"),
        "selected_table_count": len(selected_tables),
        "selected_column_count": len(selected_columns),
        "join_path_count": len(join_paths),
        "missing_evidence": missing_evidence,
        "confidence": confidence,
        "route_recommendation": route_recommendation,
        "selected_table_names": [t.get("table") for t in selected_tables],
        "selected_column_names": [(c.get("table"), c.get("column")) for c in selected_columns[:10]],
    }


def _normalize_query_shape_label(
    *,
    question: str,
    plan: dict[str, Any],
    intent: dict[str, Any] | None,
    selected_tables: list[dict[str, Any]],
    measure_candidates: list[dict[str, Any]],
    dimension_candidates: list[dict[str, Any]],
    filters: list[dict[str, Any]],
    join_paths: list[dict[str, Any]],
    formula_evidence: list[dict[str, Any]],
    legacy_query_shape: str | None,
) -> str:
    query_shape = classify_query_shape(
        question=question,
        intent=intent,
        retrieved_context=None,
        plan=plan,
        selected_tables=selected_tables,
        selected_columns=[],
        metric_candidates=measure_candidates,
        dimension_candidates=dimension_candidates,
        filter_candidates=filters,
        join_paths=join_paths,
        formula_evidence=formula_evidence,
    )
    return query_shape


def _route_recommendation_from_contract(
    *,
    query_shape: str,
    confidence: float,
    missing_evidence_flags: dict[str, Any],
    selected_tables: list[dict[str, Any]],
    ambiguities: list[str],
    can_plan: bool,
) -> tuple[str, str]:
    if query_shape == "blocked_unsafe":
        return "blocked_unsafe", "question contains blocked SQL operation wording"

    if missing_evidence_flags.get("unsupported_intent"):
        return "cannot_plan_safely", "intent contains an unsupported deterministic construct"
    if "table_selection" in ambiguities:
        return "cannot_plan_safely", "table evidence is missing or ambiguous"
    if "metric_selection" in ambiguities:
        return "cannot_plan_safely", "metric evidence is ambiguous"
    if "dimension_selection" in ambiguities:
        return "cannot_plan_safely", "dimension evidence is ambiguous"
    if "filter_selection" in ambiguities:
        return "cannot_plan_safely", "filter evidence is ambiguous"
    if "order_by_selection" in ambiguities:
        return "cannot_plan_safely", "ORDER BY target evidence is missing or ambiguous"
    if "limit_selection" in ambiguities:
        return "cannot_plan_safely", "LIMIT must be a numeric value from 1 through 1000"
    if missing_evidence_flags.get("missing_table"):
        return "cannot_plan_safely", "table evidence is missing or ambiguous"
    if missing_evidence_flags.get("missing_join_path"):
        return "cannot_plan_safely", "required join path evidence is missing"
    if missing_evidence_flags.get("missing_formula_evidence"):
        return "cannot_plan_safely", "formula evidence is missing"
    if missing_evidence_flags.get("missing_metric"):
        return "cannot_plan_safely", "metric evidence is missing"
    if missing_evidence_flags.get("missing_dimension"):
        return "cannot_plan_safely", "dimension evidence is missing"
    if missing_evidence_flags.get("missing_filter_column"):
        return "cannot_plan_safely", "filter evidence is missing"
    if confidence < 0.3 or not selected_tables:
        return "cannot_plan_safely", "planner confidence is too low"
    if query_shape == "unknown":
        return "cannot_plan_safely", "planner could not derive a supported query shape with enough confidence"
    if not can_plan:
        return "cannot_plan_safely", f"planner could not safely route query shape '{query_shape}' from current evidence"
    return "deterministic_sql_required", f"{query_shape} can be generated deterministically from current evidence"


def _required_evidence_for_query_shape(query_shape: str) -> list[str]:
    mapping = {
        "single_table_list": ["selected_table"],
        "single_table_count": ["selected_table"],
        "single_table_aggregate": ["selected_table", "metric_candidate", "aggregate_function"],
        "grouped_aggregate": ["selected_table", "metric_candidate", "dimension_candidate"],
        "joined_lookup": ["selected_table", "join_candidate", "required_join"],
        "filtered_query": ["selected_table", "filter_candidate"],
        "ranking_query": ["selected_table", "metric_candidate", "order_by_candidate"],
        "formula_query": ["selected_table", "formula_evidence"],
        "multi_metric_aggregate": ["selected_table", "metric_candidate"],
        "unknown": [],
        "blocked_unsafe": [],
    }
    return list(mapping.get(query_shape, []))


def _missing_evidence_list(missing_evidence_flags: dict[str, Any]) -> list[str]:
    ordered = [
        "unsupported_intent",
        "missing_table",
        "missing_metric",
        "missing_dimension",
        "missing_join_path",
        "missing_filter_column",
        "missing_formula_evidence",
    ]
    return [key for key in ordered if missing_evidence_flags.get(key)]


def _ambiguities_for_contract(
    selected_tables: list[dict[str, Any]],
    measure_candidates: list[dict[str, Any]],
    dimension_candidates: list[dict[str, Any]],
) -> list[str]:
    ambiguities: list[str] = []
    if len(selected_tables) > 1:
        scored = [
            float(entry.get("confidence", entry.get("score", 0.0)) or 0.0)
            for entry in selected_tables[:2]
        ]
        if len(scored) == 2 and abs(scored[0] - scored[1]) < 0.12:
            ambiguities.append("table_selection")
    if _has_close_role_ambiguity(measure_candidates):
        ambiguities.append("metric_selection")
    if _has_close_role_ambiguity(dimension_candidates):
        ambiguities.append("dimension_selection")
    return ambiguities


def _ambiguity_details_for_contract(
    ambiguities: list[str],
    selected_tables: list[dict[str, Any]],
    measure_candidates: list[dict[str, Any]],
    dimension_candidates: list[dict[str, Any]],
    filter_candidates: list[dict[str, Any]],
    retrieved_context: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    retrieved_ambiguities = dict((retrieved_context or {}).get("ambiguity_candidates") or {})
    details: list[dict[str, Any]] = []
    for ambiguity_type in ambiguities:
        if ambiguity_type == "table_selection":
            candidates = list(selected_tables[:5])
            retrieved_choices = list(retrieved_ambiguities.get("tables") or [])
        elif ambiguity_type == "metric_selection":
            candidates = list(measure_candidates[:5])
            retrieved_choices = list(retrieved_ambiguities.get("metrics") or [])
        elif ambiguity_type == "dimension_selection":
            candidates = list(dimension_candidates[:5])
            retrieved_choices = list(retrieved_ambiguities.get("dimensions") or [])
        elif ambiguity_type == "filter_selection":
            candidates = list(filter_candidates[:5])
            retrieved_choices = list(retrieved_ambiguities.get("filters") or [])
        else:
            candidates = []
            retrieved_choices = []

        choices: list[Any] = []
        seen: set[tuple[str, str]] = set()
        for choice in [*candidates, *retrieved_choices]:
            if not isinstance(choice, dict):
                continue
            table_name = str(choice.get("table") or choice.get("table_name") or "").strip()
            column_name = str(choice.get("column") or choice.get("column_name") or "").strip()
            key = (table_name, column_name)
            if key in seen:
                continue
            seen.add(key)
            choices.append(choice)
        details.append({"type": ambiguity_type, "choices": choices})
    return details


def _selected_having_for_contract(
    intent: dict[str, Any],
    selected_metric: dict[str, Any] | None,
    selected_tables: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    conditions = [
        dict(entry)
        for entry in (intent.get("structured_having") or [])
        if isinstance(entry, dict)
    ]
    if not conditions:
        return []

    table_name = ""
    if len(selected_tables) == 1:
        table_name = str(selected_tables[0].get("table") or "").strip()
    selected: list[dict[str, Any]] = []
    for condition in conditions:
        aggregate_function = str(condition.get("aggregate_function") or "").strip().lower()
        resolved = dict(condition)
        resolved["table"] = table_name
        if aggregate_function == "count":
            resolved["column"] = ""
        elif isinstance(selected_metric, dict):
            resolved["table"] = str(selected_metric.get("table") or table_name).strip()
            resolved["column"] = str(
                selected_metric.get("column") or selected_metric.get("column_name") or ""
            ).strip()
        else:
            resolved["column"] = ""
        selected.append(resolved)
    return selected


def _clause_shape_for_contract(
    *,
    query_shape: str,
    aggregate_function: str,
    has_where: bool,
    has_having: bool,
    ranking_mode: str = "",
) -> str:
    if query_shape == "single_table_list":
        return "list_only"
    if query_shape == "ranking_query" and ranking_mode != "grouped_aggregate":
        return "where_only" if has_where else "list_only"
    if query_shape == "filtered_query":
        return "aggregate_where" if aggregate_function else "where_only"
    if query_shape in {"single_table_aggregate", "single_table_count"}:
        return "aggregate_only"
    if query_shape == "grouped_aggregate" or (
        query_shape == "ranking_query" and ranking_mode == "grouped_aggregate"
    ):
        if has_where and has_having:
            return "where_group_by_having"
        if has_where:
            return "where_group_by"
        if has_having:
            return "group_by_having"
        return "group_by"
    return "unsupported"


def _build_clause_plan_for_contract(
    *,
    query_shape: str,
    route_recommendation: str,
    can_plan: bool,
    aggregate_function: str,
    intent: dict[str, Any],
    selected_tables: list[dict[str, Any]],
    selected_metric: dict[str, Any] | None,
    selected_dimensions: list[dict[str, Any]],
    selected_filters: list[dict[str, Any]],
    selected_having: list[dict[str, Any]],
    selected_order_by: dict[str, Any] | None,
    limit: int | None,
    limit_reason: str,
    join_paths: list[dict[str, Any]],
) -> dict[str, Any]:
    requested_where = list(intent.get("structured_filters") or intent.get("requested_filters") or [])
    requested_having = list(intent.get("structured_having") or intent.get("requested_having") or [])
    has_where = bool(requested_where or selected_filters)
    has_having = bool(requested_having or selected_having)
    clause_shape = _clause_shape_for_contract(
        query_shape=query_shape,
        aggregate_function=aggregate_function,
        has_where=has_where,
        has_having=has_having,
        ranking_mode=str((intent.get("ranking_diagnostics") or {}).get("mode_hint") or ""),
    )
    requires_grouping = clause_shape in {
        "group_by",
        "where_group_by",
        "group_by_having",
        "where_group_by_having",
    }
    requires_aggregate = clause_shape not in {"where_only", "unsupported"}
    if clause_shape == "list_only":
        requires_aggregate = False
    requires_metric = requires_aggregate and aggregate_function != "count"
    requires_order_by = bool(intent.get("requested_sort"))
    requires_limit = limit is not None or intent.get("limit") is not None

    def node(name: str, status: str, reason: str) -> dict[str, str]:
        return {"node": name, "status": status, "reason": reason}

    unsafe_blocked = query_shape == "blocked_unsafe" or bool(intent.get("unsafe"))
    table_scope_ok = len(selected_tables) == 1 and not join_paths
    aggregate_ok = aggregate_function in {"count", "sum", "avg", "min", "max"}
    where_ok = bool(selected_filters) and len(selected_filters) == len(requested_where or selected_filters)
    having_ok = (
        len(selected_having) == 1
        and len(selected_having) == len(requested_having or selected_having)
    )

    decision_path = [
        node(
            "unsafe_check",
            "blocked" if unsafe_blocked else "resolved",
            "unsafe request was blocked" if unsafe_blocked else "request is read-only",
        ),
        node(
            "table_scope",
            "resolved" if table_scope_ok else "blocked",
            "exactly one table and no joins were selected"
            if table_scope_ok
            else "single-table evidence is missing, ambiguous, or requires a join",
        ),
        node(
            "query_shape",
            "resolved" if clause_shape != "unsupported" else "blocked",
            f"resolved clause shape '{clause_shape}'"
            if clause_shape != "unsupported"
            else f"query shape '{query_shape}' is outside this deterministic clause tree",
        ),
        node(
            "aggregate",
            "resolved" if requires_aggregate and aggregate_ok else "blocked" if requires_aggregate else "not_required",
            f"aggregate function '{aggregate_function}' was resolved"
            if requires_aggregate and aggregate_ok
            else "aggregate function is missing or unsupported"
            if requires_aggregate
            else "this clause shape does not aggregate",
        ),
        node(
            "metric",
            "resolved" if requires_metric and isinstance(selected_metric, dict) else "blocked" if requires_metric else "not_required",
            "metric evidence was resolved"
            if requires_metric and isinstance(selected_metric, dict)
            else "metric evidence is missing or ambiguous"
            if requires_metric
            else "COUNT(*) or a non-aggregate shape does not require a metric column",
        ),
        node(
            "dimension",
            "resolved" if requires_grouping and bool(selected_dimensions) else "blocked" if requires_grouping else "not_required",
            "group dimension evidence was resolved"
            if requires_grouping and bool(selected_dimensions)
            else "group dimension evidence is missing or ambiguous"
            if requires_grouping
            else "this clause shape does not group rows",
        ),
        node(
            "where",
            "resolved" if has_where and where_ok else "blocked" if has_where else "not_required",
            "row-level filter evidence was resolved"
            if has_where and where_ok
            else "row-level filter evidence is missing or ambiguous"
            if has_where
            else "no row-level filter was requested",
        ),
        node(
            "having",
            "resolved" if has_having and having_ok else "blocked" if has_having else "not_required",
            "aggregate filter evidence was resolved"
            if has_having and having_ok
            else "aggregate filter evidence is missing, ambiguous, or unsupported"
            if has_having
            else "no aggregate filter was requested",
        ),
        node(
            "order_by",
            "resolved" if requires_order_by and isinstance(selected_order_by, dict) else "blocked" if requires_order_by else "not_required",
            "ORDER BY target and direction were resolved"
            if requires_order_by and isinstance(selected_order_by, dict)
            else "ORDER BY target is missing or ambiguous"
            if requires_order_by
            else "no ordering was requested",
        ),
        node(
            "limit",
            "resolved" if requires_limit and isinstance(limit, int) else "blocked" if requires_limit else "not_required",
            f"LIMIT {limit} was resolved by the planner"
            if requires_limit and isinstance(limit, int)
            else f"LIMIT is invalid: {limit_reason or 'missing value'}"
            if requires_limit
            else "no LIMIT is required",
        ),
    ]
    prior_blocked = any(entry["status"] == "blocked" for entry in decision_path)
    decision_path.append(
        node(
            "clause_shape",
            "resolved" if clause_shape != "unsupported" and not prior_blocked else "blocked",
            f"final clause shape '{clause_shape}' is complete"
            if clause_shape != "unsupported" and not prior_blocked
            else "clause shape is incomplete because required clause evidence is unresolved",
        )
    )
    prior_blocked = any(entry["status"] == "blocked" for entry in decision_path)
    route_ok = route_recommendation == "deterministic_sql_required" and can_plan and not prior_blocked
    decision_path.append(
        node(
            "route",
            "resolved" if route_ok else "blocked",
            "deterministic SQL generation is allowed"
            if route_ok
            else "deterministic SQL generation is blocked by unresolved evidence or unsupported scope",
        )
    )
    return {
        "clause_shape": clause_shape,
        "selected_order_by": dict(selected_order_by or {}),
        "limit": limit,
        "requires": {
            "aggregate": requires_aggregate,
            "metric": requires_metric,
            "dimension": requires_grouping,
            "where": has_where,
            "having": has_having,
            "order_by": requires_order_by,
            "limit": requires_limit,
        },
        "decision_path": decision_path,
    }


def _evidence_summary_for_contract(
    retrieved_context: dict[str, Any] | None,
    *,
    selected_tables: list[dict[str, Any]],
    selected_columns: list[dict[str, Any]],
    measure_candidates: list[dict[str, Any]],
    dimension_candidates: list[dict[str, Any]],
    filter_candidates: list[dict[str, Any]],
    join_paths: list[dict[str, Any]],
) -> dict[str, Any]:
    context = retrieved_context or {}
    return {
        "retrieval_sources": list(context.get("retrieval_sources") or []),
        "evidence_scores": dict(context.get("evidence_scores") or {}),
        "source_metadata": dict(context.get("source_metadata") or {}),
        "missing_evidence_indicators": dict(context.get("missing_evidence_indicators") or {}),
        "normalized_package_used": bool(context.get("normalized_package_used")),
        "candidate_counts": {
            "tables": len(selected_tables),
            "columns": len(selected_columns),
            "metrics": len(measure_candidates),
            "dimensions": len(dimension_candidates),
            "filters": len(filter_candidates),
            "relationship_paths": len(join_paths),
        },
    }


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


def _join_candidates_for_contract(
    join_paths: list[dict[str, Any]],
    matched_relationships: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for path in join_paths or []:
        signature = (
            str(path.get("from_table") or ""),
            str(path.get("to_table") or ""),
            str(path.get("length") or ""),
        )
        if signature in seen:
            continue
        seen.add(signature)
        candidates.append(
            {
                "from_table": str(path.get("from_table") or "").strip(),
                "to_table": str(path.get("to_table") or "").strip(),
                "length": int(path.get("length") or 0),
                "support_score": float(path.get("support_score") or 0.0),
                "source": "join_path",
            }
        )
    for rel in matched_relationships or []:
        signature = (
            str(rel.get("from_table") or ""),
            str(rel.get("to_table") or ""),
            str(rel.get("join_condition") or ""),
        )
        if signature in seen:
            continue
        seen.add(signature)
        candidates.append(
            {
                "from_table": str(rel.get("from_table") or "").strip(),
                "to_table": str(rel.get("to_table") or "").strip(),
                "join_condition": str(rel.get("join_condition") or "").strip(),
                "source": str(rel.get("source") or "relationship"),
            }
        )
    return candidates


def _resolve_limit_for_contract(
    *,
    query_shape: str,
    aggregate_function: str,
    intent: dict[str, Any],
    plan: dict[str, Any],
) -> tuple[int | None, str]:
    raw_limit = intent.get("limit", plan.get("limit"))
    if raw_limit is None:
        if query_shape == "single_table_list" or (
            query_shape == "filtered_query" and not aggregate_function
        ):
            return 50, "default_list_limit"
        return None, ""
    if isinstance(raw_limit, bool) or not isinstance(raw_limit, int):
        return None, "limit_not_numeric"
    if raw_limit < 1 or raw_limit > 1000:
        return None, "limit_out_of_safe_range"
    return raw_limit, "explicit_or_ranking_limit"


def _order_candidate_identity(entry: dict[str, Any]) -> tuple[str, str]:
    return (
        str(entry.get("table") or entry.get("table_name") or "").strip(),
        str(entry.get("column") or entry.get("column_name") or entry.get("name") or "").strip(),
    )


def _resolve_order_by_for_contract(
    *,
    intent: dict[str, Any],
    selected_tables: list[dict[str, Any]],
    selected_columns: list[dict[str, Any]],
    measure_candidates: list[dict[str, Any]],
    dimension_candidates: list[dict[str, Any]],
    filter_candidates: list[dict[str, Any]],
    selected_metric: dict[str, Any] | None,
    aggregate_function: str,
) -> tuple[dict[str, Any] | None, str, list[dict[str, Any]]]:
    sorting = dict(intent.get("requested_sort") or {})
    if not sorting:
        return None, "", []
    direction = str(sorting.get("direction") or "").strip().lower()
    if direction not in {"asc", "desc"}:
        return None, "order_by_direction_invalid", []
    target_phrase = str(sorting.get("terms") or "").strip()
    if not target_phrase:
        return None, "order_by_target_missing", []
    if re.search(r"\b(?:and|,)\b", target_phrase, re.IGNORECASE):
        return None, "multiple_order_by_targets_not_supported", []

    ranking_mode = str((intent.get("ranking_diagnostics") or {}).get("mode_hint") or "").strip()
    selected_table = (
        str(selected_tables[0].get("table") or "").strip()
        if len(selected_tables) == 1
        else ""
    )
    if ranking_mode == "grouped_aggregate":
        if aggregate_function not in {"count", "sum", "avg", "min", "max"}:
            return None, "order_by_aggregate_missing", []
        if aggregate_function == "count":
            column_name = ""
            table_name = selected_table
        elif isinstance(selected_metric, dict):
            table_name, column_name = _order_candidate_identity(selected_metric)
        else:
            return None, "order_by_metric_missing", []
        normalized_target = _normalize(target_phrase)
        metric_text = _humanize(column_name)
        aggregate_aliases = {
            "sum": {"sum", "total"},
            "avg": {"avg", "average", "mean"},
            "min": {"min", "minimum", "lowest"},
            "max": {"max", "maximum", "highest"},
            "count": {"count"},
        }[aggregate_function]
        if not any(word in _tokenize(normalized_target) for word in aggregate_aliases):
            return None, "order_by_aggregate_mismatch", []
        if aggregate_function != "count" and not set(_tokenize(metric_text)) <= set(_tokenize(normalized_target)):
            return None, "order_by_metric_mismatch", []
        return {
            "target_type": "aggregate_expression",
            "table": table_name,
            "column": column_name,
            "aggregate_function": aggregate_function,
            "direction": direction,
            "source": "selected_metric",
        }, "", []

    target_tokens = set(_tokenize(target_phrase))
    ranked: list[tuple[int, dict[str, Any]]] = []
    seen: set[tuple[str, str]] = set()
    for raw_entry in _merge_candidate_columns(
        selected_columns,
        measure_candidates,
        dimension_candidates,
        filter_candidates,
    ):
        table_name, column_name = _order_candidate_identity(raw_entry)
        identity = (table_name, column_name)
        if not table_name or not column_name or identity in seen or table_name != selected_table:
            continue
        seen.add(identity)
        labels = {
            _normalize(column_name),
            _normalize(_humanize(column_name)),
            *{
                _normalize(term)
                for term in (raw_entry.get("matched_terms") or [])
                if str(term).strip()
            },
        }
        score = 3 if _normalize(target_phrase) in labels else 0
        candidate_tokens = set().union(*(_tokenize(label) for label in labels if label))
        if not score and target_tokens and target_tokens <= candidate_tokens:
            score = 2
        if score:
            ranked.append((score, dict(raw_entry)))
    if not ranked:
        return None, "order_by_target_not_found", []
    best_score = max(score for score, _ in ranked)
    best = [entry for score, entry in ranked if score == best_score]
    if len(best) != 1:
        return None, "order_by_target_ambiguous", best
    table_name, column_name = _order_candidate_identity(best[0])
    return {
        "target_type": "column",
        "table": table_name,
        "column": column_name,
        "aggregate_function": None,
        "direction": direction,
        "source": str(best[0].get("source") or "normalized_evidence"),
    }, "", []


def _order_by_candidates_for_contract(
    sorting: dict[str, Any] | None,
    measure_candidates: list[dict[str, Any]],
    dimension_candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not isinstance(sorting, dict) or not sorting:
        return []
    by_value = str(sorting.get("by") or "").strip()
    direction = str(sorting.get("direction") or "asc").strip().lower() or "asc"
    candidates: list[dict[str, Any]] = []
    if by_value:
        candidates.append({"by": by_value, "direction": direction, "source": "plan.sorting"})
    for entry in (measure_candidates or [])[:2]:
        candidates.append(
            {
                "table": str(entry.get("table") or "").strip(),
                "column": str(entry.get("column") or "").strip(),
                "direction": direction,
                "source": "measure_candidate",
            }
        )
    for entry in (dimension_candidates or [])[:2]:
        candidates.append(
            {
                "table": str(entry.get("table") or "").strip(),
                "column": str(entry.get("column") or "").strip(),
                "direction": direction,
                "source": "dimension_candidate",
            }
        )
    return candidates


def _group_by_candidates_for_contract(
    plan: dict[str, Any],
    dimension_candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    requested_grouping = [str(value).strip() for value in (plan.get("grouping") or []) if str(value).strip()]
    candidates = []
    for entry in dimension_candidates or []:
        candidates.append(
            {
                "table": str(entry.get("table") or "").strip(),
                "column": str(entry.get("column") or "").strip(),
                "source": str(entry.get("source") or "dimension_candidate"),
            }
        )
    for value in requested_grouping:
        candidates.append({"label": value, "source": "plan.grouping"})
    return candidates


def _fallback_metric_candidates_from_selected_columns(
    selected_columns: list[dict[str, Any]],
    question: str,
) -> list[dict[str, Any]]:
    if not _aggregate_function_hint(question):
        return []

    candidates: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    question_terms = set(_content_terms(question))
    for entry in selected_columns or []:
        table_name = str(entry.get("table") or "").strip()
        column_name = str(entry.get("column") or "").strip()
        semantic_type = str(entry.get("semantic_type") or "").strip().lower()
        core_semantic_type = str(entry.get("core_semantic_type") or "").strip().lower()
        if not table_name or not column_name:
            continue
        if semantic_type in {"id", "date", "boolean"} or core_semantic_type in {"id", "date", "boolean"}:
            continue
        if semantic_type not in {"numeric_candidate", "money", "quantity", "percentage"} and core_semantic_type != "numeric_candidate":
            continue
        key = (table_name, column_name)
        if key in seen:
            continue
        seen.add(key)
        column_terms = [term for term in _tokenize(column_name) if term not in {"id", "total", "amount", "value"}]
        matched_terms = [_humanize(column_name)] if column_terms and set(column_terms) <= question_terms else []
        candidates.append(
            {
                "table": table_name,
                "column": column_name,
                "semantic_type": semantic_type or core_semantic_type or "numeric_candidate",
                "core_semantic_type": core_semantic_type or semantic_type or "numeric_candidate",
                "score": float(entry.get("confidence") or 0.55),
                "matched_terms": matched_terms,
                "source": "selected_columns_fallback",
                "reason": "metric candidate derived from numeric selected column aggregate fallback",
            }
        )
    return candidates


def _ensure_candidate_reason(entry: dict[str, Any], *, role: str) -> dict[str, Any]:
    normalized = dict(entry or {})
    if str(normalized.get("reason") or "").strip():
        return normalized
    source = str(normalized.get("source") or "retrieved_context").strip()
    matched_terms = [str(term).strip() for term in (normalized.get("matched_terms") or []) if str(term).strip()]
    reason_parts = [f"{role} candidate retrieved from {source}"]
    if matched_terms:
        reason_parts.append(f"matched terms: {', '.join(matched_terms)}")
    normalized["reason"] = "; ".join(reason_parts)
    return normalized


def _prune_single_table_aggregate_context(
    selected_tables: list[dict[str, Any]],
    selected_columns: list[dict[str, Any]],
    selected_table_names: list[str],
    measure_candidates: list[dict[str, Any]],
    dimension_candidates: list[dict[str, Any]],
    filter_candidates: list[dict[str, Any]],
    join_paths: list[dict[str, Any]],
    matched_relationships: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    if measure_candidates:
        primary_table = str(measure_candidates[0].get("table") or "").strip()
    elif selected_table_names:
        primary_table = str(selected_table_names[0] or "").strip()
    else:
        primary_table = ""
    if not primary_table:
        return (
            selected_tables,
            selected_columns,
            selected_table_names,
            measure_candidates,
            [],
            filter_candidates,
            join_paths,
            matched_relationships,
        )

    pruned_tables = [entry for entry in selected_tables if str(entry.get("table") or "").strip() == primary_table]
    pruned_table_names = [primary_table]
    pruned_columns = [entry for entry in selected_columns if str(entry.get("table") or "").strip() == primary_table]
    pruned_measures = [entry for entry in measure_candidates if str(entry.get("table") or "").strip() == primary_table]
    pruned_filters = [entry for entry in filter_candidates if str(entry.get("table") or "").strip() == primary_table]
    return (
        pruned_tables,
        pruned_columns,
        pruned_table_names,
        pruned_measures,
        [],
        pruned_filters,
        [],
        [],
    )


def _normalize_planner_output(
    *,
    question: str,
    normalized_question: str,
    intent: Any,
    retrieved_context: dict[str, Any] | None,
    plan: dict[str, Any],
    selected_tables: list[dict[str, Any]],
    selected_columns: list[dict[str, Any]],
    selected_table_names: list[str],
    selected_knowledge_base: dict[str, Any],
    knowledge_base: dict[str, Any],
    warnings: list[str],
    confidence: float,
    vector_results: dict[str, Any] | None,
    vector_used: bool,
    join_paths: list[dict[str, Any]],
    fk_relationships: dict[str, Any],
    matched_relationships: list[dict[str, Any]],
    measure_candidates: list[dict[str, Any]],
    dimension_candidates: list[dict[str, Any]],
    filter_candidates: list[dict[str, Any]],
    formula_evidence: list[dict[str, Any]],
    missing_evidence_flags: dict[str, Any],
    complex_sql_plan: dict[str, Any] | None,
    legacy_route_recommendation: str,
    debug_trace_details: dict[str, Any],
) -> dict[str, Any]:
    structured_intent = intent if isinstance(intent, dict) else {}
    intent_type = str(structured_intent.get("intent_type") or "").strip().lower()
    ranking_mode_hint = str(
        (structured_intent.get("ranking_diagnostics") or {}).get("mode_hint") or ""
    ).strip()
    metric_fallback_allowed = bool(
        not structured_intent
        or structured_intent.get("needs_aggregation")
        or structured_intent.get("aggregate_function")
        or intent_type in {"aggregate", "count", "grouped_summary", "comparison"}
        or (intent_type == "ranking" and ranking_mode_hint == "grouped_aggregate")
    )
    fallback_measure_candidates = (
        _fallback_metric_candidates_from_selected_columns(selected_columns, question)
        if metric_fallback_allowed
        else []
    )
    effective_measure_candidates = list(measure_candidates or []) or fallback_measure_candidates
    metric_is_generic = bool(intent.get("metric_is_generic")) if isinstance(intent, dict) else False
    if metric_is_generic:
        effective_measure_candidates = _merge_candidate_columns(
            effective_measure_candidates,
            fallback_measure_candidates,
        )

    schema_for_resolution = knowledge_base or selected_knowledge_base
    ranking_mode = ranking_mode_hint
    base_phrase = str(
        structured_intent.get("target_entity_phrase")
        or next(iter(structured_intent.get("source_scope") or []), "")
        or ""
    ).strip()
    explicit_base = None
    if base_phrase:
        explicit_base, base_status = _resolve_join_table(base_phrase, schema_for_resolution, [])
        if base_status != "resolved":
            explicit_base = None

    requested_metric_phrase = str(
        structured_intent.get("metric_phrase")
        or next(iter(structured_intent.get("requested_metrics") or []), "")
        or ""
    ).strip()
    if requested_metric_phrase and effective_measure_candidates:
        resolved_metrics, metric_status = _resolve_role_candidate(
            requested_metric_phrase,
            effective_measure_candidates,
            allowed_tables={explicit_base} if explicit_base else None,
        )
        if metric_status == "resolved":
            effective_measure_candidates = resolved_metrics

    standalone_aggregate_scope = bool(
        intent_type == "aggregate"
        and explicit_base
        and not structured_intent.get("structured_filters")
        and not structured_intent.get("requested_dimensions")
        and effective_measure_candidates
        and all(
            str(entry.get("table") or "").strip() == explicit_base
            for entry in effective_measure_candidates
        )
        and not (structured_intent.get("join_lookup_request") or {}).get("requested")
        and not structured_intent.get("requested_output_fields")
    )
    if standalone_aggregate_scope:
        selected_tables = [
            dict(entry)
            for entry in selected_tables
            if str(entry.get("table") or "").strip() == explicit_base
        ] or [{"table": explicit_base, "confidence": 1.0, "source": "explicit_single_table_scope"}]
        selected_table_names = [explicit_base]
        selected_columns = [
            dict(entry)
            for entry in selected_columns
            if str(entry.get("table") or "").strip() == explicit_base
        ]
        join_paths = []
        matched_relationships = []
        selected_knowledge_base = {
            explicit_base: deepcopy(
                selected_knowledge_base.get(explicit_base)
                or schema_for_resolution.get(explicit_base)
                or {}
            )
        }

    query_shape = _normalize_query_shape_label(
        question=question,
        plan=plan,
        intent=intent if isinstance(intent, dict) else None,
        selected_tables=selected_tables,
        measure_candidates=effective_measure_candidates,
        dimension_candidates=dimension_candidates,
        filters=filter_candidates,
        join_paths=join_paths,
        formula_evidence=formula_evidence,
        legacy_query_shape=(complex_sql_plan or {}).get("query_shape"),
    )

    if query_shape == "single_table_aggregate":
        (
            selected_tables,
            selected_columns,
            selected_table_names,
            effective_measure_candidates,
            dimension_candidates,
            filter_candidates,
            join_paths,
            matched_relationships,
        ) = _prune_single_table_aggregate_context(
            selected_tables,
            selected_columns,
            selected_table_names,
            effective_measure_candidates,
            dimension_candidates,
            filter_candidates,
            join_paths,
            matched_relationships,
        )
        selected_knowledge_base = {
            table_name: deepcopy(selected_knowledge_base.get(table_name) or knowledge_base.get(table_name) or {})
            for table_name in selected_table_names
            if table_name in selected_knowledge_base or table_name in knowledge_base
        }

    metric_table = str((effective_measure_candidates[0] if effective_measure_candidates else {}).get("table") or "")
    sole_selected_table = selected_table_names[0] if len(selected_table_names) == 1 else ""
    grouped_mode_hint = (
        query_shape == "grouped_aggregate"
        or (query_shape == "ranking_query" and ranking_mode == "grouped_aggregate")
    )
    if grouped_mode_hint:
        primary_table = metric_table or str(explicit_base or sole_selected_table or "")
    else:
        primary_table = str(explicit_base or metric_table or sole_selected_table or "")
    implicit_filter_phrase = str(structured_intent.get("target_entity_phrase") or "").strip()
    has_explicit_filter_request = bool(
        structured_intent.get("structured_filters")
        or structured_intent.get("requested_filters")
        or plan.get("filters")
    )
    if (
        query_shape == "single_table_list"
        and primary_table
        and implicit_filter_phrase
        and not has_explicit_filter_request
        and str(structured_intent.get("intent_type") or "").strip().lower() in {"list", "filter"}
    ):
        implicit_filter, implicit_filter_status = _source_scope_as_filter(
            implicit_filter_phrase,
            schema_for_resolution,
            {primary_table},
        )
        if implicit_filter_status == "resolved" and implicit_filter is not None:
            filter_candidates = _merge_candidate_columns([implicit_filter], filter_candidates)
            selected_columns = _merge_candidate_columns(selected_columns, [implicit_filter])
            plan["filters"] = [implicit_filter]
            query_shape = "filtered_query"
    requested_dimensions = [
        str(value).strip()
        for value in (structured_intent.get("requested_dimensions") or [])
        if str(value).strip()
    ]
    dimension_status = "not_required"
    resolved_dimensions = list(dimension_candidates)
    if primary_table and requested_dimensions:
        resolved_dimensions, dimension_status = _resolve_role_candidate(
            requested_dimensions[0],
            dimension_candidates,
            allowed_tables={primary_table},
        )
        if dimension_status == "resolved":
            dimension_candidates = resolved_dimensions

    planned_filters = [
        dict(entry) for entry in (plan.get("filters") or []) if isinstance(entry, dict)
    ]
    structured_filter_clauses = [
        dict(entry)
        for entry in (structured_intent.get("structured_filters") or [])
        if isinstance(entry, dict)
    ]
    resolved_filter_candidates: list[dict[str, Any]] = []
    all_filters_resolved = bool(structured_filter_clauses)
    for clause in structured_filter_clauses:
        field_phrase = str(clause.get("field_phrase") or clause.get("field") or "").strip()
        allowed_filter_tables = None
        base_has_exact_field = any(
            str(candidate.get("table") or "").strip() == str(explicit_base or "")
            and _humanize(str(candidate.get("column") or "")) == _humanize(field_phrase)
            for candidate in filter_candidates
        )
        if base_has_exact_field and explicit_base:
            allowed_filter_tables = {explicit_base}
        else:
            field_table, field_table_status = _resolve_join_table(
                field_phrase,
                schema_for_resolution,
                [],
            )
            if field_table_status == "resolved" and field_table:
                allowed_filter_tables = {field_table}
        resolved_filter, filter_status = _resolve_role_candidate(
            field_phrase,
            filter_candidates,
            allowed_tables=allowed_filter_tables,
        )
        if filter_status != "resolved":
            all_filters_resolved = False
            break
        resolved_filter_candidates.extend(resolved_filter)
    if all_filters_resolved:
        filter_candidates = _merge_candidate_columns(resolved_filter_candidates)
    planned_filter_tables = {
        str(entry.get("table") or "").strip()
        for entry in planned_filters
        if str(entry.get("table") or "").strip()
    }
    filters_fit_primary = not planned_filter_tables or planned_filter_tables == {primary_table}
    grouped_single_table = (
        query_shape == "grouped_aggregate"
        or (query_shape == "ranking_query" and ranking_mode == "grouped_aggregate")
    )
    row_single_table = query_shape == "filtered_query" or (
        query_shape == "ranking_query" and ranking_mode != "grouped_aggregate"
    )
    dimension_fit = not requested_dimensions or dimension_status == "resolved"
    lookup_request = dict(structured_intent.get("join_lookup_request") or {})
    lookup_requested = bool(lookup_request.get("requested"))
    resolved_evidence_tables = {
        str(entry.get("table") or "").strip()
        for entry in [
            *effective_measure_candidates,
            *dimension_candidates,
            *planned_filters,
            *resolved_filter_candidates,
        ]
        if str(entry.get("table") or "").strip()
    }
    related_output_requested = bool(
        lookup_requested
        or lookup_request.get("requested_output_fields")
        or structured_intent.get("requested_output_fields")
    )
    joined_analytics_requested = bool(
        grouped_single_table
        and (
            not dimension_fit
            or any(table_name != primary_table for table_name in resolved_evidence_tables)
        )
    )
    final_single_table_scope = bool(
        standalone_aggregate_scope
        or (
            primary_table
            and filters_fit_primary
            and dimension_fit
            and resolved_evidence_tables <= {primary_table}
            and not related_output_requested
            and not joined_analytics_requested
            and (grouped_single_table or row_single_table)
        )
    )
    if final_single_table_scope:
        selected_tables = [
            dict(entry)
            for entry in selected_tables
            if str(entry.get("table") or "").strip() == primary_table
        ]
        if not selected_tables and primary_table in schema_for_resolution:
            selected_tables = [{"table": primary_table, "confidence": 1.0, "source": "explicit_single_table_scope"}]
        selected_table_names = [primary_table]
        selected_columns = [
            dict(entry)
            for entry in selected_columns
            if str(entry.get("table") or "").strip() == primary_table
        ]
        effective_measure_candidates = [
            dict(entry)
            for entry in effective_measure_candidates
            if str(entry.get("table") or "").strip() == primary_table
        ]
        dimension_candidates = [
            dict(entry)
            for entry in dimension_candidates
            if str(entry.get("table") or "").strip() == primary_table
        ]
        filter_candidates = [
            dict(entry)
            for entry in filter_candidates
            if str(entry.get("table") or "").strip() == primary_table
        ]
        join_paths = []
        matched_relationships = []
        selected_knowledge_base = {
            primary_table: deepcopy(
                selected_knowledge_base.get(primary_table)
                or schema_for_resolution.get(primary_table)
                or {}
            )
        }

    if (
        query_shape == "single_table_list"
        and len(selected_table_names) == 1
        and implicit_filter_phrase
        and not has_explicit_filter_request
        and str(structured_intent.get("intent_type") or "").strip().lower() in {"list", "filter"}
    ):
        implicit_filter, implicit_filter_status = _source_scope_as_filter(
            implicit_filter_phrase,
            schema_for_resolution,
            {selected_table_names[0]},
        )
        if implicit_filter_status == "resolved" and implicit_filter is not None:
            filter_candidates = _merge_candidate_columns([implicit_filter], filter_candidates)
            selected_columns = _merge_candidate_columns(selected_columns, [implicit_filter])
            plan["filters"] = [implicit_filter]
            query_shape = "filtered_query"

    join_candidates = _join_candidates_for_contract(join_paths, matched_relationships)
    required_joins = _required_join_predicates(join_paths)
    group_by_candidates = _group_by_candidates_for_contract(plan, dimension_candidates)
    order_by_candidates = _order_by_candidates_for_contract(plan.get("sorting"), effective_measure_candidates, dimension_candidates)
    required_evidence = _required_evidence_for_query_shape(query_shape)
    missing_evidence = _missing_evidence_list(missing_evidence_flags)
    ambiguities = _ambiguities_for_contract(selected_tables, effective_measure_candidates, dimension_candidates)
    unique_metrics = {
        (str(entry.get("table") or "").strip(), str(entry.get("column") or "").strip())
        for entry in effective_measure_candidates
        if str(entry.get("table") or "").strip() and str(entry.get("column") or "").strip()
    }
    if metric_is_generic and len(unique_metrics) > 1 and "metric_selection" not in ambiguities:
        ambiguities.append("metric_selection")
    structured_filter_clauses = (
        list((intent or {}).get("structured_filters") or [])
        if isinstance(intent, dict)
        else []
    )
    requested_filter_count = len(
        structured_filter_clauses or list((intent or {}).get("requested_filters") or [])
        if isinstance(intent, dict)
        else []
    )
    has_filter_ambiguity = (
        _has_structured_filter_ambiguity(filter_candidates, structured_filter_clauses)
        if structured_filter_clauses
        else requested_filter_count == 1 and _has_close_role_ambiguity(filter_candidates)
    )
    if requested_filter_count and has_filter_ambiguity:
        ambiguities.append("filter_selection")
    blocking_ambiguities: set[str] = set()
    structured_intent = intent if isinstance(intent, dict) else {}
    ranking_mode = str(
        (structured_intent.get("ranking_diagnostics") or {}).get("mode_hint") or ""
    ).strip()
    if (
        "table_selection" in ambiguities
        and not join_paths
        and query_shape in {"unknown", "single_table_list", "single_table_count", "single_table_aggregate", "filtered_query", "ranking_query"}
    ):
        blocking_ambiguities.add("table_selection")
    is_filtered_aggregate = query_shape == "filtered_query" and bool(
        (intent.get("aggregate_function") if isinstance(intent, dict) else None)
        or _aggregate_function_hint(question)
    )
    if (
        (query_shape in {"single_table_aggregate", "grouped_aggregate", "ranking_query"} or is_filtered_aggregate)
        and "metric_selection" in ambiguities
        and (
            metric_is_generic
            or _explicit_metric_candidate_count(question, effective_measure_candidates) != 1
        )
    ):
        blocking_ambiguities.add("metric_selection")
    if "dimension_selection" in ambiguities and (
        query_shape == "grouped_aggregate"
        or (query_shape == "ranking_query" and ranking_mode == "grouped_aggregate")
    ):
        blocking_ambiguities.add("dimension_selection")
    if "filter_selection" in ambiguities and query_shape in {"filtered_query", "ranking_query"}:
        blocking_ambiguities.add("filter_selection")
    selected_metric = None if "metric_selection" in blocking_ambiguities else (
        dict(effective_measure_candidates[0]) if effective_measure_candidates else None
    )
    selected_dimensions = [] if "dimension_selection" in blocking_ambiguities else [
        dict(entry) for entry in dimension_candidates
    ]
    grouped_dimension_required = (
        query_shape == "grouped_aggregate"
        or (query_shape == "ranking_query" and ranking_mode == "grouped_aggregate")
    )
    if grouped_dimension_required and len(selected_dimensions) != 1:
        blocking_ambiguities.add("dimension_selection")
        selected_dimensions = []
    selected_filters = [] if "filter_selection" in blocking_ambiguities else [
        dict(entry) for entry in (plan.get("filters") or [])
    ]
    raw_aggregate_function = structured_intent.get("aggregate_function")
    aggregate_function = str(raw_aggregate_function or "").strip().lower()
    if not aggregate_function and ranking_mode != "row" and metric_fallback_allowed:
        aggregate_function = _aggregate_function_hint(question)
    selected_order_by, order_by_reason, order_by_ambiguity_choices = _resolve_order_by_for_contract(
        intent=structured_intent,
        selected_tables=selected_tables,
        selected_columns=selected_columns,
        measure_candidates=effective_measure_candidates,
        dimension_candidates=dimension_candidates,
        filter_candidates=filter_candidates,
        selected_metric=selected_metric,
        aggregate_function=aggregate_function or "",
    )
    order_by_required = bool(structured_intent.get("requested_sort"))
    if order_by_required and order_by_reason:
        blocking_ambiguities.add("order_by_selection")
    resolved_limit, limit_reason = _resolve_limit_for_contract(
        query_shape=query_shape,
        aggregate_function=aggregate_function or "",
        intent=structured_intent,
        plan=plan,
    )
    if structured_intent.get("limit") is not None and limit_reason in {
        "limit_not_numeric",
        "limit_out_of_safe_range",
    }:
        blocking_ambiguities.add("limit_selection")
    plan["limit"] = resolved_limit
    selected_order_table = str((selected_order_by or {}).get("table") or "").strip()
    if (
        final_single_table_scope
        and (not selected_order_table or selected_order_table == primary_table)
        and not related_output_requested
        and not joined_analytics_requested
    ):
        missing_evidence_flags["missing_join_path"] = False
        missing_evidence_flags["missing_table"] = False
    missing_evidence = _missing_evidence_list(missing_evidence_flags)
    grouped_table_scope_is_safe = query_shape not in {"grouped_aggregate", "ranking_query"} or (
        len(selected_tables) == 1 and not join_paths
    )
    can_plan = bool(
        selected_table_names
        and query_shape not in {"unknown", "blocked_unsafe"}
        and query_shape != "multi_metric_aggregate"
        and not missing_evidence
        and not blocking_ambiguities
        and grouped_table_scope_is_safe
    )
    route_recommendation, route_reason = _route_recommendation_from_contract(
        query_shape=query_shape,
        confidence=float(confidence or 0.0),
        missing_evidence_flags=missing_evidence_flags,
        selected_tables=selected_tables,
        ambiguities=list(blocking_ambiguities),
        can_plan=can_plan,
    )
    contract_ambiguities = sorted(blocking_ambiguities)
    ambiguity_details = _ambiguity_details_for_contract(
        contract_ambiguities,
        selected_tables,
        effective_measure_candidates,
        dimension_candidates,
        filter_candidates,
        retrieved_context,
    )
    if order_by_ambiguity_choices:
        ambiguity_details.append({"type": "order_by_selection", "choices": order_by_ambiguity_choices})
    selected_having = _selected_having_for_contract(
        structured_intent,
        selected_metric,
        selected_tables,
    )
    selected_relationship_path = dict(join_paths[0]) if join_paths else None
    clause_plan = _build_clause_plan_for_contract(
        query_shape=query_shape,
        route_recommendation=route_recommendation,
        can_plan=can_plan,
        aggregate_function=aggregate_function or "",
        intent=structured_intent,
        selected_tables=selected_tables,
        selected_metric=selected_metric,
        selected_dimensions=selected_dimensions,
        selected_filters=selected_filters,
        selected_having=selected_having,
        selected_order_by=selected_order_by,
        limit=resolved_limit,
        limit_reason=limit_reason,
        join_paths=join_paths,
    )
    sorting = dict(
        ((intent or {}).get("requested_sort") or plan.get("sorting") or {})
        if isinstance(intent, dict)
        else (plan.get("sorting") or {})
    )
    evidence_summary = _evidence_summary_for_contract(
        retrieved_context,
        selected_tables=selected_tables,
        selected_columns=selected_columns,
        measure_candidates=effective_measure_candidates,
        dimension_candidates=dimension_candidates,
        filter_candidates=filter_candidates,
        join_paths=join_paths,
    )

    normalized_complex_sql_plan = dict(complex_sql_plan or {})
    if query_shape == "single_table_aggregate" and not normalized_complex_sql_plan:
        normalized_complex_sql_plan = {
            "query_shape": query_shape,
            "metric_candidates": list(effective_measure_candidates),
            "dimension_candidates": [],
            "filter_candidates": list(filter_candidates),
            "selected_tables": list(selected_tables),
            "selected_columns": list(selected_columns),
            "join_paths": [],
            "required_joins": [],
            "aggregation_type": _aggregate_function_hint(question),
            "ordering": {},
            "limit": plan.get("limit"),
            "formula_evidence": list(formula_evidence),
            "sql_skeleton_type": query_shape,
            "missing_evidence": {},
            "route_recommendation": route_recommendation,
        }
    elif normalized_complex_sql_plan:
        normalized_complex_sql_plan["query_shape"] = query_shape
        normalized_complex_sql_plan["required_joins"] = required_joins
        normalized_complex_sql_plan["having"] = list(selected_having)
        normalized_complex_sql_plan["selected_order_by"] = dict(selected_order_by or {})
        normalized_complex_sql_plan["limit"] = resolved_limit
        normalized_complex_sql_plan["clause_plan"] = dict(clause_plan)
        normalized_complex_sql_plan["route_recommendation"] = route_recommendation

    debug_trace = [
        {"stage": "question", "value": question},
        {"stage": "intent", "value": plan.get("intent")},
        {"stage": "query_shape", "value": query_shape},
        {"stage": "selected_tables", "value": list(selected_table_names)},
        {"stage": "join_count", "value": len(join_paths)},
        {"stage": "route_recommendation", "value": route_recommendation},
        {"stage": "route_reason", "value": route_reason},
        {"stage": "clause_shape", "value": clause_plan["clause_shape"]},
        {"stage": "selected_order_by", "value": dict(selected_order_by or {})},
        {"stage": "limit", "value": resolved_limit},
    ]
    if missing_evidence:
        debug_trace.append({"stage": "missing_evidence", "value": list(missing_evidence)})
    if ambiguities:
        debug_trace.append({"stage": "ambiguities", "value": list(ambiguities)})

    normalized_result = {
        "planner_contract_version": "1.0",
        "normalized_question": normalized_question,
        "intent": intent if isinstance(intent, dict) else {"intent_type": str(intent or "").strip().lower()},
        "route": route_recommendation,
        "query_shape": query_shape,
        "route_recommendation": route_recommendation,
        "selected_tables": list(selected_tables),
        "selected_table_names": list(selected_table_names),
        "selected_columns": list(selected_columns),
        "selected_metric": selected_metric,
        "selected_dimensions": selected_dimensions,
        "selected_filters": selected_filters,
        "selected_having": selected_having,
        "selected_order_by": selected_order_by,
        "clause_plan": clause_plan,
        "selected_relationship_path": selected_relationship_path,
        "aggregate_function": aggregate_function,
        "sorting": sorting,
        "metric_candidates": list(effective_measure_candidates),
        "dimension_candidates": list(dimension_candidates),
        "filter_candidates": list(filter_candidates),
        "join_candidates": join_candidates,
        "required_joins": required_joins,
        "group_by_candidates": group_by_candidates,
        "order_by_candidates": order_by_candidates,
        "limit": resolved_limit,
        "complex_sql_plan": normalized_complex_sql_plan,
        "required_evidence": required_evidence,
        "missing_evidence": missing_evidence,
        "ambiguities": contract_ambiguities,
        "ambiguity_details": ambiguity_details,
        "can_plan": can_plan,
        "blocked_reason": route_reason if route_recommendation == "blocked_unsafe" else "",
        "planner_reason": route_reason,
        "evidence_summary": evidence_summary,
        "route_reason": route_reason,
        "debug_trace": debug_trace,
        "vector_results": dict(vector_results or {}),
        "vector_used": bool(vector_used),
        # Legacy / compatibility fields
        "route_recommendation_legacy": legacy_route_recommendation,
        "missing_evidence_flags": dict(missing_evidence_flags or {}),
        "debug_trace_details": dict(debug_trace_details or {}),
        "retrieved_context": retrieved_context if isinstance(retrieved_context, dict) else {},
        "plan": plan,
        "selected_knowledge_base": selected_knowledge_base,
        "warnings": list(warnings or []),
        "knowledge_base": knowledge_base,
        "join_paths": list(join_paths),
        "fk_relationships": fk_relationships,
        "matched_relationships": list(matched_relationships or []),
        "measure_candidates": list(effective_measure_candidates),
        "formula_evidence": list(formula_evidence),
        "filters": list(filter_candidates),
        "evidence_sources": list(plan.get("evidence_sources") or []),
        "confidence": round(float(confidence or 0.0), 2),
        "route_used": "",
    }
    return normalized_result


def build_query_context(
    question: str,
    knowledge_base: dict,
    business_glossary: dict | None = None,
    use_vector_retrieval: bool = True,
    vector_retriever: VectorRetriever | None = None,
    intent: dict[str, Any] | None = None,
    retrieved_context: dict[str, Any] | None = None,
) -> dict:
    """Build a deterministic planner contract without generating SQL."""
    normalized_question = _normalize(question)
    
    if intent and retrieved_context is not None:
        return _build_query_context_from_retrieved_context(
            question,
            normalized_question,
            intent,
            retrieved_context,
            knowledge_base,
        )

    # Legacy direct-planner compatibility path. Active QueryPipeline runtime
    # always supplies hardened intent plus normalized vector evidence above.
    enriched_kb = _enriched_kb(knowledge_base)
    
    intent = _detect_intent(normalized_question)
    dimension = _detect_dimension(normalized_question, intent)
    date_range = _detect_date_range(normalized_question)
    limit = _extract_limit(normalized_question) or _default_limit_for_intent(intent)
    sorting = _detect_sorting(normalized_question)
    semantic_hints = _semantic_hints(intent, date_range, sorting)
    glossary_matches = _glossary_matches(question, business_glossary)
    
    logger.debug(f"[DEBUG] Question: {question}")
    logger.debug(f"[DEBUG] Intent: {intent}, Dimension: {dimension}, Semantic hints: {semantic_hints}")

    vector_results: dict[str, Any] = {}
    if use_vector_retrieval:
        vector_results = _retrieve_with_vector(
            question,
            enriched_kb,
            business_glossary,
            retriever=vector_retriever,
        ) or {}
    
    logger.debug(f"[DEBUG] Vector results: {vector_results.get('used_vector') if vector_results else False}")
    if vector_results:
        logger.debug(f"[DEBUG] Vector table candidates: {vector_results.get('table_names', [])}")
        logger.debug(f"[DEBUG] Vector column candidates: {[col.get('column_name') for col in vector_results.get('columns', [])[:5]]}")

    if intent == "top_n":
        sorting = {"direction": "desc", "by": "metric"}
    elif intent == "trend":
        sorting = {"direction": "asc", "by": "date"}

    grouping = []
    if dimension:
        grouping.append(dimension)
    if intent == "trend" and "month" not in grouping:
        grouping.append("month")

    plan = {
        "question": question,
        "intent": intent,
        "metric": _primary_metric_hint(semantic_hints),
        "dimension": dimension,
        "filters": [],
        "date_range": date_range,
        "grouping": grouping,
        "sorting": sorting,
        "limit": limit,
        "question_terms": _content_terms(normalized_question),
        "semantic_hints": semantic_hints,
        "matched_glossary_terms": [term for term, _ in glossary_matches],
    }

    scored_by_name: dict[str, tuple[float, list[str]]] = {}
    for table_name, table_data in enriched_kb.items():
        score, reasons = _table_score(plan, table_name, table_data, glossary_matches, vector_results)
        if score > 0:
            scored_by_name[table_name] = (score, reasons)

    if vector_results:
        for table_name in vector_results.get("table_names") or []:
            if table_name not in enriched_kb:
                continue
            if table_name in scored_by_name:
                continue
            scored_by_name[table_name] = (1.0, ["vector retrieval match"])

        for column_meta in vector_results.get("columns") or []:
            table_name = column_meta.get("table_name")
            if not table_name or table_name not in enriched_kb:
                continue
            score, reasons = scored_by_name.get(table_name, (0.0, []))
            reasons = list(reasons)
            score += 0.7
            reasons.append(f"vector column match: {column_meta.get('column_name')}")
            scored_by_name[table_name] = (score, reasons)

    scored_tables = [
        (table_name, score, reasons)
        for table_name, (score, reasons) in scored_by_name.items()
    ]
    scored_tables.sort(key=lambda item: (-item[1], item[0]))

    filters = _detect_runtime_filters(
        question,
        _candidate_tables_for_filters(enriched_kb, scored_tables, vector_results),
    )
    if not filters:
        filters = _detect_generic_value_filters(
            question,
            _candidate_tables_for_filters(enriched_kb, scored_tables, vector_results),
        )
    if filters:
        plan["filters"] = filters
        if any(filter_data.get("type") == "status" for filter_data in filters):
            plan["semantic_hints"].add("status")
        plan["metric"] = _primary_metric_hint(plan["semantic_hints"])
        rescored_by_name: dict[str, tuple[float, list[str]]] = {}
        for table_name, table_data in enriched_kb.items():
            score, reasons = _table_score(plan, table_name, table_data, glossary_matches, vector_results)
            if score > 0:
                rescored_by_name[table_name] = (score, reasons)
        if vector_results:
            for table_name in vector_results.get("table_names") or []:
                if table_name not in enriched_kb or table_name in rescored_by_name:
                    continue
                rescored_by_name[table_name] = (1.0, ["vector retrieval match"])

            for column_meta in vector_results.get("columns") or []:
                table_name = column_meta.get("table_name")
                if not table_name or table_name not in enriched_kb:
                    continue
                score, reasons = rescored_by_name.get(table_name, (0.0, []))
                reasons = list(reasons)
                score += 0.7
                reasons.append(f"vector column match: {column_meta.get('column_name')}")
                rescored_by_name[table_name] = (score, reasons)

        scored_tables = [
            (table_name, score, reasons)
            for table_name, (score, reasons) in rescored_by_name.items()
        ]
        scored_tables.sort(key=lambda item: (-item[1], item[0]))

    selected_tables = _build_selected_table_entries(
        enriched_kb,
        scored_tables,
        plan,
        glossary_matches,
        vector_results,
    )
    simple_primary_table = _primary_table_for_simple_question_from_entries(
        question,
        plan,
        [
            {
                "table": entry.get("table"),
                "score": float(entry.get("confidence") or 0.0),
                "confidence": float(entry.get("confidence") or 0.0),
            }
            for entry in selected_tables
        ],
    )
    if simple_primary_table:
        selected_tables = [
            entry
            for entry in selected_tables
            if str(entry.get("table", "")).strip() == simple_primary_table
        ]
    selected_names = [entry["table"] for entry in selected_tables if entry.get("table") in enriched_kb]
    if len(selected_names) != len(selected_tables):
        selected_tables = [entry for entry in selected_tables if entry.get("table") in enriched_kb]
    
    logger.debug(f"[DEBUG] Selected tables before join path computation: {selected_names}")
    
    # Build FK relationship graph from knowledge base
    fk_graph = _build_fk_relationship_graph(enriched_kb)
    logger.debug(f"[DEBUG] FK relationships loaded: {len(fk_graph)} tables with relationships")
    for table_name, edges in list(fk_graph.items())[:3]:
        logger.debug(f"[DEBUG]   {table_name}: {len(edges['outgoing'])} outgoing, {len(edges['incoming'])} incoming")
    
    # Compute join paths between selected tables
    join_paths = [] if simple_primary_table else _compute_join_paths(selected_names, enriched_kb)
    logger.debug(f"[DEBUG] Computed {len(join_paths)} join paths between selected tables")
    for jp in join_paths[:3]:
        logger.debug(f"[DEBUG]   {jp['from_table']} -> {jp['to_table']} (length: {jp['length']})")

    if not simple_primary_table:
        selected_names, selected_tables = _promote_join_path_tables(
            selected_names,
            selected_tables,
            enriched_kb,
            plan,
            join_paths,
        )
        join_paths = _compute_join_paths(selected_names, enriched_kb)
    logger.debug(f"[DEBUG] Selected tables after join-path promotion: {selected_names}")
    logger.debug(f"[DEBUG] Recomputed {len(join_paths)} join paths after promotion")

    reduced_kb = {table_name: deepcopy(enriched_kb[table_name]) for table_name in selected_names}
    warnings = []
    if len(reduced_kb) < len(enriched_kb):
        warnings.append(f"Using {len(reduced_kb)} relevant table(s) instead of the full schema.")
    if intent in {"list", "top_n"}:
        warnings.append("Read-only row limits stay enabled for list-style questions.")
    if vector_results and not vector_results.get("used_vector"):
        warnings.append("Vector retrieval was unavailable; using KB and glossary rules only.")
    if not selected_names:
        warnings.append("Planner could not isolate a table safely from the available schema evidence.")

    overall_confidence = round(
        sum(entry["confidence"] for entry in selected_tables) / max(len(selected_tables), 1),
        2,
    )
    selected_columns = [
        {
            "table": entry["table"],
            **column_entry,
        }
        for entry in selected_tables
        for column_entry in entry.get("selected_columns", [])
    ]
    
    logger.debug(f"[DEBUG] Selected columns before missing table addition: {[(col['table'], col['column']) for col in selected_columns[:10]]}")
    
    # Add missing tables if selected columns belong to tables not in selected_tables
    # DISABLED: This feature changes table selection behavior and may affect rule-based generator
    # Re-enable after investigating impact on rule-based generator and test expectations
    # selected_names, selected_columns = _add_missing_tables_for_columns(
    #     selected_names,
    #     selected_columns,
    #     enriched_kb,
    # )
    
    logger.debug(f"[DEBUG] Selected columns: {[(col['table'], col['column']) for col in selected_columns[:10]]}")
    
    metric_from_selected_columns = _infer_metric_from_selected_columns(plan, selected_tables)
    metric_from_glossary = _infer_metric_from_glossary_matches(glossary_matches, enriched_kb)
    if _should_preserve_simple_list_metric(plan):
        if metric_from_selected_columns in {"money", "quantity", "percentage"}:
            plan["metric"] = metric_from_selected_columns
        elif metric_from_glossary in {"money", "quantity", "percentage"}:
            plan["metric"] = metric_from_glossary
        else:
            plan["metric"] = metric_from_selected_columns
    else:
        plan["metric"] = None

    measure_candidates: list[dict[str, Any]] = []
    dimension_candidates: list[dict[str, Any]] = []
    filter_candidates = list(plan.get("filters") or [])
    formula_evidence = list(plan.get("formula_evidence") or [])
    legacy_query_shape = _derive_complex_query_shape(
        plan,
        None,
        selected_tables,
        measure_candidates,
        dimension_candidates,
        filter_candidates,
        join_paths,
        formula_evidence,
    )

    missing_evidence_flags = _detect_missing_evidence(
        plan,
        selected_tables,
        selected_columns,
        join_paths,
        plan.get("requested_metrics", []),
        plan.get("requested_dimensions", []),
        plan.get("requested_filters", []),
        measure_candidates,
        dimension_candidates,
        filter_candidates,
        formula_evidence,
        legacy_query_shape,
    )

    legacy_route_recommendation = _compute_route_recommendation(
        plan,
        missing_evidence_flags,
        overall_confidence,
        selected_tables,
        legacy_query_shape,
    )

    complex_sql_plan = _build_complex_sql_plan(
        None,
        plan,
        selected_tables,
        selected_columns,
        measure_candidates,
        dimension_candidates,
        filter_candidates,
        join_paths,
        formula_evidence,
        missing_evidence_flags,
        legacy_route_recommendation,
        legacy_query_shape,
    )

    debug_trace_details = _build_debug_trace(
        question,
        plan,
        selected_tables,
        selected_columns,
        join_paths,
        missing_evidence_flags,
        overall_confidence,
        legacy_route_recommendation,
    )

    return _normalize_planner_output(
        question=question,
        normalized_question=normalized_question,
        intent=intent,
        retrieved_context={},
        plan=plan,
        selected_tables=selected_tables,
        selected_columns=selected_columns,
        selected_table_names=selected_names,
        selected_knowledge_base=reduced_kb,
        knowledge_base=enriched_kb,
        warnings=warnings,
        confidence=overall_confidence,
        vector_results=vector_results,
        vector_used=bool(vector_results and vector_results.get("used_vector")),
        join_paths=join_paths,
        fk_relationships=fk_graph,
        matched_relationships=[],
        measure_candidates=measure_candidates,
        dimension_candidates=dimension_candidates,
        filter_candidates=filter_candidates,
        formula_evidence=formula_evidence,
        missing_evidence_flags=missing_evidence_flags,
        complex_sql_plan=complex_sql_plan,
        legacy_route_recommendation=legacy_route_recommendation,
        debug_trace_details=debug_trace_details,
    )
