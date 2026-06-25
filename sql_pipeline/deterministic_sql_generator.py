"""
sql_pipeline/deterministic_sql_generator.py
==========================================
Deterministic SQL generation for runtime-safe SQL that can be proven from
pipeline evidence alone.

The module is structured in small stages so future phases can add joins,
grouping, filters, ranking, formulas, and multi-metric rendering without
replacing the generator:

1. capability analysis
2. normalized deterministic plan
3. plan resolution
4. SQL rendering

Phase 1A currently implements only single-table aggregate rendering.
"""

from __future__ import annotations

from dataclasses import dataclass, field
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
class DeterministicCapabilityResult:
    status: str
    query_shape: str
    supported_now: bool
    blocked_by: list[str] = field(default_factory=list)
    required_evidence: list[str] = field(default_factory=list)
    reason: str = ""


@dataclass(frozen=True)
class DeterministicSqlPlan:
    query_shape: str
    status: str = "not_applicable"
    supported_now: bool = False
    base_table: Optional[str] = None
    joins: list[dict[str, Any]] = field(default_factory=list)
    required_joins: list[dict[str, Any]] = field(default_factory=list)
    select_items: list[dict[str, Any]] = field(default_factory=list)
    where_clauses: list[str] = field(default_factory=list)
    group_by: list[str] = field(default_factory=list)
    order_by: list[str] = field(default_factory=list)
    limit: Optional[int] = None
    aggregation_type: Optional[str] = None
    metric_columns: list[str] = field(default_factory=list)
    dimension_columns: list[str] = field(default_factory=list)
    filter_columns: list[str] = field(default_factory=list)
    formula_expressions: list[str] = field(default_factory=list)
    formula_evidence: list[Any] = field(default_factory=list)
    required_evidence: list[str] = field(default_factory=list)
    missing_evidence: list[str] = field(default_factory=list)
    evidence_sources: list[str] = field(default_factory=list)
    sql_skeleton_type: Optional[str] = None
    can_render: bool = False
    route_reason: str = ""


@dataclass(frozen=True)
class DeterministicSqlResult:
    status: str
    sql: Optional[str] = None
    reason: str = ""
    plan: Optional[DeterministicSqlPlan] = None


def looks_like_single_table_aggregate_request(query_context: dict[str, Any]) -> bool:
    """Compatibility helper for the current QuestionService aggregate gate."""
    capability = analyze_deterministic_capabilities(query_context)
    return capability.status == "supported" and capability.query_shape == "single_table_aggregate"


def analyze_deterministic_capabilities(query_context: dict[str, Any]) -> DeterministicCapabilityResult:
    """Classify the deterministic query shape without generating SQL."""
    context = query_context if isinstance(query_context, dict) else {}
    plan = context.get("plan") if isinstance(context.get("plan"), dict) else {}
    selected_tables = [entry for entry in (context.get("selected_tables") or []) if isinstance(entry, dict)]
    aggregate_function = _detect_aggregate_function(plan)

    if aggregate_function is None:
        return DeterministicCapabilityResult(
            status="not_applicable",
            query_shape="unsupported",
            supported_now=False,
            reason="deterministic aggregate generation is not applicable",
        )

    if aggregate_function == "count":
        return DeterministicCapabilityResult(
            status="not_applicable",
            query_shape="single_table_count",
            supported_now=False,
            blocked_by=["count_is_handled_elsewhere"],
            reason="count remains handled by the simple query generator",
        )

    if not selected_tables:
        return DeterministicCapabilityResult(
            status="cannot_plan_safely",
            query_shape="single_table_aggregate",
            supported_now=False,
            blocked_by=["selected_table_missing"],
            required_evidence=["table_evidence"],
            reason="selected table is missing",
        )

    query_shape = _infer_query_shape(context, plan)
    if query_shape == "single_table_aggregate":
        return DeterministicCapabilityResult(
            status="supported",
            query_shape=query_shape,
            supported_now=True,
            required_evidence=["selected_table", "metric_column", "aggregate_function"],
            reason="single-table aggregate can be planned deterministically",
        )

    blocked_by = _shape_blockers(query_shape)
    required_evidence = list(blocked_by)
    return DeterministicCapabilityResult(
        status="not_applicable",
        query_shape=query_shape,
        supported_now=False,
        blocked_by=blocked_by,
        required_evidence=required_evidence,
        reason=f"{query_shape} is not implemented in deterministic SQL generation yet",
    )


def build_deterministic_sql_plan(
    *,
    query_context: dict[str, Any],
    knowledge_base: dict[str, Any],
) -> DeterministicSqlPlan:
    """Build a normalized deterministic SQL plan from runtime pipeline evidence."""
    capability = analyze_deterministic_capabilities(query_context)
    if capability.status != "supported" or capability.query_shape != "single_table_aggregate":
        return DeterministicSqlPlan(
            query_shape=capability.query_shape,
            status=capability.status,
            supported_now=capability.supported_now,
            required_evidence=list(capability.required_evidence),
            missing_evidence=list(capability.blocked_by),
            route_reason=capability.reason,
            formula_evidence=list((query_context or {}).get("formula_evidence") or []),
        )

    return _build_single_table_aggregate_plan(
        query_context=query_context,
        knowledge_base=knowledge_base,
        capability=capability,
    )


def generate_deterministic_sql(
    *,
    query_context: dict[str, Any],
    knowledge_base: dict[str, Any],
) -> DeterministicSqlResult:
    """Generate SQL from the normalized deterministic plan when it is renderable."""
    plan = build_deterministic_sql_plan(
        query_context=query_context,
        knowledge_base=knowledge_base,
    )
    if plan.status != "ready" or not plan.can_render:
        status = "cannot_plan_safely" if plan.status == "cannot_plan_safely" else "not_applicable"
        return DeterministicSqlResult(
            status=status,
            reason=plan.route_reason,
            plan=plan,
        )

    renderer = _PLAN_RENDERERS.get(plan.query_shape)
    if renderer is None:
        return DeterministicSqlResult(
            status="not_applicable",
            reason=f"no deterministic renderer is registered for {plan.query_shape}",
            plan=plan,
        )

    sql = renderer(plan)
    return DeterministicSqlResult(
        status="generated",
        sql=sql,
        reason=plan.route_reason,
        plan=plan,
    )


def generate_single_table_aggregate_sql(
    *,
    query_context: dict[str, Any],
    knowledge_base: dict[str, Any],
) -> DeterministicSqlResult:
    """Compatibility wrapper for the current Phase 1A aggregate entry point."""
    result = generate_deterministic_sql(
        query_context=query_context,
        knowledge_base=knowledge_base,
    )
    if result.status == "generated":
        return result

    plan = result.plan
    if plan and plan.query_shape == "single_table_aggregate" and plan.status == "cannot_plan_safely":
        return DeterministicSqlResult(
            status="cannot_plan_safely",
            reason=plan.route_reason,
            plan=plan,
        )
    return DeterministicSqlResult(
        status="not_applicable",
        reason=(plan.route_reason if plan else result.reason),
        plan=plan,
    )


def _build_single_table_aggregate_plan(
    *,
    query_context: dict[str, Any],
    knowledge_base: dict[str, Any],
    capability: DeterministicCapabilityResult,
) -> DeterministicSqlPlan:
    context = query_context if isinstance(query_context, dict) else {}
    plan = context.get("plan") if isinstance(context.get("plan"), dict) else {}
    selected_tables = [entry for entry in (context.get("selected_tables") or []) if isinstance(entry, dict)]
    table_name = str(selected_tables[0].get("table") or "").strip()
    if not table_name:
        return DeterministicSqlPlan(
            query_shape="single_table_aggregate",
            status="cannot_plan_safely",
            supported_now=True,
            required_evidence=list(capability.required_evidence),
            missing_evidence=["selected_table_missing"],
            route_reason="selected table is missing",
            formula_evidence=list(context.get("formula_evidence") or []),
        )

    scoped_kb = context.get("selected_knowledge_base") if isinstance(context.get("selected_knowledge_base"), dict) else knowledge_base
    table_data = (scoped_kb or {}).get(table_name) or (knowledge_base or {}).get(table_name)
    if not isinstance(table_data, dict):
        return DeterministicSqlPlan(
            query_shape="single_table_aggregate",
            status="cannot_plan_safely",
            supported_now=True,
            base_table=table_name,
            required_evidence=list(capability.required_evidence),
            missing_evidence=["table_schema_missing"],
            route_reason=f"schema metadata for table '{table_name}' is missing",
            formula_evidence=list(context.get("formula_evidence") or []),
        )

    aggregate_function = _detect_aggregate_function(plan)
    if aggregate_function is None or aggregate_function == "count":
        return DeterministicSqlPlan(
            query_shape="single_table_aggregate",
            status="not_applicable",
            supported_now=False,
            base_table=table_name,
            required_evidence=list(capability.required_evidence),
            missing_evidence=["aggregate_function_missing"],
            route_reason="aggregate intent is not supported in phase 1A",
            formula_evidence=list(context.get("formula_evidence") or []),
        )

    metric_column, metric_reason = _resolve_metric_column(
        query_context=context,
        plan=plan,
        table_name=table_name,
        table_data=table_data,
    )
    if metric_column is None:
        return DeterministicSqlPlan(
            query_shape="single_table_aggregate",
            status="cannot_plan_safely",
            supported_now=True,
            base_table=table_name,
            aggregation_type=aggregate_function,
            required_evidence=list(capability.required_evidence),
            missing_evidence=[metric_reason],
            route_reason=metric_reason,
            formula_evidence=list(context.get("formula_evidence") or []),
        )

    aggregate_alias = _aggregate_alias(aggregate_function, metric_column)
    return DeterministicSqlPlan(
        query_shape="single_table_aggregate",
        status="ready",
        supported_now=True,
        base_table=table_name,
        select_items=[
            {
                "expression": f"{aggregate_function.upper()}({metric_column})",
                "alias": aggregate_alias,
                "source_column": metric_column,
                "kind": "aggregate",
            }
        ],
        aggregation_type=aggregate_function,
        metric_columns=[metric_column],
        required_evidence=list(capability.required_evidence),
        evidence_sources=["query_context.selected_tables", "query_context.selected_columns", "knowledge_base.columns"],
        sql_skeleton_type="single_table_aggregate",
        can_render=True,
        route_reason="single-table aggregate generated deterministically",
        formula_evidence=list(context.get("formula_evidence") or []),
    )


def _render_single_table_aggregate(plan: DeterministicSqlPlan) -> str:
    select_item = plan.select_items[0]
    return f"SELECT {select_item['expression']} AS {select_item['alias']} FROM {plan.base_table};"


_PLAN_RENDERERS = {
    "single_table_aggregate": _render_single_table_aggregate,
}


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


def _infer_query_shape(context: dict[str, Any], plan: dict[str, Any]) -> str:
    selected_tables = [entry for entry in (context.get("selected_tables") or []) if isinstance(entry, dict)]
    has_joins = bool(context.get("join_paths"))
    has_grouping = bool(plan.get("grouping") or plan.get("dimension"))
    has_filters = bool(plan.get("filters") or plan.get("date_range"))
    has_formulas = bool(context.get("formula_evidence"))
    has_limit = plan.get("limit") is not None

    if has_formulas:
        return "formula_query"
    if has_grouping and has_limit:
        return "ranking_aggregate"
    if has_grouping:
        return "grouped_aggregate"
    if has_joins or len(selected_tables) != 1:
        return "multi_table_aggregate"
    if has_filters:
        return "filtered_aggregate"
    return "single_table_aggregate"


def _shape_blockers(query_shape: str) -> list[str]:
    if query_shape == "formula_query":
        return ["formula_evidence"]
    if query_shape == "ranking_aggregate":
        return ["grouping", "ordering", "limit"]
    if query_shape == "grouped_aggregate":
        return ["grouping", "dimension_columns"]
    if query_shape == "multi_table_aggregate":
        return ["selected_tables", "join_paths"]
    if query_shape == "filtered_aggregate":
        return ["filter_columns", "where_clauses"]
    return ["deterministic_support_missing"]


def _resolve_metric_column(
    *,
    query_context: dict[str, Any],
    plan: dict[str, Any],
    table_name: str,
    table_data: dict[str, Any],
) -> tuple[str | None, str]:
    table_columns = [column for column in (table_data.get("columns") or []) if isinstance(column, dict)]
    numeric_columns = [
        column for column in table_columns
        if _is_numeric_metric_column(column, table_data)
    ]
    if not numeric_columns:
        return None, "metric_not_found"

    metric_terms = _metric_query_terms(str(plan.get("question") or ""), table_name)
    if len(numeric_columns) == 1:
        column_name = str(numeric_columns[0].get("name") or "").strip()
        if not column_name:
            return None, "metric_not_found"
        if not metric_terms:
            return column_name, ""
        query_score = _metric_match_score(metric_terms, numeric_columns[0])
        if query_score > 0:
            return column_name, ""
        return None, "metric_not_found"

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

    if not metric_terms:
        return None, "metric_ambiguous"

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
        return direct_matches[0]["column"], ""
    if len(direct_matches) > 1:
        direct_matches.sort(key=lambda item: (-item["score"], item["column"]))
        top = direct_matches[0]
        second = direct_matches[1]
        if top["score"] >= second["score"] + 0.2:
            return top["column"], ""
        return None, "metric_ambiguous"

    return None, "metric_not_found"


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
    camel_spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", str(value or "").strip())
    normalized = camel_spaced.lower().replace("-", " ")
    base_tokens = [token for token in re.split(r"[^a-z0-9]+", normalized) if token]
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
