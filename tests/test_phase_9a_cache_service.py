import json
import threading

from core import cache_service
from core.app_service import AppService
from core.cache_service import (
    CACHE_CONTRACT_VERSION,
    CORRUPT,
    DISABLED,
    HIT,
    INVALID,
    MISS,
    STALE,
    STORED,
    WRITE_FAILED,
    CacheConfig,
    CacheIdentity,
    MemoryCacheStore,
    RedisCacheStore,
    build_cache_store,
    make_cache_key,
)


ARTIFACT_VERSION = "fixture-v1"


def identity(**overrides):
    base = {
        "db_engine": "mysql",
        "db_host": "localhost",
        "db_port": "3306",
        "db_name": "business_db",
        "schema_hash": "schema-a",
        "kb_fingerprint": "kb-a",
        "graph_fingerprint": "graph-a",
    }
    base.update(overrides)
    return CacheIdentity(**base)


def key(**overrides):
    return make_cache_key(
        identity=identity(**overrides.pop("identity", {})),
        artifact_type=overrides.pop("artifact_type", "fixture"),
        normalized_input=overrides.pop("input", {"question": "show orders"}),
        planner_options=overrides.pop("options", {"max_join_edges": 2}),
    )


def test_cache_key_is_deterministic_order_independent_and_hashed():
    first = make_cache_key(
        identity=identity(),
        artifact_type="fixture",
        normalized_input={"b": 2, "a": {"z": " show   orders ", "x": 1}},
        planner_options={"limit": 50, "join": ["a", "b"]},
    )
    second = make_cache_key(
        identity=identity(),
        artifact_type="fixture",
        normalized_input={"a": {"x": 1, "z": "show orders"}, "b": 2},
        planner_options={"join": ["a", "b"], "limit": 50},
    )

    assert first == second
    assert "show orders" not in first.key_hash
    assert "business_db" not in first.key_hash


def test_identity_and_option_changes_change_keys():
    baseline = key()
    assert key(identity={"db_engine": "postgres"}) != baseline
    assert key(identity={"db_host": "db.internal"}) != baseline
    assert key(identity={"db_port": "3307"}) != baseline
    assert key(identity={"db_name": "other"}) != baseline
    assert key(identity={"schema_hash": "schema-b"}) != baseline
    assert key(identity={"kb_fingerprint": "kb-b"}) != baseline
    assert key(identity={"graph_fingerprint": "graph-b"}) != baseline
    assert key(artifact_type="other") != baseline
    assert key(options={"max_join_edges": 1}) != baseline
    assert key(input={"question": "show customers"}) != baseline


def test_memory_store_hit_miss_eviction_and_namespace_invalidation():
    store = MemoryCacheStore(CacheConfig(backend="memory", memory_max_entries=2))
    first = key(input={"question": "one"})
    second = key(input={"question": "two"})
    third = key(input={"question": "three"})

    assert store.get(first, artifact_version=ARTIFACT_VERSION).state == MISS
    assert store.set(first, artifact_version=ARTIFACT_VERSION, value={"answer": 1}).state == STORED
    assert store.get(first, artifact_version=ARTIFACT_VERSION).value == {"answer": 1}

    store.set(second, artifact_version=ARTIFACT_VERSION, value={"answer": 2})
    store.set(third, artifact_version=ARTIFACT_VERSION, value={"answer": 3})
    assert store.get(first, artifact_version=ARTIFACT_VERSION).state == MISS
    assert store.stats()["evictions"] == 1
    assert store.invalidate_namespace(second.namespace) == 2
    assert store.get(second, artifact_version=ARTIFACT_VERSION).state == MISS


def test_memory_store_disabled_stale_corrupt_invalid_and_write_failure(monkeypatch):
    disabled = build_cache_store(CacheConfig(backend="disabled", enabled=False))
    assert disabled.get(key(), artifact_version=ARTIFACT_VERSION).state == DISABLED
    assert disabled.set(key(), artifact_version=ARTIFACT_VERSION, value={"ok": True}).state == DISABLED

    store = MemoryCacheStore(CacheConfig(backend="memory", ttl_seconds=1))
    item = key()
    assert store.set(item, artifact_version=ARTIFACT_VERSION, value={"ok": True}).state == STORED
    assert store.get(item, artifact_version="fixture-v2").state == STALE

    store._entries[item.key_hash] = "not-json"
    assert store.get(item, artifact_version=ARTIFACT_VERSION).state == CORRUPT

    store.set(item, artifact_version=ARTIFACT_VERSION, value={"ok": True})
    envelope = json.loads(store._entries[item.key_hash])
    envelope["surprise"] = True
    store._entries[item.key_hash] = json.dumps(envelope)
    assert store.get(item, artifact_version=ARTIFACT_VERSION).state == CORRUPT

    store.set(item, artifact_version=ARTIFACT_VERSION, value={"ok": True})
    envelope = json.loads(store._entries[item.key_hash])
    envelope["contract_version"] = "unknown"
    store._entries[item.key_hash] = json.dumps(envelope)
    assert store.get(item, artifact_version=ARTIFACT_VERSION).state == STALE

    assert store.set(item, artifact_version=ARTIFACT_VERSION, value={"password": "secret"}).state == INVALID
    monkeypatch.setattr(store, "_write_entry", lambda *_args: (_ for _ in ()).throw(OSError("full")))
    assert store.set(item, artifact_version=ARTIFACT_VERSION, value={"ok": True}).state == WRITE_FAILED


def test_cache_config_reads_phase_9a_environment(monkeypatch):
    monkeypatch.setenv("SQLSENSE_CACHE_BACKEND", "redis")
    monkeypatch.setenv("SQLSENSE_REDIS_URL", "redis://:secret@localhost:6379/0")
    monkeypatch.setenv("SQLSENSE_CACHE_PREFIX", "sqlsense/test")
    monkeypatch.setenv("SQLSENSE_CACHE_TTL_SECONDS", "123")
    monkeypatch.setenv("SQLSENSE_REDIS_CONNECT_TIMEOUT_SECONDS", "0.5")
    monkeypatch.setenv("SQLSENSE_REDIS_SOCKET_TIMEOUT_SECONDS", "0.7")
    monkeypatch.setenv("SQLSENSE_MEMORY_CACHE_MAX_ENTRIES", "9")

    config = CacheConfig.from_env()

    assert config.backend == "redis"
    assert config.prefix == "sqlsense-test"
    assert config.ttl_seconds == 123
    assert config.redis_connect_timeout_seconds == 0.5
    assert config.redis_socket_timeout_seconds == 0.7
    assert config.memory_max_entries == 9
    assert "secret" not in repr(config)


def test_memory_store_expiry_and_thread_safety(monkeypatch):
    now = [1000]
    monkeypatch.setattr(cache_service.time, "time", lambda: now[0])
    store = MemoryCacheStore(CacheConfig(backend="memory", ttl_seconds=10, memory_max_entries=100))
    item = key()
    store.set(item, artifact_version=ARTIFACT_VERSION, value={"ok": True})
    assert store.get(item, artifact_version=ARTIFACT_VERSION).state == HIT

    now[0] = 1011
    assert store.get(item, artifact_version=ARTIFACT_VERSION).state == STALE

    def write(index):
        store.set(key(input={"index": index}), artifact_version=ARTIFACT_VERSION, value={"index": index})

    threads = [threading.Thread(target=write, args=(index,)) for index in range(20)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert store.stats()["entries"] <= 100


class FakeRedis:
    def __init__(self, fail_get=False, fail_set=False):
        self.data = {}
        self.ttl = {}
        self.fail_get = fail_get
        self.fail_set = fail_set

    def get(self, redis_key):
        if self.fail_get:
            raise TimeoutError("timeout")
        return self.data.get(redis_key)

    def setex(self, redis_key, ttl, value):
        if self.fail_set:
            raise TimeoutError("timeout")
        self.data[redis_key] = value
        self.ttl[redis_key] = ttl
        return True

    def delete(self, *keys):
        count = 0
        for redis_key in keys:
            count += int(redis_key in self.data)
            self.data.pop(redis_key, None)
        return count

    def scan_iter(self, match=None, count=None):
        prefix = (match or "").split("*", 1)[0]
        return (redis_key for redis_key in list(self.data) if redis_key.startswith(prefix))


def test_redis_store_uses_ttl_hashed_keys_scan_delete_and_degrades_safely():
    fake = FakeRedis()
    config = CacheConfig(backend="redis", redis_url="redis://:secret@localhost:6379/0", prefix="sqlsense-test", ttl_seconds=42)
    store = RedisCacheStore(config, client=fake)
    item = key(input={"question": "show paid customers"})

    assert store.set(item, artifact_version=ARTIFACT_VERSION, value={"ok": True}).state == STORED
    redis_key = next(iter(fake.data))
    assert redis_key.startswith(f"sqlsense-test:{CACHE_CONTRACT_VERSION}:fixture:")
    assert fake.ttl[redis_key] == 42
    assert "show paid customers" not in redis_key
    assert "secret" not in redis_key
    assert "secret" not in repr(config)
    assert "secret" not in str(store.stats())
    assert store.get(item, artifact_version=ARTIFACT_VERSION).state == HIT

    assert store.invalidate_namespace(item.namespace) == 1
    assert store.get(item, artifact_version=ARTIFACT_VERSION).state == MISS

    assert RedisCacheStore(config, client=FakeRedis(fail_get=True)).get(item, artifact_version=ARTIFACT_VERSION).state == MISS
    assert RedisCacheStore(config, client=FakeRedis(fail_set=True)).set(item, artifact_version=ARTIFACT_VERSION, value={"ok": True}).state == WRITE_FAILED


def test_cache_service_lifecycle_is_owned_by_app_service(monkeypatch):
    service = AppService()
    item = key()
    service.cache_service.set(item, artifact_version=ARTIFACT_VERSION, value={"ok": True})
    assert service.cache_service.get(item, artifact_version=ARTIFACT_VERSION).state == HIT

    monkeypatch.setattr(service.database_service, "build_knowledge_base", lambda **_kwargs: (False, "no db", None))
    service.build_knowledge_base()
    assert service.cache_service.get(item, artifact_version=ARTIFACT_VERSION).state in {MISS, DISABLED}
