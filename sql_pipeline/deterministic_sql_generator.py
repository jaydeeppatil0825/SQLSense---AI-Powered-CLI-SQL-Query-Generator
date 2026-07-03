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

The active phase implements single-table aggregate, filtered, and grouped
aggregate rendering with evidence-bound HAVING predicates.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import re
from typing import Any, Optional

from kb_pipeline.schema_facts import resolved_semantic_type

_AGGREGATE_HINTS = {
    "sum": {"sum", "total"},
    "avg": {"average", "avg", "mean"},
    "max": {"maximum", "max", "highest"},
    "min": {"minimum", "min", "lowest"},
}
_NUMERIC_TYPE_MARKERS = ("int", "decimal", "numeric", "float", "double", "real")
_DATE_TYPE_MARKERS = ("date", "time", "timestamp")
_FILTER_OPERATORS = {
    "eq": "=",
    "neq": "<>",
    "gt": ">",
    "lt": "<",
    "gte": ">=",
    "lte": "<=",
    "before": "<",
    "after": ">",
    "between": "BETWEEN",
    "contains": "LIKE",
    "is_null": "IS NULL",
    "is_not_null": "IS NOT NULL",
}
_SAFE_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


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
    where_conjunctions: list[str] = field(default_factory=list)
    having_clauses: list[str] = field(default_factory=list)
    having_conjunctions: list[str] = field(default_factory=list)
    group_by: list[str] = field(default_factory=list)
    order_by: list[str] = field(default_factory=list)
    limit: Optional[int] = None
    aggregation_type: Optional[str] = None
    metric_columns: list[str] = field(default_factory=list)
    dimension_columns: list[str] = field(default_factory=list)
    filter_columns: list[str] = field(default_factory=list)
    having_columns: list[str] = field(default_factory=list)
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
    aggregate_function = _planner_aggregate_function(context, plan)
    contract_shape = str(context.get("query_shape") or "").strip()

    if contract_shape == "grouped_aggregate":
        intent = context.get("intent") if isinstance(context.get("intent"), dict) else {}
        if len(selected_tables) != 1 or context.get("join_paths"):
            return DeterministicCapabilityResult(
                status="cannot_plan_safely",
                query_shape="grouped_aggregate",
                supported_now=True,
                blocked_by=["single_table_grouping_required"],
                required_evidence=["selected_table", "selected_dimension", "aggregate_function"],
                reason="grouped aggregate requires exactly one selected table and no joins",
            )
        if plan.get("sorting") or intent.get("requested_sort") or context.get("limit"):
            return DeterministicCapabilityResult(
                status="not_applicable",
                query_shape="grouped_aggregate",
                supported_now=False,
                blocked_by=["ranking_not_supported"],
                reason="ranked grouped SQL is not implemented in this phase",
            )
        if context.get("formula_evidence"):
            return DeterministicCapabilityResult(
                status="not_applicable",
                query_shape="grouped_aggregate",
                supported_now=False,
                blocked_by=["formula_not_supported"],
                reason="formula grouped SQL is not implemented in this phase",
            )
        if len(list(intent.get("requested_metrics") or [])) > 1:
            return DeterministicCapabilityResult(
                status="not_applicable",
                query_shape="grouped_aggregate",
                supported_now=False,
                blocked_by=["multi_metric_not_supported"],
                reason="multi-metric grouped SQL is not implemented in this phase",
            )
        if aggregate_function not in {"sum", "avg", "min", "max", "count"}:
            return DeterministicCapabilityResult(
                status="cannot_plan_safely",
                query_shape="grouped_aggregate",
                supported_now=True,
                blocked_by=["aggregate_function_missing"],
                reason="grouped aggregate function is missing",
            )
        if not (context.get("selected_dimensions") or []):
            return DeterministicCapabilityResult(
                status="cannot_plan_safely",
                query_shape="grouped_aggregate",
                supported_now=True,
                blocked_by=["selected_dimension_missing"],
                reason="planner-selected group dimension evidence is missing",
            )
        if aggregate_function != "count" and not isinstance(context.get("selected_metric"), dict):
            return DeterministicCapabilityResult(
                status="cannot_plan_safely",
                query_shape="grouped_aggregate",
                supported_now=True,
                blocked_by=["selected_metric_missing"],
                reason="planner-selected grouped metric evidence is missing",
            )
        selected_having = [
            entry for entry in (context.get("selected_having") or [])
            if isinstance(entry, dict)
        ]
        if len(selected_having) > 1:
            return DeterministicCapabilityResult(
                status="not_applicable",
                query_shape="grouped_aggregate",
                supported_now=False,
                blocked_by=["multiple_having_conditions_not_supported"],
                reason="multiple HAVING conditions are not implemented in this phase",
            )
        return DeterministicCapabilityResult(
            status="supported",
            query_shape="grouped_aggregate",
            supported_now=True,
            required_evidence=["selected_table", "selected_dimension", "aggregate_function"],
            reason="single-table grouped aggregate can be planned deterministically",
        )

    if contract_shape == "filtered_query":
        intent = context.get("intent") if isinstance(context.get("intent"), dict) else {}
        if len(selected_tables) != 1 or context.get("join_paths"):
            return DeterministicCapabilityResult(
                status="not_applicable",
                query_shape="filtered_query",
                supported_now=False,
                blocked_by=["single_table_filter_required"],
                required_evidence=["selected_table", "selected_filter"],
                reason="filtered SQL requires exactly one selected table and no joins",
            )
        if plan.get("grouping") or plan.get("dimension"):
            return DeterministicCapabilityResult(
                status="not_applicable",
                query_shape="filtered_query",
                supported_now=False,
                blocked_by=["grouping_not_supported"],
                reason="grouped filtered SQL is not implemented",
            )
        if plan.get("sorting") or intent.get("requested_sort"):
            return DeterministicCapabilityResult(
                status="not_applicable",
                query_shape="filtered_query",
                supported_now=False,
                blocked_by=["ranking_not_supported"],
                reason="ranked filtered SQL is not implemented",
            )
        if context.get("formula_evidence"):
            return DeterministicCapabilityResult(
                status="not_applicable",
                query_shape="filtered_query",
                supported_now=False,
                blocked_by=["formula_not_supported"],
                reason="formula filtered SQL is not implemented",
            )
        if len(list(intent.get("requested_metrics") or [])) > 1:
            return DeterministicCapabilityResult(
                status="not_applicable",
                query_shape="filtered_query",
                supported_now=False,
                blocked_by=["multi_metric_not_supported"],
                reason="multi-metric filtered SQL is not implemented",
            )
        if not (context.get("selected_filters") or []):
            return DeterministicCapabilityResult(
                status="cannot_plan_safely",
                query_shape="filtered_query",
                supported_now=True,
                blocked_by=["selected_filter_missing"],
                required_evidence=["selected_table", "selected_filter"],
                reason="planner-selected filter evidence is missing",
            )
        selected_filters = [
            entry for entry in (context.get("selected_filters") or [])
            if isinstance(entry, dict)
        ]
        selected_filter_operators = {
            str(entry.get("operator") or "").strip().lower()
            for entry in selected_filters
        }
        if not selected_filter_operators or not selected_filter_operators <= set(_FILTER_OPERATORS):
            return DeterministicCapabilityResult(
                status="not_applicable",
                query_shape="filtered_query",
                supported_now=False,
                blocked_by=["filter_operator_not_supported"],
                reason="the selected filter operator is not implemented in this phase",
            )
        conjunctions = [
            str(entry.get("conjunction") or "").strip().lower()
            for entry in selected_filters
        ]
        if conjunctions and conjunctions[0]:
            return DeterministicCapabilityResult(
                status="cannot_plan_safely",
                query_shape="filtered_query",
                supported_now=True,
                blocked_by=["filter_conjunction_invalid"],
                reason="the first filter clause cannot have a conjunction",
            )
        active_conjunctions = {value or "and" for value in conjunctions[1:]}
        if not active_conjunctions <= {"and", "or"} or len(active_conjunctions) > 1:
            return DeterministicCapabilityResult(
                status="cannot_plan_safely",
                query_shape="filtered_query",
                supported_now=True,
                blocked_by=["filter_conjunction_not_supported"],
                reason="mixed or unsupported filter conjunctions cannot be planned safely",
            )
        if aggregate_function == "count":
            return DeterministicCapabilityResult(
                status="not_applicable",
                query_shape="filtered_query",
                supported_now=False,
                blocked_by=["filtered_count_not_supported"],
                reason="filtered count SQL is not implemented in this phase",
            )
        return DeterministicCapabilityResult(
            status="supported",
            query_shape="filtered_query",
            supported_now=True,
            required_evidence=["selected_table", "selected_filter"],
            reason="single-table filtered query can be planned deterministically",
        )

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
    if capability.status != "supported":
        return DeterministicSqlPlan(
            query_shape=capability.query_shape,
            status=capability.status,
            supported_now=capability.supported_now,
            required_evidence=list(capability.required_evidence),
            missing_evidence=list(capability.blocked_by),
            route_reason=capability.reason,
            formula_evidence=list((query_context or {}).get("formula_evidence") or []),
        )

    if capability.query_shape == "single_table_aggregate":
        return _build_single_table_aggregate_plan(
            query_context=query_context,
            knowledge_base=knowledge_base,
            capability=capability,
        )
    if capability.query_shape == "filtered_query":
        return _build_filtered_single_table_plan(
            query_context=query_context,
            knowledge_base=knowledge_base,
            capability=capability,
        )
    if capability.query_shape == "grouped_aggregate":
        return _build_grouped_aggregate_plan(
            query_context=query_context,
            knowledge_base=knowledge_base,
            capability=capability,
        )
    return DeterministicSqlPlan(
        query_shape=capability.query_shape,
        status="not_applicable",
        supported_now=False,
        route_reason=f"no deterministic planner is registered for {capability.query_shape}",
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

    scoped_kb = context.get("selected_knowledge_base") if isinstance(context.get("selected_knowledge_base"), dict) else {}
    # The retrieval projection may omit physical SQL types. Prefer the full KB
    # schema for deterministic type validation, while retaining scoped fallback.
    table_data = (knowledge_base or {}).get(table_name) or scoped_kb.get(table_name)
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

    aggregate_function = _planner_aggregate_function(context, plan)
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


def _build_grouped_aggregate_plan(
    *,
    query_context: dict[str, Any],
    knowledge_base: dict[str, Any],
    capability: DeterministicCapabilityResult,
) -> DeterministicSqlPlan:
    context = query_context if isinstance(query_context, dict) else {}
    plan = context.get("plan") if isinstance(context.get("plan"), dict) else {}
    intent = context.get("intent") if isinstance(context.get("intent"), dict) else {}
    selected_tables = [entry for entry in (context.get("selected_tables") or []) if isinstance(entry, dict)]
    table_name = str(selected_tables[0].get("table") or "").strip() if len(selected_tables) == 1 else ""
    scoped_kb = context.get("selected_knowledge_base") if isinstance(context.get("selected_knowledge_base"), dict) else {}
    table_data = (knowledge_base or {}).get(table_name) or scoped_kb.get(table_name)
    if not table_name or not _SAFE_IDENTIFIER_RE.fullmatch(table_name) or not isinstance(table_data, dict):
        return _cannot_plan_grouped(capability, table_name=table_name or None, reason="table_schema_missing")

    schema_columns = {
        str(column.get("name") or "").strip(): column
        for column in (table_data.get("columns") or [])
        if isinstance(column, dict) and str(column.get("name") or "").strip()
    }
    dimension_candidates = [
        entry for entry in (context.get("selected_dimensions") or [])
        if isinstance(entry, dict)
        and str(entry.get("table") or "").strip() == table_name
        and _SAFE_IDENTIFIER_RE.fullmatch(str(entry.get("column") or entry.get("column_name") or "").strip())
    ]
    if not dimension_candidates:
        return _cannot_plan_grouped(capability, table_name=table_name, reason="dimension_not_found")
    dimension_column = str(
        dimension_candidates[0].get("column") or dimension_candidates[0].get("column_name") or ""
    ).strip()
    if dimension_column not in schema_columns:
        return _cannot_plan_grouped(capability, table_name=table_name, reason="dimension_not_in_schema")

    aggregate_function = _planner_aggregate_function(context, plan)
    if aggregate_function not in {"sum", "avg", "min", "max", "count"}:
        return _cannot_plan_grouped(capability, table_name=table_name, reason="aggregate_function_missing")

    metric_column = ""
    if aggregate_function == "count":
        aggregate_expression = "COUNT(*)"
        aggregate_alias = "count_rows"
    else:
        resolved_metric, metric_reason = _resolve_metric_column(
            query_context=context,
            table_name=table_name,
            table_data=table_data,
        )
        if resolved_metric is None:
            return _cannot_plan_grouped(
                capability,
                table_name=table_name,
                reason=metric_reason,
                dimension_columns=[dimension_column],
            )
        metric_column = resolved_metric
        aggregate_expression = f"{aggregate_function.upper()}({metric_column})"
        aggregate_alias = _aggregate_alias(aggregate_function, metric_column)

    where_clauses: list[str] = []
    where_conjunctions: list[str] = []
    filter_columns: list[str] = []
    requested_filters = list(intent.get("structured_filters") or intent.get("requested_filters") or [])
    if requested_filters or context.get("selected_filters"):
        where_clauses, where_conjunctions, filter_columns, filter_reason = _resolve_filter_clauses(
            query_context=context,
            table_name=table_name,
            table_data=table_data,
        )
        if filter_reason:
            return _cannot_plan_grouped(
                capability,
                table_name=table_name,
                reason=filter_reason,
                dimension_columns=[dimension_column],
                metric_columns=[metric_column] if metric_column else [],
            )

    having_clauses: list[str] = []
    having_columns: list[str] = []
    structured_having = [
        entry for entry in (intent.get("structured_having") or [])
        if isinstance(entry, dict)
    ]
    selected_having = [
        entry for entry in (context.get("selected_having") or [])
        if isinstance(entry, dict)
    ]
    if structured_having and len(selected_having) != len(structured_having):
        return _cannot_plan_grouped(
            capability,
            table_name=table_name,
            reason="having_evidence_incomplete",
            dimension_columns=[dimension_column],
            metric_columns=[metric_column] if metric_column else [],
        )
    for condition in selected_having:
        having_function = str(condition.get("aggregate_function") or "").strip().lower()
        operator = str(condition.get("operator") or "").strip().lower()
        if having_function != aggregate_function:
            return _cannot_plan_grouped(
                capability,
                table_name=table_name,
                reason="having_aggregate_mismatch",
                dimension_columns=[dimension_column],
                metric_columns=[metric_column] if metric_column else [],
            )
        sql_operator = _FILTER_OPERATORS.get(operator)
        if operator not in {"eq", "neq", "gt", "lt", "gte", "lte"} or not sql_operator:
            return _cannot_plan_grouped(
                capability,
                table_name=table_name,
                reason="having_operator_not_supported",
                dimension_columns=[dimension_column],
                metric_columns=[metric_column] if metric_column else [],
            )
        if aggregate_function != "count":
            having_table = str(condition.get("table") or "").strip()
            having_column = str(condition.get("column") or "").strip()
            if having_table != table_name or having_column != metric_column:
                return _cannot_plan_grouped(
                    capability,
                    table_name=table_name,
                    reason="having_metric_not_selected",
                    dimension_columns=[dimension_column],
                    metric_columns=[metric_column],
                )
            having_columns.append(metric_column)
        literal, literal_reason = _filter_literal(
            condition.get("value", condition.get("value_phrase")),
            {"type": "DECIMAL(38,10)", "semantic_type": "numeric_candidate"},
            operator,
        )
        if literal_reason:
            return _cannot_plan_grouped(
                capability,
                table_name=table_name,
                reason="having_value_type_mismatch",
                dimension_columns=[dimension_column],
                metric_columns=[metric_column] if metric_column else [],
            )
        having_clauses.append(f"{aggregate_expression} {sql_operator} {literal}")

    return DeterministicSqlPlan(
        query_shape="grouped_aggregate",
        status="ready",
        supported_now=True,
        base_table=table_name,
        select_items=[
            {"expression": dimension_column, "source_column": dimension_column, "kind": "dimension"},
            {
                "expression": aggregate_expression,
                "alias": aggregate_alias,
                "source_column": metric_column,
                "kind": "aggregate",
            },
        ],
        where_clauses=where_clauses,
        where_conjunctions=where_conjunctions,
        having_clauses=having_clauses,
        group_by=[dimension_column],
        aggregation_type=aggregate_function,
        metric_columns=[metric_column] if metric_column else [],
        dimension_columns=[dimension_column],
        filter_columns=filter_columns,
        having_columns=having_columns,
        required_evidence=list(capability.required_evidence),
        evidence_sources=[
            "query_context.selected_tables",
            "query_context.selected_dimensions",
            "query_context.selected_metric",
            "query_context.selected_having",
            "knowledge_base.columns",
        ],
        sql_skeleton_type="grouped_aggregate",
        can_render=True,
        route_reason="single-table grouped aggregate generated deterministically",
    )


def _cannot_plan_grouped(
    capability: DeterministicCapabilityResult,
    *,
    table_name: str | None,
    reason: str,
    dimension_columns: list[str] | None = None,
    metric_columns: list[str] | None = None,
) -> DeterministicSqlPlan:
    return DeterministicSqlPlan(
        query_shape="grouped_aggregate",
        status="cannot_plan_safely",
        supported_now=True,
        base_table=table_name,
        dimension_columns=list(dimension_columns or []),
        metric_columns=list(metric_columns or []),
        required_evidence=list(capability.required_evidence),
        missing_evidence=[reason],
        route_reason=reason,
    )


def _build_filtered_single_table_plan(
    *,
    query_context: dict[str, Any],
    knowledge_base: dict[str, Any],
    capability: DeterministicCapabilityResult,
) -> DeterministicSqlPlan:
    context = query_context if isinstance(query_context, dict) else {}
    plan = context.get("plan") if isinstance(context.get("plan"), dict) else {}
    selected_tables = [entry for entry in (context.get("selected_tables") or []) if isinstance(entry, dict)]
    table_name = str(selected_tables[0].get("table") or "").strip()
    scoped_kb = context.get("selected_knowledge_base") if isinstance(context.get("selected_knowledge_base"), dict) else {}
    table_data = (knowledge_base or {}).get(table_name) or scoped_kb.get(table_name)
    if not table_name or not _SAFE_IDENTIFIER_RE.fullmatch(table_name) or not isinstance(table_data, dict):
        return _cannot_plan_filtered(
            capability,
            table_name=table_name or None,
            reason="table_schema_missing",
        )

    where_clauses, where_conjunctions, filter_columns, filter_reason = _resolve_filter_clauses(
        query_context=context,
        table_name=table_name,
        table_data=table_data,
    )
    if filter_reason:
        return _cannot_plan_filtered(
            capability,
            table_name=table_name,
            reason=filter_reason,
        )

    aggregate_function = _planner_aggregate_function(context, plan)
    if aggregate_function:
        metric_column, metric_reason = _resolve_metric_column(
            query_context=context,
            table_name=table_name,
            table_data=table_data,
        )
        if metric_column is None:
            return _cannot_plan_filtered(
                capability,
                table_name=table_name,
                reason=metric_reason,
                filter_columns=filter_columns,
            )
        aggregate_alias = _aggregate_alias(aggregate_function, metric_column)
        select_items = [
            {
                "expression": f"{aggregate_function.upper()}({metric_column})",
                "alias": aggregate_alias,
                "source_column": metric_column,
                "kind": "aggregate",
            }
        ]
        metric_columns = [metric_column]
        sql_skeleton_type = "filtered_single_table_aggregate"
        limit = None
    else:
        column_names = [
            str(column.get("name") or "").strip()
            for column in (table_data.get("columns") or [])
            if isinstance(column, dict)
            and _SAFE_IDENTIFIER_RE.fullmatch(str(column.get("name") or "").strip())
        ]
        if not column_names:
            return _cannot_plan_filtered(
                capability,
                table_name=table_name,
                reason="select_columns_missing",
                filter_columns=filter_columns,
            )
        select_items = [
            {"expression": column_name, "source_column": column_name, "kind": "column"}
            for column_name in column_names
        ]
        metric_columns = []
        sql_skeleton_type = "filtered_single_table_list"
        requested_limit = context.get("limit", plan.get("limit"))
        limit = int(requested_limit) if isinstance(requested_limit, int) and requested_limit > 0 else 50

    return DeterministicSqlPlan(
        query_shape="filtered_query",
        status="ready",
        supported_now=True,
        base_table=table_name,
        select_items=select_items,
        where_clauses=where_clauses,
        where_conjunctions=where_conjunctions,
        limit=limit,
        aggregation_type=aggregate_function,
        metric_columns=metric_columns,
        filter_columns=filter_columns,
        required_evidence=list(capability.required_evidence),
        evidence_sources=["query_context.selected_tables", "query_context.selected_filters", "knowledge_base.columns"],
        sql_skeleton_type=sql_skeleton_type,
        can_render=True,
        route_reason="single-table filtered SQL generated deterministically",
        formula_evidence=list(context.get("formula_evidence") or []),
    )


def _cannot_plan_filtered(
    capability: DeterministicCapabilityResult,
    *,
    table_name: str | None,
    reason: str,
    filter_columns: list[str] | None = None,
) -> DeterministicSqlPlan:
    return DeterministicSqlPlan(
        query_shape="filtered_query",
        status="cannot_plan_safely",
        supported_now=True,
        base_table=table_name,
        filter_columns=list(filter_columns or []),
        required_evidence=list(capability.required_evidence),
        missing_evidence=[reason],
        route_reason=reason,
    )


def _resolve_filter_clauses(
    *,
    query_context: dict[str, Any],
    table_name: str,
    table_data: dict[str, Any],
) -> tuple[list[str], list[str], list[str], str]:
    selected_filters = [
        entry for entry in (query_context.get("selected_filters") or [])
        if isinstance(entry, dict)
    ]
    intent = query_context.get("intent") if isinstance(query_context.get("intent"), dict) else {}
    structured_filters = [
        entry for entry in (intent.get("structured_filters") or [])
        if isinstance(entry, dict)
    ]
    if not selected_filters:
        return [], [], [], "selected_filter_missing"
    if structured_filters and len(selected_filters) != len(structured_filters):
        return [], [], [], "filter_evidence_incomplete"

    schema_columns = {
        str(column.get("name") or "").strip(): column
        for column in (table_data.get("columns") or [])
        if isinstance(column, dict) and str(column.get("name") or "").strip()
    }
    where_clauses: list[str] = []
    where_conjunctions: list[str] = []
    filter_columns: list[str] = []
    active_conjunctions: set[str] = set()
    for index, selected_filter in enumerate(selected_filters):
        filter_table = str(selected_filter.get("table") or "").strip()
        column_name = str(selected_filter.get("column") or selected_filter.get("column_name") or "").strip()
        operator = str(selected_filter.get("operator") or "").strip().lower()
        conjunction = str(selected_filter.get("conjunction") or "").strip().lower()
        if filter_table != table_name or not _SAFE_IDENTIFIER_RE.fullmatch(column_name):
            return [], [], [], "filter_column_not_selected"
        schema_column = schema_columns.get(column_name)
        if schema_column is None:
            return [], [], [], "filter_column_not_in_schema"
        if index == 0 and conjunction:
            return [], [], [], "filter_conjunction_invalid"
        normalized_conjunction = "" if index == 0 else (conjunction or "and")
        if index > 0 and normalized_conjunction not in {"and", "or"}:
            return [], [], [], "filter_conjunction_not_supported"
        if normalized_conjunction:
            active_conjunctions.add(normalized_conjunction)
            if len(active_conjunctions) > 1:
                return [], [], [], "filter_conjunction_not_supported"
        predicate, predicate_reason = _filter_predicate(
            column_name,
            schema_column,
            operator,
            selected_filter,
        )
        if predicate_reason:
            return [], [], [], predicate_reason
        where_clauses.append(predicate)
        where_conjunctions.append(normalized_conjunction)
        filter_columns.append(column_name)
    return where_clauses, where_conjunctions, filter_columns, ""


def _filter_predicate(
    column_name: str,
    schema_column: dict[str, Any],
    operator: str,
    selected_filter: dict[str, Any],
) -> tuple[str, str]:
    if operator == "is_null":
        return f"{column_name} IS NULL", ""
    if operator == "is_not_null":
        return f"{column_name} IS NOT NULL", ""
    if operator == "between":
        values = selected_filter.get("values") or selected_filter.get("value")
        if not isinstance(values, (list, tuple)) or len(values) != 2:
            return "", "filter_between_values_invalid"
        lower, lower_reason = _filter_literal(values[0], schema_column, operator)
        upper, upper_reason = _filter_literal(values[1], schema_column, operator)
        if lower_reason or upper_reason:
            return "", lower_reason or upper_reason
        return f"{column_name} BETWEEN {lower} AND {upper}", ""

    sql_operator = _FILTER_OPERATORS.get(operator)
    if not sql_operator:
        return "", "filter_operator_not_supported"
    literal, literal_reason = _filter_literal(
        selected_filter.get("value", selected_filter.get("value_phrase")),
        schema_column,
        operator,
    )
    if literal_reason:
        return "", literal_reason
    return f"{column_name} {sql_operator} {literal}", ""


def _filter_literal(value: Any, schema_column: dict[str, Any], operator: str) -> tuple[str, str]:
    if isinstance(value, (list, tuple, dict)) or value is None:
        return "", "filter_value_missing"
    text = str(value).strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        text = text[1:-1].strip()
    if not text:
        return "", "filter_value_missing"
    column_kind = _filter_column_kind(schema_column)
    if column_kind == "numeric":
        if operator not in {"eq", "neq", "gt", "lt", "gte", "lte", "between"}:
            return "", "filter_operator_type_mismatch"
        try:
            numeric = Decimal(text)
        except (InvalidOperation, ValueError):
            return "", "filter_value_type_mismatch"
        if not numeric.is_finite():
            return "", "filter_value_type_mismatch"
        return format(numeric, "f"), ""
    if column_kind == "date":
        if operator not in {"eq", "neq", "gt", "lt", "gte", "lte", "before", "after", "between"}:
            return "", "filter_operator_type_mismatch"
        return _date_filter_literal(text, schema_column)
    if operator not in {"eq", "neq", "contains"}:
        return "", "filter_operator_type_mismatch"
    if operator == "contains":
        text = f"%{text}%"
    return "'" + text.replace("'", "''") + "'", ""


def _filter_column_kind(schema_column: dict[str, Any]) -> str:
    column_type = str(schema_column.get("type") or "").strip().lower()
    semantic_type = resolved_semantic_type(schema_column)
    if any(marker in column_type for marker in _NUMERIC_TYPE_MARKERS):
        return "numeric"
    if semantic_type == "date" or any(marker in column_type for marker in _DATE_TYPE_MARKERS):
        return "date"
    return "text"


def _date_filter_literal(text: str, schema_column: dict[str, Any]) -> tuple[str, str]:
    column_type = str(schema_column.get("type") or "").strip().lower()
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            normalized = date.fromisoformat(text).isoformat()
        elif "time" in column_type or "timestamp" in column_type:
            parsed = datetime.fromisoformat(text.replace(" ", "T"))
            if parsed.tzinfo is not None:
                return "", "filter_value_type_mismatch"
            normalized = parsed.strftime("%Y-%m-%d %H:%M:%S")
        else:
            return "", "filter_value_type_mismatch"
    except ValueError:
        return "", "filter_value_type_mismatch"
    return f"'{normalized}'", ""


def _render_filtered_query(plan: DeterministicSqlPlan) -> str:
    select_parts = []
    for item in plan.select_items:
        expression = str(item.get("expression") or "").strip()
        alias = str(item.get("alias") or "").strip()
        select_parts.append(f"{expression} AS {alias}" if alias else expression)
    predicate = _render_predicates(plan.where_clauses, plan.where_conjunctions)
    sql = f"SELECT {', '.join(select_parts)} FROM {plan.base_table} WHERE {predicate}"
    if plan.limit:
        sql += f" LIMIT {plan.limit}"
    return sql + ";"


def _render_grouped_aggregate(plan: DeterministicSqlPlan) -> str:
    select_parts = []
    for item in plan.select_items:
        expression = str(item.get("expression") or "").strip()
        alias = str(item.get("alias") or "").strip()
        select_parts.append(f"{expression} AS {alias}" if alias else expression)
    sql = f"SELECT {', '.join(select_parts)} FROM {plan.base_table}"
    if plan.where_clauses:
        sql += f" WHERE {_render_predicates(plan.where_clauses, plan.where_conjunctions)}"
    sql += f" GROUP BY {', '.join(plan.group_by)}"
    if plan.having_clauses:
        sql += f" HAVING {_render_predicates(plan.having_clauses, plan.having_conjunctions)}"
    return sql + ";"


def _render_predicates(clauses: list[str], conjunctions: list[str]) -> str:
    predicate = clauses[0]
    for index, clause in enumerate(clauses[1:], start=1):
        conjunction = conjunctions[index] if index < len(conjunctions) else "and"
        predicate += f" {(conjunction or 'and').upper()} {clause}"
    return predicate


_PLAN_RENDERERS = {
    "single_table_aggregate": _render_single_table_aggregate,
    "filtered_query": _render_filtered_query,
    "grouped_aggregate": _render_grouped_aggregate,
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


def _planner_aggregate_function(context: dict[str, Any], plan: dict[str, Any]) -> str | None:
    selected_function = str(context.get("aggregate_function") or "").strip().lower()
    if selected_function:
        normalized = {"average": "avg", "mean": "avg", "total": "sum"}.get(
            selected_function,
            selected_function,
        )
        return normalized if normalized in {"sum", "avg", "min", "max", "count"} else None
    return _detect_aggregate_function(plan)


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
    table_name: str,
    table_data: dict[str, Any],
) -> tuple[str | None, str]:
    table_columns = [column for column in (table_data.get("columns") or []) if isinstance(column, dict)]
    ambiguity_types = {
        str(value).strip()
        for value in (query_context.get("ambiguities") or [])
        if str(value).strip()
    }
    if "metric_selection" in ambiguity_types:
        return None, "metric_ambiguous"

    selected_metric = query_context.get("selected_metric")
    if not isinstance(selected_metric, dict):
        return None, "metric_not_found"

    metric_table = str(selected_metric.get("table") or "").strip()
    metric_column = str(selected_metric.get("column") or selected_metric.get("column_name") or "").strip()
    if not metric_column or (metric_table and metric_table != table_name):
        return None, "metric_not_found"

    schema_column = next(
        (column for column in table_columns if str(column.get("name") or "").strip() == metric_column),
        None,
    )
    if schema_column is None or not _is_numeric_metric_column(schema_column, table_data):
        return None, "metric_not_found"
    return metric_column, ""


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
