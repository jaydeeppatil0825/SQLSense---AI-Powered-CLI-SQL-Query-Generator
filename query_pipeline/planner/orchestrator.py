"""
Structured query planning and relevant-table selection.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import date
from typing import Any
import re

from kb_pipeline.schema_facts import (
    enrich_knowledge_base_schema_facts,
    resolved_semantic_type,
)
from kb_pipeline.relationship_graph import (
    build_relationship_graph,
)
from kb_pipeline.vector import VectorRetriever
from query_pipeline.planner.filter_resolver import (
    _STATUS_VALUE_TOKENS,
    _apply_implicit_sample_filter_contract,
    _build_sample_value_filter,
    _detect_generic_value_filters,
    _detect_runtime_filters,
    _has_structured_filter_ambiguity,
    _joined_aggregate_filter_contract,
    _resolve_interval_filters_for_scope,
    _source_scope_as_filter,
    _source_scope_as_filters,
    _structured_filter_entries,
    build_filter_decision_contract,
)
from query_pipeline.planner.contract_builder import (
    _ambiguities_for_contract,
    _ambiguity_details_for_contract,
    _build_clause_plan_for_contract,
    _build_complex_sql_plan,
    _build_debug_trace,
    _compute_route_recommendation,
    _derive_complex_query_shape,
    _detect_missing_evidence,
    _ensure_candidate_reason,
    _evidence_summary_for_contract,
    _group_by_candidates_for_contract,
    _missing_evidence_list,
    _normalize_query_shape_label,
    _required_evidence_for_query_shape,
    _route_recommendation_from_contract,
)
from query_pipeline.planner.confidence import (
    _compute_intent_confidence,
    _has_close_role_ambiguity,
)
from query_pipeline.planner.having_resolver import _selected_having_for_contract
from query_pipeline.planner.phase7_bfs_join_resolver import resolve_safe_multi_hop_path
from query_pipeline.planner.phase9b_cache import cached_multi_hop_path
from query_pipeline.planner.join_resolver import (
    _WEAK_CONTEXT_WARNING,
    _all_table_outputs,
    _apply_join_lookup_contract,
    _apply_joined_aggregate_contract,
    _column_phrase_score,
    _has_strong_joined_evidence,
    _join_candidates_for_contract,
    _join_failure_context,
    _joined_aggregate_failure_context,
    _qualified_output,
    _remove_weak_context_warning,
    _resolve_join_output_field,
)
from query_pipeline.planner.query_predicates import (
    _DIMENSION_SEMANTIC_TYPES,
    _GENERIC_ROLE_TERMS,
    _NON_METRIC_SEMANTIC_TYPES,
    _NUMERIC_METRIC_SEMANTIC_TYPES,
    _SCORING_TIERS,
    _aggregate_function_hint,
    _explicit_metric_candidate_count,
    _expand_selected_tables,
    _extract_limit,
    _glossary_alias_hits_question,
    _glossary_mapped_tables,
    _has_strong_glossary_table_match,
    _is_simple_primary_table_question,
    _merge_candidate_columns,
    _primary_table_for_simple_question_from_entries,
    _question_requests_multiple_metrics,
    _required_join_predicates,
    _resolve_join_table,
    _table_phrase_score,
    strip_leading_status_entity_modifier,
)
from query_pipeline.planner.shape_router import classify_query_shape
from query_pipeline.planner.ranking_resolver import (
    _build_ranking_decision_for_contract,
    _order_by_candidates_for_contract,
    _order_candidate_identity,
    _resolve_limit_for_contract,
    _resolve_order_by_for_contract,
)
from query_pipeline.planner.text_utils import (
    _content_terms,
    _humanize,
    _normalize,
    _normalize_identifier,
    _safe_float,
    _singularize_token,
    _tokenize,
)
from query_pipeline.planner.role_resolver import (
    build_dimension_decision_contract,
    _candidate_is_numeric_metric,
    _exact_table_column_candidates,
    _fallback_metric_candidates_from_selected_columns,
    _graph_selected_evidence_entry,
    _prune_single_table_aggregate_context,
    _rank_role_candidates,
    _resolve_count_base_table,
    _resolve_entity_display_dimension,
    _resolve_metric_with_modifier,
    _resolve_owned_monetary_metric_from_schema,
    _resolve_related_sales_amount_metric,
    _resolve_role_candidate,
    _selected_evidence_entry,
    _source_selected_evidence_entry,
)
from utils.logger import get_logger

logger = get_logger()

_UNSAFE_QUERY_RE = re.compile(
    r"\b(insert|update|delete|drop|alter|truncate|create|grant|revoke)\b",
    re.IGNORECASE,
)
_UNSUPPORTED_CONTROL_RE = re.compile(
    r"\b(?:count\s+distinct|distinct\s+count|unique\b.*\bcount|count\b.*\bunique)\b",
    re.IGNORECASE,
)
_NUMERIC_AGGREGATES = {"sum", "avg"}


def _has_unsupported_control_construct(intent: dict[str, Any] | None, question: str) -> bool:
    return bool((intent or {}).get("unsupported_constructs")) or bool(_UNSUPPORTED_CONTROL_RE.search(question or ""))


def _explicit_aggregate_target_is_nonnumeric(
    metric_phrase: str,
    aggregate_function: str,
    knowledge_base: dict[str, Any],
) -> bool:
    if str(aggregate_function or "").strip().lower() not in _NUMERIC_AGGREGATES:
        return False
    phrase_tokens = {
        _singularize_token(token)
        for token in _tokenize(metric_phrase)
        if token
    }
    if not phrase_tokens:
        return False
    for table_name, table_data in (knowledge_base or {}).items():
        table_tokens = {
            _singularize_token(token)
            for token in _tokenize(str(table_name))
            if token
        }
        for column in (table_data or {}).get("columns", []) or []:
            column_name = str(column.get("name") or "").strip()
            if not column_name:
                continue
            column_tokens = {
                _singularize_token(token)
                for token in _tokenize(column_name)
                if token
            }
            if not column_tokens:
                continue
            qualified_tokens = table_tokens | column_tokens
            if phrase_tokens == column_tokens or phrase_tokens <= qualified_tokens:
                candidate = {
                    "table": str(table_name),
                    "column": column_name,
                    "type": column.get("type") or column.get("data_type") or "",
                    "data_type": column.get("data_type") or column.get("type") or "",
                    "semantic_type": resolved_semantic_type(column),
                    "core_semantic_type": resolved_semantic_type(column),
                    "is_measure": bool(column.get("is_measure")),
                    "is_date": bool(column.get("is_date")),
                }
                return not _candidate_is_numeric_metric(candidate)
    return False


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


def _enriched_kb(knowledge_base: dict) -> dict:
    if not knowledge_base:
        return {}
    return enrich_knowledge_base_schema_facts(
        deepcopy(knowledge_base),
        infer_relationships=False,
    )


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
    cache_store: Any | None = None,
    cache_database_identity: dict[str, Any] | None = None,
) -> dict:
    full_knowledge_base = dict(knowledge_base or {})
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
    filters, _interval_filter_reason = _resolve_interval_filters_for_scope(
        filters,
        list((intent or {}).get("structured_filters") or []),
        knowledge_base=knowledge_base,
        allowed_tables={name for name in selected_table_names if name},
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

    interval_raw_phrases = {
        _normalize(str(entry.get("raw_phrase") or ""))
        for entry in ((intent or {}).get("structured_filters") or [])
        if isinstance(entry, dict)
        and entry.get("filter_kind") == "date_interval"
        and str(entry.get("raw_phrase") or "").strip()
    }
    requested_filters_for_missing = [
        value for value in requested_filters
        if _normalize(str(value or "")) not in interval_raw_phrases
    ]
    missing_evidence_flags = _detect_missing_evidence(
        plan,
        selected_tables,
        selected_columns,
        join_paths,
        required_metric_phrases,
        requested_dimensions,
        requested_filters_for_missing,
        effective_measure_candidates,
        dimension_candidates,
        filters,
        retrieved_context.get("formula_evidence") or [],
        legacy_query_shape,
    )
    if _interval_filter_reason:
        missing_evidence_flags["missing_filter_column"] = True
    if ranking_mode_hint == "row":
        missing_evidence_flags["missing_metric"] = False
        missing_evidence_flags["missing_formula_evidence"] = False
    if (
        ranking_mode_hint == "grouped_aggregate"
        and str((intent or {}).get("aggregate_function") or "").strip().lower() == "count"
    ):
        missing_evidence_flags["missing_metric"] = False
        missing_evidence_flags["missing_formula_evidence"] = False
    intent_missing_phrases = set((intent or {}).get("missing_phrases") or [])
    if "target_entity_phrase" in intent_missing_phrases:
        missing_evidence_flags["missing_table"] = True
    if "metric_phrase" in intent_missing_phrases:
        missing_evidence_flags["missing_metric"] = True
    if "grouping_phrase" in intent_missing_phrases:
        missing_evidence_flags["missing_dimension"] = True
    if _has_unsupported_control_construct(intent, question):
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
        full_knowledge_base=knowledge_base,
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
    joined_aggregate_recovery_needed = bool(
        normalized_result.get("query_shape") in {"grouped_aggregate", "ranking_query"}
        and (
            normalized_result.get("missing_evidence")
            and any(
                item in {"missing_metric", "missing_join_path"}
                for item in (normalized_result.get("missing_evidence") or [])
            )
        )
        and (
            (intent or {}).get("target_entity_phrase")
            or (intent or {}).get("requested_metrics")
        )
    )
    skip_joined_aggregate_contract = bool(
        normalized_result.get("query_shape") in {"grouped_aggregate", "ranking_query"}
        and len(normalized_result.get("selected_table_names") or []) == 1
        and not normalized_result.get("selected_relationship_path")
        and not normalized_result.get("required_joins")
        and not joined_aggregate_recovery_needed
    )
    if not skip_joined_aggregate_contract:
        joined_aggregate_result = _apply_joined_aggregate_contract(
            normalized_result,
            knowledge_base,
            cache_store=cache_store,
            cache_database_identity=cache_database_identity,
        )
        if joined_aggregate_result is not normalized_result:
            return joined_aggregate_result
    join_lookup_result = _apply_join_lookup_contract(
        normalized_result,
        knowledge_base,
        cache_store=cache_store,
        cache_database_identity=cache_database_identity,
    )
    return _apply_multi_hop_join_lookup_contract(
        join_lookup_result,
        knowledge_base,
        cache_store=cache_store,
        cache_database_identity=cache_database_identity,
    )


def _apply_multi_hop_join_lookup_contract(
    context: dict[str, Any],
    knowledge_base: dict[str, Any],
    cache_store: Any | None = None,
    cache_database_identity: dict[str, Any] | None = None,
) -> dict[str, Any]:
    intent = context.get("intent") if isinstance(context.get("intent"), dict) else {}
    lookup = dict(intent.get("join_lookup_request") or {})
    if (
        not lookup.get("requested")
        or context.get("selected_join_path")
        or context.get("route_recommendation") == "deterministic_sql_required"
        or intent.get("needs_aggregation")
        or intent.get("needs_grouping")
    ):
        return context

    retrieved = context.get("retrieved_context") if isinstance(context.get("retrieved_context"), dict) else {}
    retrieved_tables = [dict(entry) for entry in (retrieved.get("matched_tables") or []) if isinstance(entry, dict)]
    retrieved_columns = [dict(entry) for entry in (retrieved.get("matched_columns") or []) if isinstance(entry, dict)]
    base_phrase = str(lookup.get("base_entity_phrase") or intent.get("target_entity_phrase") or "").strip()
    base_filter_phrase = ""
    if re.search(r"\s+for\s+", base_phrase, flags=re.IGNORECASE):
        base_phrase, base_filter_phrase = [
            part.strip()
            for part in re.split(r"\s+for\s+", base_phrase, maxsplit=1, flags=re.IGNORECASE)
        ]
    base_table, base_status = _resolve_join_table(
        strip_leading_status_entity_modifier(base_phrase),
        knowledge_base,
        retrieved_tables,
    )
    requested_fields = [str(value).strip() for value in (lookup.get("requested_output_fields") or []) if str(value).strip()]
    resolved_fields: list[dict[str, Any]] = []
    for phrase in requested_fields:
        field, status = _resolve_join_output_field(phrase, knowledge_base, retrieved_columns)
        if status == "resolved" and field:
            resolved_fields.append(dict(field))
    if (base_status != "resolved" or not base_table) and resolved_fields:
        base_table = str(resolved_fields[0].get("table") or "")
        base_status = "resolved" if base_table else "missing"
    if base_status != "resolved" or not base_table:
        return context

    field_tables = {str(field.get("table") or "") for field in resolved_fields if str(field.get("table") or "")}
    target_tables: set[str] = set(field_tables - {base_table})
    related_phrase = str(lookup.get("related_request_phrase") or "").strip()
    related_table = ""
    if related_phrase:
        resolved_related_table, target_status = _resolve_join_table(related_phrase, knowledge_base, retrieved_tables)
        if target_status == "resolved" and resolved_related_table:
            related_table = resolved_related_table
            target_tables.add(related_table)

    target_tables.discard(base_table)
    if not target_tables:
        return context

    graph = build_relationship_graph(knowledge_base, infer_relationships=False)
    path_results = []
    last_failure = {"status": "no_safe_path", "reason": "explicit field tables do not resolve to one safe multi-hop path"}
    for target_table in sorted(target_tables):
        candidate = cached_multi_hop_path(
            cache_store=cache_store,
            database_identity=cache_database_identity,
            knowledge_base=knowledge_base,
            relationship_graph=graph,
            base_table=base_table,
            target_table=target_table,
            max_depth=2,
            candidate_tables=sorted({base_table, target_table, *field_tables}),
            planner_options={"shape": "multi_hop_join_lookup"},
            compute=lambda target_table=target_table: resolve_safe_multi_hop_path(
                base_table=base_table,
                target_table=target_table,
                relationship_graph=graph,
                schema=knowledge_base,
                max_depth=2,
            ),
        )
        if candidate.get("status") != "unique_safe_path":
            last_failure = candidate
            continue
        candidate_tables = set(candidate.get("tables") or [])
        if field_tables and not field_tables <= candidate_tables:
            continue
        path_results.append(candidate)
    result = path_results[0] if len(path_results) == 1 else {
        "status": "ambiguous_path" if path_results else str(last_failure.get("status") or "no_safe_path"),
        "reason": "explicit field tables do not resolve to one safe multi-hop path" if path_results else str(last_failure.get("reason") or ""),
    }
    if result["status"] != "unique_safe_path":
        planned = dict(context)
        planned.update(
            {
                "query_shape": "joined_lookup",
                "route": "cannot_plan_safely",
                "route_recommendation": "cannot_plan_safely",
                "route_reason": f"{result['status']}: {result['reason']}",
                "planner_reason": f"{result['status']}: {result['reason']}",
                "can_plan": False,
                "selected_join_path": None,
                "missing_evidence": list(dict.fromkeys([*(planned.get("missing_evidence") or []), result["status"]])),
            }
        )
        return planned
    if len(result.get("path") or []) != 2:
        return context

    selected_join_path = {
        "base_table": base_table,
        "joined_tables": result["tables"][1:],
        "edges": result["path"],
        "path_source": "relationship_graph",
        "ambiguity_status": "resolved",
    }
    output_columns = (
        [
            _qualified_output(str(field["table"]), str(field["column"]), "requested_output_field")
            for field in resolved_fields
        ]
        if resolved_fields
        else [
            output
            for table_name in result["tables"]
            for output in _all_table_outputs(table_name, knowledge_base, "multi_hop_joined_lookup_projection")
        ]
    )
    selected_tables = [
        {
            "table": table_name,
            "confidence": 1.0,
            "selected_columns": [
                {"column": output["column"], "confidence": 1.0, "reason": output["source"]}
                for output in output_columns
                if output["table"] == table_name
            ],
        }
        for table_name in result["tables"]
    ]
    resolved_limit = intent.get("limit") if intent.get("limit") is not None else 50
    selected_filters = [
        dict(entry)
        for entry in (context.get("selected_filters") or [])
        if isinstance(entry, dict) and str(entry.get("table") or "") in set(result["tables"])
    ]
    if related_table and selected_filters:
        related_filters = [
            dict(entry)
            for entry in selected_filters
            if str(entry.get("table") or "") == related_table
        ]
        if related_filters:
            selected_filters = related_filters
    if base_filter_phrase:
        path_filters, filter_status = _source_scope_as_filters(
            base_filter_phrase,
            knowledge_base,
            set(result["tables"]),
        )
        if filter_status == "resolved" and path_filters:
            seen_filters = {
                (
                    str(entry.get("table") or ""),
                    str(entry.get("column") or ""),
                    str(entry.get("value") or ""),
                )
                for entry in selected_filters
            }
            selected_filters.extend(
                dict(entry)
                for entry in path_filters
                if (
                    str(entry.get("table") or ""),
                    str(entry.get("column") or ""),
                    str(entry.get("value") or ""),
                )
                not in seen_filters
            )
    planned = dict(context)
    planned.update(
        {
            "query_shape": "joined_lookup",
            "route": "deterministic_sql_required",
            "route_recommendation": "deterministic_sql_required",
            "route_reason": "joined lookup can be generated from one safe two-edge Relationship Graph path",
            "planner_reason": "joined lookup can be generated from one safe two-edge Relationship Graph path",
            "can_plan": True,
            "selected_tables": selected_tables,
            "selected_join_path": selected_join_path,
            "selected_relationship_path": selected_join_path,
            "selected_table_names": result["tables"],
            "selected_knowledge_base": {
                table_name: deepcopy(knowledge_base[table_name])
                for table_name in result["tables"]
            },
            "selected_columns": list(output_columns),
            "selected_output_columns": list(output_columns),
            "selected_filters": selected_filters,
            "join_paths": [selected_join_path],
            "limit": resolved_limit,
            "missing_evidence": [],
            "ambiguities": [],
            "ambiguity_details": [],
        }
    )
    clause_plan = dict(planned.get("clause_plan") or {})
    clause_plan["selected_join_path"] = selected_join_path
    clause_plan["clause_shape"] = "joined_lookup"
    clause_plan["limit"] = resolved_limit
    clause_plan["requires"] = {
        "aggregate": False,
        "metric": False,
        "dimension": False,
        "where": bool(planned.get("selected_filters")),
        "having": False,
        "order_by": False,
        "limit": True,
        "join": True,
        "requested_fields": True,
        "selected_output_columns": True,
    }
    clause_plan["decision_path"] = [
        {
            "node": node_name,
            "status": "not_required" if node_name == "where" and not planned.get("selected_filters") else "resolved",
            "reason": (
                "no row-level filter was requested"
                if node_name == "where" and not planned.get("selected_filters")
                else f"{node_name.replace('_', ' ')} resolved from deterministic evidence"
            ),
        }
        for node_name in (
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
    ]
    planned["clause_plan"] = clause_plan
    planned["plan"] = {
        **dict(planned.get("plan") or {}),
        "limit": resolved_limit,
    }
    planned["complex_sql_plan"] = {
        "query_shape": "joined_lookup",
        "selected_tables": selected_tables,
        "selected_columns": list(output_columns),
        "selected_filters": list(planned.get("selected_filters") or []),
        "selected_join_path": selected_join_path,
        "limit": resolved_limit,
        "route_recommendation": "deterministic_sql_required",
    }
    return planned


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
    full_knowledge_base: dict[str, Any] | None = None,
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
    full_knowledge_base = dict(full_knowledge_base or knowledge_base or {})
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
    invalid_explicit_metric_type = _explicit_aggregate_target_is_nonnumeric(
        requested_metric_phrase,
        str(structured_intent.get("aggregate_function") or ""),
        full_knowledge_base,
    )
    if invalid_explicit_metric_type:
        effective_measure_candidates = []
    if requested_metric_phrase and effective_measure_candidates:
        resolved_metrics, metric_status = _resolve_role_candidate(
            requested_metric_phrase,
            effective_measure_candidates,
            allowed_tables={explicit_base} if explicit_base else None,
        )
        if metric_status != "resolved":
            modifier_metric, _modifier_phrase, modifier_status = _resolve_metric_with_modifier(
                requested_metric_phrase,
                effective_measure_candidates,
            )
            if modifier_status == "resolved" and modifier_metric:
                resolved_metrics = [dict(modifier_metric)]
                metric_status = "resolved"
        if metric_status != "resolved" and ranking_mode_hint == "grouped_aggregate":
            dimension_phrase = str(
                next(iter(structured_intent.get("requested_dimensions") or []), "")
                or structured_intent.get("grouping_phrase")
                or ""
            ).strip()
            resolved_dimensions_for_metric, dimension_status_for_metric = _resolve_role_candidate(
                dimension_phrase,
                dimension_candidates,
                role="dimension",
            )
            if dimension_status_for_metric == "resolved" and len(resolved_dimensions_for_metric) == 1:
                dimension_table_for_metric = str(resolved_dimensions_for_metric[0].get("table") or "").strip()
                if dimension_table_for_metric:
                    resolved_metrics, metric_status = _resolve_role_candidate(
                        requested_metric_phrase,
                        effective_measure_candidates,
                        allowed_tables={dimension_table_for_metric},
                    )
        if metric_status == "resolved":
            effective_measure_candidates = resolved_metrics
            if (
                intent_type == "ranking"
                and ranking_mode_hint == "grouped_aggregate"
                and explicit_base
                and len(resolved_metrics) == 1
                and str(resolved_metrics[0].get("table") or "").strip() == explicit_base
                and not structured_intent.get("requested_dimensions")
            ):
                structured_intent = dict(structured_intent)
                diagnostics = dict(structured_intent.get("ranking_diagnostics") or {})
                diagnostics["mode_hint"] = "row"
                structured_intent["ranking_diagnostics"] = diagnostics
                structured_intent["aggregate_function"] = None
                structured_intent["needs_grouping"] = False
                structured_intent["requested_dimensions"] = []
                structured_intent["grouping_phrase"] = ""
                intent = structured_intent
                ranking_mode_hint = "row"
                ranking_mode = "row"

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
    source_scope_filter_phrase = str(
        next(iter(structured_intent.get("source_scope") or []), "")
        or structured_intent.get("source_scope_phrase")
        or ""
    ).strip()
    target_filter_phrase = str(structured_intent.get("target_entity_phrase") or "").strip()
    implicit_filter_phrase = source_scope_filter_phrase
    if not implicit_filter_phrase and grouped_mode_hint:
        metric_filter_phrase = str(
            structured_intent.get("metric_phrase")
            or next(iter(structured_intent.get("requested_metrics") or []), "")
            or ""
        ).strip()
        requested_dimension_phrase = str(
            next(iter(structured_intent.get("requested_dimensions") or []), "")
            or structured_intent.get("grouping_phrase")
            or ""
        ).strip()
        if metric_filter_phrase:
            implicit_filter_phrase = metric_filter_phrase
        elif (
            str(structured_intent.get("intent_type") or "").strip().lower() != "ranking"
            and target_filter_phrase
            and _humanize(target_filter_phrase) != _humanize(requested_dimension_phrase)
        ):
            implicit_filter_phrase = target_filter_phrase
    if not implicit_filter_phrase and not grouped_mode_hint:
        implicit_filter_phrase = target_filter_phrase
    structured_filter_entries = [
        entry for entry in (structured_intent.get("structured_filters") or [])
        if isinstance(entry, dict)
    ]
    lookup_request = dict(structured_intent.get("join_lookup_request") or {})
    lookup_requested = bool(lookup_request.get("requested"))
    has_explicit_filter_request = bool(
        structured_filter_entries
        or structured_intent.get("requested_filters")
        or plan.get("filters")
    )
    has_explicit_non_interval_filter_request = bool(
        [entry for entry in structured_filter_entries if entry.get("filter_kind") != "date_interval"]
        or structured_intent.get("requested_filters")
    )
    if (
        query_shape in {"single_table_list", "single_table_count", "filtered_query", "grouped_aggregate", "ranking_query", "joined_lookup"}
        and primary_table
        and implicit_filter_phrase
        and not has_explicit_non_interval_filter_request
        and not lookup_requested
        and not structured_intent.get("requested_output_fields")
        and (
            str(structured_intent.get("intent_type") or "").strip().lower() in {"list", "filter", "count"}
            or grouped_mode_hint
            or str(structured_intent.get("intent_type") or "").strip().lower() == "ranking"
        )
    ):
        implicit_filters, implicit_filter_status = _source_scope_as_filters(
            implicit_filter_phrase,
            schema_for_resolution,
            {primary_table},
        )
        if implicit_filter_status == "resolved" and implicit_filters:
            filter_candidates = _merge_candidate_columns(implicit_filters, filter_candidates)
            selected_columns = _merge_candidate_columns(selected_columns, implicit_filters)
            existing_filters = [
                dict(entry) for entry in (plan.get("filters") or []) if isinstance(entry, dict)
            ]
            existing_filters.extend(dict(entry) for entry in implicit_filters)
            plan["filters"] = existing_filters
            if query_shape in {"single_table_list", "filtered_query", "joined_lookup"}:
                query_shape = "filtered_query"
                row_single_table = True
    if (
        query_shape in {"single_table_list", "single_table_count", "filtered_query", "grouped_aggregate", "ranking_query", "joined_lookup"}
        and selected_table_names
        and not has_explicit_non_interval_filter_request
    ):
        runtime_filter_scope = {
            table_name: full_knowledge_base.get(table_name) or schema_for_resolution.get(table_name) or {}
            for table_name in selected_table_names
            if table_name in full_knowledge_base or table_name in schema_for_resolution
        }
        runtime_filters = _detect_runtime_filters(question, runtime_filter_scope)
        if runtime_filters:
            existing_signatures = {
                (
                    str(entry.get("table") or ""),
                    str(entry.get("column") or ""),
                    str(entry.get("value") or ""),
                )
                for entry in (plan.get("filters") or [])
                if isinstance(entry, dict)
            }
            new_runtime_filters = [
                dict(entry)
                for entry in runtime_filters
                if (
                    str(entry.get("table") or ""),
                    str(entry.get("column") or ""),
                    str(entry.get("value") or ""),
                )
                not in existing_signatures
            ]
            if new_runtime_filters:
                filter_candidates = _merge_candidate_columns(new_runtime_filters, filter_candidates)
                selected_columns = _merge_candidate_columns(selected_columns, new_runtime_filters)
                plan["filters"] = [
                    *[dict(entry) for entry in (plan.get("filters") or []) if isinstance(entry, dict)],
                    *new_runtime_filters,
                ]
                if query_shape in {"single_table_list", "filtered_query", "joined_lookup"}:
                    query_shape = "filtered_query"
                    row_single_table = True
    requested_dimensions = [
        str(value).strip()
        for value in (structured_intent.get("requested_dimensions") or [])
        if str(value).strip()
    ]
    dimension_status = "not_required"
    resolved_dimensions = list(dimension_candidates)
    if primary_table and requested_dimensions:
        dimension_phrase = requested_dimensions[0]
        global_dimension_result = _rank_role_candidates(
            dimension_phrase,
            dimension_candidates,
            role="dimension",
        )
        global_selected = dict((global_dimension_result.get("selected") or {}).get("candidate") or {})
        global_tier = str((global_dimension_result.get("selected") or {}).get("tier") or "")
        global_table = str(global_selected.get("table") or "").strip()
        if (
            global_dimension_result.get("status") == "resolved"
            and global_table
            and global_table != primary_table
            and global_tier in {"exact_normalized_column", "owner_qualified_exact"}
        ):
            resolved_dimensions = [global_selected]
            dimension_status = "resolved"
        else:
            resolved_dimensions, dimension_status = _resolve_role_candidate(
                dimension_phrase,
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
        preferred_filter_table = str(explicit_base or primary_table or sole_selected_table or "").strip()
        preferred_exact_candidates = _exact_table_column_candidates(
            preferred_filter_table,
            field_phrase,
            schema_for_resolution,
        )
        if preferred_exact_candidates:
            filter_candidates = _merge_candidate_columns(preferred_exact_candidates, filter_candidates)
        base_has_exact_field = any(
            str(candidate.get("table") or "").strip() == preferred_filter_table
            and _humanize(str(candidate.get("column") or "")) == _humanize(field_phrase)
            for candidate in filter_candidates
        )
        if base_has_exact_field and preferred_filter_table:
            allowed_filter_tables = {preferred_filter_table}
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
            owner_context=preferred_filter_table,
        )
        sample_filter_tables = (
            {preferred_filter_table}
            if preferred_filter_table
            else {selected_table_names[0]} if len(selected_table_names) == 1 else set()
        )
        if filter_status != "resolved" and sample_filter_tables:
            sample_filter, sample_status = _build_sample_value_filter(
                value_phrase=str(clause.get("value_phrase") or clause.get("value") or ""),
                knowledge_base=full_knowledge_base,
                allowed_tables=sample_filter_tables,
                owner_table=next(iter(sample_filter_tables)) if len(sample_filter_tables) == 1 else None,
                source="structured_sample_value_filter",
            )
            if sample_status == "resolved" and sample_filter is not None:
                resolved_filter = [sample_filter]
                filter_status = "resolved"
        if filter_status != "resolved":
            all_filters_resolved = False
            break
        for candidate in resolved_filter:
            raw_phrase = str(clause.get("raw_phrase") or "").strip()
            resolved_value = candidate.get("value", clause.get("value", clause.get("value_phrase", "")))
            selected_filter = {
                "type": "value",
                "table": str(candidate.get("table") or ""),
                "column": str(candidate.get("column") or ""),
                "value": resolved_value,
                "term": raw_phrase or str(resolved_value or clause.get("value_phrase") or ""),
                "operator": str(clause.get("operator") or "unknown"),
                "field_phrase": field_phrase,
                "value_phrase": clause.get("value_phrase", resolved_value),
                "conjunction": clause.get("conjunction"),
                "raw_phrase": raw_phrase,
                "evidence_score": float(candidate.get("score") or candidate.get("confidence") or 0.0),
            }
            if clause.get("filter_kind") == "date_interval":
                selected_filter.update(
                    {
                        "filter_kind": "date_interval",
                        "interval_granularity": clause.get("interval_granularity"),
                        "date_column_phrase": clause.get("date_column_phrase"),
                        "values": list(clause.get("values") or []),
                    }
                )
            resolved_filter_candidates.append(selected_filter)
    if all_filters_resolved:
        filter_candidates = _merge_candidate_columns(resolved_filter_candidates)
        plan["filters"] = [dict(entry) for entry in resolved_filter_candidates]
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
    metric_evidence_for_scope = (
        effective_measure_candidates
        if (
            query_shape in {"single_table_aggregate", "grouped_aggregate"}
            or (query_shape == "ranking_query" and ranking_mode == "grouped_aggregate")
        )
        else []
    )
    resolved_evidence_tables = {
        str(entry.get("table") or "").strip()
        for entry in [
            *metric_evidence_for_scope,
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
        query_shape in {"single_table_list", "filtered_query", "grouped_aggregate", "ranking_query"}
        and len(selected_table_names) == 1
        and implicit_filter_phrase
        and not has_explicit_non_interval_filter_request
        and (
            str(structured_intent.get("intent_type") or "").strip().lower() in {"list", "filter"}
            or grouped_mode_hint
            or str(structured_intent.get("intent_type") or "").strip().lower() == "ranking"
        )
    ):
        implicit_filters, implicit_filter_status = _source_scope_as_filters(
            implicit_filter_phrase,
            schema_for_resolution,
            {selected_table_names[0]},
        )
        if implicit_filter_status == "resolved" and implicit_filters:
            filter_candidates = _merge_candidate_columns(implicit_filters, filter_candidates)
            selected_columns = _merge_candidate_columns(selected_columns, implicit_filters)
            existing_filters = [
                dict(entry) for entry in (plan.get("filters") or []) if isinstance(entry, dict)
            ]
            existing_filters.extend(dict(entry) for entry in implicit_filters)
            plan["filters"] = existing_filters
            if query_shape in {"single_table_list", "filtered_query"}:
                query_shape = "filtered_query"

    join_candidates = _join_candidates_for_contract(join_paths, matched_relationships)
    required_joins = _required_join_predicates(join_paths)
    group_by_candidates = _group_by_candidates_for_contract(plan, dimension_candidates)
    order_by_candidates = _order_by_candidates_for_contract(plan.get("sorting"), effective_measure_candidates, dimension_candidates)
    required_evidence = _required_evidence_for_query_shape(query_shape)
    missing_evidence = _missing_evidence_list(missing_evidence_flags)
    unsupported_intent = _has_unsupported_control_construct(structured_intent, question)
    if unsupported_intent:
        missing_evidence_flags["unsupported_intent"] = True
    if invalid_explicit_metric_type:
        missing_evidence_flags["missing_metric"] = True
        missing_evidence_flags["invalid_metric_type"] = True
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
    non_interval_filter_clauses = [
        clause for clause in structured_filter_clauses
        if not (isinstance(clause, dict) and clause.get("filter_kind") == "date_interval")
    ]
    requested_filter_count = len(
        non_interval_filter_clauses or list((intent or {}).get("requested_filters") or [])
        if isinstance(intent, dict)
        else []
    )
    has_filter_ambiguity = (
        _has_structured_filter_ambiguity(filter_candidates, non_interval_filter_clauses)
        if non_interval_filter_clauses
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
        or (
            bool(intent.get("needs_aggregation")) if isinstance(intent, dict) else False
        )
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
    metric_selection_required = (
        query_shape in {"single_table_aggregate", "grouped_aggregate", "ranking_query"}
        or is_filtered_aggregate
    )
    grouped_dimension_required = (
        query_shape == "grouped_aggregate"
        or (query_shape == "ranking_query" and ranking_mode == "grouped_aggregate")
    )
    selected_metric = (
        None
        if "metric_selection" in blocking_ambiguities or not metric_selection_required
        else dict(effective_measure_candidates[0]) if effective_measure_candidates else None
    )
    selected_dimensions = (
        []
        if "dimension_selection" in blocking_ambiguities or not grouped_dimension_required
        else [dict(entry) for entry in dimension_candidates]
    )
    if grouped_dimension_required and len(selected_dimensions) != 1:
        blocking_ambiguities.add("dimension_selection")
        selected_dimensions = []
    if (
        grouped_dimension_required
        and not selected_dimensions
        and isinstance(selected_metric, dict)
        and requested_dimensions
    ):
        metric_table = str(selected_metric.get("table") or "").strip()
        exact_dimensions = _exact_table_column_candidates(
            metric_table,
            str(requested_dimensions[0]),
            full_knowledge_base,
        )
        if len(exact_dimensions) == 1:
            selected_dimensions = [dict(exact_dimensions[0])]
            dimension_candidates = [dict(exact_dimensions[0])]
            blocking_ambiguities.discard("dimension_selection")
            if "dimension_selection" in ambiguities:
                ambiguities = [value for value in ambiguities if value != "dimension_selection"]
            missing_evidence_flags["missing_dimension"] = False
    selected_filters = [] if "filter_selection" in blocking_ambiguities else [
        dict(entry) for entry in (plan.get("filters") or [])
    ]
    if grouped_dimension_required:
        role_tables = {
            str(entry.get("table") or "").strip()
            for entry in [
                *([selected_metric] if isinstance(selected_metric, dict) else []),
                *selected_dimensions,
                *selected_filters,
            ]
            if isinstance(entry, dict) and str(entry.get("table") or "").strip()
        }
        if len(role_tables) == 1:
            role_table = next(iter(role_tables))
            selected_tables = [
                dict(entry)
                for entry in selected_tables
                if str(entry.get("table") or "").strip() == role_table
            ] or [{"table": role_table, "confidence": 1.0, "source": "resolved_single_table_roles"}]
            selected_table_names = [role_table]
            selected_columns = [
                dict(entry)
                for entry in selected_columns
                if str(entry.get("table") or "").strip() == role_table
            ]
            selected_knowledge_base = {
                role_table: deepcopy(
                    selected_knowledge_base.get(role_table)
                    or schema_for_resolution.get(role_table)
                    or {}
                )
            }
            join_paths = []
            matched_relationships = []
            blocking_ambiguities.discard("table_selection")
    final_interval_primary_table = primary_table if primary_table in knowledge_base else (
        selected_table_names[0] if len(selected_table_names) == 1 else ""
    )
    final_interval_tables = {final_interval_primary_table} if final_single_table_scope and final_interval_primary_table else {
        str(name) for name in selected_table_names if str(name)
    }
    has_date_interval_clause = any(
        isinstance(entry, dict) and entry.get("filter_kind") == "date_interval"
        for entry in structured_filter_clauses
    )
    if has_date_interval_clause and final_interval_tables:
        allow_relative_owner_date = bool(
            len(final_interval_tables) == 1
            and any(
                str(entry.get("table") or "").strip() in final_interval_tables
                and entry.get("filter_kind") != "date_interval"
                for entry in selected_filters
            )
            and any(
                isinstance(entry, dict)
                and entry.get("filter_kind") == "date_interval"
                and entry.get("interval_granularity") == "relative_days"
                and not str(entry.get("date_column_phrase") or "").strip()
                for entry in structured_filter_clauses
            )
        )
        selected_filters, final_interval_reason = _resolve_interval_filters_for_scope(
            selected_filters,
            structured_filter_clauses,
            knowledge_base=full_knowledge_base,
            allowed_tables=final_interval_tables,
            preferred_table=final_interval_primary_table or next(iter(final_interval_tables), None),
            preferred_owner_phrases=[implicit_filter_phrase, primary_table, final_interval_primary_table],
            allow_preferred_owner_date=allow_relative_owner_date,
        )
        plan["filters"] = [dict(entry) for entry in selected_filters]
        if final_interval_reason:
            missing_evidence_flags["missing_filter_column"] = True
        else:
            missing_evidence_flags["missing_filter_column"] = False
            blocking_ambiguities.discard("filter_selection")
    source_filter_primary_table = primary_table if primary_table in selected_table_names else (
        selected_table_names[0] if len(selected_table_names) == 1 else ""
    )
    source_filter_allowed_tables = {
        str(name).strip()
        for name in selected_table_names
        if str(name).strip()
    } or ({source_filter_primary_table} if source_filter_primary_table else set())
    if (
        not selected_filters
        and grouped_dimension_required
        and source_filter_allowed_tables
        and implicit_filter_phrase
    ):
        implicit_filters, implicit_filter_status = _source_scope_as_filters(
            implicit_filter_phrase,
            full_knowledge_base,
            source_filter_allowed_tables,
        )
        if implicit_filter_status == "resolved" and implicit_filters:
            selected_filters = [dict(entry) for entry in implicit_filters]
            plan["filters"] = [dict(entry) for entry in implicit_filters]
            filter_candidates = _merge_candidate_columns(implicit_filters, filter_candidates)
            selected_columns = _merge_candidate_columns(selected_columns, implicit_filters)
            missing_evidence_flags["missing_filter_column"] = False
            blocking_ambiguities.discard("filter_selection")
    if (
        not selected_filters
        and grouped_dimension_required
        and source_filter_primary_table
    ):
        runtime_filter_scope = {
            source_filter_primary_table: (
                full_knowledge_base.get(source_filter_primary_table)
                or schema_for_resolution.get(source_filter_primary_table)
                or {}
            )
        }
        runtime_filters = _detect_runtime_filters(question, runtime_filter_scope)
        if runtime_filters:
            selected_filters = [dict(entry) for entry in runtime_filters]
            plan["filters"] = [dict(entry) for entry in runtime_filters]
            filter_candidates = _merge_candidate_columns(runtime_filters, filter_candidates)
            selected_columns = _merge_candidate_columns(selected_columns, runtime_filters)
            missing_evidence_flags["missing_filter_column"] = False
            blocking_ambiguities.discard("filter_selection")
    if (
        query_shape == "filtered_query"
        and explicit_base
        and selected_filters
        and all(str(entry.get("table") or "").strip() == explicit_base for entry in selected_filters)
        and not related_output_requested
    ):
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
        selected_knowledge_base = {
            explicit_base: deepcopy(
                selected_knowledge_base.get(explicit_base)
                or schema_for_resolution.get(explicit_base)
                or {}
            )
        }
        join_paths = []
        matched_relationships = []
    late_filter_phrase = implicit_filter_phrase or target_filter_phrase
    late_filter_allowed_tables = source_filter_allowed_tables or {
        str(name).strip()
        for name in selected_table_names
        if str(name).strip()
    }
    if selected_filters and late_filter_phrase and late_filter_allowed_tables:
        implicit_filters, implicit_filter_status = _source_scope_as_filters(
            late_filter_phrase,
            full_knowledge_base,
            late_filter_allowed_tables,
        )
        if implicit_filter_status == "resolved" and implicit_filters:
            implicit_by_signature = {
                (
                    str(entry.get("table") or "").strip(),
                    str(entry.get("column") or "").strip(),
                    str(entry.get("value") or "").strip(),
                ): dict(entry)
                for entry in implicit_filters
            }
            normalized_filters = [
                implicit_by_signature.get(
                    (
                        str(entry.get("table") or "").strip(),
                        str(entry.get("column") or "").strip(),
                        str(entry.get("value") or "").strip(),
                    ),
                    dict(entry),
                )
                for entry in selected_filters
            ]
            if normalized_filters != selected_filters:
                selected_filters = normalized_filters
                plan["filters"] = [dict(entry) for entry in selected_filters]
                filter_candidates = _merge_candidate_columns(selected_filters, filter_candidates)
                selected_columns = _merge_candidate_columns(selected_columns, selected_filters)
    if selected_filters:
        structured_signatures = {
            (
                str(entry.get("raw_phrase") or "").strip().lower(),
                str(entry.get("column") or entry.get("field") or entry.get("field_phrase") or "").strip().lower(),
            )
            for entry in structured_filter_clauses
            if isinstance(entry, dict)
        }
        implicit_structured_filters = [
            dict(entry)
            for entry in selected_filters
            if (
                str(entry.get("raw_phrase") or "").strip().lower(),
                str(entry.get("column") or entry.get("field") or entry.get("field_phrase") or "").strip().lower(),
            )
            not in structured_signatures
            and entry.get("filter_kind") != "date_interval"
            and entry.get("source") == "source_scope_value_filter"
        ]
        if implicit_structured_filters:
            structured_intent = dict(structured_intent)
            structured_filter_clauses = [
                *[dict(entry) for entry in structured_filter_clauses if isinstance(entry, dict)],
                *implicit_structured_filters,
            ]
            structured_intent["structured_filters"] = structured_filter_clauses
            intent = structured_intent
    filter_decision = build_filter_decision_contract(
        selected_filters=selected_filters,
        selected_having=[],
        knowledge_base=full_knowledge_base,
        selected_tables=selected_tables,
        selected_join_path=None,
        join_paths=join_paths,
    )
    if filter_decision.get("overall_status") != "resolved":
        selected_filters = []
        plan["filters"] = []
        missing_evidence_flags["missing_filter_column"] = True
        blocking_ambiguities.add("filter_selection")
    raw_aggregate_function = structured_intent.get("aggregate_function")
    aggregate_function = str(raw_aggregate_function or "").strip().lower()
    planner_intent = str(plan.get("intent") or "").strip().lower()
    if not aggregate_function and (intent_type == "count" or planner_intent == "count"):
        aggregate_function = "count"
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
    ranking_decision = _build_ranking_decision_for_contract(
        query_shape=query_shape,
        intent=structured_intent,
        selected_tables=selected_tables,
        selected_metric=selected_metric,
        selected_dimensions=selected_dimensions,
        selected_order_by=selected_order_by,
        aggregate_function=aggregate_function or "",
        limit=resolved_limit,
        limit_reason=limit_reason,
        order_by_reason=order_by_reason,
        order_by_ambiguity_choices=order_by_ambiguity_choices,
    )
    if order_by_required and ranking_decision.get("status") != "resolved":
        selected_order_by = None
        blocking_ambiguities.add("ranking_selection")
        reason_code = str(ranking_decision.get("reason_code") or "").strip()
        if reason_code and not order_by_reason:
            order_by_reason = reason_code
    if order_by_required and blocking_ambiguities and ranking_decision.get("status") == "resolved":
        selected_order_by = None
        blocking_ambiguities.add("ranking_selection")
        ranking_decision = {
            **ranking_decision,
            "status": "ambiguous",
            "score": 0.0,
            "score_reasons": [],
            "evidence_reasons": [],
            "ambiguity_group_key": "ranking_selection",
            "reason_code": "ranking_blocked_by_unresolved_evidence",
        }
        if not order_by_reason:
            order_by_reason = "ranking_blocked_by_unresolved_evidence"
    selected_relationship_path = dict(join_paths[0]) if join_paths else None
    dimension_decision: dict[str, Any] = {}
    if grouped_dimension_required:
        dimension_phrase_for_decision = str(
            next(iter(structured_intent.get("requested_dimensions") or []), "")
            or structured_intent.get("grouping_phrase")
            or plan.get("dimension")
            or ""
        ).strip()
        selected_dimension = selected_dimensions[0] if len(selected_dimensions) == 1 else None
        dimension_owner_context = [
            str(structured_intent.get("target_entity_phrase") or ""),
            str(next(iter(structured_intent.get("source_scope") or []), "")),
            str((selected_metric or {}).get("table") or "") if isinstance(selected_metric, dict) else "",
        ]
        dimension_decision = build_dimension_decision_contract(
            dimension_phrase=dimension_phrase_for_decision,
            dimension_candidates=dimension_candidates,
            dimension_mode="grouping_dimension",
            selected_dimension=selected_dimension,
            selected_join_path=selected_relationship_path,
            owner_context=dimension_owner_context,
        )
        if dimension_decision.get("status") != "resolved":
            selected_dimensions = []
            blocking_ambiguities.add("dimension_selection")
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
    if (
        query_shape in {"grouped_aggregate", "ranking_query"}
        and len(selected_tables) == 1
        and not join_paths
        and not missing_evidence
        and not blocking_ambiguities
        and selected_dimensions
        and aggregate_function in {"count", "sum", "avg", "min", "max"}
        and (aggregate_function == "count" or isinstance(selected_metric, dict))
        and (not structured_intent.get("requested_sort") or isinstance(selected_order_by, dict))
    ):
        confidence = max(float(confidence or 0.0), 0.86)
        warnings = _remove_weak_context_warning(list(warnings or []))
    grouped_table_scope_is_safe = query_shape not in {"grouped_aggregate", "ranking_query"} or (
        len(selected_tables) == 1 and not join_paths
    )
    can_plan = bool(
        selected_table_names
        and query_shape not in {"unknown", "blocked_unsafe"}
        and query_shape != "multi_metric_aggregate"
        and not unsupported_intent
        and not invalid_explicit_metric_type
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
    if invalid_explicit_metric_type:
        route_recommendation = "cannot_plan_safely"
        route_reason = "requested aggregate metric is not numeric"
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
    if ranking_decision and ranking_decision.get("status") != "resolved":
        ambiguity_details.append({"type": "ranking_selection", "decision": dict(ranking_decision)})
    selected_having = _selected_having_for_contract(
        structured_intent,
        selected_metric,
        selected_tables,
    )
    filter_decision = build_filter_decision_contract(
        selected_filters=selected_filters,
        selected_having=selected_having,
        knowledge_base=full_knowledge_base,
        selected_tables=selected_tables,
        selected_join_path=selected_relationship_path,
        join_paths=join_paths,
    )
    if not selected_filters and filter_decision.get("resolved_where_filters"):
        selected_filters = [
            {
                "table": entry.get("table"),
                "column": entry.get("column"),
                "field_phrase": entry.get("column"),
                "raw_phrase": entry.get("normalized_value"),
                "operator": entry.get("operator"),
                "value": entry.get("value", entry.get("normalized_value")),
                "value_phrase": entry.get("normalized_value"),
                "values": list(entry.get("values") or [entry.get("value", entry.get("normalized_value"))]),
                "conjunction": "",
                "source": entry.get("evidence_tier"),
            }
            for entry in filter_decision.get("resolved_where_filters") or []
            if isinstance(entry, dict)
        ]
        plan["filters"] = [dict(entry) for entry in selected_filters]
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
    if ranking_decision:
        clause_plan["ranking_decision"] = dict(ranking_decision)
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
        normalized_complex_sql_plan["ranking_decision"] = dict(ranking_decision or {})
        normalized_complex_sql_plan["dimension_decision"] = dict(dimension_decision or {})
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

    result_intent = dict(intent) if isinstance(intent, dict) else {"intent_type": str(intent or "").strip().lower()}
    if selected_filters:
        deduped_structured_filters: dict[tuple[str, str], dict[str, Any]] = {}
        for entry in [
            *[
                dict(item)
                for item in (result_intent.get("structured_filters") or [])
                if isinstance(item, dict)
            ],
            *[dict(item) for item in selected_filters if isinstance(item, dict)],
        ]:
            raw_key = _normalize(str(entry.get("raw_phrase") or entry.get("term") or ""))
            value_key = _normalize(str(entry.get("value_phrase") or entry.get("value") or ""))
            key = (raw_key, value_key)
            if key not in deduped_structured_filters or (
                entry.get("table") and entry.get("column")
            ):
                deduped_structured_filters[key] = entry
        result_intent["structured_filters"] = list(deduped_structured_filters.values())

    normalized_result = {
        "planner_contract_version": "1.0",
        "normalized_question": normalized_question,
        "intent": result_intent,
        "route": route_recommendation,
        "query_shape": query_shape,
        "route_recommendation": route_recommendation,
        "selected_tables": list(selected_tables),
        "selected_table_names": list(selected_table_names),
        "selected_columns": list(selected_columns),
        "selected_metric": selected_metric,
        "selected_dimensions": selected_dimensions,
        "dimension_decision": dimension_decision,
        "selected_filters": selected_filters,
        "filter_decision": filter_decision,
        "selected_having": selected_having,
        "selected_order_by": selected_order_by,
        "ranking_decision": ranking_decision,
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
    cache_store: Any | None = None,
    cache_database_identity: dict[str, Any] | None = None,
) -> dict:
    """Build a deterministic planner contract without generating SQL."""
    normalized_question = _normalize(question)
    
    if intent and retrieved_context is not None:
        if not intent.get("intent_contract_version"):
            canonical_intent = _canonical_build_intent(normalized_question)
            canonical_lookup = canonical_intent.get("join_lookup_request") or {}
            intent_missing_structural_evidence = bool(
                (canonical_intent.get("structured_having") and not intent.get("structured_having"))
                or (canonical_intent.get("aggregate_function") and not intent.get("aggregate_function"))
                or (canonical_lookup.get("requested") and not (intent.get("join_lookup_request") or {}).get("requested"))
            )
            if intent_missing_structural_evidence:
                intent = canonical_intent
        return _build_query_context_from_retrieved_context(
            question,
            normalized_question,
            intent,
            retrieved_context,
            knowledge_base,
            cache_store=cache_store,
            cache_database_identity=cache_database_identity,
        )

    from query_pipeline.planner.legacy_planner import build_legacy_query_context

    return build_legacy_query_context(
        question,
        knowledge_base,
        business_glossary=business_glossary,
        use_vector_retrieval=use_vector_retrieval,
        vector_retriever=vector_retriever,
    )
