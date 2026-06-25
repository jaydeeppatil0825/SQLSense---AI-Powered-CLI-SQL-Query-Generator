"""
sql_pipeline/deterministic_sql_generator.py
==========================================
Deterministic SQL generation for narrow, evidence-backed aggregate queries.

This phase only supports single-table aggregate queries with:
- one selected table
- no joins
- no grouping
- no formulas
- one clear numeric metric column
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Optional

from kb_pipeline.schema_facts import column_business_description, column_business_terms, resolved_semantic_type

_AGGREGATE_HINTS = {
    "sum": {"sum", "total"},
    "avg": {"average", "avg", "mean"},
    "max": {"maximum", "max", "highest"},
    "min": {"minimum", "min", "lowest"},
}
_GENERIC_QUERY_TERMS = {
    "show",
    "list",
    "display",
    "get",
    "fetch",
    "tell",
    "me",
    "all",
    "of",
    "for",
    "from",
    "in",
    "on",
    "at",
    "to",
    "with",
    "the",
    "a",
    "an",
    "records",
    "record",
    "rows",
    "row",
}
_NUMERIC_TYPE_MARKERS = ("int", "decimal", "numeric", "float", "double", "real")


@dataclass(frozen=True)
class DeterministicSqlResult:
    status: str
    sql: Optional[str] = None
    reason: str = ""


def generate_single_table_aggregate_sql(
    *,
    query_context: dict[str, Any],
    knowledge_base: dict[str, Any],
) -> DeterministicSqlResult:
    """Generate aggregate SQL when evidence is strong enough."""
    context = query_context if isinstance(query_context, dict) else {}
    plan = context.get("plan") if isinstance(context.get("plan"), dict) else {}
    selected_tables = [entry for entry in (context.get("selected_tables") or []) if isinstance(entry, dict)]
    join_paths = list(context.get("join_paths") or [])
    formula_evidence = list(context.get("formula_evidence") or [])

    if len(selected_tables) != 1:
        return DeterministicSqlResult("not_applicable", reason="requires exactly one selected table")
    if join_paths:
        return DeterministicSqlResult("not_applicable", reason="join paths are not supported in phase 1A")
    if formula_evidence:
        return DeterministicSqlResult("not_applicable", reason="formula queries are not supported in phase 1A")
    if plan.get("grouping") or plan.get("dimension"):
        return DeterministicSqlResult("not_applicable", reason="grouped queries are not supported in phase 1A")
    if plan.get("filters") or plan.get("date_range"):
        return DeterministicSqlResult("not_applicable", reason="filtered aggregates are not supported in phase 1A")

    aggregate_function = _detect_aggregate_function(plan)
    if aggregate_function is None:
        return DeterministicSqlResult("not_applicable", reason="aggregate intent is not supported in phase 1A")
    if aggregate_function == "count":
        return DeterministicSqlResult("not_applicable", reason="count remains handled by the simple query generator")

    table_name = str(selected_tables[0].get("table") or "").strip()
    if not table_name:
        return DeterministicSqlResult("cannot_plan_safely", reason="selected table is missing")

    scoped_kb = context.get("selected_knowledge_base") if isinstance(context.get("selected_knowledge_base"), dict) else knowledge_base
    table_data = (scoped_kb or {}).get(table_name) or (knowledge_base or {}).get(table_name)
    if not isinstance(table_data, dict):
        return DeterministicSqlResult("cannot_plan_safely", reason=f"schema metadata for table '{table_name}' is missing")

    metric_column = _resolve_metric_column(query_context=context, plan=plan, table_name=table_name, table_data=table_data)
    if metric_column is None:
        return DeterministicSqlResult(
            "cannot_plan_safely",
            reason="could not identify one clear numeric metric column for this single-table aggregate",
        )

    sql = f"SELECT {aggregate_function.upper()}({metric_column}) AS {_aggregate_alias(aggregate_function, metric_column)} FROM {table_name};"
    return DeterministicSqlResult("generated", sql=sql, reason="single-table aggregate generated deterministically")


def _detect_aggregate_function(plan: dict[str, Any]) -> str | None:
    intent = str(plan.get("intent") or "").strip().lower()
    question_text = str(plan.get("question") or "").strip().lower()
    question_terms = set(_tokenize(question_text))

    if intent == "count":
        return "count"
    if intent == "total":
        return "sum"
    if intent == "average":
        return "avg"

    for function_name, hints in _AGGREGATE_HINTS.items():
        if question_terms & hints:
            return function_name
    return None


def _resolve_metric_column(
    *,
    query_context: dict[str, Any],
    plan: dict[str, Any],
    table_name: str,
    table_data: dict[str, Any],
) -> str | None:
    table_columns = [column for column in (table_data.get("columns") or []) if isinstance(column, dict)]
    numeric_columns = [
        column for column in table_columns
        if _is_numeric_metric_column(column, table_data)
    ]
    if not numeric_columns:
        return None
    if len(numeric_columns) == 1:
        return str(numeric_columns[0].get("name") or "").strip() or None

    selected_columns = [
        entry for entry in (query_context.get("selected_columns") or [])
        if isinstance(entry, dict) and str(entry.get("table") or "").strip() == table_name
    ]
    selected_confidence = {
        str(entry.get("column") or "").strip(): float(entry.get("confidence") or 0.0)
        for entry in selected_columns
        if str(entry.get("column") or "").strip()
    }
    measure_candidates = [
        entry for entry in (query_context.get("measure_candidates") or [])
        if isinstance(entry, dict) and str(entry.get("table") or "").strip() == table_name
    ]
    measure_candidate_names = {
        str(entry.get("column") or "").strip()
        for entry in measure_candidates
        if str(entry.get("column") or "").strip()
    }
    metric_terms = _metric_query_terms(str(plan.get("question") or ""), table_name)

    scored_candidates = []
    for column in numeric_columns:
        column_name = str(column.get("name") or "").strip()
        if not column_name:
            continue
        query_score = _metric_match_score(metric_terms, column)
        selected_score = selected_confidence.get(column_name, 0.0)
        measure_score = 0.5 if column_name in measure_candidate_names else 0.0
        scored_candidates.append(
            {
                "column": column_name,
                "query_score": query_score,
                "score": query_score + selected_score + measure_score,
            }
        )

    direct_matches = [candidate for candidate in scored_candidates if candidate["query_score"] > 0]
    if len(direct_matches) == 1:
        return direct_matches[0]["column"]
    if len(direct_matches) > 1:
        direct_matches.sort(key=lambda item: (-item["score"], item["column"]))
        top = direct_matches[0]
        second = direct_matches[1]
        if top["score"] >= second["score"] + 0.2:
            return top["column"]
        return None

    return None


def _metric_query_terms(question: str, table_name: str) -> set[str]:
    tokens = set(_tokenize(question))
    aggregate_terms = set().union(*_AGGREGATE_HINTS.values())
    table_terms = set(_tokenize(table_name))
    return {
        token for token in tokens
        if token not in _GENERIC_QUERY_TERMS
        and token not in aggregate_terms
        and token not in table_terms
    }


def _metric_match_score(metric_terms: set[str], column: dict[str, Any]) -> float:
    if not metric_terms:
        return 0.0

    search_tokens = set(_tokenize(str(column.get("name") or "")))
    for term in column_business_terms(column):
        search_tokens.update(_tokenize(term))
    search_tokens.update(_tokenize(column_business_description(column)))

    overlap = metric_terms & search_tokens
    if not overlap:
        return 0.0
    return round(len(overlap) / max(len(metric_terms), 1), 4)


def _is_numeric_metric_column(column: dict[str, Any], table_data: dict[str, Any]) -> bool:
    column_name = str(column.get("name") or "").strip()
    if not column_name:
        return False
    if column_name in {str(value) for value in (table_data.get("primary_keys") or []) if str(value)}:
        return False
    if column_name in {
        str(foreign_key.get("column") or "").strip()
        for foreign_key in (table_data.get("foreign_keys") or [])
        if isinstance(foreign_key, dict)
    }:
        return False

    normalized_name = _normalize_identifier(column_name)
    if normalized_name == "id" or normalized_name.endswith("_id"):
        return False

    semantic_type = resolved_semantic_type(column)
    if semantic_type in {"id", "date", "boolean"}:
        return False

    column_type = str(column.get("type") or "").strip().lower()
    return any(marker in column_type for marker in _NUMERIC_TYPE_MARKERS)


def _aggregate_alias(function_name: str, column_name: str) -> str:
    return f"{function_name}_{_normalize_identifier(column_name)}"


def _normalize_identifier(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")


def _tokenize(value: str) -> list[str]:
    base_tokens = [token for token in re.split(r"[^a-z0-9]+", str(value or "").strip().lower()) if token]
    expanded: list[str] = []
    seen: set[str] = set()
    for token in base_tokens:
        for candidate in (token, _singularize(token)):
            if candidate and candidate not in seen:
                expanded.append(candidate)
                seen.add(candidate)
    return expanded


def _singularize(token: str) -> str:
    if token.endswith("ies") and len(token) > 3:
        return token[:-3] + "y"
    if token.endswith("ses") and len(token) > 3:
        return token[:-2]
    if token.endswith("s") and not token.endswith("ss") and len(token) > 1:
        return token[:-1]
    return token
