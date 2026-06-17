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
    " group ",
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


def _is_direct_term_match(question: str, term: str) -> bool:
    normalized_question = _normalize(question)
    normalized_term = _normalize(term)
    if normalized_term and normalized_term in normalized_question:
        return True
    term_tokens = set(_tokenize(term))
    question_terms = set(_tokenize(question))
    return bool(term_tokens and term_tokens <= question_terms)


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


def _table_name_direct_match(question: str, table_name: str) -> bool:
    normalized_question = _normalize(question)
    human_table = _humanize(table_name)
    if human_table and human_table in normalized_question:
        return True

    question_terms = set(_tokenize(question))
    table_terms = set(_tokenize(table_name))
    table_terms.add(_singularize(table_name))
    return bool(table_terms & question_terms)


def _pick_table(
    question: str,
    knowledge_base: dict,
    query_plan: dict | None,
    glossary_matches: list[tuple[str, dict[str, Any]]],
    selected_tables: list[dict[str, Any]] | None = None,
    vector_results: dict[str, Any] | None = None,
) -> str | None:
    if not knowledge_base:
        return None

    selected_entries = [
        entry for entry in (selected_tables or [])
        if entry.get("table") in knowledge_base
    ]
    aggregate_intent = str((query_plan or {}).get("intent") or "") in {"total", "average"}
    if len(selected_entries) == 1:
        return str(selected_entries[0]["table"])
    if len(selected_entries) > 1:
        direct_matches = [
            entry for entry in selected_entries
            if _table_name_direct_match(question, str(entry.get("table", "")))
        ]
        if len(direct_matches) == 1:
            return str(direct_matches[0]["table"])

        top_confidence = float(selected_entries[0].get("confidence") or 0.0)
        second_confidence = float(selected_entries[1].get("confidence") or 0.0)
        if top_confidence >= 0.85 and (
            (top_confidence - second_confidence) >= 0.15 or second_confidence < 0.7
        ):
            return str(selected_entries[0]["table"])
        if aggregate_intent:
            return str(selected_entries[0]["table"])

    if query_plan:
        selected = list(query_plan.get("selected_table_names") or [])
        if len(selected) == 1 and selected[0] in knowledge_base:
            return selected[0]

    for table_name in list((vector_results or {}).get("table_names") or []):
        if table_name in knowledge_base:
            return table_name

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
    question_terms = set(_tokenize(question))

    for raw_value in (column.get("sample_values") or []):
        if raw_value is None:
            continue
        sample_value = str(raw_value)
        normalized_value = _normalize(sample_value)
        sample_terms = set(_tokenize(sample_value))
        if not normalized_value or not sample_terms:
            continue
        if normalized_value in normalized_question or sample_terms <= question_terms:
            return sample_value
    return None


def _glossary_mapped_columns(
    question: str,
    table_name: str,
    glossary_matches: list[tuple[str, dict[str, Any]]],
) -> list[str]:
    mapped_columns = []
    for term, term_data in glossary_matches:
        if not _is_direct_term_match(question, term):
            continue
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
    primary_keys = {str(value) for value in (table_data.get("primary_keys") or []) if value}
    foreign_keys = {
        str(fk.get("column"))
        for fk in (table_data.get("foreign_keys") or [])
        if fk.get("column")
    }
    question_terms = set(_tokenize(question))

    def _is_identifier_column(column_name: str, column: dict[str, Any]) -> bool:
        normalized_name = _normalize_identifier(column_name)
        if column_name in primary_keys or column_name in foreign_keys:
            return True
        if normalized_name == "id" or normalized_name.endswith("_id"):
            return True
        semantic_type = str(column.get("semantic_type", "")).lower()
        return semantic_type == "id"

    def _measure_score(column_name: str, column: dict[str, Any]) -> float:
        if _is_identifier_column(column_name, column):
            return -1.0
        semantic_type = str(column.get("semantic_type", "")).lower()
        column_type = str(column.get("type", "")).lower()
        tokens = set(_tokenize(column_name))
        score = 0.0

        if semantic_type == "money":
            score += 4.0
        elif semantic_type == "quantity":
            score += 3.0
        elif semantic_type == "percentage":
            score += 2.5

        if _column_type_is_numeric(column_type):
            score += 2.0

        if tokens & question_terms:
            score += 1.8 * len(tokens & question_terms)
        if tokens & {"amount", "value", "total", "cost", "price", "balance", "rate"}:
            score += 1.4
        if tokens & {"final", "gross", "net", "subtotal"}:
            score += 1.0
        if not (tokens & question_terms) and tokens & {"tax", "gst", "vat", "levy", "rebate", "discount"}:
            score -= 1.6
        if tokens & {"type", "name", "label", "status", "state"}:
            score -= 1.5

        return score

    scored_glossary_candidates: list[tuple[float, str]] = []
    for column_name in glossary_columns:
        column = column_lookup.get(column_name)
        if not column:
            continue
        semantic_type = str(column.get("semantic_type", "")).lower()
        if semantic_type in {"money", "quantity", "percentage"} and not _is_identifier_column(column_name, column):
            score = _measure_score(column_name, column)
            if score > 0:
                scored_glossary_candidates.append((score + 0.5, column_name))
    if scored_glossary_candidates:
        scored_glossary_candidates.sort(key=lambda item: (-item[0], item[1]))
        return scored_glossary_candidates[0][1]

    semantic_hints = set((query_plan or {}).get("semantic_hints") or [])
    preferred_semantics = []
    if "money" in semantic_hints:
        preferred_semantics.append("money")
    if "quantity" in semantic_hints:
        preferred_semantics.append("quantity")
    if "percentage" in semantic_hints:
        preferred_semantics.append("percentage")
    preferred_semantics.extend(["money", "quantity", "percentage"])

    best_scored: tuple[float, str] | None = None
    for column in columns:
        column_name = str(column.get("name", ""))
        semantic_type = str(column.get("semantic_type", "")).lower()
        if preferred_semantics and semantic_type not in preferred_semantics and semantic_type not in {"money", "quantity", "percentage"}:
            continue
        score = _measure_score(column_name, column)
        if score <= 0:
            continue
        if best_scored is None or score > best_scored[0]:
            best_scored = (score, column_name)

    if best_scored:
        return best_scored[1]

    for column in columns:
        column_name = str(column.get("name", ""))
        if _is_identifier_column(column_name, column):
            continue
        if _column_type_is_numeric(str(column.get("type", ""))):
            return column_name

    return None


def _selected_column_names(
    table_name: str,
    selected_tables: list[dict[str, Any]] | None,
) -> list[str]:
    for entry in selected_tables or []:
        if entry.get("table") != table_name:
            continue
        return [
            str(column_entry.get("column"))
            for column_entry in (entry.get("selected_columns") or [])
            if column_entry.get("column")
        ]
    return []


def _pick_quantity_column(table_data: dict[str, Any]) -> str | None:
    for column in _table_columns(table_data):
        if str(column.get("semantic_type", "")).lower() == "quantity":
            return str(column.get("name", ""))
    for column in _table_columns(table_data):
        name = _normalize_identifier(column.get("name", ""))
        if any(token in name for token in ("qty", "quantity", "count", "units", "volume")):
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


def _escape_sql_literal(value: Any) -> str:
    return str(value).replace("'", "''")


def _where_sql(clauses: list[str]) -> str:
    if not clauses:
        return ""
    return " WHERE " + " AND ".join(dict.fromkeys(clauses))


def _resolve_status_value(
    question: str,
    filter_data: dict[str, Any],
    column: dict[str, Any],
) -> str | None:
    sample_values = [str(value) for value in (column.get("sample_values") or []) if value is not None]
    sample_lookup = {value.lower(): value for value in sample_values}

    explicit_value = str(filter_data.get("value") or "").strip()
    if explicit_value:
        matched = sample_lookup.get(explicit_value.lower())
        if matched:
            return matched
        return explicit_value

    return _status_value_for_question(question, column)


def _resolve_filter_value(
    question: str,
    filter_data: dict[str, Any],
    column: dict[str, Any],
) -> str | None:
    if filter_data.get("type") == "status":
        return _resolve_status_value(question, filter_data, column)

    explicit_value = str(filter_data.get("value") or "").strip()
    if explicit_value:
        return explicit_value

    for raw_value in (column.get("sample_values") or []):
        if raw_value is None:
            continue
        sample_value = str(raw_value)
        normalized_value = _normalize(sample_value)
        if normalized_value and normalized_value in _normalize(question):
            return sample_value
    return None


def _build_where_clauses(
    question: str,
    table_data: dict[str, Any],
    query_plan: dict | None,
) -> list[str]:
    clauses: list[str] = []
    if not query_plan:
        return clauses

    date_range = query_plan.get("date_range") or {}
    if date_range:
        date_column = _pick_date_column(table_data)
        if date_column:
            start = date_range.get("start")
            end_exclusive = date_range.get("end_exclusive")
            if start:
                clauses.append(f"{date_column} >= '{_escape_sql_literal(start)}'")
            if end_exclusive:
                clauses.append(f"{date_column} < '{_escape_sql_literal(end_exclusive)}'")

    for filter_data in query_plan.get("filters") or []:
        filter_column_name = str(filter_data.get("column") or "")
        target_column = None
        if filter_column_name:
            target_column = next(
                (column for column in _table_columns(table_data) if str(column.get("name", "")) == filter_column_name),
                None,
            )
        if not target_column or not target_column.get("name"):
            if filter_data.get("type") != "status":
                continue
            target_column = _pick_status_column(table_data)
        if not target_column or not target_column.get("name"):
            continue
        resolved_value = _resolve_filter_value(question, filter_data, target_column)
        if resolved_value is None:
            continue
        clauses.append(
            f"{target_column['name']} = '{_escape_sql_literal(resolved_value)}'"
        )

    return clauses


def _default_sort_clause(
    table_data: dict[str, Any],
    query_plan: dict | None,
) -> str:
    sorting = (query_plan or {}).get("sorting") or {}
    sort_by = str(sorting.get("by") or "").lower()
    direction = str(sorting.get("direction") or "asc").upper()
    if direction not in {"ASC", "DESC"}:
        direction = "ASC"

    if sort_by == "date":
        date_column = _pick_date_column(table_data)
        if date_column:
            return f" ORDER BY {date_column} {direction}"
    if sort_by == "quantity":
        quantity_column = _pick_quantity_column(table_data)
        if quantity_column:
            return f" ORDER BY {quantity_column} {direction}"

    columns = _table_columns(table_data)
    requested_tokens = set(_tokenize(sort_by))
    if requested_tokens:
        ranked_columns: list[tuple[float, str]] = []
        for column in columns:
            column_name = str(column.get("name", ""))
            column_tokens = set(_tokenize(column_name))
            if not column_tokens:
                continue
            overlap = len(requested_tokens & column_tokens)
            if not overlap:
                continue
            score = overlap * 2.0
            semantic_type = str(column.get("semantic_type", "")).lower()
            if semantic_type in {"money", "quantity", "percentage", "date"}:
                score += 1.0
            ranked_columns.append((score, column_name))
        ranked_columns.sort(key=lambda item: (-item[0], item[1]))
        if ranked_columns:
            return f" ORDER BY {ranked_columns[0][1]} {direction}"
    return ""


def _effective_limit(user_question: str, query_plan: dict | None) -> int:
    limit = (query_plan or {}).get("limit")
    if isinstance(limit, int) and limit > 0:
        return limit
    return _extract_limit_from_question(user_question)


def _try_count(table_name: str, where_clauses: list[str]) -> str:
    alias = f"total_{_normalize_identifier(table_name) or 'records'}"
    return f"SELECT COUNT(*) AS {alias} FROM {table_name}{_where_sql(where_clauses)};"


def _try_latest(
    table_name: str,
    table_data: dict[str, Any],
    question: str,
    where_clauses: list[str],
    limit: int,
) -> str | None:
    date_column = _pick_date_column(table_data)
    if not date_column:
        return None
    # Use explicit columns from schema instead of SELECT *
    columns = _table_columns(table_data)
    column_names = [str(col.get("name", "")) for col in columns if col.get("name")]
    if not column_names:
        # Fallback to SELECT * if no columns found
        column_list = "*"
    else:
        column_list = ", ".join(column_names)
    
    return (
        f"SELECT {column_list} FROM {table_name}"
        f"{_where_sql(where_clauses)}"
        f" ORDER BY {date_column} DESC LIMIT {limit};"
    )


def _try_total_or_average(
    table_name: str,
    table_data: dict[str, Any],
    question: str,
    glossary_columns: list[str],
    query_plan: dict | None,
    where_clauses: list[str],
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
    return (
        f"SELECT {func}({measure_column}) AS {alias} "
        f"FROM {table_name}{_where_sql(where_clauses)};"
    )


def _try_show_all(
    table_name: str,
    table_data: dict[str, Any],
    question: str,
    query_plan: dict | None,
    where_clauses: list[str],
    limit: int,
) -> str | None:
    normalized_question = _normalize(question)
    intent = str((query_plan or {}).get("intent") or "")
    if intent == "list" or re.search(r"\b(show|list|display|get|fetch|view|see|give|tell)\b", normalized_question):
        # Use explicit columns from schema instead of SELECT *
        columns = _table_columns(table_data)
        column_names = [str(col.get("name", "")) for col in columns if col.get("name")]
        if not column_names:
            # Fallback to SELECT * if no columns found
            column_list = "*"
        else:
            column_list = ", ".join(column_names)
        
        order_by_sql = _default_sort_clause(table_data, query_plan)
        return f"SELECT {column_list} FROM {table_name}{_where_sql(where_clauses)}{order_by_sql} LIMIT {limit};"
    return None


def generate_simple_sql(
    user_question: str,
    knowledge_base: dict,
    query_plan: dict | None = None,
    business_glossary: dict | None = None,
    selected_tables: list[dict[str, Any]] | None = None,
    vector_results: dict[str, Any] | None = None,
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
    table_name = _pick_table(
        user_question,
        knowledge_base,
        query_plan,
        glossary_matches,
        selected_tables=selected_tables,
        vector_results=vector_results,
    )
    if not table_name or table_name not in knowledge_base:
        return None

    table_data = knowledge_base[table_name]
    glossary_columns = _selected_column_names(table_name, selected_tables)
    glossary_columns.extend(_glossary_mapped_columns(user_question, table_name, glossary_matches))
    normalized_question = _normalize(user_question)
    intent = (query_plan or {}).get("intent")
    limit = _effective_limit(user_question, query_plan)
    where_clauses = _build_where_clauses(user_question, table_data, query_plan)

    if intent == "count" or re.search(r"\b(count|how many|number of)\b", normalized_question):
        return _try_count(table_name, where_clauses)

    if re.search(r"\b(latest|recent|newest|last|most recent)\b", normalized_question):
        latest_sql = _try_latest(table_name, table_data, user_question, where_clauses, limit)
        if latest_sql:
            return latest_sql

    aggregate_sql = _try_total_or_average(
        table_name,
        table_data,
        user_question,
        glossary_columns,
        query_plan,
        where_clauses,
    )
    if aggregate_sql:
        return aggregate_sql

    return _try_show_all(table_name, table_data, user_question, query_plan, where_clauses, limit)
