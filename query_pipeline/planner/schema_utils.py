"""Shared schema/type helpers for deterministic planning."""

from __future__ import annotations

from typing import Any


def _candidate_semantic_type(entry: dict[str, Any]) -> str:
    return str(
        entry.get("semantic_type")
        or entry.get("core_semantic_type")
        or ""
    ).strip().lower()


def _is_numeric_sql_type(data_type: str) -> bool:
    normalized = str(data_type or "").strip().lower()
    return any(
        token in normalized
        for token in (
            "decimal",
            "numeric",
            "number",
            "int",
            "float",
            "double",
            "real",
            "money",
        )
    )


def _is_textual_sql_type(data_type: str) -> bool:
    normalized = str(data_type or "").strip().lower()
    return any(
        token in normalized
        for token in (
            "char",
            "text",
            "date",
            "time",
            "bool",
            "json",
        )
    )
