"""Versioned semantic mapping contract helpers."""

from __future__ import annotations

import datetime as _dt
import re
from typing import Any

SEMANTIC_MAPPING_CONTRACT_VERSION = "semantic-mapping-v1"

ALLOWED_SEMANTIC_ROLES = {
    "identifier",
    "metric",
    "dimension",
    "filter",
    "date",
    "status",
    "text",
    "unknown",
}

SQL_TEXT_RE = re.compile(
    r"\b(select|insert|update|delete|drop|alter|truncate|create|merge)\b|--|/\*|;",
    re.IGNORECASE,
)


def utc_now_iso() -> str:
    return _dt.datetime.now(_dt.UTC).replace(microsecond=0).isoformat()


def bounded_text(value: Any, *, max_length: int = 120) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip())
    return text[:max_length]


def data_type_family(column_type: str) -> str:
    value = str(column_type or "").lower()
    if any(token in value for token in ("int", "decimal", "numeric", "float", "double", "real")):
        return "numeric"
    if any(token in value for token in ("date", "time", "year")):
        return "date"
    if any(token in value for token in ("bool", "bit")):
        return "boolean"
    if any(token in value for token in ("char", "text", "enum", "json")):
        return "text"
    return "unknown"


def semantic_role_from_type(semantic_type: str) -> str:
    value = str(semantic_type or "").lower()
    if value == "id":
        return "identifier"
    if value in {"money", "quantity", "percentage", "numeric_candidate"}:
        return "metric"
    if value == "date":
        return "date"
    if value == "status":
        return "status"
    if value in {"name", "text", "code", "reference", "category_candidate", "text_candidate"}:
        return "dimension"
    return "unknown"
