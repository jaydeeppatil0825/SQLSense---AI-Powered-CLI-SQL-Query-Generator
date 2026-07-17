"""Ranking, ORDER BY, and LIMIT helpers for the deterministic planner."""

from __future__ import annotations

import re
from typing import Any


def _planner():
    from query_pipeline import query_planner as _qp

    return _qp


def _normalize(text: str) -> str:
    return _planner()._normalize(text)


def _humanize(text: str) -> str:
    return _planner()._humanize(text)


def _tokenize(text: str) -> list[str]:
    return _planner()._tokenize(text)


def _merge_candidate_columns(*groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return _planner()._merge_candidate_columns(*groups)


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


def _ranking_mode_for_contract(intent: dict[str, Any], query_shape: str) -> str:
    mode_hint = str((intent.get("ranking_diagnostics") or {}).get("mode_hint") or "").strip()
    if mode_hint == "grouped_aggregate":
        return "grouped_aggregate_ranking"
    if mode_hint == "ordered_list":
        return "ordered_list"
    if intent.get("requested_sort"):
        return "row_ranking"
    if query_shape == "ranking_query":
        return "row_ranking"
    return ""


def _ranking_projection_mode(ranking_mode: str) -> str:
    if ranking_mode == "grouped_aggregate_ranking":
        return "grouped_aggregate_projection"
    if ranking_mode == "ordered_list":
        return "ordered_list_projection"
    if ranking_mode == "row_ranking":
        return "row_projection"
    return ""


def _ranking_direction_conflict(intent: dict[str, Any]) -> bool:
    markers = (intent.get("keyword_markers") or {}).get("ranking") or []
    directions = {
        str(entry.get("normalized") or "").strip().lower()
        for entry in markers
        if isinstance(entry, dict)
    }
    return "asc" in directions and "desc" in directions


def _ranking_rejected_alternatives(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rejected: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        table_name, column_name = _order_candidate_identity(entry)
        identity = (table_name, column_name)
        if not table_name or not column_name or identity in seen:
            continue
        seen.add(identity)
        rejected.append(
            {
                "table": table_name,
                "column": column_name,
                "source": str(entry.get("source") or ""),
                "score": entry.get("score"),
            }
        )
    return rejected


def _build_ranking_decision_for_contract(
    *,
    query_shape: str,
    intent: dict[str, Any],
    selected_tables: list[dict[str, Any]],
    selected_metric: dict[str, Any] | None,
    selected_dimensions: list[dict[str, Any]],
    selected_order_by: dict[str, Any] | None,
    aggregate_function: str,
    limit: int | None,
    limit_reason: str,
    order_by_reason: str,
    order_by_ambiguity_choices: list[dict[str, Any]],
) -> dict[str, Any]:
    sorting = dict(intent.get("requested_sort") or {})
    if not sorting:
        return {}

    ranking_mode = _ranking_mode_for_contract(intent, query_shape)
    target_table = (
        str(selected_tables[0].get("table") or "").strip()
        if len(selected_tables) == 1
        else ""
    )
    selected_direction = str(
        (selected_order_by or {}).get("direction") or sorting.get("direction") or ""
    ).strip().upper()
    terms = str(sorting.get("terms") or "").strip()
    reason_code = ""
    ambiguity_group_key = ""
    rejected_alternatives = _ranking_rejected_alternatives(order_by_ambiguity_choices)

    if _ranking_direction_conflict(intent):
        status = "ambiguous"
        reason_code = "ranking_direction_conflict"
        ambiguity_group_key = "ranking_direction"
    elif selected_direction.lower() not in {"asc", "desc"}:
        status = "unsupported"
        reason_code = "order_by_direction_invalid"
        ambiguity_group_key = "ranking_direction"
    elif order_by_reason:
        status = "ambiguous" if "ambiguous" in order_by_reason else "unsupported"
        reason_code = order_by_reason
        ambiguity_group_key = f"order_by:{_normalize(terms)}" if terms else "order_by"
    elif limit_reason in {"limit_not_numeric", "limit_out_of_safe_range"}:
        status = "unsupported"
        reason_code = limit_reason
        ambiguity_group_key = "ranking_limit"
    elif ranking_mode == "grouped_aggregate_ranking" and aggregate_function not in {"count", "sum", "avg", "min", "max"}:
        status = "unsupported"
        reason_code = "ranking_aggregate_missing"
        ambiguity_group_key = "ranking_aggregate"
    elif ranking_mode == "grouped_aggregate_ranking" and not selected_dimensions:
        status = "unsupported"
        reason_code = "ranking_grouping_dimension_missing"
        ambiguity_group_key = "ranking_dimension"
    elif ranking_mode == "grouped_aggregate_ranking" and len(selected_dimensions) > 1:
        status = "ambiguous"
        reason_code = "ranking_grouping_dimension_ambiguous"
        ambiguity_group_key = "ranking_dimension"
    elif not isinstance(selected_order_by, dict):
        status = "unsupported"
        reason_code = "order_by_target_missing"
        ambiguity_group_key = "order_by"
    else:
        status = "resolved"

    ranking_metric: dict[str, Any] = {}
    if isinstance(selected_order_by, dict):
        ranking_metric = {
            "table": str(selected_order_by.get("table") or "").strip(),
            "column": str(selected_order_by.get("column") or "").strip(),
        }
    elif isinstance(selected_metric, dict):
        table_name, column_name = _order_candidate_identity(selected_metric)
        ranking_metric = {"table": table_name, "column": column_name}

    grouping_dimension: dict[str, Any] = {}
    if selected_dimensions:
        table_name, column_name = _order_candidate_identity(selected_dimensions[0])
        grouping_dimension = {"table": table_name, "column": column_name}

    score_reasons: list[str] = []
    if status == "resolved":
        score_reasons.extend(["ranking_direction_resolved", "order_by_target_resolved"])
        if isinstance(limit, int):
            score_reasons.append("ranking_limit_resolved")
        if ranking_mode == "grouped_aggregate_ranking":
            score_reasons.extend(["aggregate_metric_resolved", "grouping_dimension_resolved"])

    return {
        "status": status,
        "ranking_mode": ranking_mode,
        "target_entity": target_table,
        "target_table": target_table,
        "ranking_metric": ranking_metric,
        "aggregate_function": aggregate_function or None,
        "grouping_dimension": grouping_dimension,
        "direction": selected_direction if selected_direction in {"ASC", "DESC"} else "",
        "limit": limit,
        "selected_projection_mode": _ranking_projection_mode(ranking_mode),
        "score": 1.0 if status == "resolved" else 0.0,
        "score_reasons": score_reasons,
        "evidence_reasons": score_reasons,
        "rejected_alternatives": rejected_alternatives,
        "ambiguity_group_key": ambiguity_group_key,
        "reason_code": reason_code,
    }


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
