"""
semantic/ai_semantic_enricher.py
=================================
AI-powered enrichment of the knowledge base with business meaning.

This module uses the local Ollama backend to enrich one table at a time.
Small prompts keep local Llama3 responsive and allow partial success when one
table fails or times out.
"""

from __future__ import annotations

import copy
import json
import re

from ai.sql_generator import _call_ai_backend
from semantic.erp_metadata import build_rule_based_business_purpose, sanitize_business_purpose, sanitize_short_text
from utils.logger import get_logger

logger = get_logger()

_LAST_ENRICHMENT_REASON: str | None = None
_LAST_ENRICHED_TABLES: list[str] = []
_LAST_FALLBACK_TABLES: dict[str, str] = {}

_SYSTEM_PROMPT = """You are a database semantics assistant.
Return ONLY compact valid JSON.
Do not include markdown.
Do not include explanations.
Do not invent tables or columns.
Keep every text value very short.
Use at most 2 business terms per column.
Use exactly 1 short business question per table.
Prefer one or two words where possible.
"""

_TABLE_JSON_FORMAT = {
    "type": "object",
    "properties": {
        "d": {"type": "string"},
        "p": {"type": "string"},
        "q": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": 1,
        },
    },
    "required": ["d", "p", "q"],
}

_COLUMN_JSON_FORMAT = {
    "type": "object",
    "properties": {
        "c": {
            "type": "object",
            "additionalProperties": {
                "type": "object",
                "properties": {
                    "d": {"type": "string"},
                    "b": {
                        "type": "array",
                        "items": {"type": "string"},
                        "maxItems": 2,
                    },
                    "m": {"type": "string"},
                    "me": {"type": "boolean"},
                    "di": {"type": "boolean"},
                    "dt": {"type": "boolean"},
                },
                "required": ["d", "b", "m", "me", "di", "dt"],
            },
        },
    },
    "required": ["c"],
}


def _describe_ai_enrichment_failure(exc: Exception, backend: str) -> str:
    """Return a short, non-sensitive reason suitable for CLI/log output."""
    backend_label = "NVIDIA" if backend == "nvidia" else "Local AI"
    exc_text = str(exc).lower()

    if isinstance(exc, json.JSONDecodeError):
        return f"{backend_label} returned invalid JSON"
    if "timed out" in exc_text or "timeout" in exc_text:
        return f"{backend_label} timed out"
    if "api_key" in exc_text or "api key" in exc_text:
        return f"{backend_label} API key is missing or invalid"
    if "ollama is not running" in exc_text:
        return "Ollama is not running"
    if "connection" in exc_text or "unreachable" in exc_text or "refused" in exc_text:
        if backend == "local":
            return "Ollama is not running"
        return f"{backend_label} service is unreachable"
    if isinstance(exc, ValueError):
        return str(exc)
    return f"{backend_label} enrichment is unavailable"


def get_last_enrichment_reason() -> str | None:
    """Return the last AI enrichment fallback reason for CLI reporting."""
    return _LAST_ENRICHMENT_REASON


def get_last_enrichment_report() -> tuple[list[str], dict[str, str]]:
    """Return enriched tables and per-table fallback reasons from the last run."""
    return list(_LAST_ENRICHED_TABLES), dict(_LAST_FALLBACK_TABLES)


def _clean_ai_response(response: str) -> str:
    """Clean AI response by removing fences and surrounding text."""
    response = re.sub(r"```json\s*", "", response)
    response = re.sub(r"```\s*", "", response)

    start = response.find("{")
    end = response.rfind("}")
    if start == -1 or end == -1 or end < start:
        return response.strip()
    return response[start : end + 1].strip()


def _table_summary_prompt(table_name: str, table_data: dict) -> str:
    """Build a compact table-only prompt."""
    lines = [
        f"Table: {table_name}",
        "Return JSON only using keys d, p, q.",
        "Keep every description under 6 words.",
        "Keep each question under 8 words.",
    ]
    column_names = [col.get("name", "") for col in table_data.get("columns", [])]
    if column_names:
        lines.append("Columns: " + ", ".join(column_names))

    return (
        "\n".join(lines)
        + "\n\nReturn JSON in exactly this shape:\n"
        + '{"d":"...","p":"...","q":["..."]}'
    )


def _column_batch_prompt(table_name: str, columns: list[dict]) -> str:
    """Build a compact prompt for a small batch of columns."""
    lines = [
        f"Table: {table_name}",
        "Return JSON only using key c.",
        "Keep every description under 5 words.",
        "Use at most 2 short business terms.",
        "Columns:",
    ]
    for col in columns:
        col_name = col.get("name", "")
        col_type = col.get("type", "")
        sem_type = col.get("semantic_type", "general")
        lines.append(f"- {col_name} | type={col_type} | semantic_type={sem_type}")

    return (
        "\n".join(lines)
        + "\n\nReturn JSON in exactly this shape:\n"
        + '{"c":{"column_name":{"d":"...","b":["..."],"m":"general","me":false,"di":false,"dt":false}}}\n'
        + "Only include columns from this table."
    )


def _parse_table_summary(response: str) -> dict:
    """Parse table-level enrichment JSON."""
    cleaned = _clean_ai_response(response)
    data = json.loads(cleaned)
    if {"d", "p", "q"} <= data.keys():
        return {
            "business_description": str(data.get("d", "")).strip(),
            "business_purpose": str(data.get("p", "")).strip(),
            "possible_business_questions": [
                str(item).strip()
                for item in data.get("q", [])
                if str(item).strip()
            ][:1],
        }
    raise ValueError("Invalid enrichment structure: missing d, p, or q")


def _parse_column_enrichment(response: str) -> dict:
    """Parse column-level enrichment JSON."""
    cleaned = _clean_ai_response(response)
    data = json.loads(cleaned)
    if "c" in data:
        if not isinstance(data["c"], dict):
            raise ValueError("Invalid enrichment structure: c must be an object")
        return {
            str(col_name): {
                "business_description": str(col_info.get("d", "")).strip(),
                "business_terms": [
                    str(item).strip()
                    for item in col_info.get("b", [])
                    if str(item).strip()
                ][:2],
                "metric_type": str(col_info.get("m", "general")).strip() or "general",
                "is_measure": bool(col_info.get("me", False)),
                "is_dimension": bool(col_info.get("di", False)),
                "is_date": bool(col_info.get("dt", False)),
            }
            for col_name, col_info in data["c"].items()
            if isinstance(col_info, dict)
        }

    if "columns" not in data or not isinstance(data["columns"], dict):
        raise ValueError("Invalid enrichment structure: missing columns")
    return data["columns"]


def _apply_table_enrichment(table_name: str, table_data: dict, enrichment: dict) -> None:
    """Apply enrichment data to one table in-place."""
    module_name = table_data.get("module", "master data")
    table_data["business_description"] = sanitize_short_text(
        enrichment.get("business_description", ""),
        fallback=table_data.get("business_description", ""),
    )
    table_data["business_purpose"] = sanitize_business_purpose(
        enrichment.get("business_purpose", ""),
        table_name,
        module_name,
    )
    if table_data["business_purpose"] == build_rule_based_business_purpose(table_name, module_name):
        logger.info(f"AI business purpose for '{table_name}' was invalid; using rule-based fallback.")

    clean_questions = []
    for question in enrichment.get("possible_business_questions", [])[:1]:
        text = str(question or "").strip()
        if not text or len(text) > 100 or not any(ch.isalpha() for ch in text):
            continue
        clean_questions.append(text)
    table_data["possible_business_questions"] = clean_questions


def _apply_column_enrichment(table_data: dict, col_map: dict) -> None:
    """Apply column enrichment data to one table in-place."""
    for col in table_data.get("columns", []):
        col_name = col.get("name", "")
        if col_name not in col_map:
            continue
        col_info = col_map[col_name]
        col["business_description"] = sanitize_short_text(col_info.get("business_description", ""))
        col["business_terms"] = list(col_info.get("business_terms", []))[:2]
        col["metric_type"] = sanitize_short_text(col_info.get("metric_type", "general"), fallback="general") or "general"
        col["is_measure"] = bool(col_info.get("is_measure", False))
        col["is_dimension"] = bool(col_info.get("is_dimension", False))
        col["is_date"] = bool(col_info.get("is_date", False))


def _chunk_columns(columns: list[dict], size: int = 3) -> list[list[dict]]:
    """Split columns into small batches for reliable local AI responses."""
    return [columns[idx : idx + size] for idx in range(0, len(columns), size)]


def enrich_knowledge_base_with_ai(knowledge_base: dict, backend: str = "local") -> dict:
    """
    Enrich the knowledge base one table at a time with local Ollama.

    If one table fails, only that table falls back to the rule-based version and
    the rest of the enrichment continues.
    """
    global _LAST_ENRICHMENT_REASON, _LAST_ENRICHED_TABLES, _LAST_FALLBACK_TABLES

    _LAST_ENRICHMENT_REASON = None
    _LAST_ENRICHED_TABLES = []
    _LAST_FALLBACK_TABLES = {}
    backend = "local"

    logger.info("Starting AI semantic enrichment")
    enriched_kb = copy.deepcopy(knowledge_base)

    for table_name, table_data in enriched_kb.items():
        print(f"  [AI] Enriching table: {table_name}")
        try:
            working_table = copy.deepcopy(table_data)

            summary_messages = [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": _table_summary_prompt(table_name, working_table)},
            ]
            summary_response = _call_ai_backend(
                summary_messages,
                backend=backend,
                response_format=_TABLE_JSON_FORMAT,
            )
            table_enrichment = _parse_table_summary(summary_response)
            _apply_table_enrichment(table_name, working_table, table_enrichment)

            for column_batch in _chunk_columns(working_table.get("columns", [])):
                column_messages = [
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": _column_batch_prompt(table_name, column_batch)},
                ]
                column_response = _call_ai_backend(
                    column_messages,
                    backend=backend,
                    response_format=_COLUMN_JSON_FORMAT,
                )
                column_enrichment = _parse_column_enrichment(column_response)
                _apply_column_enrichment(working_table, column_enrichment)

            enriched_kb[table_name] = working_table
            _LAST_ENRICHED_TABLES.append(table_name)
            print(f"  [OK] AI enrichment completed for table: {table_name}")
        except Exception as exc:
            reason = _describe_ai_enrichment_failure(exc, backend)
            _LAST_FALLBACK_TABLES[table_name] = reason
            print(f"  [INFO] {table_name}: {reason}. Using rule-based fallback.")
            logger.info(f"AI enrichment unavailable for table '{table_name}': {reason}. Using rule-based knowledge base.")
            logger.debug("AI enrichment technical details", exc_info=True)

    if _LAST_FALLBACK_TABLES and not _LAST_ENRICHED_TABLES:
        _LAST_ENRICHMENT_REASON = next(iter(_LAST_FALLBACK_TABLES.values()))
        return knowledge_base

    if _LAST_FALLBACK_TABLES:
        _LAST_ENRICHMENT_REASON = "Partial AI enrichment fallback"
    logger.info("AI semantic enrichment completed")
    return enriched_kb
