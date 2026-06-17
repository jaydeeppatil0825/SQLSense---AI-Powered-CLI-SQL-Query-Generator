"""
utils/sql_validator.py

Provides SQL cleanup, safety validation, and schema-driven structure validation.
All functions in this module are stateless and generic.
"""

from __future__ import annotations

import re
from typing import Any

# Forbidden DML/DDL keywords that must never appear in a safe SELECT query.
_FORBIDDEN_KEYWORDS = [
    "DROP",
    "DELETE",
    "UPDATE",
    "INSERT",
    "ALTER",
    "TRUNCATE",
    "CREATE",
    "RECREATE",
    "REPLACE",
    "EXEC",
    "EXECUTE",
    "GRANT",
    "REVOKE",
]

_CLAUSE_KEYWORDS = {
    "SELECT", "FROM", "WHERE", "JOIN", "LEFT", "RIGHT", "INNER", "OUTER",
    "FULL", "CROSS", "NATURAL", "ON", "USING", "GROUP", "ORDER", "BY",
    "HAVING", "LIMIT", "OFFSET", "UNION", "AS",
}
_INVALID_TABLE_START_KEYWORDS = {
    "WHERE", "GROUP", "ORDER", "LIMIT", "HAVING", "JOIN", "ON", "USING",
    "LEFT", "RIGHT", "INNER", "OUTER", "FULL", "CROSS", "NATURAL", "UNION", "BY",
    "SELECT",
}
_SQL_FUNCTIONS = {
    "COUNT", "SUM", "AVG", "MIN", "MAX", "DATE_FORMAT", "DATE", "YEAR", "MONTH",
    "DAY", "NOW", "CURDATE", "COALESCE", "IFNULL", "ROUND", "CAST", "UPPER",
    "LOWER", "TRIM", "SUBSTRING", "CONCAT", "ABS", "IF", "NULLIF",
}
_SQL_KEYWORDS = {
    "SELECT", "FROM", "WHERE", "JOIN", "LEFT", "RIGHT", "INNER", "OUTER",
    "FULL", "CROSS", "NATURAL", "ON", "USING", "GROUP", "ORDER", "BY",
    "HAVING", "LIMIT", "OFFSET", "UNION", "ALL", "DISTINCT", "AS",
    "CASE", "WHEN", "THEN", "ELSE", "END", "AND", "OR", "NOT", "NULL", "IS",
    "IN", "LIKE", "BETWEEN", "ASC", "DESC", "TRUE", "FALSE",
}
_PUNCTUATION_TOKENS = {",", ";", "(", ")", ".", "*", "=", "<", ">", "<=", ">=", "!=", "<>"}
_TOKEN_RE = re.compile(r"`(?:``|[^`])+`|<=|>=|<>|!=|[(),.;=*<>]|\.|[A-Za-z_][A-Za-z0-9_]*|\d+(?:\.\d+)?")
_PREAMBLE_PATTERNS = re.compile(
    r"^("
    r"here\s+is(\s+the)?\s+sql[\s:]*"
    r"|here\s+is(\s+a)?\s+query[\s:]*"
    r"|sql\s+statement[\s\w]*:+"
    r"|sql\s+query[\s\w]*:+"
    r"|the\s+sql[\s\w]*:+"
    r"|query[\s:]*"
    r"|result[\s:]*"
    r"|output[\s:]*"
    r"|answer[\s:]*"
    r")\s*",
    re.IGNORECASE,
)


def _repair_order_by(sql: str) -> str:
    """Remove obviously malformed ORDER BY tails without altering valid SQL."""
    sql = re.sub(r"\bORDER\s+BY\s+(?=LIMIT\b)", "", sql, flags=re.IGNORECASE)
    sql = re.sub(r"\bORDER\s+BY\s*(?=;|$)", "", sql, flags=re.IGNORECASE)
    sql = re.sub(r"[ \t]{2,}", " ", sql)
    sql = re.sub(r"\n{3,}", "\n\n", sql)
    return sql.strip()


def _normalize_sql_response_text(sql: str) -> str:
    """Remove wrapper text while preserving the full model response body."""
    if not isinstance(sql, str):
        return ""

    text = sql.strip()
    if not text:
        return ""

    text = re.sub(r"```(?:sql)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"```", "", text).strip()
    text = _PREAMBLE_PATTERNS.sub("", text).strip()
    return text


def _has_output_wrapper_or_trailer(raw_sql: str, cleaned_sql: str) -> bool:
    """Return True when the response contains text beyond the SQL statement."""
    raw = str(raw_sql or "").strip()
    cleaned = str(cleaned_sql or "").strip()
    if not raw or not cleaned:
        return False
    if "```" in raw:
        return True
    if not re.match(r"^\s*SELECT\b", raw, re.IGNORECASE):
        return True
    if raw == cleaned:
        return False
    return raw.rstrip("; \t\r\n") != cleaned.rstrip("; \t\r\n")


def clean_sql_response(sql: str) -> str:
    """
    Clean a raw SQL-like response into a single executable SELECT statement.

    This removes markdown fences, labels, leading explanations, and trailing prose.
    """
    if not isinstance(sql, str):
        return ""

    text = _normalize_sql_response_text(sql)
    if not text:
        return ""

    select_match = re.search(r"\bSELECT\b", text, re.IGNORECASE)
    if not select_match:
        return text.strip()

    text = text[select_match.start():]
    sql_lines: list[str] = []
    sql_line_re = re.compile(
        r"^\s*("
        r"SELECT|FROM|WHERE|JOIN|LEFT|RIGHT|INNER|OUTER|CROSS|FULL|"
        r"ON|GROUP\s+BY|ORDER\s+BY|HAVING|LIMIT|OFFSET|UNION|WITH|"
        r"AS|AND|OR|NOT|IN|EXISTS|BETWEEN|LIKE|IS\s+NULL|IS\s+NOT|"
        r"CASE|WHEN|THEN|ELSE|END|COUNT|SUM|AVG|MAX|MIN|DISTINCT|"
        r"COALESCE|IFNULL|DATE_FORMAT|DATE|YEAR|MONTH|DAY|NOW|CURDATE|"
        r"\(|\)|\w+\s*[=<>!]|\w+\.\w+|`\w"
        r")",
        re.IGNORECASE,
    )

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            if sql_lines:
                break
            continue

        if ";" in stripped:
            sql_lines.append(stripped[: stripped.index(";") + 1])
            break

        if sql_lines and not sql_line_re.match(line):
            break

        sql_lines.append(line)

    cleaned = "\n".join(sql_lines).strip() or text.strip()
    return _repair_order_by(cleaned)


def _strip_string_literals(sql: str) -> str:
    """Mask string literals so identifier parsing ignores their contents."""
    return re.sub(r"'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"", "''", sql)


def _is_identifier(token: str) -> bool:
    normalized = _normalize_identifier(token)
    return bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", normalized))


def _normalize_identifier(token: str) -> str:
    text = str(token or "")
    if text.startswith("`") and text.endswith("`"):
        return text[1:-1].replace("``", "`")
    return text


def _tokenize_sql(sql: str) -> list[str]:
    masked = _strip_string_literals(sql)
    return [token for token in _TOKEN_RE.findall(masked) if token and not token.isspace()]


def _extract_table_references(sql: str, knowledge_base: dict[str, Any]) -> tuple[bool, str, list[str], dict[str, str]]:
    """Extract referenced tables and aliases from FROM/JOIN clauses."""
    kb_tables = {str(name).lower(): str(name) for name in (knowledge_base or {}).keys()}
    tokens = _tokenize_sql(sql)
    referenced_tables: list[str] = []
    alias_to_table: dict[str, str] = {}

    idx = 0
    while idx < len(tokens):
        token_upper = tokens[idx].upper()
        if token_upper not in {"FROM", "JOIN"}:
            idx += 1
            continue

        keyword = token_upper
        cursor = idx + 1
        if cursor >= len(tokens):
            return False, f"SQL is missing a table name after {keyword}.", [], {}

        next_token = tokens[cursor]
        next_upper = next_token.upper()
        if next_upper in _INVALID_TABLE_START_KEYWORDS or next_token in {",", ";", ")", "."}:
            found = next_upper if next_upper in _INVALID_TABLE_START_KEYWORDS else next_token
            return False, f"SQL is missing a valid table name after {keyword}. Found '{found}' instead.", [], {}
        if next_token == "(":
            return False, f"Subqueries are not allowed after {keyword} in generated SQL.", [], {}
        if not _is_identifier(next_token):
            return False, f"SQL has an invalid table reference after {keyword}: '{next_token}'.", [], {}
        
        # Partial SQL guard: reject FROM LIMIT, FROM..., missing FROM table
        if next_upper == "LIMIT":
            return False, f"SQL has invalid FROM clause: FROM LIMIT. This is incomplete SQL.", [], {}
        if next_token == "...":
            return False, f"SQL has incomplete FROM clause with ellipsis placeholder.", [], {}

        table_name = _normalize_identifier(next_token)
        cursor += 1
        if cursor + 1 < len(tokens) and tokens[cursor] == "." and _is_identifier(tokens[cursor + 1]):
            table_name = _normalize_identifier(tokens[cursor + 1])
            cursor += 2

        if table_name.lower() not in kb_tables:
            return (
                False,
                f"Table '{table_name}' does not exist in the knowledge base. "
                f"Available tables: {', '.join(sorted(knowledge_base.keys()))}",
                [],
                {},
            )

        canonical_table = kb_tables[table_name.lower()]
        referenced_tables.append(canonical_table)
        alias_to_table[canonical_table.lower()] = canonical_table

        if cursor < len(tokens):
            alias_token = tokens[cursor]
            alias_upper = alias_token.upper()
            if alias_upper == "AS":
                cursor += 1
                if cursor >= len(tokens) or not _is_identifier(tokens[cursor]):
                    return False, f"SQL has an invalid alias after table '{canonical_table}'.", [], {}
                alias_to_table[_normalize_identifier(tokens[cursor]).lower()] = canonical_table
                cursor += 1
            elif _is_identifier(alias_token) and alias_upper not in _CLAUSE_KEYWORDS:
                alias_to_table[_normalize_identifier(alias_token).lower()] = canonical_table
                cursor += 1

        idx = cursor

    if not referenced_tables:
        return False, "SQL is missing a valid table name after FROM or JOIN.", [], {}

    return True, "Table references are valid.", referenced_tables, alias_to_table


def _validate_join_conditions(sql: str) -> tuple[bool, str]:
    """Ensure explicit JOIN clauses have ON/USING conditions."""
    tokens = _tokenize_sql(sql)
    clause_boundaries = {"WHERE", "GROUP", "ORDER", "HAVING", "LIMIT", "UNION", ";"}

    idx = 0
    while idx < len(tokens):
        if tokens[idx].upper() != "JOIN":
            idx += 1
            continue

        requires_condition = True
        prev_upper = tokens[idx - 1].upper() if idx > 0 else ""
        if prev_upper in {"CROSS", "NATURAL"}:
            requires_condition = False

        cursor = idx + 1
        while cursor < len(tokens) and tokens[cursor] not in clause_boundaries and tokens[cursor].upper() not in {"JOIN", "LEFT", "RIGHT", "INNER", "OUTER", "FULL", "CROSS", "NATURAL"}:
            if tokens[cursor].upper() in {"ON", "USING"}:
                break
            cursor += 1

        if not requires_condition:
            idx += 1
            continue

        if cursor >= len(tokens) or tokens[cursor].upper() not in {"ON", "USING"}:
            return False, "SQL has a JOIN without an ON or USING condition."

        condition_keyword = tokens[cursor].upper()
        cursor += 1
        if cursor >= len(tokens) or tokens[cursor].upper() in clause_boundaries or tokens[cursor] in {",", ";", ")"}:
            return False, f"SQL has an incomplete {condition_keyword} clause in a JOIN."

        idx += 1

    return True, "JOIN conditions are valid."


def _table_columns(knowledge_base: dict[str, Any], table_name: str) -> set[str]:
    return {
        str(col.get("name", "")).lower()
        for col in knowledge_base.get(table_name, {}).get("columns", [])
    }


def _resolve_table_for_alias(alias: str, alias_to_table: dict[str, str]) -> str | None:
    return alias_to_table.get(str(alias or "").lower())


def _validate_join_predicates(
    sql: str,
    knowledge_base: dict[str, Any],
    alias_to_table: dict[str, str],
) -> tuple[bool, str]:
    """Validate JOIN ON/USING predicates against runtime schema metadata."""
    tokens = _tokenize_sql(sql)
    clause_boundaries = {"WHERE", "GROUP", "ORDER", "HAVING", "LIMIT", "UNION", ";"}
    join_boundaries = {"JOIN", "LEFT", "RIGHT", "INNER", "OUTER", "FULL", "CROSS", "NATURAL"}
    known_aliases = set(alias_to_table.keys())

    idx = 0
    while idx < len(tokens):
        if tokens[idx].upper() != "JOIN":
            idx += 1
            continue

        prev_upper = tokens[idx - 1].upper() if idx > 0 else ""
        if prev_upper in {"CROSS", "NATURAL"}:
            idx += 1
            continue

        cursor = idx + 1
        while cursor < len(tokens):
            cursor_upper = tokens[cursor].upper()
            if cursor_upper in {"ON", "USING"}:
                break
            if cursor_upper in clause_boundaries or cursor_upper in join_boundaries:
                break
            cursor += 1

        if cursor >= len(tokens) or tokens[cursor].upper() not in {"ON", "USING"}:
            idx += 1
            continue

        keyword = tokens[cursor].upper()
        condition_start = cursor + 1
        condition_end = condition_start
        while condition_end < len(tokens):
            upper = tokens[condition_end].upper()
            if upper in clause_boundaries or upper in join_boundaries:
                break
            condition_end += 1
        condition_tokens = tokens[condition_start:condition_end]

        if keyword == "USING":
            using_columns = [
                _normalize_identifier(token).lower()
                for token in condition_tokens
                if _is_identifier(token) and token.upper() not in _SQL_KEYWORDS
            ]
            if not using_columns:
                return False, "SQL has a JOIN USING clause without a column name."
            for column in using_columns:
                owner_count = sum(
                    1
                    for table_name in set(alias_to_table.values())
                    if column in _table_columns(knowledge_base, table_name)
                )
                if owner_count < 2:
                    return False, f"JOIN USING column '{column}' does not exist on both joined tables."
            idx = condition_end
            continue

        saw_valid_predicate = False
        pos = 0
        while pos + 6 < len(condition_tokens):
            left_alias = condition_tokens[pos]
            left_dot = condition_tokens[pos + 1]
            left_column = condition_tokens[pos + 2]
            operator = condition_tokens[pos + 3]
            right_alias = condition_tokens[pos + 4]
            right_dot = condition_tokens[pos + 5]
            right_column = condition_tokens[pos + 6]
            if (
                _is_identifier(left_alias)
                and left_dot == "."
                and _is_identifier(left_column)
                and operator == "="
                and _is_identifier(right_alias)
                and right_dot == "."
                and _is_identifier(right_column)
            ):
                left_table = _resolve_table_for_alias(_normalize_identifier(left_alias), alias_to_table)
                right_table = _resolve_table_for_alias(_normalize_identifier(right_alias), alias_to_table)
                left_column_name = _normalize_identifier(left_column)
                right_column_name = _normalize_identifier(right_column)
                if not left_table or _normalize_identifier(left_alias).lower() not in known_aliases:
                    return False, f"Alias or table '{_normalize_identifier(left_alias)}' is not defined in JOIN predicate."
                if not right_table or _normalize_identifier(right_alias).lower() not in known_aliases:
                    return False, f"Alias or table '{_normalize_identifier(right_alias)}' is not defined in JOIN predicate."
                if left_column_name.lower() not in _table_columns(knowledge_base, left_table):
                    return False, f"JOIN predicate column '{_normalize_identifier(left_alias)}.{left_column_name}' does not exist in table '{left_table}'."
                if right_column_name.lower() not in _table_columns(knowledge_base, right_table):
                    return False, f"JOIN predicate column '{_normalize_identifier(right_alias)}.{right_column_name}' does not exist in table '{right_table}'."
                saw_valid_predicate = True
                pos += 7
                continue
            pos += 1

        if not saw_valid_predicate:
            return False, "JOIN ON clause must contain a valid alias.column = alias.column predicate."

        idx = condition_end

    return True, "JOIN predicates are valid."


def _validate_qualified_columns(sql: str, knowledge_base: dict[str, Any], alias_to_table: dict[str, str]) -> tuple[bool, str]:
    """Validate qualified alias.column references against the schema."""
    if not knowledge_base:
        return True, "No schema metadata available."

    for alias, column in re.findall(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\.\s*([A-Za-z_][A-Za-z0-9_]*)\b", _strip_string_literals(sql)):
        alias_lower = alias.lower()
        if alias.upper() in _SQL_FUNCTIONS:
            continue
        if alias_lower not in alias_to_table:
            return False, f"Alias or table '{alias}' is not defined in FROM/JOIN clauses."

        table_name = alias_to_table[alias_lower]
        known_columns = {
            str(col.get("name", "")).lower()
            for col in knowledge_base.get(table_name, {}).get("columns", [])
        }
        if column.lower() not in known_columns:
            return False, f"Column '{alias}.{column}' does not exist in table '{table_name}'."

    return True, "Qualified columns are valid."


def _validate_single_table_columns(sql: str, knowledge_base: dict[str, Any], referenced_tables: list[str]) -> tuple[bool, str]:
    """Validate unqualified columns when exactly one table is referenced."""
    if len(referenced_tables) != 1:
        return True, "Skipping single-table column validation."

    table_name = referenced_tables[0]
    known_columns = {
        str(col.get("name", "")).lower()
        for col in knowledge_base.get(table_name, {}).get("columns", [])
    }
    if not known_columns:
        return True, "No column metadata available."

    select_match = re.search(r"\bSELECT\s+(.*?)\bFROM\b", sql, re.IGNORECASE | re.DOTALL)
    if not select_match:
        return True, "No SELECT list found."

    select_segment = _strip_string_literals(select_match.group(1))
    alias_tokens = {
        match.group(1).lower()
        for match in re.finditer(r"\bAS\s+([A-Za-z_][A-Za-z0-9_]*)\b", select_segment, re.IGNORECASE)
    }
    tokens = _tokenize_sql(select_segment)

    for index, token in enumerate(tokens):
        if not _is_identifier(token):
            continue
        token_upper = token.upper()
        token_lower = _normalize_identifier(token).lower()
        previous_token = tokens[index - 1] if index > 0 else ""
        next_token = tokens[index + 1] if index + 1 < len(tokens) else ""
        if token_upper in _SQL_KEYWORDS or token_upper in _SQL_FUNCTIONS:
            continue
        if previous_token == "." or next_token == ".":
            continue
        if previous_token.upper() == "AS":
            continue
        if next_token == "(":
            continue
        if token_lower == table_name.lower():
            continue
        if token_lower in known_columns or token_lower in alias_tokens:
            continue
        return False, f"Column '{_normalize_identifier(token)}' does not exist in table '{table_name}'."

    return True, "Single-table columns are valid."


def _column_owner_matches(identifier: str, knowledge_base: dict[str, Any], referenced_tables: list[str]) -> list[str]:
    identifier_lower = str(identifier or "").lower()
    matches = []
    for table_name in referenced_tables:
        known_columns = {
            str(col.get("name", "")).lower()
            for col in knowledge_base.get(table_name, {}).get("columns", [])
        }
        if identifier_lower in known_columns:
            matches.append(table_name)
    return matches


def _collect_select_aliases(select_segment: str) -> set[str]:
    aliases = {
        match.group(1).lower()
        for match in re.finditer(r"\bAS\s+([A-Za-z_][A-Za-z0-9_]*)\b", select_segment, re.IGNORECASE)
    }
    return aliases


def _extract_clause_segments(sql: str) -> list[tuple[str, str]]:
    segments: list[tuple[str, str]] = []
    patterns = [
        ("SELECT", r"\bSELECT\s+(.*?)\bFROM\b"),
        ("WHERE", r"\bWHERE\s+(.*?)(?=\bGROUP\s+BY\b|\bORDER\s+BY\b|\bHAVING\b|\bLIMIT\b|\bUNION\b|;|$)"),
        ("GROUP BY", r"\bGROUP\s+BY\s+(.*?)(?=\bHAVING\b|\bORDER\s+BY\b|\bLIMIT\b|\bUNION\b|;|$)"),
        ("HAVING", r"\bHAVING\s+(.*?)(?=\bORDER\s+BY\b|\bLIMIT\b|\bUNION\b|;|$)"),
        ("ORDER BY", r"\bORDER\s+BY\s+(.*?)(?=\bLIMIT\b|\bUNION\b|;|$)"),
    ]
    for label, pattern in patterns:
        match = re.search(pattern, sql, re.IGNORECASE | re.DOTALL)
        if match:
            segments.append((label, match.group(1)))
    return segments


def _validate_unqualified_columns(
    sql: str,
    knowledge_base: dict[str, Any],
    referenced_tables: list[str],
    alias_to_table: dict[str, str],
) -> tuple[bool, str]:
    """Validate unqualified identifiers in clauses against schema metadata."""
    if not referenced_tables:
        return True, "No referenced tables to validate."

    masked_sql = _strip_string_literals(sql)
    select_match = re.search(r"\bSELECT\s+(.*?)\bFROM\b", masked_sql, re.IGNORECASE | re.DOTALL)
    output_aliases = _collect_select_aliases(select_match.group(1) if select_match else "")
    table_aliases = set(alias_to_table.keys())
    table_names = {table.lower() for table in referenced_tables}

    for clause_name, segment in _extract_clause_segments(masked_sql):
        tokens = _tokenize_sql(segment)
        for index, token in enumerate(tokens):
            if not _is_identifier(token):
                continue

            token_upper = token.upper()
            token_lower = _normalize_identifier(token).lower()
            previous_token = tokens[index - 1] if index > 0 else ""
            next_token = tokens[index + 1] if index + 1 < len(tokens) else ""

            if token_upper in _SQL_KEYWORDS or token_upper in _SQL_FUNCTIONS:
                continue
            if previous_token == "." or next_token == ".":
                continue
            if previous_token.upper() == "AS":
                continue
            if next_token == "(":
                continue
            if token_lower in output_aliases or token_lower in table_aliases or token_lower in table_names:
                continue

            matches = _column_owner_matches(token, knowledge_base, referenced_tables)
            if not matches:
                return False, f"Column '{token}' in {clause_name} does not exist in referenced tables: {', '.join(referenced_tables)}."
            if len(matches) > 1:
                return False, f"Column '{token}' in {clause_name} is ambiguous across tables: {', '.join(matches)}."

    return True, "Unqualified columns are valid."


def _validate_select_list(sql: str) -> tuple[bool, str]:
    match = re.search(r"\bSELECT\s+(.*?)\bFROM\b", sql, re.IGNORECASE | re.DOTALL)
    if not match:
        return False, "SQL SELECT list is missing or incomplete."
    select_segment = match.group(1).strip()
    if not select_segment:
        return False, "SQL SELECT list is empty."
    if select_segment.endswith(","):
        return False, "SQL SELECT list has a dangling comma."
    if not _split_select_expressions(select_segment):
        return False, "SQL SELECT list does not contain a valid expression."
    return True, "SELECT list is valid."


def _split_select_expressions(select_segment: str) -> list[str]:
    expressions: list[str] = []
    current: list[str] = []
    depth = 0
    for char in select_segment:
        if char == "(":
            depth += 1
        elif char == ")" and depth > 0:
            depth -= 1
        if char == "," and depth == 0:
            expression = "".join(current).strip()
            if expression:
                expressions.append(expression)
            current = []
            continue
        current.append(char)
    expression = "".join(current).strip()
    if expression:
        expressions.append(expression)
    return expressions


def _expression_alias(expression: str) -> str | None:
    match = re.search(r"\bAS\s+([A-Za-z_][A-Za-z0-9_]*)\s*$", expression, re.IGNORECASE)
    if match:
        return match.group(1)
    return None


def _expression_without_alias(expression: str) -> str:
    return re.sub(r"\bAS\s+[A-Za-z_][A-Za-z0-9_]*\s*$", "", expression, flags=re.IGNORECASE).strip()


def _normalize_expression(expression: str) -> str:
    return re.sub(r"\s+", "", str(expression or "").strip().lower())


def _validate_group_by_for_aggregates(sql: str) -> tuple[bool, str]:
    select_match = re.search(r"\bSELECT\s+(.*?)\bFROM\b", sql, re.IGNORECASE | re.DOTALL)
    if not select_match:
        return True, "No SELECT list found."

    select_segment = _strip_string_literals(select_match.group(1))
    expressions = _split_select_expressions(select_segment)
    has_aggregate = any(re.search(r"\b(?:COUNT|SUM|AVG|MIN|MAX)\s*\(", expression, re.IGNORECASE) for expression in expressions)
    if not has_aggregate:
        return True, "No aggregate GROUP BY validation needed."

    non_aggregate_expressions = [
        expression
        for expression in expressions
        if not re.search(r"\b(?:COUNT|SUM|AVG|MIN|MAX)\s*\(", expression, re.IGNORECASE)
        and _expression_without_alias(expression) not in {"*", ""}
    ]
    if not non_aggregate_expressions:
        return True, "Aggregate query has no non-aggregate SELECT expressions."

    group_match = re.search(r"\bGROUP\s+BY\s+(.*?)(?=\bHAVING\b|\bORDER\s+BY\b|\bLIMIT\b|\bUNION\b|;|$)", sql, re.IGNORECASE | re.DOTALL)
    if not group_match:
        return False, "Aggregate queries with non-aggregate SELECT columns must include GROUP BY."

    grouped_expressions = {
        _normalize_expression(expression)
        for expression in _split_select_expressions(_strip_string_literals(group_match.group(1)))
    }

    for expression in non_aggregate_expressions:
        expression_without_alias = _expression_without_alias(expression)
        alias = _expression_alias(expression)
        normalized_expression = _normalize_expression(expression_without_alias)
        normalized_alias = _normalize_expression(alias or "")
        if normalized_expression in grouped_expressions or (normalized_alias and normalized_alias in grouped_expressions):
            continue
        return False, f"GROUP BY is missing non-aggregate SELECT expression '{expression_without_alias}'."

    return True, "GROUP BY matches non-aggregate SELECT expressions."


def _has_dangling_comma(sql: str) -> bool:
    return bool(re.search(r",\s*(FROM|WHERE|GROUP\s+BY|ORDER\s+BY|HAVING|LIMIT|JOIN|;|$)", sql, re.IGNORECASE))


def _has_partial_clause(sql: str) -> tuple[bool, str]:
    if re.search(r"\b(?:FROM|JOIN|WHERE|ON|GROUP\s+BY|ORDER\s+BY|HAVING)\s*;?\s*$", sql, re.IGNORECASE):
        return True, "SQL ends with an incomplete clause."
    if re.search(r"(=|<|>|<=|>=|<>|!=|AND|OR)\s*;?\s*$", sql, re.IGNORECASE):
        return True, "SQL ends with an incomplete expression."
    return False, ""


def _looks_like_sql_trailer(text: str) -> bool:
    """Return True when trailing content appears to be another SQL statement."""
    trailer = str(text or "").strip()
    if not trailer:
        return False
    return bool(
        re.search(
            r"\b("
            r"SELECT|WITH|UNION|FROM|JOIN|WHERE|GROUP|ORDER|HAVING|LIMIT|"
            r"DROP|DELETE|UPDATE|INSERT|ALTER|TRUNCATE|CREATE|RECREATE|REPLACE|EXEC|EXECUTE|GRANT|REVOKE"
            r")\b",
            trailer,
            re.IGNORECASE,
        )
    )


def _contains_sql_comment(sql: str) -> bool:
    """
    Return True when SQL contains MySQL comment syntax.

    The CLI is intentionally conservative here. MySQL supports `#`, `--`,
    and `/* ... */` comments, including executable version comments such as
    `/*! ... */`, so generated SQL with comments is rejected outright.
    """
    return (
        "#" in sql
        or "--" in sql
        or "/*" in sql
        or "*/" in sql
    )


def validate_sql(sql: str) -> tuple[bool, str]:
    """
    Validate a SQL string for safety before it is executed against the database.

    Checks are performed in the following order:

    1. **Type check** — Returns ``(False, "SQL must be a string.")``
       if *sql* is not a string type.

    2. **Empty / None check** — Returns ``(False, "SQL query is empty or missing.")``
       if *sql* is ``None``, the empty string, or contains only whitespace characters.

    3. **SELECT prefix check** — Returns ``(False, "Only SELECT queries are allowed.")``
       if the trimmed query does not begin with the token ``SELECT``
       (comparison is case-insensitive).

    4. **Forbidden-keyword check** — Scans the entire query for any of the keywords
       ``DROP``, ``DELETE``, ``UPDATE``, ``INSERT``, ``ALTER``, ``TRUNCATE``,
       ``CREATE``, or ``REPLACE`` using ``\\b`` word-boundary anchors so that column
       names that merely *contain* one of these substrings are not flagged.
       Returns ``(False, "Dangerous SQL command detected: <keyword>")`` naming the
       first match found.

    5. **Multiple-statement check** — Returns
       ``(False, "Multiple SQL statements are not allowed.")`` if a semicolon is
       found anywhere other than the very end of the trimmed query.

    6. **Pass** — Returns ``(True, "SQL is safe")`` when all checks pass.

    Args:
        sql: The SQL string to validate.  May be ``None``.

    Returns:
        A ``(valid: bool, message: str)`` tuple where *valid* is ``True`` only
        when every safety check passes.
    """
    # Check 1: type check
    if not isinstance(sql, str):
        return False, "SQL must be a string."

    # Check 2: empty / None / whitespace-only
    if not sql or not sql.strip():
        return False, "SQL query is empty or missing."

    normalized = _normalize_sql_response_text(sql)
    cleaned = clean_sql_response(sql)
    if not cleaned:
        return False, "SQL query is empty or missing."
    stripped = cleaned.strip()

    # Check 3: must start with SELECT (case-insensitive)
    if not stripped.upper().startswith("SELECT"):
        return False, "Only SELECT queries are allowed."

    # Check 4: reject comments. Comments can hide or split injected SQL.
    if _contains_sql_comment(normalized):
        return False, "SQL comments are not allowed."

    # Check 5: forbidden keywords as whole words (case-insensitive, word boundaries)
    upper_sql = normalized.upper()
    for keyword in _FORBIDDEN_KEYWORDS:
        pattern = r"\b" + keyword + r"\b"
        if re.search(pattern, upper_sql):
            return False, f"Dangerous SQL command detected: {keyword}"

    # Check 6: additional SQL after the extracted statement is not allowed.
    trailing = normalized[len(stripped):].strip() if normalized.startswith(stripped) else ""
    inner = stripped[:-1] if stripped.endswith(";") else stripped
    if ";" in inner or _looks_like_sql_trailer(trailing):
        return False, "Multiple SQL statements are not allowed."

    if _has_output_wrapper_or_trailer(sql, stripped):
        return False, "SQL output contains explanation text, markdown, or non-SQL content outside the SELECT statement."

    return True, "SQL is safe"


def add_limit_if_missing(sql: str, limit: int = 50) -> str:
    """
    Append a ``LIMIT`` clause to a SQL string when one is not already present.

    The function performs a **case-insensitive** search for an existing ``LIMIT``
    token.  If one is found the SQL is returned **unchanged**.  If one is *not*
    found, ``LIMIT <limit>`` is inserted immediately before any trailing
    semicolon (and any surrounding whitespace adjacent to that semicolon is
    preserved as-is).

    Examples::

        >>> add_limit_if_missing("SELECT * FROM users")
        'SELECT * FROM users LIMIT 50'

        >>> add_limit_if_missing("SELECT * FROM users;")
        'SELECT * FROM users LIMIT 50;'

        >>> add_limit_if_missing("SELECT * FROM users LIMIT 10")
        'SELECT * FROM users LIMIT 10'

        >>> add_limit_if_missing("SELECT * FROM users", limit=100)
        'SELECT * FROM users LIMIT 100'

    Args:
        sql:   The SQL string to process.
        limit: The row limit to append when no ``LIMIT`` clause is present.
               Defaults to ``50``.

    Returns:
        The original *sql* string if a ``LIMIT`` clause already exists, or the
        *sql* string with ``LIMIT <limit>`` appended before any trailing
        semicolon.
    """
    # If a LIMIT clause is already present (any case), return unchanged.
    if re.search(r"\bLIMIT\b", sql, re.IGNORECASE):
        return sql

    # Append LIMIT before a trailing semicolon, if one exists.
    # We match optional whitespace + semicolon at the very end of the string.
    trailing_semi_pattern = r"(\s*;)\s*$"
    match = re.search(trailing_semi_pattern, sql)
    if match:
        # Insert before the semicolon (and its preceding whitespace)
        insert_pos = match.start()
        return sql[:insert_pos] + f" LIMIT {limit}" + sql[insert_pos:]

    # No trailing semicolon — simply append.
    return sql + f" LIMIT {limit}"


def extract_requested_limit(text: str) -> int | None:
    """
    Return an explicit user-requested row limit from natural language text.

    Only numeric row-count requests are recognized. This intentionally avoids
    inventing a default LIMIT for questions that did not ask for one.
    """
    if not isinstance(text, str):
        return None

    match = re.search(
        r"\b(?:top|first|limit|show|get|return|fetch)\s+(\d+)\b"
        r"|\b(\d+)\s+(?:rows?|records?|results?|items?)\b",
        text,
        re.IGNORECASE,
    )
    if not match:
        return None
    return int(match.group(1) or match.group(2))


def validate_sql_structure(sql: str, knowledge_base: dict) -> tuple[bool, str]:
    """
    Validate that an AI-generated SQL string has a correct executable structure.

    This is separate from validate_sql() (which checks safety rules).
    This function checks whether the SQL is actually executable MySQL SELECT
    syntax, catching common AI failure modes like:
      - Natural language preamble left in the output
      - Missing FROM clause
      - Referencing tables that don't exist in the knowledge base
      - Markdown fences or explanation text embedded in the SQL

    Checks performed (in order)
    ----------------------------
    1. Empty string.
    2. Must start with SELECT (case-insensitive).
    3. Must contain FROM.
    4. Must NOT contain natural-language phrases that indicate the AI
       returned an explanation instead of (or alongside) SQL.
    5. Must NOT contain markdown fences.
    6. All table names referenced after FROM / JOIN must exist in the
       knowledge base (only checks bare identifiers, not subqueries).

    Args:
        sql:            The SQL string to check.
        knowledge_base: Dict loaded from semantic/knowledge_base.json.
                        Used to verify table names are real.

    Returns:
        (True,  "SQL structure is valid")      — all checks passed
        (False, <reason>)                      — first failing check
    """
    cleaned = clean_sql_response(sql)
    if not cleaned:
        return False, "SQL is empty."

    stripped = cleaned.strip()

    # Check 1: must start with SELECT.
    if not stripped.upper().startswith("SELECT"):
        return False, "SQL does not start with SELECT."

    if _has_output_wrapper_or_trailer(sql, stripped):
        return False, "SQL output contains explanation text, markdown, or non-SQL content outside the SELECT statement."

    # Check 2: comments are not allowed in generated SQL.
    if _contains_sql_comment(stripped):
        return False, "SQL contains comments."

    upper = stripped.upper()

    # Check 4: must contain FROM.
    if "FROM" not in upper:
        return False, "SQL is missing a FROM clause."

    # Check 5: no markdown fences.
    if "```" in stripped:
        return False, "SQL contains markdown code fences."

    # Check 6: ORDER BY must name an expression before LIMIT/end.
    if re.search(r"\bORDER\s+BY\s*(?:LIMIT\b|;|$)", stripped, re.IGNORECASE):
        return False, "SQL has an incomplete ORDER BY clause."

    select_ok, select_reason = _validate_select_list(stripped)
    if not select_ok:
        return False, select_reason

    # Check 7: verify table names after FROM and JOIN exist in knowledge base.
    if knowledge_base:
        table_ok, table_reason, referenced_tables, alias_to_table = _extract_table_references(stripped, knowledge_base)
        if not table_ok:
            return False, table_reason

        join_ok, join_reason = _validate_join_conditions(stripped)
        if not join_ok:
            return False, join_reason

        predicate_ok, predicate_reason = _validate_join_predicates(stripped, knowledge_base, alias_to_table)
        if not predicate_ok:
            return False, predicate_reason

        if re.search(r"\bSELECT\s+(?:\w+\.)?\*", stripped, re.IGNORECASE):
            for table_name in referenced_tables:
                row_count = knowledge_base.get(table_name, {}).get("row_count")
                if isinstance(row_count, int) and row_count > 1000:
                    return (
                        False,
                        f"SELECT * is not allowed on large table '{table_name}' ({row_count} rows).",
                    )

        qualified_ok, qualified_reason = _validate_qualified_columns(stripped, knowledge_base, alias_to_table)
        if not qualified_ok:
            return False, qualified_reason

        single_ok, single_reason = _validate_single_table_columns(stripped, knowledge_base, referenced_tables)
        if not single_ok:
            return False, single_reason

        unqualified_ok, unqualified_reason = _validate_unqualified_columns(stripped, knowledge_base, referenced_tables, alias_to_table)
        if not unqualified_ok:
            return False, unqualified_reason

    group_ok, group_reason = _validate_group_by_for_aggregates(stripped)
    if not group_ok:
        return False, group_reason

    if _has_dangling_comma(stripped):
        return False, "SQL contains a dangling comma before the next clause."

    is_partial, partial_reason = _has_partial_clause(stripped)
    if is_partial:
        return False, partial_reason

    return True, "SQL structure is valid"
