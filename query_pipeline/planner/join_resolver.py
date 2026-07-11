"""Direct join contract helpers for the deterministic planner."""

from __future__ import annotations

from copy import deepcopy
import re
from typing import Any

from kb_pipeline.relationship_graph import (
    build_relationship_graph,
    find_safe_direct_join_relationships,
)
from query_pipeline.planner.filter_resolver import (
    _STATUS_VALUE_TOKENS,
    _build_sample_value_filter,
    _joined_aggregate_filter_contract,
    _resolve_interval_filters_for_scope,
    _source_scope_as_filter,
)
from query_pipeline.planner.role_resolver import (
    _candidate_is_numeric_metric,
    _graph_selected_evidence_entry,
    _rank_role_candidates,
    _resolve_count_base_table,
    _resolve_entity_display_dimension,
    _resolve_owned_monetary_metric_from_schema,
    _resolve_related_sales_amount_metric,
    _resolve_role_candidate,
    _selected_evidence_entry,
    _source_selected_evidence_entry,
)


def _planner():
    from query_pipeline import query_planner as _qp

    return _qp


def _normalize(text: str) -> str:
    return _planner()._normalize(text)


def _humanize(text: str) -> str:
    return _planner()._humanize(text)


def _tokenize(text: str) -> list[str]:
    return _planner()._tokenize(text)


def _singularize_token(token: str) -> str:
    return _planner()._singularize_token(token)


def _safe_float(value: Any, default: float = 0.0) -> float:
    return _planner()._safe_float(value, default)


def _remove_weak_context_warning(warnings: list[Any]) -> list[Any]:
    return _planner()._remove_weak_context_warning(warnings)


def _resolve_metric_with_modifier(metric_phrase: str, metric_candidates: list[dict[str, Any]]):
    return _planner()._resolve_metric_with_modifier(metric_phrase, metric_candidates)


def _build_clause_plan_for_contract(*args, **kwargs):
    return _planner()._build_clause_plan_for_contract(*args, **kwargs)
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

_WEAK_CONTEXT_WARNING = "Retrieved context is weak; planner confidence is low."

_SCORING_TIERS = {
    "exact_normalized_column": 1.0,
    "owner_qualified_exact": 0.99,
    "kb_glossary_semantic": 0.92,
    "numeric_metric_eligible": 0.74,
    "dimension_type_eligible": 0.7,
    "sample_value_filter_match": 0.88,
    "direct_graph_compatible": 0.96,
    "selected_join_path_agreement": 1.0,
    "aggregate_ranking_keyword_agreement": 0.93,
    "source_phrase_agreement": 0.9,
}

_NUMERIC_METRIC_SEMANTIC_TYPES = {
    "money",
    "quantity",
    "percentage",
    "numeric_candidate",
    "number",
    "decimal",
    "integer",
    "float",
}

_NON_METRIC_SEMANTIC_TYPES = {
    "status",
    "text",
    "text_candidate",
    "category",
    "category_candidate",
    "date",
    "name",
    "code",
    "id",
    "reference",
}

_DIMENSION_SEMANTIC_TYPES = {
    "status",
    "text",
    "text_candidate",
    "category",
    "category_candidate",
    "date",
    "name",
    "code",
    "reference",
}


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _has_strong_joined_evidence(selected_evidence: dict[str, Any], *, aggregate_function: str | None = None) -> bool:
    graph = selected_evidence.get("relationship_graph") or {}
    if graph.get("tier") != "selected_join_path_agreement":
        return False
    dimension_tier = (selected_evidence.get("dimension") or {}).get("tier")
    if dimension_tier not in {"exact_normalized_column", "owner_qualified_exact", "kb_glossary_semantic"}:
        return False
    if aggregate_function == "count":
        return True
    metric_tier = (selected_evidence.get("metric") or {}).get("tier")
    return metric_tier in {"exact_normalized_column", "owner_qualified_exact", "kb_glossary_semantic"}


def _remove_weak_context_warning(warnings: list[Any]) -> list[Any]:
    return [warning for warning in warnings if warning != _WEAK_CONTEXT_WARNING]


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
            "selected_evidence": {
                blocked_node: {
                    "status": "blocked",
                    "selected": None,
                    "tier": None,
                    "score": None,
                    "reasons": [reason],
                    "losing_candidates": [],
                    "tie_reason": reason if "ambiguous" in reason or "multiple" in reason else "",
                }
            },
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
    metric_tokens = [_singularize_token(token) for token in _tokenize(metric_phrase)]
    if not aggregate_function and grouped_candidate and metric_tokens and metric_tokens[0] in _STATUS_VALUE_TOKENS:
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
    dimension_hint_result = _rank_role_candidates(
        dimension_phrase,
        dimension_candidates,
        role="dimension",
    )
    dimension_table_hint = ""
    if dimension_hint_result.get("status") == "resolved":
        dimension_table_hint = str(
            (dimension_hint_result.get("selected") or {}).get("candidate", {}).get("table") or ""
        ).strip()
    metric: dict[str, Any] | None = None
    metric_evidence_result: dict[str, Any] = {"status": "not_required", "selected": None, "ranked": []}
    modifier_filter_phrase: str | None = None
    if aggregate_function != "count":
        metric_evidence_result = _rank_role_candidates(metric_phrase, metric_candidates, role="metric")
        if metric_evidence_result.get("status") == "ambiguous" and dimension_table_hint:
            narrowed_metric_result = _rank_role_candidates(
                metric_phrase,
                metric_candidates,
                role="metric",
                allowed_tables={dimension_table_hint},
            )
            if narrowed_metric_result.get("status") == "resolved":
                metric_evidence_result = narrowed_metric_result
        modifier_metric = None
        modifier_phrase = None
        modifier_status = "missing"
        modifier_evidence_result: dict[str, Any] = {"status": "missing", "selected": None, "ranked": []}
        original_tier = str((metric_evidence_result.get("selected") or {}).get("tier") or "")
        should_try_modifier = (
            metric_evidence_result.get("status") != "resolved"
            or original_tier not in {"exact_normalized_column", "owner_qualified_exact", "kb_glossary_semantic"}
        )
        if should_try_modifier:
            tokens = _tokenize(metric_phrase)
            for split_at in range(1, len(tokens)):
                candidate_modifier_phrase = " ".join(tokens[:split_at]).strip()
                residual_phrase = " ".join(tokens[split_at:]).strip()
                candidate_result = _rank_role_candidates(residual_phrase, metric_candidates, role="metric")
                candidate_status = str(candidate_result.get("status") or "missing")
                if candidate_status == "resolved":
                    modifier_metric = dict(candidate_result.get("selected", {}).get("candidate") or {})
                    modifier_phrase = candidate_modifier_phrase
                    modifier_status = "resolved"
                    modifier_evidence_result = candidate_result
                    break
                if candidate_status == "ambiguous":
                    modifier_status = "ambiguous"
                    modifier_evidence_result = candidate_result
                    break
        if modifier_status == "resolved" and modifier_metric is not None:
            metric = modifier_metric
            metric_evidence_result = modifier_evidence_result
            aggregate_words = {"total", "sum", "average", "avg", "mean", "maximum", "max", "minimum", "min"}
            if _normalize(modifier_phrase or "") not in aggregate_words:
                modifier_filter_phrase = modifier_phrase
        elif metric_evidence_result.get("status") == "resolved":
            metric = dict(metric_evidence_result.get("selected", {}).get("candidate") or {})
        else:
            if modifier_status == "ambiguous":
                metric_evidence_result = modifier_evidence_result
            else:
                metric_evidence_result = metric_evidence_result
            if modifier_status == "ambiguous":
                return _joined_aggregate_failure_context(
                    context,
                    blocked_node="metric",
                    reason="joined aggregate metric evidence is ambiguous",
                    resolved_nodes={"unsafe_check", "table_scope", "query_shape", "aggregate"},
                )
            sales_metric = None
            sales_metric_status = "missing"
            metric_tokens_for_sales = _tokenize(metric_phrase)
            if (
                metric_tokens_for_sales
                and _singularize_token(metric_tokens_for_sales[0]) in _STATUS_VALUE_TOKENS
                and dimension_table_hint
            ):
                sales_phrase = " ".join(metric_tokens_for_sales[1:]).strip()
                sales_metric, sales_metric_status = _resolve_related_sales_amount_metric(
                    sales_phrase,
                    dimension_table=dimension_table_hint,
                    knowledge_base=knowledge_base,
                )
            if sales_metric_status == "resolved" and sales_metric is not None:
                metric = sales_metric
                metric_evidence_result = {
                    "status": "resolved",
                    "selected": {
                        "candidate": dict(sales_metric),
                        "tier": "direct_graph_compatible",
                        "score": _SCORING_TIERS["direct_graph_compatible"],
                        "candidate_score": _safe_float(sales_metric.get("score")),
                        "reasons": ["generic sales wording resolved to one graph-related amount-like measure"],
                    },
                    "ranked": [],
                    "tie_reason": "",
                }
                modifier_filter_phrase = metric_tokens_for_sales[0]
            elif sales_metric_status == "ambiguous":
                return _joined_aggregate_failure_context(
                    context,
                    blocked_node="metric",
                    reason="joined aggregate sales metric evidence is ambiguous",
                    resolved_nodes={"unsafe_check", "table_scope", "query_shape", "aggregate"},
                )
            schema_metric, schema_metric_status = _resolve_owned_monetary_metric_from_schema(
                metric_phrase,
                knowledge_base,
            )
            if metric is not None:
                pass
            elif schema_metric_status == "resolved" and schema_metric is not None:
                metric = schema_metric
                metric_evidence_result = {
                    "status": "resolved",
                    "selected": {
                        "candidate": dict(schema_metric),
                        "tier": "numeric_metric_eligible",
                        "score": _SCORING_TIERS["numeric_metric_eligible"],
                        "candidate_score": _safe_float(schema_metric.get("score")),
                        "reasons": ["fallback-only schema/profile metric evidence"],
                    },
                    "ranked": [],
                    "tie_reason": "",
                }
            else:
                return _joined_aggregate_failure_context(
                    context,
                    blocked_node="metric",
                    reason=f"joined aggregate metric evidence is {schema_metric_status if metric_evidence_result.get('status') == 'missing' else metric_evidence_result.get('status')}",
                    resolved_nodes={"unsafe_check", "table_scope", "query_shape", "aggregate"},
                )

    dimension_evidence_result = _rank_role_candidates(
        dimension_phrase,
        dimension_candidates,
        role="dimension",
    )
    if dimension_evidence_result.get("status") == "resolved":
        resolved_dimensions = [dict(dimension_evidence_result.get("selected", {}).get("candidate") or {})]
        dimension_status = "resolved"
    else:
        resolved_dimensions = []
        dimension_status = str(dimension_evidence_result.get("status") or "missing")
    if dimension_status != "resolved" or len(resolved_dimensions) != 1:
        display_dimensions, display_status = _resolve_entity_display_dimension(
            dimension_phrase,
            dimension_candidates,
            knowledge_base,
        )
        if display_status == "resolved" and len(display_dimensions) == 1:
            resolved_dimensions = display_dimensions
            display_tier = "owner_qualified_exact"
            display_reason = "unique owner entity display column resolved from deterministic evidence"
            dimension_evidence_result = {
                "status": "resolved",
                "selected": {
                    "candidate": dict(display_dimensions[0]),
                    "tier": display_tier,
                    "score": _SCORING_TIERS[display_tier],
                    "candidate_score": _safe_float(display_dimensions[0].get("score")),
                    "reasons": [display_reason],
                },
                "ranked": [],
                "tie_reason": "",
            }
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
    source_evidence_status = "not_required"
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
        source_evidence_status = base_status
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
        knowledge_base,
        base_table=base_table,
        preferred_date_phrases=[
            metric_phrase,
            source_phrase,
            str(intent.get("target_entity_phrase") or ""),
            base_table,
        ],
        allow_preferred_owner_date=bool(ranking_candidate),
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
    selected_evidence = {
        "metric": (
            {
                "status": "not_required",
                "selected": None,
                "tier": None,
                "score": None,
                "reasons": ["COUNT(*) does not require a metric column"],
                "losing_candidates": [],
                "tie_reason": "",
            }
            if aggregate_function == "count"
            else _selected_evidence_entry(metric_evidence_result)
        ),
        "dimension": _selected_evidence_entry(dimension_evidence_result),
        "filters": [
            {
                "status": "resolved",
                "selected": dict(entry),
                "tier": "sample_value_filter_match" if entry.get("source") in {"metric_modifier_value_filter", "source_scope_value_filter"} else "kb_glossary_semantic",
                "score": _SCORING_TIERS["sample_value_filter_match"] if entry.get("source") in {"metric_modifier_value_filter", "source_scope_value_filter"} else _safe_float(entry.get("score") or entry.get("evidence_score"), 0.0),
                "reasons": ["row-level filter resolved from deterministic candidate evidence"],
                "losing_candidates": [],
                "tie_reason": "",
            }
            for entry in selected_filters
        ],
        "source_table": _source_selected_evidence_entry(base_table, source_phrase, source_evidence_status),
        "order_by": (
            {
                "status": "resolved",
                "selected": dict(selected_order_by),
                "tier": "aggregate_ranking_keyword_agreement",
                "score": _SCORING_TIERS["aggregate_ranking_keyword_agreement"],
                "reasons": ["ranking ORDER BY uses selected aggregate alias"],
                "losing_candidates": [],
                "tie_reason": "",
            }
            if selected_order_by
            else {
                "status": "not_required",
                "selected": None,
                "tier": None,
                "score": None,
                "reasons": ["no aggregate ordering was requested"],
                "losing_candidates": [],
                "tie_reason": "",
            }
        ),
        "relationship_graph": _graph_selected_evidence_entry(edge),
    }
    planned_confidence = _safe_float(context.get("confidence"), 0.0)
    planned_warnings = list(context.get("warnings") or [])
    if _has_strong_joined_evidence(selected_evidence, aggregate_function=aggregate_function):
        planned_confidence = max(planned_confidence, 0.86)
        planned_warnings = _remove_weak_context_warning(planned_warnings)
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
    planned_intent["aggregate_function"] = aggregate_function
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
            "selected_evidence": selected_evidence,
            "join_paths": [],
            "required_joins": [required_join],
            "limit": limit,
            "confidence": round(planned_confidence, 2),
            "warnings": planned_warnings,
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
        "selected_evidence": selected_evidence,
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

    selected_filters, interval_reason = _resolve_interval_filters_for_scope(
        selected_filters,
        [dict(entry) for entry in structured_filters if isinstance(entry, dict)],
        knowledge_base=knowledge_base,
        allowed_tables=candidate_tables,
    )
    filter_tables = {
        str(entry.get("table") or "") for entry in selected_filters if str(entry.get("table") or "")
    }
    if interval_reason:
        return _join_failure_context(
            context,
            blocked_node="where",
            reason=interval_reason,
            resolved_nodes=resolved_nodes,
        )

    source_filter_phrase = str(
        next(iter(intent.get("source_scope") or []), "")
        or intent.get("source_scope_phrase")
        or ""
    ).strip()
    if source_filter_phrase:
        source_table, source_table_status = _resolve_join_table(
            source_filter_phrase,
            knowledge_base,
            retrieved_tables,
        )
        if source_table_status == "resolved" and source_table in candidate_tables:
            source_filter_phrase = ""
    if source_filter_phrase and not selected_filters:
        implicit_filter, implicit_status = _build_sample_value_filter(
            value_phrase=source_filter_phrase,
            knowledge_base=knowledge_base,
            allowed_tables=candidate_tables,
            source="source_scope_value_filter",
        )
        if implicit_status != "resolved":
            implicit_filter, implicit_status = _source_scope_as_filter(
                source_filter_phrase,
                knowledge_base,
                candidate_tables,
            )
        if implicit_status != "resolved" or implicit_filter is None:
            return _join_failure_context(
                context,
                blocked_node="where",
                reason=f"joined WHERE source-scope evidence is {implicit_status}",
                resolved_nodes=resolved_nodes,
            )
        selected_filters.append(implicit_filter)
        filter_tables = {
            str(entry.get("table") or "") for entry in selected_filters if str(entry.get("table") or "")
        }

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
    selected_evidence = {
        "metric": {"status": "not_required", "selected": None, "tier": None, "score": None, "reasons": [], "losing_candidates": [], "tie_reason": ""},
        "dimension": {"status": "not_required", "selected": None, "tier": None, "score": None, "reasons": [], "losing_candidates": [], "tie_reason": ""},
        "filters": [
            {
                "status": "resolved",
                "selected": dict(entry),
                "tier": "sample_value_filter_match",
                "score": _safe_float(entry.get("score") or entry.get("evidence_score"), 0.0),
                "reasons": ["row-level filter resolved from deterministic candidate evidence"],
                "losing_candidates": [],
                "tie_reason": "",
            }
            for entry in selected_filters
        ],
        "source_table": _source_selected_evidence_entry(base_table, base_phrase, "resolved" if base_table else "missing"),
        "order_by": {"status": "not_required", "selected": None, "tier": None, "score": None, "reasons": [], "losing_candidates": [], "tie_reason": ""},
        "relationship_graph": _graph_selected_evidence_entry(edge),
    }
    planned_confidence = max(_safe_float(context.get("confidence"), 0.0), 0.86)
    planned_warnings = _remove_weak_context_warning(list(context.get("warnings") or []))
    decision_path = [
        {
            "node": node_name,
            "status": "not_required" if node_name == "where" and not selected_filters else "resolved",
            "reason": (
                "no row-level filter was requested"
                if node_name == "where" and not selected_filters
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
            "selected_filters": list(selected_filters),
            "selected_join_path": selected_join_path,
            "selected_relationship_path": selected_join_path,
            "selected_evidence": selected_evidence,
            "join_paths": [selected_join_path],
            "required_joins": [required_join],
            "limit": resolved_limit,
            "confidence": round(planned_confidence, 2),
            "warnings": planned_warnings,
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
                    "where": bool(selected_filters),
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
        "selected_filters": list(selected_filters),
        "selected_join_path": selected_join_path,
        "selected_evidence": selected_evidence,
        "required_joins": [required_join],
        "limit": resolved_limit,
        "route_recommendation": "deterministic_sql_required",
    }
    return planned


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


