"""
utils/sql_validator.py

Provides SQL safety validation and LIMIT injection utilities.
All functions in this module are stateless and have no external dependencies.
"""

import re

# Forbidden DML/DDL keywords that must never appear in a safe SELECT query.
_FORBIDDEN_KEYWORDS = [
    "DROP",
    "DELETE",
    "UPDATE",
    "INSERT",
    "ALTER",
    "TRUNCATE",
    "CREATE",
    "REPLACE",
]


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

    stripped = sql.strip()

    # Check 3: must start with SELECT (case-insensitive)
    if not stripped.upper().startswith("SELECT"):
        return False, "Only SELECT queries are allowed."

    # Check 4: reject comments. Comments can hide or split injected SQL.
    if _contains_sql_comment(stripped):
        return False, "SQL comments are not allowed."

    # Check 5: forbidden keywords as whole words (case-insensitive, word boundaries)
    upper_sql = stripped.upper()
    for keyword in _FORBIDDEN_KEYWORDS:
        pattern = r"\b" + keyword + r"\b"
        if re.search(pattern, upper_sql):
            return False, f"Dangerous SQL command detected: {keyword}"

    # Check 6: semicolon not at the very end (multiple statements)
    # Strip trailing whitespace for this check; a semicolon is only allowed as
    # the very last non-whitespace character.
    without_trailing = stripped.rstrip()
    # Remove one optional trailing semicolon and check the remainder
    if without_trailing.endswith(";"):
        inner = without_trailing[:-1]
    else:
        inner = without_trailing

    if ";" in inner:
        return False, "Multiple SQL statements are not allowed."

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
    if not sql or not sql.strip():
        return False, "SQL is empty."

    stripped = sql.strip()

    # Check 1: must start with SELECT.
    if not stripped.upper().startswith("SELECT"):
        return False, "SQL does not start with SELECT."

    # Check 2: comments are not allowed in generated SQL.
    if _contains_sql_comment(stripped):
        return False, "SQL contains comments."

    upper = stripped.upper()

    # Check 3: natural-language phrases that indicate the AI returned text.
    # These patterns mean the AI included an explanation rather than clean SQL.
    # Checked before the FROM check so NL preambles are identified correctly.
    nl_patterns = [
        r"\bSQL\s+statement\b",          # "SQL statement to show..."
        r"\bHere\s+is\b",                # "Here is the SQL..."
        r"\bThe\s+query\s+is\b",         # "The query is..."
        r"\bThis\s+query\b",             # "This query shows..."
        r"\bexplanation\b",
        r"\bNote\s*:",                   # "Note: ..."
        r"\bThis\s+will\b",              # "This will return..."
        r"\bThe\s+above\b",
    ]
    for pattern in nl_patterns:
        if re.search(pattern, stripped, re.IGNORECASE):
            return False, f"SQL contains natural language text (pattern: {pattern.strip()})."

    # Check 4: must contain FROM.
    if "FROM" not in upper:
        return False, "SQL is missing a FROM clause."

    # Check 5: no markdown fences.
    if "```" in stripped:
        return False, "SQL contains markdown code fences."

    # Check 6: ORDER BY must name an expression before LIMIT/end.
    if re.search(r"\bORDER\s+BY\s*(?:LIMIT\b|;|$)", stripped, re.IGNORECASE):
        return False, "SQL has an incomplete ORDER BY clause."

    # Check 7: verify table names after FROM and JOIN exist in knowledge base.
    if knowledge_base:
        kb_tables = {t.lower() for t in knowledge_base.keys()}
        # Extract bare table names after FROM / JOIN keywords.
        # Pattern: FROM/JOIN followed by optional schema prefix then table name.
        table_refs = re.findall(
            r"\b(?:FROM|JOIN)\s+(?:`?[\w]+`?\.)?`?(\w+)`?",
            stripped,
            re.IGNORECASE,
        )
        for ref in table_refs:
            # Skip subquery aliases, CTE names, and common SQL keywords.
            skip = {"select", "where", "on", "set", "values", "into"}
            if ref.lower() in skip:
                continue
            if ref.lower() not in kb_tables:
                return (
                    False,
                    f"Table '{ref}' does not exist in the knowledge base. "
                    f"Available tables: {', '.join(sorted(knowledge_base.keys()))}",
                )

    return True, "SQL structure is valid"
