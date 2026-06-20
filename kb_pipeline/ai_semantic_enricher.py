"""
semantic/ai_semantic_enricher.py
=================================
AI-powered enrichment of the knowledge base with business meaning.

This module uses the configured AI backend to enrich one table at a time.
Small prompts keep requests compact and allow partial success when one
table fails or times out.
"""

from __future__ import annotations

import copy
import json
import re

from core.ai_backend_service import call_ai_backend as _call_ai_backend
from kb_pipeline.schema_facts import (
    build_rule_based_business_purpose,
    sanitize_business_purpose,
    sanitize_short_text,
)
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
                    "s": {"type": "string"},
                    "cf": {"type": "number"},
                    "r": {"type": "string"},
                    "me": {"type": "boolean"},
                    "di": {"type": "boolean"},
                    "dt": {"type": "boolean"},
                },
                "required": ["d", "b", "s", "cf", "r", "me", "di", "dt"],
            },
        },
    },
    "required": ["c"],
}

_CANDIDATE_TYPES = {"numeric_candidate", "text_candidate", "category_candidate"}
_STRUCTURAL_TYPES = {"id", "date", "boolean"}
_FINAL_SEMANTIC_TYPES = {
    "money",
    "quantity",
    "date",
    "status",
    "id",
    "name",
    "text",
    "boolean",
    "percentage",
    "code",
    "reference",
    "general",
    "numeric_candidate",
    "text_candidate",
    "category_candidate",
}
_AI_RETURN_SEMANTIC_TYPES = _FINAL_SEMANTIC_TYPES - _CANDIDATE_TYPES
_SAMPLE_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "by", "for", "from", "in", "is",
    "my", "of", "on", "or", "our", "the", "to", "us", "we", "with",
}
_QUESTION_PRONOUNS = {"my", "our", "we", "us"}
_PURPOSE_VERBS = {"store", "stores", "track", "tracks", "hold", "holds", "record", "records", "contain", "contains", "list", "lists"}
_TECHNICAL_WORDS = {"id", "date", "time", "timestamp"}


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


def _normalize_free_text(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip().lower())


def _humanize_identifier(text: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", _normalize_free_text(text)).strip("_")
    return normalized.replace("_", " ").strip()


def _tokenize_text(text: str) -> set[str]:
    return {token for token in re.split(r"[^a-z0-9]+", _normalize_free_text(text)) if token}


def _identifier_tokens(text: str) -> list[str]:
    return [token for token in re.split(r"[^a-z0-9]+", _normalize_free_text(text)) if token]


def _singularize_word(word: str) -> str:
    if word.endswith("ies") and len(word) > 3:
        return word[:-3] + "y"
    if word.endswith("ses") and len(word) > 3:
        return word[:-2]
    if word.endswith("s") and not word.endswith("ss") and len(word) > 1:
        return word[:-1]
    return word


def _singularize_phrase(text: str) -> str:
    tokens = _identifier_tokens(text)
    if not tokens:
        return ""
    tokens[-1] = _singularize_word(tokens[-1])
    return " ".join(tokens)


def _format_phrase_list(values: list[str]) -> str:
    cleaned = [value for value in values if value]
    if not cleaned:
        return ""
    if len(cleaned) == 1:
        return cleaned[0]
    if len(cleaned) == 2:
        return f"{cleaned[0]} and {cleaned[1]}"
    return f"{', '.join(cleaned[:-1])}, and {cleaned[-1]}"


def _collect_table_sample_texts(table_data: dict) -> set[str]:
    sample_texts: set[str] = set()
    for column in table_data.get("columns", []):
        for value in column.get("sample_values", []) or []:
            cleaned = _normalize_free_text(value)
            if cleaned:
                sample_texts.add(cleaned)
    return sample_texts


def _looks_like_literal_value(text: str) -> bool:
    cleaned = _normalize_free_text(text)
    if not cleaned:
        return True
    if not any(char.isalpha() for char in cleaned):
        return True
    if re.fullmatch(r"[\d\s,.$:/\\-]+", cleaned):
        return True
    return False


def _looks_like_technical_phrase(text: str) -> bool:
    cleaned = _normalize_free_text(text)
    if not cleaned:
        return True
    if "_" in cleaned:
        return True
    tokens = _identifier_tokens(cleaned)
    if not tokens:
        return True
    if tokens[-1] in _TECHNICAL_WORDS:
        return True
    if len(tokens) == 2 and tokens[1] in {"on", "at"}:
        return True
    return False


def _looks_like_sample_echo(text: str, sample_texts: set[str]) -> bool:
    cleaned = _normalize_free_text(text)
    if not cleaned or not sample_texts:
        return False
    if cleaned in sample_texts:
        return True

    text_tokens = _tokenize_text(cleaned) - _SAMPLE_STOPWORDS
    if not text_tokens:
        return False

    for sample in sample_texts:
        sample_tokens = _tokenize_text(sample) - _SAMPLE_STOPWORDS
        if sample_tokens and text_tokens <= sample_tokens:
            return True
    return False


def _fallback_table_description(table_name: str) -> str:
    return (_singularize_phrase(table_name) or _humanize_identifier(table_name) or "record").title()


def _collect_related_entities(table_data: dict) -> tuple[list[str], list[str]]:
    outgoing: list[str] = []
    incoming: list[str] = []

    def _add_unique(target: list[str], value: str) -> None:
        if value and value not in target:
            target.append(value)

    for foreign_key in table_data.get("foreign_keys", []):
        related = _singularize_phrase(foreign_key.get("referenced_table", ""))
        _add_unique(outgoing, related)

    for relationship in table_data.get("relationships", []):
        from_table = str(relationship.get("from_table", "")).strip()
        to_table = str(relationship.get("to_table", "")).strip()
        direction = str(relationship.get("direction", "")).strip().lower()
        if direction == "incoming":
            _add_unique(incoming, _singularize_phrase(from_table))
        else:
            table_name = str(table_data.get("table_name", "")).strip()
            if table_name and from_table == table_name:
                _add_unique(outgoing, _singularize_phrase(to_table))
            elif to_table:
                _add_unique(incoming, _singularize_phrase(from_table))

    return outgoing[:3], incoming[:3]


def _fallback_table_purpose(table_name: str, table_data: dict) -> str:
    label = _singularize_phrase(table_name) or _humanize_identifier(table_name) or "record"
    outgoing_entities, incoming_entities = _collect_related_entities(table_data)

    if outgoing_entities:
        relation_phrase = _format_phrase_list(outgoing_entities)
        return f"Stores {label} records linked to {relation_phrase}."
    if incoming_entities:
        relation_phrase = _format_phrase_list(incoming_entities)
        return f"Stores {label} records used in {relation_phrase}."
    return build_rule_based_business_purpose(table_name)


def _column_entity_phrase(column_name: str, table_data: dict) -> str:
    tokens = _identifier_tokens(column_name)
    filtered = [token for token in tokens if token not in _TECHNICAL_WORDS]
    if filtered:
        filtered[-1] = _singularize_word(filtered[-1])
        return " ".join(filtered)

    outgoing_entities, _incoming_entities = _collect_related_entities(table_data)
    if outgoing_entities:
        return _format_phrase_list(outgoing_entities)

    table_name = str(table_data.get("table_name", "")).strip()
    return _singularize_phrase(table_name) or _humanize_identifier(table_name)


def _column_context_tokens(column: dict, semantic_type: str) -> set[str]:
    table_data = column.get("_table_context", {})
    context_tokens = _tokenize_text(column.get("name", "")) | _tokenize_text(table_data.get("table_name", ""))
    outgoing_entities, incoming_entities = _collect_related_entities(table_data)
    for entity in [*outgoing_entities, *incoming_entities]:
        context_tokens |= _tokenize_text(entity)
    return context_tokens


def _fallback_column_description(column_name: str, semantic_type: str, table_data: dict) -> str:
    label = _humanize_identifier(column_name) or _column_entity_phrase(column_name, table_data) or "field"
    return f"Description for {label} field."


def _readable_column_phrase(column_name: str) -> str:
    return _humanize_identifier(column_name)


def _fallback_business_terms(column_name: str, semantic_type: str, table_data: dict) -> list[str]:
    primary = _readable_column_phrase(column_name)
    if not primary:
        return []

    terms = [primary]

    deduped: list[str] = []
    for term in terms:
        normalized = _normalize_free_text(term)
        if term and normalized and not _looks_like_technical_phrase(term) and term not in deduped:
            deduped.append(term)
    return deduped[:2]


def _sanitize_table_description(text: str, table_name: str, table_data: dict) -> str:
    cleaned = sanitize_short_text(text, fallback=_fallback_table_description(table_name))
    fallback = _fallback_table_description(table_name)
    if _looks_like_literal_value(cleaned) or _looks_like_sample_echo(cleaned, _collect_table_sample_texts(table_data)):
        return fallback
    if _looks_like_technical_phrase(cleaned):
        return fallback
    if len(cleaned.split()) < 2:
        return fallback
    return cleaned


def _sanitize_table_purpose(text: str, table_name: str, table_data: dict) -> str:
    fallback = _fallback_table_purpose(table_name, table_data)
    cleaned = sanitize_business_purpose(text, table_name)
    tokens = _tokenize_text(cleaned)
    if (
        _looks_like_literal_value(cleaned)
        or _looks_like_sample_echo(cleaned, _collect_table_sample_texts(table_data))
        or _looks_like_technical_phrase(cleaned)
        or "?" in cleaned
        or len(tokens) < 3
        or tokens & _QUESTION_PRONOUNS
        or not (tokens & _PURPOSE_VERBS)
    ):
        return fallback
    return cleaned


def _sanitize_business_questions(
    questions: list[str],
    table_name: str,
    table_data: dict,
    business_purpose: str,
) -> list[str]:
    fallback_purpose = build_rule_based_business_purpose(table_name)
    if business_purpose == fallback_purpose:
        return []

    sample_texts = _collect_table_sample_texts(table_data)
    context_tokens = _tokenize_text(table_name)
    for column in table_data.get("columns", []):
        context_tokens |= _tokenize_text(column.get("name", ""))

    clean_questions: list[str] = []
    for question in questions[:1]:
        text = sanitize_short_text(question)
        if not text or len(text) > 100 or not any(ch.isalpha() for ch in text):
            continue
        tokens = _tokenize_text(text)
        if tokens & _QUESTION_PRONOUNS:
            continue
        if _looks_like_sample_echo(text, sample_texts):
            continue
        if not (tokens & context_tokens):
            continue
        clean_questions.append(text)
    return clean_questions


def _sanitize_column_description(text: str, column: dict, semantic_type: str) -> str:
    table_data = column.get("_table_context", {})
    fallback = _fallback_column_description(str(column.get("name", "")), semantic_type, table_data)
    cleaned = sanitize_short_text(text, fallback=fallback)
    sample_texts = {
        _normalize_free_text(value)
        for value in (column.get("sample_values", []) or [])
        if _normalize_free_text(value)
    }
    if _looks_like_literal_value(cleaned) or _looks_like_sample_echo(cleaned, sample_texts):
        return fallback
    if _looks_like_technical_phrase(cleaned):
        return fallback
    if len(cleaned.split()) < 2:
        return fallback
    return cleaned


def _sanitize_business_terms(terms: list[str], column: dict, semantic_type: str) -> list[str]:
    table_data = column.get("_table_context", {})
    context_tokens = _column_context_tokens(column, semantic_type)
    sample_texts = {
        _normalize_free_text(value)
        for value in (column.get("sample_values", []) or [])
        if _normalize_free_text(value)
    }
    clean_terms: list[str] = []
    for term in terms[:2]:
        cleaned = sanitize_short_text(term)
        if not cleaned or _looks_like_literal_value(cleaned):
            continue
        if _looks_like_sample_echo(cleaned, sample_texts):
            continue
        if _looks_like_technical_phrase(cleaned):
            continue
        if not (_tokenize_text(cleaned) & context_tokens):
            continue
        if len(cleaned.split()) > 4:
            continue
        if cleaned not in clean_terms:
            clean_terms.append(cleaned)

    if clean_terms:
        return clean_terms
    return _fallback_business_terms(str(column.get("name", "")), semantic_type, table_data)


def _sanitize_reason(text: str, fallback: str, column: dict) -> str:
    cleaned = sanitize_short_text(text, fallback=fallback)
    sample_texts = {
        _normalize_free_text(value)
        for value in (column.get("sample_values", []) or [])
        if _normalize_free_text(value)
    }
    if _looks_like_literal_value(cleaned) or _looks_like_sample_echo(cleaned, sample_texts):
        return fallback
    if _looks_like_technical_phrase(cleaned):
        return fallback
    if len(cleaned.split()) < 3:
        return fallback
    return cleaned


def _table_summary_prompt(table_name: str, table_data: dict) -> str:
    """Build a compact table-only prompt."""
    lines = [
        f"Table: {table_name}",
        "Return JSON only using keys d, p, q.",
        "Describe schema meaning only, not literal sample values.",
        "Do not copy names, labels, cities, dates, amounts, or codes from rows.",
        "If unsure, keep descriptions neutral and generic.",
        "Only add q when confidence is high; otherwise return an empty list.",
        "Keep every description under 10 words.",
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


def _is_structural_semantic(column: dict) -> bool:
    return str(column.get("semantic_type", "general")).lower() in _STRUCTURAL_TYPES


def _candidate_columns(table_data: dict) -> list[dict]:
    return [
        column
        for column in table_data.get("columns", [])
        if str(column.get("semantic_type", "general")).lower() in _CANDIDATE_TYPES
    ]


def _nearby_column_names(table_data: dict, column_name: str, radius: int = 2) -> list[str]:
    columns = list(table_data.get("columns", []))
    names = [str(column.get("name", "")) for column in columns]
    try:
        index = names.index(column_name)
    except ValueError:
        return []

    nearby: list[str] = []
    start = max(0, index - radius)
    end = min(len(names), index + radius + 1)
    for idx in range(start, end):
        if idx == index:
            continue
        candidate = names[idx].strip()
        if candidate:
            nearby.append(candidate)
    return nearby


def _column_relationship_hints(table_name: str, column_name: str, table_data: dict) -> list[str]:
    hints: list[str] = []
    for foreign_key in table_data.get("foreign_keys", []):
        if str(foreign_key.get("column", "")) != column_name:
            continue
        referenced_table = str(foreign_key.get("referenced_table", "")).strip()
        referenced_column = str(foreign_key.get("referenced_column", "")).strip()
        if referenced_table and referenced_column:
            hints.append(f"{table_name}.{column_name} -> {referenced_table}.{referenced_column}")

    for relationship in table_data.get("relationships", []):
        if str(relationship.get("from_column", "")) == column_name:
            from_table = str(relationship.get("from_table", "")).strip()
            to_table = str(relationship.get("to_table", "")).strip()
            to_column = str(relationship.get("to_column", "")).strip()
            if from_table and to_table and to_column:
                hints.append(f"{from_table}.{column_name} -> {to_table}.{to_column}")
        elif str(relationship.get("to_column", "")) == column_name:
            from_table = str(relationship.get("from_table", "")).strip()
            from_column = str(relationship.get("from_column", "")).strip()
            to_table = str(relationship.get("to_table", "")).strip()
            if from_table and from_column and to_table:
                hints.append(f"{from_table}.{from_column} -> {to_table}.{column_name}")
    return list(dict.fromkeys(hints))[:4]


def _table_relationship_hints(table_name: str, table_data: dict) -> list[str]:
    hints: list[str] = []
    for foreign_key in table_data.get("foreign_keys", []):
        from_column = str(foreign_key.get("column", "")).strip()
        referenced_table = str(foreign_key.get("referenced_table", "")).strip()
        referenced_column = str(foreign_key.get("referenced_column", "")).strip()
        if from_column and referenced_table and referenced_column:
            hints.append(f"{table_name}.{from_column} -> {referenced_table}.{referenced_column}")
    for relationship in table_data.get("relationships", []):
        from_table = str(relationship.get("from_table", "")).strip()
        from_column = str(relationship.get("from_column", "")).strip()
        to_table = str(relationship.get("to_table", "")).strip()
        to_column = str(relationship.get("to_column", "")).strip()
        if from_table and from_column and to_table and to_column:
            hints.append(f"{from_table}.{from_column} -> {to_table}.{to_column}")
    return list(dict.fromkeys(hints))[:8]

def _profile_summary(column: dict) -> str:
    samples = [str(value) for value in (column.get("sample_values", []) or []) if value is not None][:5]
    return (
        f"samples={samples or []} | "
        f"min={column.get('min_value', None)} | "
        f"max={column.get('max_value', None)} | "
        f"unique={column.get('unique_count', None)} | "
        f"nulls={column.get('null_count', None)}"
    )


def _column_batch_prompt(table_name: str, table_data: dict, columns: list[dict]) -> str:
    """Build a compact prompt for candidate columns using profiling and schema context."""
    lines = [
        f"Table: {table_name}",
        "Return JSON only using key c.",
        "Decide final semantic meaning only for the listed candidate columns.",
        "Never change structural facts like id/date/boolean.",
        "Column descriptions must explain the role of the column, not repeat sample values or raw identifiers.",
        "Business terms must be readable user-search phrases, not *_id, *_date, nearby column names, or literal sample values.",
        "Keep every description under 8 words.",
        "Use at most 2 short business terms.",
        "Allowed semantic_type values: money, quantity, percentage, status, name, text, code, reference, date, general.",
        "Do not return numeric_candidate, text_candidate, or category_candidate.",
        "Candidate columns:",
    ]
    for col in columns:
        col_name = col.get("name", "")
        col_type = col.get("type", "")
        sem_type = col.get("semantic_type", "general")
        pk_flag = bool(col.get("is_primary_key", False))
        fk_flag = bool(col.get("is_foreign_key", False))
        nearby = _nearby_column_names(table_data, str(col_name))
        relationships = _column_relationship_hints(table_name, str(col_name), table_data)
        lines.append(
            f"- {col_name} | type={col_type} | candidate_type={sem_type} | "
            f"pk={pk_flag} | fk={fk_flag} | {_profile_summary(col)} | "
            f"nearby={nearby} | relationships={relationships}"
        )

    table_relationships = _table_relationship_hints(table_name, table_data)
    if table_relationships:
        lines.append("Table relationships:")
        for hint in table_relationships:
            lines.append(f"- {hint}")

    lines.append("Other table columns:")
    for col in table_data.get("columns", []):
        lines.append(
            f"- {col.get('name', '')} | type={col.get('type', '')} | semantic_type={col.get('semantic_type', 'general')}"
        )

    return (
        "\n".join(lines)
        + "\n\nReturn JSON in exactly this shape:\n"
        + '{"c":{"column_name":{"d":"...","b":["..."],"s":"quantity","cf":0.84,"r":"sample values and nearby columns indicate units","me":true,"di":false,"dt":false}}}\n'
        + "Only include columns from this table."
    )


def _parse_table_summary(response: str) -> dict:
    """Parse table-level enrichment JSON."""
    cleaned = _clean_ai_response(response)
    data = json.loads(cleaned)
    if {"d", "p", "q"} <= data.keys():
        return {
            "business_description": str(data.get("d", data.get("table_description", ""))).strip(),
            "business_purpose": str(data.get("p", data.get("table_purpose", ""))).strip(),
            "possible_business_questions": [
                str(item).strip()
                for item in data.get("q", data.get("possible_business_questions", []))
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
                "business_description": str(col_info.get("d", col_info.get("column_description", ""))).strip(),
                "business_terms": [
                    str(item).strip()
                    for item in col_info.get("b", col_info.get("business_terms", []))
                    if str(item).strip()
                ][:2],
                "semantic_type": str(col_info.get("s", col_info.get("m", "general"))).strip() or "general",
                "confidence": float(col_info.get("cf", 0.0) or 0.0),
                "reason": str(col_info.get("r", "")).strip(),
                "is_measure": bool(col_info.get("me", col_info.get("is_measure", False))),
                "is_dimension": bool(col_info.get("di", col_info.get("is_dimension", False))),
                "is_date": bool(col_info.get("dt", col_info.get("is_date", False))),
            }
            for col_name, col_info in data["c"].items()
            if isinstance(col_info, dict)
        }

    if "columns" not in data or not isinstance(data["columns"], dict):
        raise ValueError("Invalid enrichment structure: missing columns")
    return data["columns"]


def _apply_table_enrichment(table_name: str, table_data: dict, enrichment: dict) -> None:
    """Apply enrichment data to one table in-place."""
    fallback_purpose = build_rule_based_business_purpose(table_name)
    table_data["business_description"] = _sanitize_table_description(
        enrichment.get("business_description", ""),
        table_name,
        table_data,
    )
    table_data["business_purpose"] = _sanitize_table_purpose(
        enrichment.get("business_purpose", ""),
        table_name,
        table_data,
    )
    if table_data["business_purpose"] == fallback_purpose:
        logger.info(f"AI business purpose for '{table_name}' was invalid; using rule-based fallback.")

    table_data["possible_business_questions"] = _sanitize_business_questions(
        enrichment.get("possible_business_questions", []),
        table_name,
        table_data,
        table_data["business_purpose"],
    )


def _normalize_ai_semantic_type(value: str, fallback: str) -> str:
    semantic_type = str(value or "").strip().lower()
    if semantic_type in _AI_RETURN_SEMANTIC_TYPES:
        return semantic_type
    return str(fallback or "general").strip().lower() or "general"


def _finalize_candidate_semantic_type(column: dict, col_info: dict, semantic_type: str) -> str:
    column_type = str(column.get("type", "")).lower()
    combined_text = " ".join(
        [
            str(column.get("name", "")),
            str(col_info.get("business_description", "")),
            str(col_info.get("reason", "")),
            " ".join(str(value) for value in col_info.get("business_terms", [])),
        ]
    ).lower()
    tokens = {token for token in re.split(r"[^a-z0-9]+", combined_text) if token}

    is_measure = bool(col_info.get("is_measure", False))
    is_dimension = bool(col_info.get("is_dimension", False))

    if semantic_type == "name" and {"reason", "description", "comment", "note", "text", "message"} & tokens:
        return "text"
    if semantic_type not in _CANDIDATE_TYPES | {"general"}:
        return semantic_type

    if {"percent", "percentage", "ratio"} & tokens or ("rate" in tokens and is_measure):
        return "percentage"
    if is_measure:
        if any(token in column_type for token in ("decimal", "numeric", "float", "double", "real")):
            if {"unit", "units", "qty", "quantity", "count", "volume"} & tokens:
                return "quantity"
            return "money"
        if any(token in column_type for token in ("int", "integer", "bigint", "smallint", "tinyint")):
            return "quantity"

    if {"status", "state", "stage"} & tokens:
        return "status"
    if {"code", "reference"} & tokens:
        return "code"
    if {"name", "label", "title"} & tokens:
        return "name"
    if semantic_type in {"text_candidate", "category_candidate"} or is_dimension:
        return "text"
    if semantic_type == "numeric_candidate":
        if any(token in column_type for token in ("decimal", "numeric", "float", "double", "real")):
            return "money"
        if any(token in column_type for token in ("int", "integer", "bigint", "smallint", "tinyint")):
            return "quantity"
    return semantic_type


def _apply_column_enrichment(table_data: dict, col_map: dict) -> None:
    """Apply column enrichment data to one table in-place."""
    for col in table_data.get("columns", []):
        col_name = col.get("name", "")
        if col_name not in col_map:
            continue
        if _is_structural_semantic(col):
            continue
        col["_table_context"] = table_data
        col_info = col_map[col_name]
        fallback_semantic = str(col.get("semantic_type", "general")).lower()
        semantic_type = _normalize_ai_semantic_type(col_info.get("semantic_type", fallback_semantic), fallback_semantic)
        semantic_type = _finalize_candidate_semantic_type(col, col_info, semantic_type)
        col["business_description"] = _sanitize_column_description(col_info.get("business_description", ""), col, semantic_type)
        col["business_terms"] = _sanitize_business_terms(list(col_info.get("business_terms", [])), col, semantic_type)
        col["semantic_type"] = semantic_type
        col["confidence"] = max(float(col.get("confidence", 0.0) or 0.0), min(max(float(col_info.get("confidence", 0.0) or 0.0), 0.0), 1.0))
        col["reason"] = _sanitize_reason(col_info.get("reason", ""), str(col.get("reason", "")), col)
        col["metric_type"] = semantic_type if semantic_type in {"money", "quantity", "percentage"} else "general"
        col["is_measure"] = semantic_type in {"money", "quantity", "percentage"} and bool(col_info.get("is_measure", False))
        col["is_dimension"] = semantic_type in {"status", "name", "text", "code", "reference"} or bool(col_info.get("is_dimension", False))
        col["is_date"] = bool(col_info.get("is_date", False) or semantic_type == "date")
        col.pop("_table_context", None)


def _chunk_columns(columns: list[dict], size: int = 3) -> list[list[dict]]:
    """Split columns into small batches for reliable local AI responses."""
    return [columns[idx : idx + size] for idx in range(0, len(columns), size)]


def enrich_knowledge_base_with_ai(knowledge_base: dict, backend: str = "local") -> dict:
    """
    Enrich the knowledge base one table at a time with the configured backend.

    If one table fails, only that table falls back to the rule-based version and
    the rest of the enrichment continues.
    """
    global _LAST_ENRICHMENT_REASON, _LAST_ENRICHED_TABLES, _LAST_FALLBACK_TABLES

    _LAST_ENRICHMENT_REASON = None
    _LAST_ENRICHED_TABLES = []
    _LAST_FALLBACK_TABLES = {}
    logger.info("Starting AI semantic enrichment")
    enriched_kb = copy.deepcopy(knowledge_base)

    for table_name, table_data in enriched_kb.items():
        print(f"  [AI] Enriching table: {table_name}")
        try:
            working_table = copy.deepcopy(table_data)
            primary_keys = {str(value) for value in working_table.get("primary_keys", []) if value}
            foreign_keys = {
                str(fk.get("column", ""))
                for fk in working_table.get("foreign_keys", [])
                if fk.get("column")
            }
            for column in working_table.get("columns", []):
                column_name = str(column.get("name", ""))
                column["is_primary_key"] = column_name in primary_keys
                column["is_foreign_key"] = column_name in foreign_keys

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

            candidate_columns = _candidate_columns(working_table)
            for column_batch in _chunk_columns(candidate_columns):
                column_messages = [
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": _column_batch_prompt(table_name, working_table, column_batch)},
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
