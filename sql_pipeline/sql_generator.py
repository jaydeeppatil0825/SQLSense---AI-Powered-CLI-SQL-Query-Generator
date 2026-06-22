"""
ai/sql_generator.py
====================
Dispatches SQL generation requests to the configured AI backend and returns
a clean, validated SQL string.

This module belongs to the SQL Generation Pipeline. It must use only runtime
schema/context evidence supplied by planning and must not invent tables,
columns, joins, or formulas outside that evidence.
"""

from __future__ import annotations

import re

from dotenv import load_dotenv

from sql_pipeline.prompt_builder import build_sql_prompt
from core.ai_backend_service import call_ai_backend, check_ollama_status as _shared_check_ollama_status
from utils.logger import get_logger
from sql_pipeline.sql_validator import clean_sql_response


load_dotenv()
logger = get_logger()


def _repair_order_by(sql: str) -> str:
    """
    Fix malformed ORDER BY clauses that some models produce.

    Patterns repaired:
    1. "ORDER BY LIMIT n" -> "LIMIT n"
    2. "ORDER BY ;" -> removed
    3. dangling "ORDER BY" -> removed
    """
    sql = re.sub(r"\bORDER\s+BY\s+(?=LIMIT\b)", "", sql, flags=re.IGNORECASE)
    sql = re.sub(r"\bORDER\s+BY\s*(?=;|$)", "", sql, flags=re.IGNORECASE)
    sql = re.sub(r"[ \t]{2,}", " ", sql)
    sql = re.sub(r"\n{3,}", "\n\n", sql)
    return sql.strip()


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


def extract_sql_only(response_text: str) -> str:
    """Extract the first clean SELECT statement from a raw AI response."""
    text = str(response_text or "").strip()
    text = re.sub(r"```(?:sql)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"```", "", text).strip()

    select_match = re.search(r"\bSELECT\b", text, re.IGNORECASE)
    if not select_match:
        return ""

    text = text[select_match.start():]
    sql_lines: list[str] = []
    sql_line_re = re.compile(
        r"^\s*("
        r"SELECT|FROM|WHERE|JOIN|LEFT|RIGHT|INNER|OUTER|CROSS|FULL|"
        r"ON|GROUP\s+BY|ORDER\s+BY|HAVING|LIMIT|OFFSET|UNION|WITH|"
        r"AS|AND|OR|NOT|IN|EXISTS|BETWEEN|LIKE|IS\s+NULL|IS\s+NOT|"
        r"CASE|WHEN|THEN|ELSE|END|"
        r"COUNT|SUM|AVG|MAX|MIN|DISTINCT|COALESCE|IFNULL|IF\s*\(|"
        r"DATE_FORMAT|DATE|YEAR|MONTH|DAY|NOW|CURDATE|"
        r"--|\(|\)|\w+\s*[=<>!]|\w+\.\w+|`\w"
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
            semicolon_pos = stripped.index(";")
            sql_lines.append(stripped[: semicolon_pos + 1])
            break
        if sql_lines and not sql_line_re.match(line):
            break
        sql_lines.append(line)

    return _repair_order_by("\n".join(sql_lines).strip())


def _clean_sql_response(raw: str) -> str:
    """Backward-compatible public alias for SQL cleanup."""
    return clean_sql_response(raw)


def check_ollama_status(api_url: str | None = None, timeout: int = 5) -> tuple[bool, str]:
    """Return whether the local Ollama server is reachable."""
    return _shared_check_ollama_status(api_url=api_url, timeout=timeout)


def _call_ollama(messages: list[dict], response_format: dict | str | None = None) -> str:
    """Backward-compatible local backend wrapper used by existing tests."""
    return call_ai_backend(
        messages,
        backend="local",
        response_format=response_format,
        temperature=0,
        max_tokens=300,
    )


def _call_ai_backend(
    messages: list[dict],
    backend: str,
    response_format: dict | str | None = None,
) -> str:
    """Dispatch messages to the chosen backend and return the raw response."""
    return call_ai_backend(
        messages,
        backend=backend,
        response_format=response_format,
        temperature=0,
        max_tokens=2048,
    )


def generate_sql(
    user_question: str,
    knowledge_base: dict,
    backend: str | None = None,
    normalized_question: str | None = None,
    intent: dict | None = None,
    retrieved_context: dict | None = None,
    query_plan: dict | None = None,
    selected_tables: list[dict] | None = None,
    selected_columns: list[dict] | None = None,
    measure_candidates: list[dict] | None = None,
    dimension_candidates: list[dict] | None = None,
    filter_candidates: list[dict] | None = None,
    business_glossary: dict | None = None,
    join_paths: list[dict] | None = None,
    formula_evidence: list[dict] | None = None,
    evidence_sources: list[str] | None = None,
    route_recommendation: str | None = None,
) -> str:
    """Generate a SQL SELECT statement from the provided runtime context."""
    messages = build_sql_prompt(
        user_question,
        knowledge_base,
        normalized_question=normalized_question,
        intent=intent,
        retrieved_context=retrieved_context,
        query_plan=query_plan,
        selected_tables=selected_tables,
        selected_columns=selected_columns,
        measure_candidates=measure_candidates,
        dimension_candidates=dimension_candidates,
        filter_candidates=filter_candidates,
        business_glossary=business_glossary,
        join_paths=join_paths,
        formula_evidence=formula_evidence,
        evidence_sources=evidence_sources,
        route_recommendation=route_recommendation,
    )
    raw_response = _call_ai_backend(messages, backend or "local")
    return _clean_sql_response(raw_response)


def generate_sql_with_retry(
    user_question: str,
    knowledge_base: dict,
    backend: str,
    first_attempt_sql: str,
    validation_reason: str,
    normalized_question: str | None = None,
    intent: dict | None = None,
    retrieved_context: dict | None = None,
    query_plan: dict | None = None,
    selected_tables: list[dict] | None = None,
    selected_columns: list[dict] | None = None,
    measure_candidates: list[dict] | None = None,
    dimension_candidates: list[dict] | None = None,
    filter_candidates: list[dict] | None = None,
    business_glossary: dict | None = None,
    validation_context: dict | None = None,
    join_paths: list[dict] | None = None,
    formula_evidence: list[dict] | None = None,
    evidence_sources: list[str] | None = None,
    route_recommendation: str | None = None,
) -> str:
    """Retry AI SQL generation once after a failed first attempt."""
    base_messages = build_sql_prompt(
        user_question,
        knowledge_base,
        normalized_question=normalized_question,
        intent=intent,
        retrieved_context=retrieved_context,
        query_plan=query_plan,
        selected_tables=selected_tables,
        selected_columns=selected_columns,
        measure_candidates=measure_candidates,
        dimension_candidates=dimension_candidates,
        filter_candidates=filter_candidates,
        business_glossary=business_glossary,
        join_paths=join_paths,
        formula_evidence=formula_evidence,
        evidence_sources=evidence_sources,
        route_recommendation=route_recommendation,
    )

    correction_system = (
        "You are correcting a previously invalid MySQL SELECT statement. "
        "Follow the structured plan, selected tables, selected columns, glossary, and relationships below exactly. "
        "Return ONLY one corrected executable SELECT statement. "
        "No explanation. No markdown. No comments. No extra text.\n\n"
        f"{base_messages[0]['content']}"
    )

    selected_column_entries = validation_context.get("selected_columns", []) if validation_context else []
    join_conditions = validation_context.get("join_conditions", []) if validation_context else []
    join_skeletons = validation_context.get("join_skeletons", []) if validation_context else []
    measure_entries = validation_context.get("measure_candidates", []) if validation_context else []
    dimension_entries = validation_context.get("dimension_candidates", []) if validation_context else []
    filter_entries = validation_context.get("filter_candidates", []) if validation_context else []
    formula_entries = validation_context.get("formula_evidence", formula_evidence or []) if validation_context else (formula_evidence or [])
    source_entries = validation_context.get("evidence_sources", evidence_sources or []) if validation_context else (evidence_sources or [])

    correction_user = (
        f"Original question: {user_question}\n\n"
        f"Rejected SQL:\n{first_attempt_sql}\n\n"
        f"Validation failure: {validation_reason}\n\n"
        f"Runtime schema and retrieval context:\n{validation_context or {}}\n\n"
        "Required tables: " + ", ".join([t.get("table", "") for t in (selected_tables or [])]) + "\n"
        "Required columns: " + ", ".join([f"{c.get('table', '')}.{c.get('column', '')}" for c in selected_column_entries]) + "\n"
        "Measure candidates: " + ", ".join([f"{c.get('table', '')}.{c.get('column', '')}" for c in measure_entries]) + "\n"
        "Dimension candidates: " + ", ".join([f"{c.get('table', '')}.{c.get('column', '')}" for c in dimension_entries]) + "\n"
        "Filter candidates: " + ", ".join([f"{c.get('table', '')}.{c.get('column', '')}" for c in filter_entries if c.get('table') and c.get('column')]) + "\n"
        "Relationship/join paths: " + str(join_paths or []) + "\n"
        "Join predicates to use: " + ", ".join(join_conditions) + "\n"
        "FROM/JOIN candidates to use: " + " | ".join(join_skeletons) + "\n\n"
        "Formula evidence: " + str(formula_entries) + "\n"
        "Evidence sources: " + ", ".join(str(entry) for entry in source_entries) + "\n\n"
        "Correct the SQL so it follows the plan, selected tables, selected relationships, glossary context, and safety rules. "
        "Use only allowed tables and columns from the schema context. "
        "If the query needs multiple tables, your SQL must use one of the provided FROM/JOIN candidates as the starting structure. "
        "If no formula evidence is provided, do not invent a derived expression. "
        "Qualify columns with table aliases when more than one table is used. "
        "The first non-whitespace characters must be SELECT. "
        "The SQL must include a valid FROM table name before any WHERE, GROUP BY, ORDER BY, or LIMIT clause. "
        "Every JOIN must include a real table and an ON predicate using existing columns on both sides. "
        "Every non-aggregate selected expression must appear in GROUP BY. "
        "ORDER BY must use a selected alias or valid schema column. "
        "Output complete SQL only, with valid FROM and JOIN clauses, no ellipsis, no placeholder FROM, no incomplete JOIN."
    )

    messages = [
        {"role": "system", "content": correction_system},
        {"role": "user", "content": correction_user},
    ]

    raw_response = _call_ai_backend(messages, backend or "local")
    return _clean_sql_response(raw_response)
