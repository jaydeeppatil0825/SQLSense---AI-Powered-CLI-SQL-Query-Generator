"""HAVING/group-filter helpers for the deterministic planner."""

from __future__ import annotations

from typing import Any


def _selected_having_for_contract(
    intent: dict[str, Any],
    selected_metric: dict[str, Any] | None,
    selected_tables: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    conditions = [
        dict(entry)
        for entry in (intent.get("structured_having") or [])
        if isinstance(entry, dict)
    ]
    if not conditions:
        return []

    table_name = ""
    if len(selected_tables) == 1:
        table_name = str(selected_tables[0].get("table") or "").strip()
    selected: list[dict[str, Any]] = []
    for condition in conditions:
        aggregate_function = str(condition.get("aggregate_function") or "").strip().lower()
        resolved = dict(condition)
        resolved["table"] = table_name
        if aggregate_function == "count":
            resolved["column"] = ""
        elif isinstance(selected_metric, dict):
            resolved["table"] = str(selected_metric.get("table") or table_name).strip()
            resolved["column"] = str(
                selected_metric.get("column") or selected_metric.get("column_name") or ""
            ).strip()
        else:
            resolved["column"] = ""
        selected.append(resolved)
    return selected
