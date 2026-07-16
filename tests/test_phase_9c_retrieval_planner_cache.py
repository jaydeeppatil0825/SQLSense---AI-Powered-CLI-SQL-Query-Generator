import json

from core.cache_service import CacheConfig, MemoryCacheStore, RedisCacheStore
from query_pipeline.intent_builder import build_intent
from query_pipeline.planner.phase9c_cache import (
    PLANNER_EVIDENCE_ARTIFACT_TYPE,
    RETRIEVAL_EVIDENCE_ARTIFACT_TYPE,
    cached_retrieve_context,
)
from query_pipeline.query_pipeline import QueryPipeline


DB_ID = {"db_engine": "mysql", "db_host": "localhost", "db_port": "3306", "db_name": "phase9c"}


def _kb() -> dict:
    return {
        "accounts": {
            "columns": [
                {"name": "account_id", "type": "INTEGER", "semantic_type": "id"},
                {"name": "account_label", "type": "VARCHAR(100)", "semantic_type": "name", "is_dimension": True},
            ],
            "primary_keys": ["account_id"],
            "foreign_keys": [],
            "relationships": [],
        },
        "deals": {
            "columns": [
                {"name": "deal_id", "type": "INTEGER", "semantic_type": "id"},
                {"name": "account_id", "type": "INTEGER", "semantic_type": "id"},
                {"name": "deal_value", "type": "DECIMAL(12,2)", "semantic_type": "money", "is_measure": True},
                {"name": "close_date", "type": "DATE", "semantic_type": "date", "is_date": True},
            ],
            "primary_keys": ["deal_id"],
            "foreign_keys": [{"column": "account_id", "referenced_table": "accounts", "referenced_column": "account_id"}],
            "relationships": [],
        },
    }


def _glossary() -> dict:
    return {
        "deal value": {
            "mapped_columns": [{"table": "deals", "column": "deal_value", "confidence": "high"}],
            "business_terms": ["deal amount"],
        }
    }


class FakeVector:
    def __init__(self, *, index="v1", model="m1"):
        self.index = index
        self.model = model
        self.package_calls = 0

    def get_status(self):
        return {
            "backend": "fake",
            "index": self.index,
            "embedding": {"backend": "fake", "model": self.model, "dimension": 384},
        }

    def get_normalized_evidence_package(self, query, top_k=8):
        self.package_calls += 1
        return {
            "candidate_tables": [
                {"table_name": "deals", "score": 0.94},
                {"table_name": "accounts", "score": 0.88},
            ],
            "candidate_columns": [
                {"table_name": "deals", "column_name": "deal_value", "score": 0.94, "semantic_type": "money", "data_type": "DECIMAL(12,2)", "is_measure": True},
                {"table_name": "accounts", "column_name": "account_label", "score": 0.9, "semantic_type": "name", "data_type": "VARCHAR(100)", "is_dimension": True},
                {"table_name": "deals", "column_name": "close_date", "score": 0.7, "semantic_type": "date", "data_type": "DATE", "is_date": True},
            ],
            "candidate_metrics": [
                {"table_name": "deals", "column_name": "deal_value", "score": 0.96, "semantic_type": "money", "data_type": "DECIMAL(12,2)", "is_measure": True}
            ],
            "candidate_dimensions": [
                {"table_name": "accounts", "column_name": "account_label", "score": 0.91, "semantic_type": "name", "data_type": "VARCHAR(100)", "is_dimension": True}
            ],
            "candidate_dates": [
                {"table_name": "deals", "column_name": "close_date", "score": 0.75, "semantic_type": "date", "data_type": "DATE", "is_date": True}
            ],
            "relationships": [
                {"from_table": "deals", "from_column": "account_id", "to_table": "accounts", "to_column": "account_id", "score": 0.8, "source": "vector"}
            ],
            "glossary_matches": [{"term": "deal value", "score": 0.8}],
            "evidence_scores": {"overall": 0.9},
            "retrieval_sources": ["fake_vector"],
            "ambiguity_candidates": {},
            "missing_evidence_indicators": {},
            "source_metadata": {"backend": "fake"},
        }

    def get_relevant_columns(self, _query, top_k=4):
        return []


class FailingRedis:
    def get(self, _key):
        raise TimeoutError("timeout")

    def setex(self, *_args):
        raise TimeoutError("timeout")


class DictRedis:
    def __init__(self):
        self.data = {}

    def get(self, key):
        return self.data.get(key)

    def setex(self, key, _ttl, value):
        self.data[key] = value
        return True

    def delete(self, *keys):
        for key in keys:
            self.data.pop(key, None)
        return len(keys)

    def scan_iter(self, match=None, count=None):
        prefix = (match or "").split("*", 1)[0]
        return (key for key in list(self.data) if key.startswith(prefix))


def _retrieve(store, vector, *, question="total deal value by account", kb=None, glossary=None, require=True):
    intent = build_intent(question)
    return cached_retrieve_context(
        cache_store=store,
        database_identity=DB_ID,
        normalized_question=question,
        intent=intent,
        knowledge_base=kb or _kb(),
        business_glossary=glossary if glossary is not None else _glossary(),
        vector_retriever=vector,
        require_normalized_vector_evidence=require,
    )


def test_retrieval_cache_miss_hit_and_normalized_question_key():
    store = MemoryCacheStore(CacheConfig(backend="memory"))
    vector = FakeVector()

    first = _retrieve(store, vector)
    second = _retrieve(store, vector)

    assert first == second
    assert vector.package_calls == 1
    assert store.stats()["artifacts"][RETRIEVAL_EVIDENCE_ARTIFACT_TYPE]["hit"] == 1


def test_retrieval_key_changes_for_question_options_and_fingerprints():
    store = MemoryCacheStore(CacheConfig(backend="memory"))
    vector = FakeVector()
    _retrieve(store, vector)
    _retrieve(store, vector, question="deal close date by account")
    _retrieve(store, vector, require=False)
    _retrieve(store, FakeVector(index="v2"))
    _retrieve(store, FakeVector(model="m2"))
    changed_glossary = _glossary()
    changed_glossary["deal value"]["business_terms"] = ["deal revenue"]
    _retrieve(store, vector, glossary=changed_glossary)
    changed_kb = _kb()
    changed_kb["deals"]["columns"][2]["unique_count"] = 10
    _retrieve(store, vector, kb=changed_kb)

    assert store.stats()["artifacts"][RETRIEVAL_EVIDENCE_ARTIFACT_TYPE]["miss"] == 7


def test_removed_column_malformed_and_authority_artifacts_recompute():
    store = MemoryCacheStore(CacheConfig(backend="memory"))
    vector = FakeVector()
    expected = _retrieve(store, vector)
    key_hash = next(iter(store._entries))

    envelope = json.loads(store._entries[key_hash])
    envelope["payload"]["payload"]["matched_columns"][0]["column"] = "missing_column"
    store._entries[key_hash] = json.dumps(envelope)
    assert _retrieve(store, vector) == expected

    store._entries[key_hash] = "not-json"
    assert _retrieve(store, vector) == expected

    envelope = json.loads(store._entries[key_hash])
    envelope["payload"]["payload"]["selected_join_path"] = {"base_table": "deals"}
    store._entries[key_hash] = json.dumps(envelope)
    assert _retrieve(store, vector) == expected
    assert vector.package_calls == 4


def test_redis_failure_recomputes_and_memory_redis_match():
    failing = RedisCacheStore(CacheConfig(backend="redis", redis_url="redis://localhost:6379/0"), client=FailingRedis())
    vector = FakeVector()
    assert _retrieve(failing, vector)["matched_tables"]
    assert vector.package_calls == 1

    memory = MemoryCacheStore(CacheConfig(backend="memory"))
    redis = RedisCacheStore(CacheConfig(backend="redis", redis_url="redis://localhost:6379/0"), client=DictRedis())
    memory_vector = FakeVector()
    redis_vector = FakeVector()
    assert _retrieve(memory, memory_vector) == _retrieve(redis, redis_vector)
    assert _retrieve(memory, memory_vector) == _retrieve(redis, redis_vector)
    assert memory.stats()["artifacts"][RETRIEVAL_EVIDENCE_ARTIFACT_TYPE]["hit"] == 1
    assert redis.stats()["artifacts"][RETRIEVAL_EVIDENCE_ARTIFACT_TYPE]["hit"] == 1


def test_cache_enabled_disabled_planner_output_identical_and_forbidden_artifacts_absent():
    question = "total deal value by account"
    cached = QueryPipeline().run(
        question,
        _kb(),
        business_glossary=_glossary(),
        vector_retriever=FakeVector(),
        cache_store=MemoryCacheStore(CacheConfig(backend="memory")),
        cache_database_identity=DB_ID,
    ).to_dict()
    disabled = QueryPipeline().run(
        question,
        _kb(),
        business_glossary=_glossary(),
        vector_retriever=FakeVector(),
        cache_store=None,
        cache_database_identity=DB_ID,
    ).to_dict()

    assert cached == disabled

    store = MemoryCacheStore(CacheConfig(backend="memory"))
    QueryPipeline().run(
        question,
        _kb(),
        business_glossary=_glossary(),
        vector_retriever=FakeVector(),
        cache_store=store,
        cache_database_identity=DB_ID,
    )
    raw_cache = "\n".join(store._entries.values())
    assert RETRIEVAL_EVIDENCE_ARTIFACT_TYPE in store.stats()["artifacts"]
    assert PLANNER_EVIDENCE_ARTIFACT_TYPE in store.stats()["artifacts"]
    for forbidden in ("selected_join_path", "generated_sql", "validation_result", "executor_authorization", "result_rows"):
        assert forbidden not in raw_cache
