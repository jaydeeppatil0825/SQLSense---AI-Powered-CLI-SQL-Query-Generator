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

from dataclasses import dataclass, field, replace
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
    clause_shape: str = "unsupported"
    decision_path: list[dict[str, str]] = field(default_factory=list)
    status: str = "not_applicable"
    supported_now: bool = False
    base_table: Optional[str] = None
    joins: list[dict[str, Any]] = field(default_factory=list)
    required_joins: list[dict[str, Any]] = field(default_factory=list)
    selected_join_path: Optional[dict[str, Any]] = None
    selected_output_columns: list[dict[str, Any]] = field(default_factory=list)
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

    if contract_shape == "joined_aggregate":
        selected_path = context.get("selected_join_path")
        selected_dimensions = [
            entry for entry in (context.get("selected_dimensions") or []) if isinstance(entry, dict)
        ]
        if (
            len(selected_tables) != 2
            or not isinstance(selected_path, dict)
            or len(list(selected_path.get("joined_tables") or [])) != 1
            or len(list(selected_path.get("edges") or [])) != 1
        ):
            return DeterministicCapabilityResult(
                status="cannot_plan_safely",
                query_shape="joined_aggregate",
                supported_now=True,
                blocked_by=["selected_join_path_missing"],
                reason="joined aggregate requires exactly two tables and one planner-selected graph edge",
            )
        if aggregate_function not in {"count", "sum", "avg", "min", "max"}:
            return DeterministicCapabilityResult(
                status="cannot_plan_safely",
                query_shape="joined_aggregate",
                supported_now=True,
                blocked_by=["aggregate_function_missing"],
                reason="joined aggregate function is missing or unsupported",
            )
        if len(selected_dimensions) != 1:
            return DeterministicCapabilityResult(
                status="cannot_plan_safely",
                query_shape="joined_aggregate",
                supported_now=True,
                blocked_by=["selected_dimension_missing"],
                reason="joined aggregate requires one planner-selected grouping dimension",
            )
        if aggregate_function != "count" and not isinstance(context.get("selected_metric"), dict):
            return DeterministicCapabilityResult(
                status="cannot_plan_safely",
                query_shape="joined_aggregate",
                supported_now=True,
                blocked_by=["selected_metric_missing"],
                reason="joined aggregate metric is missing",
            )
        required_evidence = [
            "selected_join_path",
            "selected_dimension",
            "aggregate_function",
            "selected_output_columns",
        ]
        if aggregate_function != "count":
            required_evidence.append("selected_metric")
        return DeterministicCapabilityResult(
            status="supported",
            query_shape="joined_aggregate",
            supported_now=True,
            required_evidence=required_evidence,
            reason="two-table aggregate is authorized by one selected Relationship Graph edge",
        )

    if contract_shape == "joined_lookup":
        intent = context.get("intent") if isinstance(context.get("intent"), dict) else {}
        selected_path = context.get("selected_join_path")
        selected_outputs = [
            entry for entry in (context.get("selected_output_columns") or []) if isinstance(entry, dict)
        ]
        if (
            len(selected_tables) != 2
            or not isinstance(selected_path, dict)
            or len(list(selected_path.get("joined_tables") or [])) != 1
            or len(list(selected_path.get("edges") or [])) != 1
        ):
            return DeterministicCapabilityResult(
                status="cannot_plan_safely",
                query_shape="joined_lookup",
                supported_now=True,
                blocked_by=["selected_join_path_missing"],
                reason="joined lookup requires exactly two tables and one planner-selected graph edge",
            )
        if not selected_outputs:
            return DeterministicCapabilityResult(
                status="cannot_plan_safely",
                query_shape="joined_lookup",
                supported_now=True,
                blocked_by=["selected_output_columns_missing"],
                reason="planner-selected joined output columns are missing",
            )
        if (
            intent.get("needs_aggregation")
            or intent.get("needs_grouping")
            or intent.get("structured_having")
            or intent.get("requested_sort")
            or context.get("formula_evidence")
        ):
            return DeterministicCapabilityResult(
                status="cannot_plan_safely",
                query_shape="joined_lookup",
                supported_now=True,
                blocked_by=["joined_analytics_not_supported"],
                reason="joined analytics are not supported in Phase 5",
            )
        limit = context.get("limit")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
            return DeterministicCapabilityResult(
                status="cannot_plan_safely",
                query_shape="joined_lookup",
                supported_now=True,
                blocked_by=["limit_out_of_safe_range"],
                reason="joined lookup LIMIT must be between 1 and 1000",
            )
        return DeterministicCapabilityResult(
            status="supported",
            query_shape="joined_lookup",
            supported_now=True,
            required_evidence=["selected_join_path", "selected_output_columns"],
            reason="two-table lookup is authorized by the selected Relationship Graph edge",
        )

    if contract_shape == "ranking_query":
        intent = context.get("intent") if isinstance(context.get("intent"), dict) else {}
        if intent.get("unsupported_constructs"):
            return DeterministicCapabilityResult(
                status="cannot_plan_safely",
                query_shape="ranking_query",
                supported_now=True,
                blocked_by=["unsupported_intent"],
                reason="ranking intent contains an unsupported deterministic construct",
            )
        if len(selected_tables) != 1 or context.get("join_paths"):
            return DeterministicCapabilityResult(
                status="cannot_plan_safely",
                query_shape="ranking_query",
                supported_now=True,
                blocked_by=["single_table_ranking_required"],
                reason="ranking requires exactly one selected table and no joins",
            )
        if context.get("formula_evidence") or len(list(intent.get("requested_metrics") or [])) > 1:
            return DeterministicCapabilityResult(
                status="cannot_plan_safely",
                query_shape="ranking_query",
                supported_now=True,
                blocked_by=["unsupported_ranking_scope"],
                reason="formula and multi-metric ranking are not supported",
            )
        if not isinstance(context.get("selected_order_by"), dict):
            return DeterministicCapabilityResult(
                status="cannot_plan_safely",
                query_shape="ranking_query",
                supported_now=True,
                blocked_by=["selected_order_by_missing"],
                reason="planner-selected ORDER BY evidence is missing",
            )
        limit = context.get("limit")
        if limit is not None and (
            isinstance(limit, bool) or not isinstance(limit, int) or limit < 1 or limit > 1000
        ):
            return DeterministicCapabilityResult(
                status="cannot_plan_safely",
                query_shape="ranking_query",
                supported_now=True,
                blocked_by=["limit_out_of_safe_range"],
                reason="ranking LIMIT must be between 1 and 1000",
            )
        ranking_mode = str((intent.get("ranking_diagnostics") or {}).get("mode_hint") or "")
        if ranking_mode == "grouped_aggregate":
            if aggregate_function not in {"sum", "avg", "min", "max", "count"}:
                return DeterministicCapabilityResult(
                    status="cannot_plan_safely",
                    query_shape="ranking_query",
                    supported_now=True,
                    blocked_by=["aggregate_function_missing"],
                    reason="grouped ranking aggregate function is missing",
                )
            if len(list(context.get("selected_dimensions") or [])) != 1:
                return DeterministicCapabilityResult(
                    status="cannot_plan_safely",
                    query_shape="ranking_query",
                    supported_now=True,
                    blocked_by=["selected_dimension_missing"],
                    reason="grouped ranking requires one selected dimension",
                )
            if aggregate_function != "count" and not isinstance(context.get("selected_metric"), dict):
                return DeterministicCapabilityResult(
                    status="cannot_plan_safely",
                    query_shape="ranking_query",
                    supported_now=True,
                    blocked_by=["selected_metric_missing"],
                    reason="grouped ranking metric evidence is missing",
                )
        return DeterministicCapabilityResult(
            status="supported",
            query_shape="ranking_query",
            supported_now=True,
            required_evidence=["selected_table", "selected_order_by"],
            reason="single-table ranking can be planned deterministically",
        )

    if contract_shape == "grouped_aggregate":
        intent = context.get("intent") if isinstance(context.get("intent"), dict) else {}
        if intent.get("unsupported_constructs"):
            return DeterministicCapabilityResult(
                status="cannot_plan_safely",
                query_shape="grouped_aggregate",
                supported_now=True,
                blocked_by=["unsupported_intent"],
                reason="grouped intent contains an unsupported deterministic construct",
            )
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


def _actual_clause_shape(plan: DeterministicSqlPlan) -> str:
    if plan.query_shape == "joined_lookup" and plan.joins:
        return "joined_lookup"
    has_where = bool(plan.where_clauses)
    has_grouping = bool(plan.group_by)
    has_having = bool(plan.having_clauses)
    has_aggregate = bool(plan.aggregation_type)
    if has_grouping:
        if has_where and has_having:
            return "where_group_by_having"
        if has_where:
            return "where_group_by"
        if has_having:
            return "group_by_having"
        return "group_by"
    if has_where:
        return "aggregate_where" if has_aggregate else "where_only"
    if has_aggregate:
        return "aggregate_only"
    if plan.select_items:
        return "list_only"
    return "unsupported"


def _decision_path_from_plan(plan: DeterministicSqlPlan, clause_shape: str) -> list[dict[str, str]]:
    requires_grouping = clause_shape in {
        "group_by",
        "where_group_by",
        "group_by_having",
        "where_group_by_having",
    }
    requires_where = clause_shape in {"where_only", "aggregate_where", "where_group_by", "where_group_by_having"}
    requires_having = clause_shape in {"group_by_having", "where_group_by_having"}
    requires_aggregate = clause_shape not in {"list_only", "where_only", "unsupported"}
    requires_metric = requires_aggregate and plan.aggregation_type != "count"
    requires_order_by = bool(plan.order_by)
    requires_limit = plan.limit is not None

    def node(name: str, status: str, reason: str) -> dict[str, str]:
        return {"node": name, "status": status, "reason": reason}

    ready = plan.status == "ready" and plan.can_render
    return [
        node("unsafe_check", "resolved", "request reached deterministic generation"),
        node("table_scope", "resolved" if plan.base_table else "blocked", "single table resolved" if plan.base_table else "table missing"),
        node("query_shape", "resolved" if clause_shape != "unsupported" else "blocked", f"resolved clause shape '{clause_shape}'"),
        node("aggregate", "resolved" if requires_aggregate and plan.aggregation_type else "blocked" if requires_aggregate else "not_required", "aggregate resolved" if requires_aggregate and plan.aggregation_type else "aggregate not required" if not requires_aggregate else "aggregate missing"),
        node("metric", "resolved" if requires_metric and plan.metric_columns else "blocked" if requires_metric else "not_required", "metric resolved" if requires_metric and plan.metric_columns else "metric not required" if not requires_metric else "metric missing"),
        node("dimension", "resolved" if requires_grouping and plan.dimension_columns else "blocked" if requires_grouping else "not_required", "dimension resolved" if requires_grouping and plan.dimension_columns else "dimension not required" if not requires_grouping else "dimension missing"),
        node("where", "resolved" if requires_where and plan.where_clauses else "blocked" if requires_where else "not_required", "WHERE resolved" if requires_where and plan.where_clauses else "WHERE not required" if not requires_where else "WHERE evidence missing"),
        node("having", "resolved" if requires_having and plan.having_clauses else "blocked" if requires_having else "not_required", "HAVING resolved" if requires_having and plan.having_clauses else "HAVING not required" if not requires_having else "HAVING evidence missing"),
        node("order_by", "resolved" if requires_order_by else "not_required", "ORDER BY resolved" if requires_order_by else "ORDER BY not required"),
        node("limit", "resolved" if requires_limit else "not_required", "LIMIT resolved" if requires_limit else "LIMIT not required"),
        node("clause_shape", "resolved" if clause_shape != "unsupported" else "blocked", f"resolved clause shape '{clause_shape}'"),
        node("route", "resolved" if ready else "blocked", "deterministic SQL generation is allowed" if ready else plan.route_reason or "plan is not renderable"),
    ]


def _apply_clause_plan_contract(
    plan: DeterministicSqlPlan,
    query_context: dict[str, Any],
) -> DeterministicSqlPlan:
    context = query_context if isinstance(query_context, dict) else {}
    clause_plan = context.get("clause_plan") if isinstance(context.get("clause_plan"), dict) else {}
    declared_shape = str(clause_plan.get("clause_shape") or "").strip()
    actual_shape = _actual_clause_shape(plan)
    clause_shape = declared_shape or actual_shape
    decision_path = [
        dict(entry)
        for entry in (clause_plan.get("decision_path") or [])
        if isinstance(entry, dict)
    ] or _decision_path_from_plan(plan, clause_shape)
    declared_requires = clause_plan.get("requires") if isinstance(clause_plan.get("requires"), dict) else {}
    declared_order_by = (
        clause_plan.get("selected_order_by")
        if isinstance(clause_plan.get("selected_order_by"), dict)
        else {}
    )
    context_order_by = (
        context.get("selected_order_by")
        if isinstance(context.get("selected_order_by"), dict)
        else {}
    )
    order_by_mismatch = bool(declared_requires) and (
        bool(declared_requires.get("order_by")) != bool(plan.order_by)
        or (bool(declared_requires.get("order_by")) and declared_order_by != context_order_by)
    )
    limit_mismatch = bool(declared_requires) and (
        bool(declared_requires.get("limit")) != (plan.limit is not None)
        or clause_plan.get("limit") != plan.limit
    )
    declared_join_path = context.get("selected_join_path")
    join_mismatch = plan.query_shape in {"joined_lookup", "joined_aggregate"} and (
        not isinstance(declared_join_path, dict)
        or declared_join_path != plan.selected_join_path
        or clause_plan.get("selected_join_path") != plan.selected_join_path
    )
    output_mismatch = plan.query_shape in {"joined_lookup", "joined_aggregate"} and (
        list(context.get("selected_output_columns") or []) != plan.selected_output_columns
    )

    if plan.status == "ready" and (
        (declared_shape and declared_shape != actual_shape)
        or order_by_mismatch
        or limit_mismatch
        or join_mismatch
        or output_mismatch
    ):
        return replace(
            plan,
            clause_shape=declared_shape,
            decision_path=decision_path,
            status="cannot_plan_safely",
            can_render=False,
            missing_evidence=list(plan.missing_evidence) + ["clause_plan_mismatch"],
            route_reason="planner clause requirements do not match resolved SQL clauses",
        )
    if plan.status == "ready" and any(
        entry.get("status") == "blocked" for entry in decision_path
    ):
        return replace(
            plan,
            clause_shape=clause_shape,
            decision_path=decision_path,
            status="cannot_plan_safely",
            can_render=False,
            missing_evidence=list(plan.missing_evidence) + ["clause_plan_blocked"],
            route_reason="planner clause decision path contains a blocked node",
        )
    return replace(
        plan,
        clause_shape=clause_shape,
        decision_path=decision_path,
    )


def build_deterministic_sql_plan(
    *,
    query_context: dict[str, Any],
    knowledge_base: dict[str, Any],
) -> DeterministicSqlPlan:
    """Build a normalized deterministic SQL plan from runtime pipeline evidence."""
    capability = analyze_deterministic_capabilities(query_context)
    if capability.status != "supported":
        plan = DeterministicSqlPlan(
            query_shape=capability.query_shape,
            status=capability.status,
            supported_now=capability.supported_now,
            required_evidence=list(capability.required_evidence),
            missing_evidence=list(capability.blocked_by),
            route_reason=capability.reason,
            formula_evidence=list((query_context or {}).get("formula_evidence") or []),
        )
        return _apply_clause_plan_contract(plan, query_context)

    if capability.query_shape in {
        "single_table_aggregate",
        "filtered_query",
        "grouped_aggregate",
        "ranking_query",
    }:
        plan = _build_single_table_clause_plan(
            query_context=query_context,
            knowledge_base=knowledge_base,
            capability=capability,
        )
    elif capability.query_shape == "joined_lookup":
        plan = _build_joined_lookup_plan(
            query_context=query_context,
            knowledge_base=knowledge_base,
            capability=capability,
        )
    elif capability.query_shape == "joined_aggregate":
        plan = _build_joined_aggregate_plan(
            query_context=query_context,
            knowledge_base=knowledge_base,
            capability=capability,
        )
    else:
        plan = DeterministicSqlPlan(
            query_shape=capability.query_shape,
            status="not_applicable",
            supported_now=False,
            route_reason=f"no deterministic planner is registered for {capability.query_shape}",
        )
    return _apply_clause_plan_contract(plan, query_context)


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


def _single_table_clause_shape(
    context: dict[str, Any],
    capability: DeterministicCapabilityResult,
    aggregate_function: str | None,
) -> str:
    intent = context.get("intent") if isinstance(context.get("intent"), dict) else {}
    has_where = bool(
        intent.get("structured_filters")
        or intent.get("requested_filters")
        or context.get("selected_filters")
    )
    has_having = bool(
        intent.get("structured_having")
        or intent.get("requested_having")
        or context.get("selected_having")
    )
    if capability.query_shape == "single_table_aggregate":
        return "aggregate_only"
    if capability.query_shape == "filtered_query":
        return "aggregate_where" if aggregate_function else "where_only"
    ranking_mode = str((intent.get("ranking_diagnostics") or {}).get("mode_hint") or "")
    if capability.query_shape == "ranking_query" and ranking_mode != "grouped_aggregate":
        return "where_only" if has_where else "list_only"
    if capability.query_shape == "grouped_aggregate" or (
        capability.query_shape == "ranking_query" and ranking_mode == "grouped_aggregate"
    ):
        if has_where and has_having:
            return "where_group_by_having"
        if has_where:
            return "where_group_by"
        if has_having:
            return "group_by_having"
        return "group_by"
    return "unsupported"


def _cannot_plan_single_table(
    capability: DeterministicCapabilityResult,
    *,
    table_name: str | None,
    reason: str,
    aggregation_type: str | None = None,
    metric_columns: list[str] | None = None,
    dimension_columns: list[str] | None = None,
    filter_columns: list[str] | None = None,
    having_columns: list[str] | None = None,
) -> DeterministicSqlPlan:
    return DeterministicSqlPlan(
        query_shape=capability.query_shape,
        status="cannot_plan_safely",
        supported_now=True,
        base_table=table_name,
        aggregation_type=aggregation_type,
        metric_columns=list(metric_columns or []),
        dimension_columns=list(dimension_columns or []),
        filter_columns=list(filter_columns or []),
        having_columns=list(having_columns or []),
        required_evidence=list(capability.required_evidence),
        missing_evidence=[reason],
        route_reason=reason,
    )


def _build_single_table_clause_plan(
    *,
    query_context: dict[str, Any],
    knowledge_base: dict[str, Any],
    capability: DeterministicCapabilityResult,
) -> DeterministicSqlPlan:
    """Resolve one single-table clause tree before choosing SQL expressions."""
    context = query_context if isinstance(query_context, dict) else {}
    planner_plan = context.get("plan") if isinstance(context.get("plan"), dict) else {}
    selected_tables = [
        entry for entry in (context.get("selected_tables") or [])
        if isinstance(entry, dict)
    ]
    if len(selected_tables) != 1 or context.get("join_paths"):
        return _cannot_plan_single_table(
            capability,
            table_name=None,
            reason="single_table_scope_required",
        )

    table_name = str(selected_tables[0].get("table") or "").strip()
    scoped_kb = (
        context.get("selected_knowledge_base")
        if isinstance(context.get("selected_knowledge_base"), dict)
        else {}
    )
    table_data = (knowledge_base or {}).get(table_name) or scoped_kb.get(table_name)
    if not table_name or not _SAFE_IDENTIFIER_RE.fullmatch(table_name) or not isinstance(table_data, dict):
        return _cannot_plan_single_table(
            capability,
            table_name=table_name or None,
            reason="table_schema_missing",
        )

    schema_columns = {
        str(column.get("name") or "").strip(): column
        for column in (table_data.get("columns") or [])
        if isinstance(column, dict)
        and _SAFE_IDENTIFIER_RE.fullmatch(str(column.get("name") or "").strip())
    }
    if not schema_columns:
        return _cannot_plan_single_table(
            capability,
            table_name=table_name,
            reason="select_columns_missing",
        )

    aggregate_function = _planner_aggregate_function(context, planner_plan)
    clause_shape = _single_table_clause_shape(context, capability, aggregate_function)
    requires_grouping = clause_shape in {
        "group_by",
        "where_group_by",
        "group_by_having",
        "where_group_by_having",
    }
    requires_where = clause_shape in {
        "where_only",
        "aggregate_where",
        "where_group_by",
        "where_group_by_having",
    }
    requires_having = clause_shape in {"group_by_having", "where_group_by_having"}
    requires_aggregate = clause_shape not in {"list_only", "where_only", "unsupported"}

    if clause_shape == "unsupported":
        return _cannot_plan_single_table(
            capability,
            table_name=table_name,
            reason="clause_shape_not_supported",
        )
    if requires_aggregate and aggregate_function not in {"count", "sum", "avg", "min", "max"}:
        return _cannot_plan_single_table(
            capability,
            table_name=table_name,
            reason="aggregate_function_missing",
        )
    if capability.query_shape == "single_table_aggregate" and aggregate_function == "count":
        return _cannot_plan_single_table(
            capability,
            table_name=table_name,
            reason="count_is_handled_elsewhere",
            aggregation_type=aggregate_function,
        )

    metric_column = ""
    aggregate_expression = ""
    aggregate_alias = ""
    if requires_aggregate:
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
                return _cannot_plan_single_table(
                    capability,
                    table_name=table_name,
                    reason=metric_reason,
                    aggregation_type=aggregate_function,
                )
            metric_column = resolved_metric
            aggregate_expression = f"{aggregate_function.upper()}({metric_column})"
            aggregate_alias = _aggregate_alias(aggregate_function, metric_column)

    dimension_column = ""
    if requires_grouping:
        dimension_candidates = [
            entry for entry in (context.get("selected_dimensions") or [])
            if isinstance(entry, dict)
        ]
        if len(dimension_candidates) != 1:
            return _cannot_plan_single_table(
                capability,
                table_name=table_name,
                reason="dimension_not_found" if not dimension_candidates else "dimension_ambiguous",
                aggregation_type=aggregate_function,
                metric_columns=[metric_column] if metric_column else [],
            )
        dimension_entry = dimension_candidates[0]
        dimension_table = str(dimension_entry.get("table") or "").strip()
        dimension_column = str(
            dimension_entry.get("column") or dimension_entry.get("column_name") or ""
        ).strip()
        if (
            dimension_table != table_name
            or not _SAFE_IDENTIFIER_RE.fullmatch(dimension_column)
            or dimension_column not in schema_columns
        ):
            return _cannot_plan_single_table(
                capability,
                table_name=table_name,
                reason="dimension_not_in_schema",
                aggregation_type=aggregate_function,
                metric_columns=[metric_column] if metric_column else [],
            )

    where_clauses: list[str] = []
    where_conjunctions: list[str] = []
    filter_columns: list[str] = []
    if requires_where:
        where_clauses, where_conjunctions, filter_columns, filter_reason = _resolve_filter_clauses(
            query_context=context,
            table_name=table_name,
            table_data=table_data,
        )
        if filter_reason:
            return _cannot_plan_single_table(
                capability,
                table_name=table_name,
                reason=filter_reason,
                aggregation_type=aggregate_function,
                metric_columns=[metric_column] if metric_column else [],
                dimension_columns=[dimension_column] if dimension_column else [],
            )

    having_clauses: list[str] = []
    having_columns: list[str] = []
    if requires_having:
        having_clauses, having_columns, having_reason = _resolve_having_clauses(
            query_context=context,
            table_name=table_name,
            aggregate_function=aggregate_function or "",
            aggregate_expression=aggregate_expression,
            metric_column=metric_column,
        )
        if having_reason:
            return _cannot_plan_single_table(
                capability,
                table_name=table_name,
                reason=having_reason,
                aggregation_type=aggregate_function,
                metric_columns=[metric_column] if metric_column else [],
                dimension_columns=[dimension_column] if dimension_column else [],
                filter_columns=filter_columns,
            )

    order_by: list[str] = []
    selected_order_by = context.get("selected_order_by")
    if selected_order_by is not None:
        if not isinstance(selected_order_by, dict):
            return _cannot_plan_single_table(
                capability,
                table_name=table_name,
                reason="selected_order_by_invalid",
            )
        direction = str(selected_order_by.get("direction") or "").strip().lower()
        target_type = str(selected_order_by.get("target_type") or "").strip()
        order_table = str(selected_order_by.get("table") or "").strip()
        order_column = str(selected_order_by.get("column") or "").strip()
        if direction not in {"asc", "desc"} or order_table != table_name:
            return _cannot_plan_single_table(
                capability,
                table_name=table_name,
                reason="selected_order_by_invalid",
            )
        if target_type == "column":
            if order_column not in schema_columns:
                return _cannot_plan_single_table(
                    capability,
                    table_name=table_name,
                    reason="order_by_column_not_in_schema",
                )
            order_expression = order_column
        elif target_type == "aggregate_expression":
            order_function = str(selected_order_by.get("aggregate_function") or "").strip().lower()
            if order_function != aggregate_function or order_column != metric_column:
                return _cannot_plan_single_table(
                    capability,
                    table_name=table_name,
                    reason="order_by_aggregate_mismatch",
                )
            order_expression = aggregate_expression
        else:
            return _cannot_plan_single_table(
                capability,
                table_name=table_name,
                reason="order_by_target_type_invalid",
            )
        order_by = [f"{order_expression} {direction.upper()}"]

    limit = context.get("limit")
    if limit is not None and (
        isinstance(limit, bool) or not isinstance(limit, int) or limit < 1 or limit > 1000
    ):
        return _cannot_plan_single_table(
            capability,
            table_name=table_name,
            reason="limit_out_of_safe_range",
        )

    if requires_grouping:
        select_items = [
            {"expression": dimension_column, "source_column": dimension_column, "kind": "dimension"},
            {
                "expression": aggregate_expression,
                "alias": aggregate_alias,
                "source_column": metric_column,
                "kind": "aggregate",
            },
        ]
        sql_skeleton_type = "grouped_aggregate"
    elif requires_aggregate:
        select_items = [
            {
                "expression": aggregate_expression,
                "alias": aggregate_alias,
                "source_column": metric_column,
                "kind": "aggregate",
            }
        ]
        sql_skeleton_type = (
            "filtered_single_table_aggregate"
            if requires_where
            else "single_table_aggregate"
        )
    else:
        select_items = [
            {"expression": column_name, "source_column": column_name, "kind": "column"}
            for column_name in schema_columns
        ]
        sql_skeleton_type = "ranked_single_table_list" if order_by else "filtered_single_table_list"

    return DeterministicSqlPlan(
        query_shape=capability.query_shape,
        status="ready",
        supported_now=True,
        base_table=table_name,
        select_items=select_items,
        where_clauses=where_clauses,
        where_conjunctions=where_conjunctions,
        having_clauses=having_clauses,
        group_by=[dimension_column] if dimension_column else [],
        order_by=order_by,
        aggregation_type=aggregate_function,
        limit=limit,
        metric_columns=[metric_column] if metric_column else [],
        dimension_columns=[dimension_column] if dimension_column else [],
        filter_columns=filter_columns,
        having_columns=having_columns,
        required_evidence=list(capability.required_evidence),
        evidence_sources=[
            "query_context.selected_tables",
            "query_context.selected_metric",
            "query_context.selected_dimensions",
            "query_context.selected_filters",
            "query_context.selected_having",
            "knowledge_base.columns",
        ],
        sql_skeleton_type=sql_skeleton_type,
        can_render=True,
        route_reason=f"single-table {clause_shape} SQL generated deterministically",
        formula_evidence=list(context.get("formula_evidence") or []),
    )


def _render_single_table_aggregate(plan: DeterministicSqlPlan) -> str:
    return _render_plan_in_canonical_order(plan)


def _resolve_join_filter_clauses(
    query_context: dict[str, Any],
    knowledge_base: dict[str, Any],
    allowed_tables: set[str],
) -> tuple[list[str], list[str], list[str], str]:
    selected_filters = [
        entry for entry in (query_context.get("selected_filters") or []) if isinstance(entry, dict)
    ]
    intent = query_context.get("intent") if isinstance(query_context.get("intent"), dict) else {}
    structured_filters = [
        entry for entry in (intent.get("structured_filters") or []) if isinstance(entry, dict)
    ]
    if not structured_filters:
        return [], [], [], ""
    if len(selected_filters) != len(structured_filters):
        return [], [], [], "filter_evidence_incomplete"
    clauses: list[str] = []
    conjunctions: list[str] = []
    filter_columns: list[str] = []
    active_conjunctions: set[str] = set()
    for index, selected_filter in enumerate(selected_filters):
        table_name = str(selected_filter.get("table") or "").strip()
        column_name = str(selected_filter.get("column") or "").strip()
        operator = str(selected_filter.get("operator") or "").strip().lower()
        conjunction = str(selected_filter.get("conjunction") or "").strip().lower()
        if table_name not in allowed_tables or not _SAFE_IDENTIFIER_RE.fullmatch(column_name):
            return [], [], [], "filter_column_not_selected"
        schema_column = next(
            (
                column for column in knowledge_base.get(table_name, {}).get("columns", []) or []
                if str(column.get("name") or "") == column_name
            ),
            None,
        )
        if schema_column is None:
            return [], [], [], "filter_column_not_in_schema"
        if index == 0 and conjunction:
            return [], [], [], "filter_conjunction_invalid"
        normalized_conjunction = "" if index == 0 else (conjunction or "and")
        if normalized_conjunction and normalized_conjunction not in {"and", "or"}:
            return [], [], [], "filter_conjunction_not_supported"
        if normalized_conjunction:
            active_conjunctions.add(normalized_conjunction)
            if len(active_conjunctions) > 1:
                return [], [], [], "filter_conjunction_not_supported"
        predicate, predicate_reason = _filter_predicate(
            f"{table_name}.{column_name}",
            schema_column,
            operator,
            selected_filter,
        )
        if predicate_reason:
            return [], [], [], predicate_reason
        clauses.append(predicate)
        conjunctions.append(normalized_conjunction)
        filter_columns.append(f"{table_name}.{column_name}")
    return clauses, conjunctions, filter_columns, ""


def _build_joined_lookup_plan(
    *,
    query_context: dict[str, Any],
    knowledge_base: dict[str, Any],
    capability: DeterministicCapabilityResult,
) -> DeterministicSqlPlan:
    selected_path = query_context.get("selected_join_path")
    if not isinstance(selected_path, dict):
        return DeterministicSqlPlan(
            query_shape="joined_lookup",
            status="cannot_plan_safely",
            supported_now=True,
            missing_evidence=["selected_join_path_missing"],
            route_reason="planner-selected join path is missing",
        )
    base_table = str(selected_path.get("base_table") or "").strip()
    joined_tables = [str(value).strip() for value in (selected_path.get("joined_tables") or [])]
    edges = [dict(edge) for edge in (selected_path.get("edges") or []) if isinstance(edge, dict)]
    if len(joined_tables) != 1 or len(edges) != 1:
        return DeterministicSqlPlan(
            query_shape="joined_lookup",
            status="cannot_plan_safely",
            supported_now=True,
            missing_evidence=["selected_join_path_invalid"],
            route_reason="joined lookup requires one joined table and one direct graph edge",
        )
    joined_table = joined_tables[0]
    edge = edges[0]
    if (
        selected_path.get("path_source") != "relationship_graph"
        or selected_path.get("ambiguity_status") != "resolved"
        or edge.get("safe_for_planner") is not True
        or {str(edge.get("from_table") or ""), str(edge.get("to_table") or "")} != {base_table, joined_table}
    ):
        return DeterministicSqlPlan(
            query_shape="joined_lookup",
            status="cannot_plan_safely",
            supported_now=True,
            missing_evidence=["selected_join_path_not_authorized"],
            route_reason="selected join path is not an authorized Relationship Graph edge",
        )
    for table_name, column_name in (
        (str(edge.get("from_table") or ""), str(edge.get("from_column") or "")),
        (str(edge.get("to_table") or ""), str(edge.get("to_column") or "")),
    ):
        if table_name not in knowledge_base or column_name not in {
            str(column.get("name") or "")
            for column in knowledge_base[table_name].get("columns", []) or []
        }:
            return DeterministicSqlPlan(
                query_shape="joined_lookup",
                status="cannot_plan_safely",
                supported_now=True,
                missing_evidence=["join_edge_not_in_schema"],
                route_reason="selected join edge references an unknown schema column",
            )

    outputs = [
        dict(entry) for entry in (query_context.get("selected_output_columns") or []) if isinstance(entry, dict)
    ]
    select_items: list[dict[str, Any]] = []
    for output in outputs:
        table_name = str(output.get("table") or "")
        column_name = str(output.get("column") or "")
        expression = str(output.get("expression") or "")
        alias = str(output.get("alias") or "")
        if (
            table_name not in {base_table, joined_table}
            or expression != f"{table_name}.{column_name}"
            or alias != f"{table_name}__{column_name}"
            or column_name not in {
                str(column.get("name") or "")
                for column in knowledge_base.get(table_name, {}).get("columns", []) or []
            }
        ):
            return DeterministicSqlPlan(
                query_shape="joined_lookup",
                status="cannot_plan_safely",
                supported_now=True,
                missing_evidence=["selected_output_columns_invalid"],
                route_reason="planner-selected joined output columns do not match schema evidence",
            )
        select_items.append({"expression": expression, "alias": alias, "kind": "column"})

    where_clauses, where_conjunctions, filter_columns, filter_reason = _resolve_join_filter_clauses(
        query_context,
        knowledge_base,
        {base_table, joined_table},
    )
    if filter_reason:
        return DeterministicSqlPlan(
            query_shape="joined_lookup",
            status="cannot_plan_safely",
            supported_now=True,
            missing_evidence=[filter_reason],
            route_reason=filter_reason,
        )
    limit = query_context.get("limit")
    return DeterministicSqlPlan(
        query_shape="joined_lookup",
        status="ready",
        supported_now=True,
        base_table=base_table,
        joins=[{"table": joined_table, "edge": edge}],
        required_joins=[edge],
        selected_join_path=dict(selected_path),
        selected_output_columns=outputs,
        select_items=select_items,
        where_clauses=where_clauses,
        where_conjunctions=where_conjunctions,
        filter_columns=filter_columns,
        limit=limit,
        required_evidence=list(capability.required_evidence),
        evidence_sources=["query_context.selected_join_path", "relationship_graph", "knowledge_base.columns"],
        sql_skeleton_type="joined_lookup",
        can_render=True,
        route_reason="joined lookup SQL generated from planner-selected Relationship Graph evidence",
    )


def _build_joined_aggregate_plan(
    *,
    query_context: dict[str, Any],
    knowledge_base: dict[str, Any],
    capability: DeterministicCapabilityResult,
) -> DeterministicSqlPlan:
    selected_path = query_context.get("selected_join_path")
    if not isinstance(selected_path, dict):
        return DeterministicSqlPlan(
            query_shape="joined_aggregate",
            status="cannot_plan_safely",
            supported_now=True,
            missing_evidence=["selected_join_path_missing"],
            route_reason="planner-selected joined aggregate path is missing",
        )
    base_table = str(selected_path.get("base_table") or "").strip()
    joined_tables = [str(value).strip() for value in (selected_path.get("joined_tables") or [])]
    edges = [dict(edge) for edge in (selected_path.get("edges") or []) if isinstance(edge, dict)]
    if len(joined_tables) != 1 or len(edges) != 1:
        return DeterministicSqlPlan(
            query_shape="joined_aggregate",
            status="cannot_plan_safely",
            supported_now=True,
            missing_evidence=["selected_join_path_invalid"],
            route_reason="joined aggregate requires one joined table and one direct graph edge",
        )
    joined_table = joined_tables[0]
    edge = edges[0]
    if (
        selected_path.get("path_source") != "relationship_graph"
        or selected_path.get("ambiguity_status") != "resolved"
        or edge.get("safe_for_planner") is not True
        or {str(edge.get("from_table") or ""), str(edge.get("to_table") or "")}
        != {base_table, joined_table}
    ):
        return DeterministicSqlPlan(
            query_shape="joined_aggregate",
            status="cannot_plan_safely",
            supported_now=True,
            missing_evidence=["selected_join_path_not_authorized"],
            route_reason="selected joined aggregate path is not authorized by Relationship Graph",
        )

    schema_columns = {
        table_name: {
            str(column.get("name") or "")
            for column in knowledge_base.get(table_name, {}).get("columns", []) or []
            if str(column.get("name") or "")
        }
        for table_name in (base_table, joined_table)
    }
    for table_name, column_name in (
        (str(edge.get("from_table") or ""), str(edge.get("from_column") or "")),
        (str(edge.get("to_table") or ""), str(edge.get("to_column") or "")),
    ):
        if column_name not in schema_columns.get(table_name, set()):
            return DeterministicSqlPlan(
                query_shape="joined_aggregate",
                status="cannot_plan_safely",
                supported_now=True,
                missing_evidence=["join_edge_not_in_schema"],
                route_reason="selected joined aggregate edge references an unknown schema column",
            )

    aggregate_function = _planner_aggregate_function(
        query_context,
        query_context.get("plan") if isinstance(query_context.get("plan"), dict) else {},
    )
    if aggregate_function not in {"count", "sum", "avg", "min", "max"}:
        return DeterministicSqlPlan(
            query_shape="joined_aggregate",
            status="cannot_plan_safely",
            supported_now=True,
            missing_evidence=["aggregate_function_missing"],
            route_reason="joined aggregate function is missing or unsupported",
        )

    metric_column = ""
    if aggregate_function == "count":
        aggregate_expression = "COUNT(*)"
        aggregate_alias = f"count__{base_table}__rows"
    else:
        selected_metric = query_context.get("selected_metric")
        if not isinstance(selected_metric, dict):
            return DeterministicSqlPlan(
                query_shape="joined_aggregate",
                status="cannot_plan_safely",
                supported_now=True,
                missing_evidence=["selected_metric_missing"],
                route_reason="planner-selected joined aggregate metric is missing",
            )
        metric_table = str(selected_metric.get("table") or "").strip()
        metric_column = str(selected_metric.get("column") or "").strip()
        if metric_table != base_table or metric_column not in schema_columns.get(base_table, set()):
            return DeterministicSqlPlan(
                query_shape="joined_aggregate",
                status="cannot_plan_safely",
                supported_now=True,
                missing_evidence=["selected_metric_invalid"],
                route_reason="planner-selected joined aggregate metric does not match the base schema",
            )
        aggregate_expression = f"{aggregate_function.upper()}({base_table}.{metric_column})"
        aggregate_alias = f"{aggregate_function}__{base_table}__{metric_column}"

    selected_dimensions = [
        dict(entry) for entry in (query_context.get("selected_dimensions") or []) if isinstance(entry, dict)
    ]
    if len(selected_dimensions) != 1:
        return DeterministicSqlPlan(
            query_shape="joined_aggregate",
            status="cannot_plan_safely",
            supported_now=True,
            missing_evidence=["selected_dimension_missing"],
            route_reason="planner-selected joined aggregate dimension is missing or ambiguous",
        )
    dimension_table = str(selected_dimensions[0].get("table") or "").strip()
    dimension_column = str(selected_dimensions[0].get("column") or "").strip()
    if dimension_table != joined_table or dimension_column not in schema_columns.get(joined_table, set()):
        return DeterministicSqlPlan(
            query_shape="joined_aggregate",
            status="cannot_plan_safely",
            supported_now=True,
            missing_evidence=["selected_dimension_invalid"],
            route_reason="planner-selected joined aggregate dimension does not match the joined schema",
        )
    dimension_expression = f"{dimension_table}.{dimension_column}"
    dimension_alias = f"{dimension_table}__{dimension_column}"
    outputs = [
        dict(entry)
        for entry in (query_context.get("selected_output_columns") or [])
        if isinstance(entry, dict)
    ]
    if len(outputs) != 2:
        return DeterministicSqlPlan(
            query_shape="joined_aggregate",
            status="cannot_plan_safely",
            supported_now=True,
            missing_evidence=["selected_output_columns_missing"],
            route_reason="planner-selected joined aggregate outputs are missing",
        )
    dimension_output, aggregate_output = outputs
    if (
        dimension_output.get("kind") != "dimension"
        or str(dimension_output.get("table") or "") != dimension_table
        or str(dimension_output.get("column") or "") != dimension_column
        or str(dimension_output.get("expression") or "") != dimension_expression
        or str(dimension_output.get("alias") or "") != dimension_alias
        or aggregate_output.get("kind") != "aggregate"
        or str(aggregate_output.get("table") or "") != base_table
        or str(aggregate_output.get("column") or "") != metric_column
        or str(aggregate_output.get("aggregate_function") or "") != aggregate_function
        or str(aggregate_output.get("expression") or "") != aggregate_expression
        or str(aggregate_output.get("alias") or "") != aggregate_alias
    ):
        return DeterministicSqlPlan(
            query_shape="joined_aggregate",
            status="cannot_plan_safely",
            supported_now=True,
            missing_evidence=["selected_output_columns_invalid"],
            route_reason="planner-selected joined aggregate outputs do not match resolved evidence",
        )

    where_clauses, where_conjunctions, filter_columns, filter_reason = _resolve_join_filter_clauses(
        query_context,
        knowledge_base,
        {base_table, joined_table},
    )
    if filter_reason:
        return DeterministicSqlPlan(
            query_shape="joined_aggregate",
            status="cannot_plan_safely",
            supported_now=True,
            missing_evidence=[filter_reason],
            route_reason=filter_reason,
        )

    having_clauses: list[str] = []
    having_columns: list[str] = []
    if query_context.get("selected_having") or (
        isinstance(query_context.get("intent"), dict)
        and query_context["intent"].get("structured_having")
    ):
        having_clauses, having_columns, having_reason = _resolve_having_clauses(
            query_context=query_context,
            table_name=base_table,
            aggregate_function=aggregate_function,
            aggregate_expression=aggregate_expression,
            metric_column=metric_column,
        )
        if having_reason:
            return DeterministicSqlPlan(
                query_shape="joined_aggregate",
                status="cannot_plan_safely",
                supported_now=True,
                missing_evidence=[having_reason],
                route_reason=having_reason,
            )

    order_by: list[str] = []
    selected_order_by = query_context.get("selected_order_by")
    if selected_order_by is not None:
        if not isinstance(selected_order_by, dict):
            return DeterministicSqlPlan(
                query_shape="joined_aggregate",
                status="cannot_plan_safely",
                supported_now=True,
                missing_evidence=["selected_order_by_invalid"],
                route_reason="planner-selected joined aggregate ORDER BY is invalid",
            )
        direction = str(selected_order_by.get("direction") or "").strip().lower()
        if (
            direction not in {"asc", "desc"}
            or selected_order_by.get("target_type") != "aggregate_expression"
            or str(selected_order_by.get("table") or "") != base_table
            or str(selected_order_by.get("column") or "") != metric_column
            or str(selected_order_by.get("aggregate_function") or "") != aggregate_function
        ):
            return DeterministicSqlPlan(
                query_shape="joined_aggregate",
                status="cannot_plan_safely",
                supported_now=True,
                missing_evidence=["selected_order_by_mismatch"],
                route_reason="planner-selected ORDER BY does not match the joined aggregate output",
            )
        order_by = [f"{aggregate_alias} {direction.upper()}"]

    limit = query_context.get("limit")
    if limit is not None and (
        isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000
    ):
        return DeterministicSqlPlan(
            query_shape="joined_aggregate",
            status="cannot_plan_safely",
            supported_now=True,
            missing_evidence=["limit_out_of_safe_range"],
            route_reason="joined aggregate LIMIT must be between 1 and 1000",
        )

    return DeterministicSqlPlan(
        query_shape="joined_aggregate",
        status="ready",
        supported_now=True,
        base_table=base_table,
        joins=[{"table": joined_table, "edge": edge}],
        required_joins=[edge],
        selected_join_path=dict(selected_path),
        selected_output_columns=outputs,
        select_items=[
            {
                "expression": str(dimension_output["expression"]),
                "alias": str(dimension_output["alias"]),
                "kind": "dimension",
            },
            {
                "expression": str(aggregate_output["expression"]),
                "alias": str(aggregate_output["alias"]),
                "kind": "aggregate",
            },
        ],
        where_clauses=where_clauses,
        where_conjunctions=where_conjunctions,
        having_clauses=having_clauses,
        group_by=[dimension_expression],
        order_by=order_by,
        limit=limit,
        aggregation_type=aggregate_function,
        metric_columns=[f"{base_table}.{metric_column}"] if metric_column else [],
        dimension_columns=[dimension_expression],
        filter_columns=filter_columns,
        having_columns=having_columns,
        required_evidence=list(capability.required_evidence),
        evidence_sources=[
            "query_context.selected_join_path",
            "query_context.selected_metric",
            "query_context.selected_dimensions",
            "relationship_graph",
            "knowledge_base.columns",
        ],
        sql_skeleton_type="joined_aggregate",
        can_render=True,
        route_reason="joined aggregate SQL generated from planner-selected Relationship Graph evidence",
    )


def _resolve_having_clauses(
    *,
    query_context: dict[str, Any],
    table_name: str,
    aggregate_function: str,
    aggregate_expression: str,
    metric_column: str,
) -> tuple[list[str], list[str], str]:
    intent = (
        query_context.get("intent")
        if isinstance(query_context.get("intent"), dict)
        else {}
    )
    structured_having = [
        entry for entry in (intent.get("structured_having") or [])
        if isinstance(entry, dict)
    ]
    selected_having = [
        entry for entry in (query_context.get("selected_having") or [])
        if isinstance(entry, dict)
    ]
    if len(selected_having) != 1:
        return [], [], "having_evidence_incomplete"
    if structured_having and len(selected_having) != len(structured_having):
        return [], [], "having_evidence_incomplete"

    condition = selected_having[0]
    having_function = str(condition.get("aggregate_function") or "").strip().lower()
    operator = str(condition.get("operator") or "").strip().lower()
    if having_function != aggregate_function:
        return [], [], "having_aggregate_mismatch"
    sql_operator = _FILTER_OPERATORS.get(operator)
    if operator not in {"eq", "neq", "gt", "lt", "gte", "lte"} or not sql_operator:
        return [], [], "having_operator_not_supported"

    having_table = str(condition.get("table") or "").strip()
    having_column = str(condition.get("column") or "").strip()
    if having_table and having_table != table_name:
        return [], [], "having_metric_not_selected"
    having_columns: list[str] = []
    if aggregate_function == "count":
        if having_column:
            return [], [], "having_metric_not_selected"
    elif having_column != metric_column:
        return [], [], "having_metric_not_selected"
    else:
        having_columns.append(metric_column)

    literal, literal_reason = _filter_literal(
        condition.get("value", condition.get("value_phrase")),
        {"type": "DECIMAL(38,10)", "semantic_type": "numeric_candidate"},
        operator,
    )
    if literal_reason:
        return [], [], "having_value_type_mismatch"
    return [f"{aggregate_expression} {sql_operator} {literal}"], having_columns, ""


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
    return _render_plan_in_canonical_order(plan)


def _render_grouped_aggregate(plan: DeterministicSqlPlan) -> str:
    return _render_plan_in_canonical_order(plan)


def _render_plan_in_canonical_order(plan: DeterministicSqlPlan) -> str:
    select_parts = []
    for item in plan.select_items:
        expression = str(item.get("expression") or "").strip()
        alias = str(item.get("alias") or "").strip()
        select_parts.append(f"{expression} AS {alias}" if alias else expression)
    sql = f"SELECT {', '.join(select_parts)} FROM {plan.base_table}"
    for join in plan.joins:
        joined_table = str(join.get("table") or "")
        edge = join.get("edge") if isinstance(join.get("edge"), dict) else {}
        sql += (
            f" INNER JOIN {joined_table} ON "
            f"{edge.get('from_table')}.{edge.get('from_column')} = "
            f"{edge.get('to_table')}.{edge.get('to_column')}"
        )
    if plan.where_clauses:
        sql += f" WHERE {_render_predicates(plan.where_clauses, plan.where_conjunctions)}"
    if plan.group_by:
        sql += f" GROUP BY {', '.join(plan.group_by)}"
    if plan.having_clauses:
        sql += f" HAVING {_render_predicates(plan.having_clauses, plan.having_conjunctions)}"
    if plan.order_by:
        sql += f" ORDER BY {', '.join(plan.order_by)}"
    if plan.limit:
        sql += f" LIMIT {plan.limit}"
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
    "ranking_query": _render_plan_in_canonical_order,
    "joined_lookup": _render_plan_in_canonical_order,
    "joined_aggregate": _render_plan_in_canonical_order,
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
    intent = context.get("intent") if isinstance(context.get("intent"), dict) else {}
    if (
        str(context.get("query_shape") or "") != "joined_aggregate"
        and str((intent.get("ranking_diagnostics") or {}).get("mode_hint") or "") == "row"
    ):
        return None
    selected_function = str(context.get("aggregate_function") or "").strip().lower()
    if selected_function:
        normalized = {"average": "avg", "mean": "avg", "total": "sum"}.get(
            selected_function,
            selected_function,
        )
        return normalized if normalized in {"sum", "avg", "min", "max", "count"} else None
    if intent and (
        "aggregate_function" in intent
        or "needs_aggregation" in intent
        or "intent_type" in intent
    ):
        return None
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
