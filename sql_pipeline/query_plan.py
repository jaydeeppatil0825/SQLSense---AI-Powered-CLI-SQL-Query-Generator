"""Canonical deterministic query-plan boundary for SQL rendering."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

SQL_PLAN_CONTRACT_VERSION = "sql-plan-v1"

_BLOCKING_ROUTES = {"cannot_plan_safely", "blocked_unsafe"}
_SECRET_KEYS = {
    "password",
    "token",
    "authorization",
    "cookie",
    "credentials",
    "result_rows",
    "rows",
    "embeddings",
}


def _clean_copy(value: Any) -> Any:
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key).lower()
            if any(secret in key_text for secret in _SECRET_KEYS):
                continue
            cleaned[key] = _clean_copy(item)
        return cleaned
    if isinstance(value, list):
        return [_clean_copy(item) for item in value]
    return deepcopy(value)


def _first_table(entries: list[dict[str, Any]]) -> str:
    for entry in entries:
        table_name = str(entry.get("table") or "").strip()
        if table_name:
            return table_name
    return ""


def _selected_tables(context: dict[str, Any]) -> list[str]:
    names = [
        str(value).strip()
        for value in (context.get("selected_table_names") or [])
        if str(value).strip()
    ]
    if names:
        return names
    return [
        str(entry.get("table") or "").strip()
        for entry in (context.get("selected_tables") or [])
        if isinstance(entry, dict) and str(entry.get("table") or "").strip()
    ]


def _selected_metric(context: dict[str, Any]) -> dict[str, Any]:
    metric = context.get("selected_metric")
    if isinstance(metric, dict):
        return dict(metric)
    for entry in context.get("measure_candidates") or []:
        if isinstance(entry, dict):
            return dict(entry)
    return {}


def _infer_legacy_query_shape(context: dict[str, Any], plan: dict[str, Any]) -> str:
    if not plan:
        return ""
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


def _shape_required_fields_missing(query_shape: str, plan: "DeterministicQueryPlan") -> list[str]:
    missing: list[str] = []
    if not query_shape:
        return ["query_shape"]
    if query_shape in {
        "single_table_list",
        "single_table_count",
        "single_table_aggregate",
        "filtered_query",
        "grouped_aggregate",
        "ranking_query",
    } and not plan.base_table:
        missing.append("base_table")
    if query_shape in {"joined_lookup", "joined_aggregate"} and not plan.selected_join_path:
        missing.append("selected_join_path")
    if query_shape in {"grouped_aggregate", "joined_aggregate"} and not plan.group_by_columns:
        missing.append("group_by_columns")
    return missing


@dataclass(frozen=True)
class DeterministicQueryPlan:
    contract_version: str = SQL_PLAN_CONTRACT_VERSION
    query_shape: str = ""
    route: str = ""
    executable: bool = False
    reason_code: str = ""
    base_table: str = ""
    source_tables: list[str] = field(default_factory=list)
    selected_projection_mode: str = ""
    selected_columns: list[dict[str, Any]] = field(default_factory=list)
    projection_aliases: list[str] = field(default_factory=list)
    aggregate_function: str = ""
    aggregate_table: str = ""
    aggregate_column: str = ""
    aggregate_alias: str = ""
    aggregate_expressions: list[dict[str, Any]] = field(default_factory=list)
    where_filters: list[dict[str, Any]] = field(default_factory=list)
    having_filters: list[dict[str, Any]] = field(default_factory=list)
    conjunction_structure: list[str] = field(default_factory=list)
    group_by_columns: list[dict[str, Any]] = field(default_factory=list)
    order_by: dict[str, Any] = field(default_factory=dict)
    ranking_decision: dict[str, Any] = field(default_factory=dict)
    limit: int | None = None
    join_type: str = ""
    selected_join_path: dict[str, Any] = field(default_factory=dict)
    join_edges: list[dict[str, Any]] = field(default_factory=list)
    joined_tables: list[str] = field(default_factory=list)
    grain_decision: dict[str, Any] = field(default_factory=dict)
    grain_preserved: bool | None = None
    schema_fingerprint: str = ""
    kb_fingerprint: str = ""
    graph_fingerprint: str = ""
    planner_contract_version: str = ""
    sql_plan_contract_version: str = SQL_PLAN_CONTRACT_VERSION
    filter_decision: dict[str, Any] = field(default_factory=dict)
    metric_decision: dict[str, Any] = field(default_factory=dict)
    dimension_decision: dict[str, Any] = field(default_factory=dict)
    join_decision: dict[str, Any] = field(default_factory=dict)
    ambiguity: dict[str, Any] = field(default_factory=dict)
    missing_required_fields: list[str] = field(default_factory=list)
    _legacy_context: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            key: _clean_copy(value)
            for key, value in self.__dict__.items()
            if not key.startswith("_")
        }

    def to_legacy_context(self) -> dict[str, Any]:
        context = _clean_copy(self._legacy_context)
        context["deterministic_query_plan"] = self.to_dict()
        context["query_shape"] = self.query_shape
        context["route"] = self.route
        context["route_recommendation"] = self.route
        return context


def build_deterministic_query_plan(planner_context: dict[str, Any]) -> DeterministicQueryPlan:
    context = planner_context if isinstance(planner_context, dict) else {}
    intent = context.get("intent") if isinstance(context.get("intent"), dict) else {}
    plan = context.get("plan") if isinstance(context.get("plan"), dict) else {}
    clause_plan = context.get("clause_plan") if isinstance(context.get("clause_plan"), dict) else {}
    selected_path = context.get("selected_join_path") if isinstance(context.get("selected_join_path"), dict) else {}
    metric = _selected_metric(context)
    selected_tables = _selected_tables(context)
    selected_dimensions = [
        dict(entry)
        for entry in (context.get("selected_dimensions") or [])
        if isinstance(entry, dict)
    ]
    grain_decision = (
        context.get("phase8a_grain_analysis")
        if isinstance(context.get("phase8a_grain_analysis"), dict)
        else context.get("grain_decision")
        if isinstance(context.get("grain_decision"), dict)
        else {}
    )
    route = str(context.get("route") or context.get("route_recommendation") or "").strip()
    aggregate_function = str(
        context.get("aggregate_function")
        or intent.get("aggregate_function")
        or plan.get("aggregation_type")
        or plan.get("aggregate_function")
        or ""
    ).strip().lower()
    query_shape = str(
        context.get("query_shape")
        or clause_plan.get("query_shape")
        or _infer_legacy_query_shape(context, plan)
    ).strip()
    base_table = str(
        selected_path.get("base_table")
        or metric.get("table")
        or _first_table(context.get("selected_tables") or [])
        or (selected_tables[0] if selected_tables else "")
    ).strip()
    selected_columns = [
        dict(entry)
        for entry in (
            context.get("selected_output_columns")
            or context.get("selected_columns")
            or []
        )
        if isinstance(entry, dict)
    ]
    selected_aggregate_items = [
        dict(entry)
        for entry in selected_columns
        if str(entry.get("kind") or "").strip() == "aggregate"
    ]
    aggregate_expressions = [
        dict(entry)
        for entry in (context.get("aggregate_expressions") or selected_aggregate_items)
        if isinstance(entry, dict)
    ]
    aggregate_alias = str((context.get("selected_aggregate") or {}).get("alias") or "").strip()
    if not aggregate_alias and selected_aggregate_items:
        aggregate_alias = str(selected_aggregate_items[0].get("alias") or "").strip()
    where_filters = [
        dict(entry)
        for entry in (context.get("selected_filters") or plan.get("filters") or [])
        if isinstance(entry, dict)
    ]
    having_filters = [
        dict(entry)
        for entry in (context.get("selected_having") or [])
        if isinstance(entry, dict)
    ]
    ranking_decision = (
        dict(context.get("ranking_decision"))
        if isinstance(context.get("ranking_decision"), dict)
        else {}
    )
    canonical = DeterministicQueryPlan(
        query_shape=query_shape,
        route=route,
        reason_code=str(context.get("reason_code") or context.get("planner_reason") or context.get("route_reason") or ""),
        base_table=base_table,
        source_tables=selected_tables,
        selected_projection_mode=str(ranking_decision.get("selected_projection_mode") or clause_plan.get("projection_mode") or ""),
        selected_columns=selected_columns,
        projection_aliases=[str(entry.get("alias") or "") for entry in selected_columns if str(entry.get("alias") or "")],
        aggregate_function=aggregate_function,
        aggregate_table=str(metric.get("table") or "").strip(),
        aggregate_column=str(metric.get("column") or "").strip(),
        aggregate_alias=aggregate_alias,
        aggregate_expressions=aggregate_expressions,
        where_filters=where_filters,
        having_filters=having_filters,
        conjunction_structure=[str(entry.get("conjunction") or "") for entry in where_filters],
        group_by_columns=selected_dimensions,
        order_by=dict(context.get("selected_order_by") or {}),
        ranking_decision=ranking_decision,
        limit=context.get("limit") if isinstance(context.get("limit"), int) else plan.get("limit") if isinstance(plan.get("limit"), int) else None,
        join_type="inner" if selected_path else "",
        selected_join_path=dict(selected_path),
        join_edges=[dict(edge) for edge in (selected_path.get("edges") or []) if isinstance(edge, dict)],
        joined_tables=[str(value) for value in (selected_path.get("joined_tables") or [])],
        grain_decision=dict(grain_decision),
        grain_preserved=grain_decision.get("grain_preserved") if grain_decision else None,
        schema_fingerprint=str(context.get("schema_fingerprint") or context.get("schema_hash") or ""),
        kb_fingerprint=str(context.get("kb_fingerprint") or ""),
        graph_fingerprint=str(context.get("graph_fingerprint") or ""),
        planner_contract_version=str(context.get("planner_contract_version") or ""),
        filter_decision=dict(context.get("filter_decision") or {}),
        metric_decision=dict(context.get("metric_decision") or {}),
        dimension_decision=dict(context.get("dimension_decision") or {}),
        join_decision=dict(context.get("join_decision") or {}),
        ambiguity=dict(context.get("ambiguity") or {}),
        _legacy_context=_clean_copy(context),
    )
    missing_required = _shape_required_fields_missing(query_shape, canonical)
    blocked = route in _BLOCKING_ROUTES or context.get("can_plan") is False or bool(context.get("missing_evidence")) or bool(missing_required)
    return DeterministicQueryPlan(
        **{
            **canonical.__dict__,
            "executable": bool(query_shape and not blocked),
            "reason_code": canonical.reason_code or ("missing_required_render_fields" if missing_required else ""),
            "missing_required_fields": missing_required,
        }
    )
