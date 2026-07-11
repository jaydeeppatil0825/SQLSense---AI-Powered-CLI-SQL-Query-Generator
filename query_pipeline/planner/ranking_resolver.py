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
