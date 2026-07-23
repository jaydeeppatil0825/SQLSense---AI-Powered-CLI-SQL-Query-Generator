"""Final planner contract-building helpers."""

from __future__ import annotations

from typing import Any

from query_pipeline.planner.confidence import _has_close_role_ambiguity
from query_pipeline.planner.query_predicates import (
    _required_join_predicates,
    classify_query_shape,
)
from query_pipeline.planner.text_utils import _normalize


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


