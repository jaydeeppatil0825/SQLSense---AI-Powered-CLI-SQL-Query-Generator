from __future__ import annotations

import re
from typing import Any


REDACTED = "<redacted>"
_SENSITIVE_KEY_PARTS = (
    "password",
    "passwd",
    "token",
    "authorization",
    "cookie",
    "secret",
    "credential",
    "api_key",
    "apikey",
)
_RAW_PAYLOAD_KEYS = {
    "question",
    "raw_question",
    "sql",
    "generated_sql",
    "rows",
    "result_rows",
    "embedding",
    "embeddings",
    "vector",
}
_URL_CREDENTIAL_RE = re.compile(r"(?i)([a-z][a-z0-9+.-]*://[^:/\s]+:)[^@\s]+(@)")
_ASSIGNMENT_SECRET_RE = re.compile(
    r"(?i)\b(password|token|api[_-]?key|authorization|cookie|secret)\s*([=:])\s*[^&\s,;]+"
)


def _redact_text(value: str) -> str:
    value = _URL_CREDENTIAL_RE.sub(r"\1" + REDACTED + r"\2", value)
    return _ASSIGNMENT_SECRET_RE.sub(lambda match: f"{match.group(1)}{match.group(2)}{REDACTED}", value)


def _is_sensitive_key(key: Any) -> bool:
    lowered = str(key or "").lower()
    return any(part in lowered for part in _SENSITIVE_KEY_PARTS) or lowered in _RAW_PAYLOAD_KEYS


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: (REDACTED if _is_sensitive_key(key) else redact(item))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact(item) for item in value)
    if isinstance(value, str):
        return _redact_text(value)
    return value
