from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, asdict
import hashlib
from typing import Any, Iterator
from uuid import uuid4


def _hash(value: Any) -> str:
    text = str(value or "").strip()
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16] if text else ""


def database_identity_hash(identity: dict[str, Any] | None) -> str:
    if not identity:
        return ""
    parts = (
        identity.get("db_engine") or identity.get("database_type") or "",
        identity.get("db_host") or "",
        identity.get("db_port") or "",
        identity.get("db_name") or identity.get("database_name") or "",
    )
    return _hash("|".join(str(part) for part in parts))


@dataclass(frozen=True)
class ObservabilityContext:
    request_id: str
    trace_id: str
    session_id_hash: str = ""
    database_identity_hash: str = ""
    schema_fingerprint: str = ""
    artifact_version: str = ""

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


_current_context: ContextVar[ObservabilityContext] = ContextVar(
    "sqlsense_observability_context",
    default=ObservabilityContext(request_id="", trace_id=""),
)


def new_context(
    *,
    request_id: str | None = None,
    trace_id: str | None = None,
    session_id: str = "",
    database_identity: dict[str, Any] | None = None,
    schema_fingerprint: str = "",
    artifact_version: str = "",
) -> ObservabilityContext:
    request_id = request_id or uuid4().hex
    return ObservabilityContext(
        request_id=request_id,
        trace_id=trace_id or request_id,
        session_id_hash=_hash(session_id),
        database_identity_hash=database_identity_hash(database_identity),
        schema_fingerprint=str(schema_fingerprint or ""),
        artifact_version=str(artifact_version or ""),
    )


def current_context() -> ObservabilityContext:
    return _current_context.get()


@contextmanager
def bind_context(context: ObservabilityContext) -> Iterator[ObservabilityContext]:
    token = _current_context.set(context)
    try:
        yield context
    finally:
        _current_context.reset(token)
