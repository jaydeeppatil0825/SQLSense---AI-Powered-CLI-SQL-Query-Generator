"""WHERE/HAVING predicate rendering for deterministic SQL plans."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from kb_pipeline.schema_facts import resolved_semantic_type

FILTER_OPERATORS = {
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

NUMERIC_TYPE_MARKERS = ("int", "decimal", "numeric", "float", "double", "real")
DATE_TYPE_MARKERS = ("date", "time", "timestamp")


def dedupe_filters(filters: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for entry in filters:
        key = (
            str(entry.get("table") or ""),
            str(entry.get("column") or entry.get("column_name") or ""),
            str(entry.get("operator") or "").lower(),
            str(entry.get("value") or entry.get("values") or "").lower(),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(entry)
    return deduped


def filter_predicate(
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
        if filter_column_kind(schema_column) == "date":
            range_reason = validate_date_range_values(values)
            if range_reason:
                return "", range_reason
        lower, lower_reason = filter_literal(values[0], schema_column, operator)
        upper, upper_reason = filter_literal(values[1], schema_column, operator)
        if lower_reason or upper_reason:
            return "", lower_reason or upper_reason
        return f"{column_name} BETWEEN {lower} AND {upper}", ""

    sql_operator = FILTER_OPERATORS.get(operator)
    if not sql_operator:
        return "", "filter_operator_not_supported"
    literal, literal_reason = filter_literal(
        selected_filter.get("value", selected_filter.get("value_phrase")),
        schema_column,
        operator,
    )
    if literal_reason:
        return "", literal_reason
    return f"{column_name} {sql_operator} {literal}", ""


def filter_literal(value: Any, schema_column: dict[str, Any], operator: str) -> tuple[str, str]:
    if isinstance(value, (list, tuple, dict)) or value is None:
        return "", "filter_value_missing"
    text = str(value).strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        text = text[1:-1].strip()
    if not text:
        return "", "filter_value_missing"
    column_kind = filter_column_kind(schema_column)
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
        return date_filter_literal(text, schema_column)
    if operator not in {"eq", "neq", "contains"}:
        return "", "filter_operator_type_mismatch"
    if operator == "contains":
        text = f"%{text}%"
    return "'" + text.replace("'", "''") + "'", ""


def filter_column_kind(schema_column: dict[str, Any]) -> str:
    column_type = str(schema_column.get("type") or "").strip().lower()
    semantic_type = resolved_semantic_type(schema_column)
    if any(marker in column_type for marker in NUMERIC_TYPE_MARKERS):
        return "numeric"
    if semantic_type == "date" or any(marker in column_type for marker in DATE_TYPE_MARKERS):
        return "date"
    return "text"


def date_filter_literal(text: str, schema_column: dict[str, Any]) -> tuple[str, str]:
    column_type = str(schema_column.get("type") or "").strip().lower()
    try:
        if re_full_date(text):
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


def validate_date_range_values(values: Any) -> str:
    try:
        start = date.fromisoformat(str(values[0]).strip())
        end = date.fromisoformat(str(values[1]).strip())
    except (TypeError, ValueError):
        return "filter_value_type_mismatch"
    return "filter_between_range_invalid" if start > end else ""


def re_full_date(text: str) -> bool:
    return (
        len(text) == 10
        and text[4] == "-"
        and text[7] == "-"
        and text[:4].isdigit()
        and text[5:7].isdigit()
        and text[8:].isdigit()
    )
