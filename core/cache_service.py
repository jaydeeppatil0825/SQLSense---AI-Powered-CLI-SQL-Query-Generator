"""Deterministic cache contract and stores for SQLSense.

Phase 9A wires only the cache foundation. No planner, SQL, retrieval, grain,
or result-row artifact is cached here.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from threading import RLock
from typing import Any, Protocol

CACHE_CONTRACT_VERSION = "sqlsense-cache-v1"
DEFAULT_CACHE_PREFIX = "sqlsense"
DEFAULT_MEMORY_CACHE_MAX_ENTRIES = 256
DEFAULT_CACHE_TTL_SECONDS = 900
DEFAULT_REDIS_TIMEOUT_SECONDS = 1.0

HIT = "hit"
MISS = "miss"
STALE = "stale"
INVALID = "invalid"
CORRUPT = "corrupt"
DISABLED = "disabled"
WRITE_FAILED = "write_failed"
STORED = "stored"

_SENSITIVE_KEY_PARTS = ("password", "secret", "token", "credential", "api_key", "cookie")
_ENVELOPE_FIELDS = {
    "contract_version",
    "artifact_type",
    "artifact_version",
    "created_at",
    "expires_at",
    "namespace",
    "key_hash",
    "identity",
    "payload",
}


@dataclass(frozen=True)
class CacheConfig:
    backend: str = "memory"
    enabled: bool = True
    prefix: str = DEFAULT_CACHE_PREFIX
    ttl_seconds: int = DEFAULT_CACHE_TTL_SECONDS
    memory_max_entries: int = DEFAULT_MEMORY_CACHE_MAX_ENTRIES
    redis_url: str = field(default="", repr=False)
    redis_connect_timeout_seconds: float = DEFAULT_REDIS_TIMEOUT_SECONDS
    redis_socket_timeout_seconds: float = DEFAULT_REDIS_TIMEOUT_SECONDS
    contract_version: str = CACHE_CONTRACT_VERSION

    @classmethod
    def from_env(cls) -> "CacheConfig":
        backend = os.getenv("SQLSENSE_CACHE_BACKEND", "memory").strip().lower() or "memory"
        enabled = os.getenv("SQLSENSE_CACHE_ENABLED", "true").strip().lower() not in {"0", "false", "no", "off"}
        return cls(
            backend=backend,
            enabled=enabled and backend != "disabled",
            prefix=_safe_prefix(os.getenv("SQLSENSE_CACHE_PREFIX", DEFAULT_CACHE_PREFIX)),
            ttl_seconds=_positive_int("SQLSENSE_CACHE_TTL_SECONDS", DEFAULT_CACHE_TTL_SECONDS),
            memory_max_entries=_positive_int("SQLSENSE_MEMORY_CACHE_MAX_ENTRIES", DEFAULT_MEMORY_CACHE_MAX_ENTRIES),
            redis_url=os.getenv("SQLSENSE_REDIS_URL", "").strip(),
            redis_connect_timeout_seconds=_positive_float(
                "SQLSENSE_REDIS_CONNECT_TIMEOUT_SECONDS",
                DEFAULT_REDIS_TIMEOUT_SECONDS,
            ),
            redis_socket_timeout_seconds=_positive_float(
                "SQLSENSE_REDIS_SOCKET_TIMEOUT_SECONDS",
                DEFAULT_REDIS_TIMEOUT_SECONDS,
            ),
        )


@dataclass(frozen=True)
class CacheIdentity:
    db_engine: str
    db_host: str
    db_port: str
    db_name: str
    schema_hash: str
    kb_fingerprint: str
    graph_fingerprint: str
    contract_version: str = CACHE_CONTRACT_VERSION


@dataclass(frozen=True)
class CacheKey:
    namespace: str
    key_hash: str
    artifact_type: str


@dataclass(frozen=True)
class CacheRead:
    state: str
    value: Any = None
    reason: str = ""


@dataclass(frozen=True)
class CacheWrite:
    state: str
    reason: str = ""


class CacheStore(Protocol):
    def get(self, key: CacheKey, *, artifact_version: str) -> CacheRead: ...
    def set(self, key: CacheKey, *, artifact_version: str, value: Any) -> CacheWrite: ...
    def delete(self, key: CacheKey) -> None: ...
    def invalidate_namespace(self, namespace: str) -> int: ...
    def clear(self) -> None: ...
    def stats(self) -> dict[str, Any]: ...


def build_cache_store(config: CacheConfig | None = None) -> CacheStore:
    config = config or CacheConfig.from_env()
    if not config.enabled or config.backend == "disabled":
        return DisabledCacheStore(config)
    if config.backend == "redis":
        return RedisCacheStore(config)
    return MemoryCacheStore(config)


def make_cache_key(
    *,
    identity: CacheIdentity,
    artifact_type: str,
    normalized_input: Any,
    planner_options: Any | None = None,
) -> CacheKey:
    namespace_payload = {
        "contract_version": identity.contract_version,
        "db_engine": identity.db_engine,
        "db_host": identity.db_host,
        "db_port": identity.db_port,
        "db_name": identity.db_name,
        "schema_hash": identity.schema_hash,
        "kb_fingerprint": identity.kb_fingerprint,
        "graph_fingerprint": identity.graph_fingerprint,
    }
    key_payload = {
        **namespace_payload,
        "artifact_type": artifact_type,
        "input_hash": _digest(normalized_input),
        "planner_options_hash": _digest(planner_options or {}),
    }
    return CacheKey(
        namespace=_digest(namespace_payload),
        key_hash=_digest(key_payload),
        artifact_type=artifact_type,
    )


class DisabledCacheStore:
    def __init__(self, config: CacheConfig | None = None):
        self.config = config or CacheConfig(backend="disabled", enabled=False)

    def get(self, key: CacheKey, *, artifact_version: str) -> CacheRead:
        return CacheRead(DISABLED, reason="cache disabled")

    def set(self, key: CacheKey, *, artifact_version: str, value: Any) -> CacheWrite:
        return CacheWrite(DISABLED, reason="cache disabled")

    def delete(self, key: CacheKey) -> None:
        return None

    def invalidate_namespace(self, namespace: str) -> int:
        return 0

    def clear(self) -> None:
        return None

    def stats(self) -> dict[str, Any]:
        return _base_stats(self.config, entries=0)


class MemoryCacheStore:
    def __init__(self, config: CacheConfig | None = None):
        self.config = config or CacheConfig()
        self._lock = RLock()
        self._entries: OrderedDict[str, str] = OrderedDict()
        self._stats = _counter_stats()

    def get(self, key: CacheKey, *, artifact_version: str) -> CacheRead:
        with self._lock:
            raw = self._entries.get(key.key_hash)
            if raw is None:
                self._stats["misses"] += 1
                return CacheRead(MISS)
            self._entries.move_to_end(key.key_hash)
        read = _decode_envelope(raw, key=key, artifact_version=artifact_version, config=self.config)
        self._record_read(read, key)
        return read

    def set(self, key: CacheKey, *, artifact_version: str, value: Any) -> CacheWrite:
        if _contains_sensitive_value(value):
            return CacheWrite(INVALID, reason="cache value contains sensitive data")
        try:
            raw = _encode_envelope(key=key, artifact_version=artifact_version, value=value, config=self.config)
            self._write_entry(key.key_hash, raw)
        except Exception as exc:
            self._stats["write_failures"] += 1
            return CacheWrite(WRITE_FAILED, reason=str(exc))
        return CacheWrite(STORED)

    def delete(self, key: CacheKey) -> None:
        with self._lock:
            self._entries.pop(key.key_hash, None)

    def invalidate_namespace(self, namespace: str) -> int:
        with self._lock:
            keys = [item_key for item_key, raw in self._entries.items() if _raw_namespace(raw) == namespace]
            for item_key in keys:
                self._entries.pop(item_key, None)
            return len(keys)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def stats(self) -> dict[str, Any]:
        with self._lock:
            return {**_base_stats(self.config, len(self._entries)), **self._stats}

    def _write_entry(self, key_hash: str, raw: str) -> None:
        with self._lock:
            self._entries[key_hash] = raw
            self._entries.move_to_end(key_hash)
            self._stats["writes"] += 1
            while len(self._entries) > self.config.memory_max_entries:
                self._entries.popitem(last=False)
                self._stats["evictions"] += 1

    def _record_read(self, read: CacheRead, key: CacheKey) -> None:
        if read.state == HIT:
            self._stats["hits"] += 1
        elif read.state == MISS:
            self._stats["misses"] += 1
        elif read.state == STALE:
            self._stats["stale"] += 1
        elif read.state == CORRUPT:
            self._stats["corrupt"] += 1
            self.delete(key)


class RedisCacheStore:
    def __init__(self, config: CacheConfig | None = None, client: Any | None = None):
        self.config = config or CacheConfig.from_env()
        self._client = client
        self._stats = _counter_stats()

    @property
    def client(self) -> Any:
        if self._client is None:
            if not self.config.redis_url:
                raise RuntimeError("SQLSENSE_REDIS_URL is not configured")
            try:
                import redis
            except ImportError as exc:
                raise RuntimeError("redis-py is not installed") from exc
            self._client = redis.Redis.from_url(
                self.config.redis_url,
                decode_responses=True,
                socket_connect_timeout=self.config.redis_connect_timeout_seconds,
                socket_timeout=self.config.redis_socket_timeout_seconds,
            )
        return self._client

    def get(self, key: CacheKey, *, artifact_version: str) -> CacheRead:
        try:
            raw = self.client.get(self._redis_key(key))
        except Exception:
            self._stats["misses"] += 1
            return CacheRead(MISS, reason="redis unavailable")
        if raw is None:
            self._stats["misses"] += 1
            return CacheRead(MISS)
        read = _decode_envelope(raw, key=key, artifact_version=artifact_version, config=self.config)
        if read.state == CORRUPT:
            self.delete(key)
        self._record_read(read)
        return read

    def set(self, key: CacheKey, *, artifact_version: str, value: Any) -> CacheWrite:
        if _contains_sensitive_value(value):
            return CacheWrite(INVALID, reason="cache value contains sensitive data")
        try:
            raw = _encode_envelope(key=key, artifact_version=artifact_version, value=value, config=self.config)
            self.client.setex(self._redis_key(key), self.config.ttl_seconds, raw)
            self._stats["writes"] += 1
            return CacheWrite(STORED)
        except Exception as exc:
            self._stats["write_failures"] += 1
            return CacheWrite(WRITE_FAILED, reason=exc.__class__.__name__)

    def delete(self, key: CacheKey) -> None:
        try:
            self.client.delete(self._redis_key(key))
        except Exception:
            return None

    def invalidate_namespace(self, namespace: str) -> int:
        deleted = 0
        pattern = self._redis_pattern(namespace)
        try:
            batch: list[str] = []
            for redis_key in self.client.scan_iter(match=pattern, count=100):
                batch.append(redis_key)
                if len(batch) >= 100:
                    deleted += int(self.client.delete(*batch) or 0)
                    batch = []
            if batch:
                deleted += int(self.client.delete(*batch) or 0)
        except Exception:
            return deleted
        return deleted

    def clear(self) -> None:
        self.invalidate_namespace("*")

    def stats(self) -> dict[str, Any]:
        return {**_base_stats(self.config, entries=None), **self._stats}

    def _redis_key(self, key: CacheKey) -> str:
        return f"{self.config.prefix}:{self.config.contract_version}:{key.artifact_type}:{key.namespace}:{key.key_hash}"

    def _redis_pattern(self, namespace: str) -> str:
        return f"{self.config.prefix}:{self.config.contract_version}:*:{namespace}:*"

    def _record_read(self, read: CacheRead) -> None:
        if read.state == HIT:
            self._stats["hits"] += 1
        elif read.state == MISS:
            self._stats["misses"] += 1
        elif read.state == STALE:
            self._stats["stale"] += 1
        elif read.state == CORRUPT:
            self._stats["corrupt"] += 1


def _encode_envelope(*, key: CacheKey, artifact_version: str, value: Any, config: CacheConfig) -> str:
    now = int(time.time())
    envelope = {
        "contract_version": config.contract_version,
        "artifact_type": key.artifact_type,
        "artifact_version": artifact_version,
        "created_at": now,
        "expires_at": now + config.ttl_seconds,
        "namespace": key.namespace,
        "key_hash": key.key_hash,
        "identity": {"namespace": key.namespace},
        "payload": _normalize(value),
    }
    return _stable_json(envelope)


def _decode_envelope(raw: str, *, key: CacheKey, artifact_version: str, config: CacheConfig) -> CacheRead:
    try:
        envelope = json.loads(raw)
    except Exception:
        return CacheRead(CORRUPT, reason="cache entry is not valid JSON")
    if not isinstance(envelope, dict) or set(envelope) != _ENVELOPE_FIELDS:
        return CacheRead(CORRUPT, reason="cache entry has unexpected fields")
    if envelope["contract_version"] != config.contract_version:
        return CacheRead(STALE, reason="cache contract version changed")
    if envelope["artifact_type"] != key.artifact_type:
        return CacheRead(STALE, reason="artifact type changed")
    if envelope["artifact_version"] != artifact_version:
        return CacheRead(STALE, reason="artifact version changed")
    if envelope["namespace"] != key.namespace or envelope["key_hash"] != key.key_hash:
        return CacheRead(STALE, reason="cache identity mismatch")
    if int(envelope["expires_at"] or 0) < int(time.time()):
        return CacheRead(STALE, reason="cache entry expired")
    payload = envelope["payload"]
    if _contains_sensitive_value(payload):
        return CacheRead(CORRUPT, reason="cache entry contains sensitive data")
    return CacheRead(HIT, value=payload)


def _stable_json(value: Any) -> str:
    return json.dumps(_normalize(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _normalize(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _normalize(value[key]) for key in sorted(value)}
    if isinstance(value, (list, tuple)):
        return [_normalize(item) for item in value]
    if isinstance(value, str):
        return " ".join(value.strip().split())
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)


def _digest(value: Any) -> str:
    return hashlib.sha256(_stable_json(value).encode("utf-8")).hexdigest()


def _contains_sensitive_value(value: Any) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            if any(part in str(key).lower() for part in _SENSITIVE_KEY_PARTS):
                return True
            if _contains_sensitive_value(item):
                return True
        return False
    if isinstance(value, (list, tuple)):
        return any(_contains_sensitive_value(item) for item in value)
    if isinstance(value, str):
        text = value.strip().lower()
        return "://" in text and "@" in text
    return False


def _raw_namespace(raw: str) -> str:
    try:
        envelope = json.loads(raw)
    except Exception:
        return ""
    namespace = envelope.get("namespace")
    return namespace if isinstance(namespace, str) else ""


def _counter_stats() -> dict[str, int]:
    return {
        "hits": 0,
        "misses": 0,
        "stale": 0,
        "corrupt": 0,
        "writes": 0,
        "evictions": 0,
        "write_failures": 0,
    }


def _base_stats(config: CacheConfig, entries: int | None) -> dict[str, Any]:
    return {
        **_counter_stats(),
        "backend": config.backend,
        "enabled": config.enabled,
        "entries": entries,
        "max_entries": config.memory_max_entries,
        "ttl_seconds": config.ttl_seconds,
        "contract_version": config.contract_version,
    }


def _positive_int(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        return default
    return max(1, value)


def _positive_float(name: str, default: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError:
        return default
    return max(0.1, value)


def _safe_prefix(value: str) -> str:
    cleaned = "".join(char if char.isalnum() or char in {"-", "_"} else "-" for char in value.strip())
    return cleaned or DEFAULT_CACHE_PREFIX
