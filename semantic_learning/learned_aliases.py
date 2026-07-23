"""Versioned learned-alias candidate contract."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Any

LEARNED_ALIAS_CONTRACT_VERSION = "learned-alias-v1"

_SECRET_RE = re.compile(
    r"(password|token|api[_-]?key|authorization|cookie|mysql://|postgresql://)",
    re.IGNORECASE,
)
_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")
_PHONE_RE = re.compile(r"\b(?:\+?\d[\d\s().-]{7,}\d)\b")
_LONG_NUMBER_RE = re.compile(r"\b\d{7,}\b")
_SPACE_RE = re.compile(r"\s+")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_phrase(phrase: str) -> str:
    return _SPACE_RE.sub(" ", re.sub(r"[^a-z0-9]+", " ", str(phrase or "").lower())).strip()


def redact_phrase(phrase: str) -> str:
    text = str(phrase or "")
    if _SECRET_RE.search(text):
        return ""
    text = _EMAIL_RE.sub("[redacted-email]", text)
    text = _PHONE_RE.sub("[redacted-phone]", text)
    text = _LONG_NUMBER_RE.sub("[redacted-number]", text)
    return normalize_phrase(text)


def stable_hash(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, default=str, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass
class LearnedAliasCandidate:
    database_identity_hash: str
    schema_fingerprint: str
    phrase: str
    target_type: str
    table: str
    semantic_role: str
    column: str = ""
    value: str = ""
    source: str = "successful_validated_query"
    contract_version: str = LEARNED_ALIAS_CONTRACT_VERSION
    normalized_phrase: str = ""
    support_count: int = 1
    reject_count: int = 0
    confidence_score: float = 0.2
    first_seen_at: str = field(default_factory=utc_now)
    last_seen_at: str = field(default_factory=utc_now)
    status: str = "pending"
    evidence_examples_hashes: list[str] = field(default_factory=list)
    promotion_reason: str = ""
    rejection_reason: str = ""
    created_by: str = "system"

    def __post_init__(self) -> None:
        self.normalized_phrase = redact_phrase(self.normalized_phrase or self.phrase)
        self.phrase = self.normalized_phrase
        self.target_type = str(self.target_type or "").strip().lower()
        self.semantic_role = str(self.semantic_role or "").strip().lower()
        self.table = str(self.table or "").strip()
        self.column = str(self.column or "").strip()
        self.value = str(self.value or "").strip()
        self.status = str(self.status or "pending").strip().lower()
        self.support_count = int(self.support_count or 0)
        self.reject_count = int(self.reject_count or 0)
        self.confidence_score = round(min(max(float(self.confidence_score or 0.0), 0.0), 1.0), 4)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "LearnedAliasCandidate":
        allowed = {field.name for field in cls.__dataclass_fields__.values()}
        return cls(**{key: value for key, value in dict(data or {}).items() if key in allowed})


def alias_key(candidate: LearnedAliasCandidate | dict[str, Any]) -> str:
    item = candidate.to_dict() if isinstance(candidate, LearnedAliasCandidate) else dict(candidate or {})
    return "|".join(
        [
            str(item.get("database_identity_hash", "")),
            str(item.get("schema_fingerprint", "")),
            str(item.get("normalized_phrase") or redact_phrase(str(item.get("phrase", "")))),
            str(item.get("target_type", "")),
            str(item.get("table", "")),
            str(item.get("column", "")),
            str(item.get("value", "")),
            str(item.get("semantic_role", "")),
        ]
    )
