"""
ai/simple_query_generator.py
============================
Deterministic SQL generator for simple, mostly single-table questions.

This module is intentionally generic:
- No fixed table aliases
- No database-specific table mappings
- No demo-specific join recipes

It relies on the active knowledge base, active glossary, and the reduced schema
selected by the query planner.
"""

from __future__ import annotations

import re
from typing import Any


_COMPLEX_KEYWORDS = {
    " by ",
    " group ",
    " top ",
    " highest ",
    " lowest ",
    " monthly ",
    " trend ",
    " compare ",
    " comparison ",
    " versus ",
    " vs ",
    " breakdown ",
    " distribution ",
    " ranking ",
    " join ",
}

_DATE_PATTERNS = ("date", "time", "timestamp", "created", "updated", "modified")
_STATUS_VALUE_FALLBACKS = {
    "pending": ["Pending", "pending", "Unpaid", "unpaid", "Open", "open"],
    "unpaid": ["Unpaid", "unpaid", "Pending", "pending"],
    "paid": ["Paid", "paid", "Closed", "closed"],
    "open": ["Open", "open"],
    "closed": ["Closed", "closed"],
    "active": ["Active", "active", "Enabled", "enabled"],
    "inactive": ["Inactive", "inactive", "Disabled", "disabled"],
    "cancelled": ["Cancelled", "cancelled", "Canceled", "canceled"],
    "canceled": ["Canceled", "canceled", "Cancelled", "cancelled"],
    "approved": ["Approved", "approved"],
}


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip().lower())


def _normalize_identifier(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", _normalize(text)).strip("_")


def _humanize(text: str) -> str:
    return _normalize_identifier(text).replace("_", " ").strip()


def _singularize(text: str) -> str:
    value = _humanize(text)
    if value.endswith("ies") and len(value) > 3:
        return value[:-3] + "y"
    if value.endswith("ses") and len(value) > 3:
        return value[:-2]
    if value.endswith("s") and not value.endswith("ss") and len(value) > 1:
        return value[:-1]
    return value


def _tokenize(text: str) -> list[str]:
    raw_tokens = [token for token in re.split(r"[^a-z0-9]+", _normalize(text)) if token]
    tokens: list[str] = []
    seen: set[str] = set()
    for token in raw_tokens:
        for candidate in (token, _singularize(token)):
            if candidate and candidate not in seen:
                tokens.append(candidate)
                seen.add(candidate)
    return tokens


def _column_type_is_numeric(column_type: str) -> bool:
    normalized = _normalize(column_type)
    return any(token in normalized for token in ("int", "decimal", "numeric", "float", "double", "real"))


def _table_columns(table_data: dict[str, Any]) -> list[dict[str, Any]]:
    return list(table_data.get("columns", []))


def _find_glossary_matches(question: str, glossary: dict | None) -> list[tuple[str, dict[str, Any]]]:
    if not glossary:
        return []

    normalized_question = _normalize(question)
    question_terms = set(_tokenize(question))
    matches = []
    for term, term_data in glossary.items():
        normalized_term = _normalize(term)
        if normalized_term and normalized_term in normalized_question:
            matches.append((term, term_data))
            continue
        aliases = term_data.get("business_terms", []) or []
        if any(set(_tokenize(alias)) <= question_terms for alias in aliases if _tokenize(alias)):
            matches.append((term, term_data))
    return matches


def _score_table(
    question: str,
    table_name: str,
    table_data: dict[str, Any],
    glossary_matches: list[tuple[str, dict[str, Any]]],
) -> float:
    score = 0.0
    normalized_question = _normalize(question)
    question_terms = set(_tokenize(question))
    table_terms = {
        table_name.lower(),
        _humanize(table_name),
        _singularize(table_name),
    }

    if any(term and term in normalized_question for term in table_terms):
        score += 3.0

    column_terms = {
        _normalize(column.get("name", ""))
        for column in _table_columns(table_data)
        if column.get("name")
    }
    overlap = len(question_terms & {token for term in column_terms for token in _tokenize(term)})
    score += overlap * 0.25

    for _, term_data in glossary_matches:
        for mapping in term_data.get("mapped_columns", []):
            if mapping.get("table") == table_name:
                score += 1.5
                break

    return score


def _pick_table(
    question: str,
    knowledge_base: dict,
    query_plan: dict | None,
    glossary_matches: list[tuple[str, dict[str, Any]]],
) -> str | None:
    if not knowledge_base:
        return None

    if query_plan:
        selected = list(query_plan.get("selected_table_names") or [])
        if len(selected) == 1 and selected[0] in knowledge_base:
            return selected[0]

    if len(knowledge_base) == 1:
        return next(iter(knowledge_base))

    scored = []
    for table_name, table_data in knowledge_base.items():
        score = _score_table(question, table_name, table_data, glossary_matches)
        if score > 0:
            scored.append((score, table_name))
    if not scored:
        return None
    scored.sort(reverse=True)
    return scored[0][1]


def _pick_date_column(table_data: dict[str, Any]) -> str | None:
    for column in _table_columns(table_data):
        if str(column.get("semantic_type", "")).lower() == "date":
            return str(column.get("name", ""))
    for column in _table_columns(table_data):
        name = _normalize_identifier(column.get("name", ""))
        if any(pattern in name for pattern in _DATE_PATTERNS):
            return str(column.get("name", ""))
    return None


def _pick_status_column(table_data: dict[str, Any]) -> dict[str, Any] | None:
    for column in _table_columns(table_data):
        if str(column.get("semantic_type", "")).lower() == "status":
            return column
    for column in _table_columns(table_data):
        name = _normalize_identifier(column.get("name", ""))
        if any(token in name for token in ("status", "state", "stage")):
            return column
    return None


def _status_value_for_question(question: str, column: dict[str, Any]) -> str | None:
    normalized_question = _normalize(question)
    sample_values = [str(value) for value in (column.get("sample_values") or []) if value is not None]
    sample_set = set(sample_values)

    for trigger, fallbacks in _STATUS_VALUE_FALLBACKS.items():
        if re.search(r"\b" + re.escape(trigger) + r"\b", normalized_question):
            for value in fallbacks:
                if value in sample_set:
                    return value
            return fallbacks[0]
    return None


def _glossary_mapped_columns(
    table_name: str,
    glossary_matches: list[tuple[str, dict[str, Any]]],
) -> list[str]:
    mapped_columns = []
    for _, term_data in glossary_matches:
        for mapping in term_data.get("mapped_columns", []):
            if mapping.get("table") == table_name and mapping.get("column"):
                mapped_columns.append(str(mapping["column"]))
    return mapped_columns


def _pick_measure_column(
    question: str,
    table_data: dict[str, Any],
    glossary_columns: list[str],
    query_plan: dict | None,
) -> str | None:
    columns = _table_columns(table_data)
    column_lookup = {str(column.get("name", "")): column for column in columns}

    for column_name in glossary_columns:
        column = column_lookup.get(column_name)
        if not column:
            continue
        semantic_type = str(column.get("semantic_type", "")).lower()
        if semantic_type in {"money", "quantity", "percentage"}:
            return column_name

    semantic_hints = set((query_plan or {}).get("semantic_hints") or [])
    preferred_semantics = []
    if "money" in semantic_hints:
        preferred_semantics.append("money")
    if "quantity" in semantic_hints:
        preferred_semantics.append("quantity")
    if "percentage" in semantic_hints:
        preferred_semantics.append("percentage")
    preferred_semantics.extend(["money", "quantity", "percentage"])

    for semantic_type in preferred_semantics:
        for column in columns:
            if str(column.get("semantic_type", "")).lower() == semantic_type:
                return str(column.get("name", ""))

    for column in columns:
        if _column_type_is_numeric(str(column.get("type", ""))):
            return str(column.get("name", ""))

    return None


def _extract_limit_from_question(user_question: str) -> int:
    match = re.search(
        r"\b(?:latest|last|recent|first|top|show|get|fetch)\s+(\d+)\b"
        r"|\b(\d+)\s+(?:rows?|records?|results?)\b",
        user_question,
        re.IGNORECASE,
    )
    if match:
        return int(match.group(1) or match.group(2))
    return 50


def _is_complex_question(user_question: str, query_plan: dict | None) -> bool:
    normalized = f" {_normalize(user_question)} "
    if any(keyword in normalized for keyword in _COMPLEX_KEYWORDS):
        return True
    if not query_plan:
        return False
    if query_plan.get("intent") in {"top_n", "trend", "comparison"}:
        return True
    if query_plan.get("dimension") and query_plan.get("intent") not in {"list", "count"}:
        return True
    if len(query_plan.get("grouping") or []) > 0:
        return True
    return False


def _try_count(table_name: str) -> str:
    alias = f"total_{_normalize_identifier(table_name) or 'records'}"
    return f"SELECT COUNT(*) AS {alias} FROM {table_name};"


def _try_latest(table_name: str, table_data: dict[str, Any], question: str) -> str | None:
    date_column = _pick_date_column(table_data)
    if not date_column:
        return None
    limit = _extract_limit_from_question(question)
    return f"SELECT * FROM {table_name} ORDER BY {date_column} DESC LIMIT {limit};"


def _try_status_filter(table_name: str, table_data: dict[str, Any], question: str) -> str | None:
    column = _pick_status_column(table_data)
    if not column:
        return None
    value = _status_value_for_question(question, column)
    if not value:
        return None
    return f"SELECT * FROM {table_name} WHERE {column.get('name')} = '{value}' LIMIT 50;"


def _try_total_or_average(
    table_name: str,
    table_data: dict[str, Any],
    question: str,
    glossary_columns: list[str],
    query_plan: dict | None,
) -> str | None:
    normalized_question = _normalize(question)
    if re.search(r"\b(total|sum)\b", normalized_question):
        func = "SUM"
        alias_prefix = "total"
    elif re.search(r"\b(average|avg|mean)\b", normalized_question):
        func = "AVG"
        alias_prefix = "average"
    else:
        return None

    measure_column = _pick_measure_column(question, table_data, glossary_columns, query_plan)
    if not measure_column:
        return None

    alias = f"{alias_prefix}_{_normalize_identifier(measure_column)}"
    return f"SELECT {func}({measure_column}) AS {alias} FROM {table_name};"


def _try_show_all(table_name: str, question: str) -> str | None:
    normalized_question = _normalize(question)
    if re.search(r"\b(show|list|display|get|fetch|view|see|give)\b", normalized_question):
        return f"SELECT * FROM {table_name} LIMIT 50;"
    return None


def generate_simple_sql(
    user_question: str,
    knowledge_base: dict,
    query_plan: dict | None = None,
    business_glossary: dict | None = None,
) -> str | None:
    """
    Try to generate SQL for a simple question using dynamic KB-driven logic.

    Returns ready-to-execute SQL or None so the AI path can handle richer
    grouped, trend, comparison, or multi-table questions.
    """
    if not user_question or not knowledge_base:
        return None

    if _is_complex_question(user_question, query_plan):
        return None

    glossary_matches = _find_glossary_matches(user_question, business_glossary)
    table_name = _pick_table(user_question, knowledge_base, query_plan, glossary_matches)
    if not table_name or table_name not in knowledge_base:
        return None

    table_data = knowledge_base[table_name]
    glossary_columns = _glossary_mapped_columns(table_name, glossary_matches)
    normalized_question = _normalize(user_question)
    intent = (query_plan or {}).get("intent")

    if intent == "count" or re.search(r"\b(count|how many|number of)\b", normalized_question):
        return _try_count(table_name)

    if re.search(r"\b(latest|recent|newest|last|most recent)\b", normalized_question):
        latest_sql = _try_latest(table_name, table_data, user_question)
        if latest_sql:
            return latest_sql

    if any(re.search(r"\b" + re.escape(trigger) + r"\b", normalized_question) for trigger in _STATUS_VALUE_FALLBACKS):
        status_sql = _try_status_filter(table_name, table_data, user_question)
        if status_sql:
            return status_sql

    aggregate_sql = _try_total_or_average(table_name, table_data, user_question, glossary_columns, query_plan)
    if aggregate_sql:
        return aggregate_sql

    if intent in {"pending_outstanding", "low_stock"}:
        return None

    return _try_show_all(table_name, user_question)
