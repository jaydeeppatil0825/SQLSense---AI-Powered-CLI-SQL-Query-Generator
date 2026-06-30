"""
ai/sql_generator.py
====================
Legacy SQL-generation compatibility boundary.

Runtime AI SQL generation and retry are disabled. The public entry points are
kept temporarily for compatibility and fail closed when called.
"""

from __future__ import annotations

import re

from dotenv import load_dotenv

from sql_pipeline.prompt_builder import build_sql_prompt
from core.ai_backend_service import check_ollama_status as _shared_check_ollama_status
from utils.logger import get_logger
from sql_pipeline.sql_validator import clean_sql_response, sanitize_ai_sql_output


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


def _blocked_route_reason(route_recommendation: str | None) -> str | None:
    route = str(route_recommendation or "").strip().lower()
    if route in {"needs_clarification", "cannot_plan_safely"}:
        return route
    return None


def _normalize_ai_sql_output(raw_response: str) -> str:
    cleaned, safely_extractable, _ = sanitize_ai_sql_output(raw_response)
    if safely_extractable:
        return cleaned
    return str(raw_response or "").strip()


def check_ollama_status(api_url: str | None = None, timeout: int = 5) -> tuple[bool, str]:
    """Return whether the local Ollama server is reachable."""
    return _shared_check_ollama_status(api_url=api_url, timeout=timeout)


def _call_ollama(messages: list[dict], response_format: dict | str | None = None) -> str:
    """Block the legacy runtime Ollama SQL-generation entry point."""
    del messages, response_format
    raise RuntimeError(
        "Runtime AI backend calls are disabled outside KB semantic enrichment."
    )


def _call_ai_backend(*args, **kwargs):
    raise RuntimeError(
        "Runtime AI backend calls are disabled outside KB semantic enrichment."
    )


def generate_sql(*args, **kwargs):
    raise RuntimeError(
        "AI SQL generation is disabled. Runtime SQL must be generated deterministically."
    )


def generate_sql_with_retry(*args, **kwargs):
    raise RuntimeError(
        "AI SQL retry is disabled. Invalid SQL must not be repaired by AI."
    )
