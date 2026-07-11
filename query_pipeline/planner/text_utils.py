"""Shared text and value helpers for deterministic planning."""

from __future__ import annotations

import re
from typing import Any

_QUESTION_STOP_WORDS = {
    "show",
    "list",
    "display",
    "get",
    "fetch",
    "what",
    "which",
    "where",
    "when",
    "how",
    "many",
    "current",
    "latest",
    "recent",
    "all",
    "records",
    "record",
    "data",
    "table",
}


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip().lower())


def _normalize_identifier(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", _normalize(text)).strip("_")


def _humanize(text: str) -> str:
    return _normalize_identifier(text).replace("_", " ").strip()


def _singularize_token(token: str) -> str:
    if token.endswith("ies") and len(token) > 3:
        return token[:-3] + "y"
    if token.endswith("ses") and len(token) > 3:
        return token[:-2]
    if token.endswith("s") and not token.endswith(("ss", "us")) and len(token) > 1:
        return token[:-1]
    return token


def _tokenize(text: str) -> list[str]:
    return [token for token in re.split(r"[^a-z0-9]+", _normalize(text)) if token]


def _content_terms(question: str) -> list[str]:
    return [token for token in _tokenize(question) if token not in _QUESTION_STOP_WORDS]


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
