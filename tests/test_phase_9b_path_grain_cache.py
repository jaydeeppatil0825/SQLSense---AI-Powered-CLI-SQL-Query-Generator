from copy import deepcopy

from core.cache_service import CacheConfig, MemoryCacheStore, RedisCacheStore
from query_pipeline.planner.phase7_bfs_join_resolver import resolve_safe_multi_hop_path
from query_pipeline.planner.phase8a_grain_analyzer import analyze_selected_path_grain
from query_pipeline.planner.phase9b_cache import (
    GRAIN_ANALYSIS_ARTIFACT_TYPE,
    RELATIONSHIP_PATH_ARTIFACT_TYPE,
    cached_direct_join_relationships,
    cached_grain_analysis,
    cached_multi_hop_path,
)


DB_ID = {"db_engine": "mysql", "db_host": "localhost", "db_port": "3306", "db_name": "phase9b"}


def _schema() -> dict:
    return {
        "orders": {
            "primary_keys": ["order_id"],
            "foreign_keys": [{"column": "customer_id", "referenced_table": "customers", "referenced_column": "customer_id"}],
            "columns": [{"name": "order_id"}, {"name": "customer_id"}, {"name": "amount"}],
        },
        "customers": {
            "primary_keys": ["customer_id"],
            "foreign_keys": [{"column": "city_id", "referenced_table": "cities", "referenced_column": "city_id"}],
            "columns": [{"name": "customer_id"}, {"name": "city_id"}, {"name": "name"}],
        },
        "cities": {
            "primary_keys": ["city_id"],
            "foreign_keys": [],
            "columns": [{"name": "city_id"}, {"name": "city_name"}],
        },
    }


def _graph(*, unsafe_city=False, bridge=False, extra=False) -> dict:
    schema = _schema()
    graph = {table: {"table_name": table, "edges": []} for table in schema}
    for child, child_col, parent, parent_col, safe, rel_type in (
        ("orders", "customer_id", "customers", "customer_id", True, "foreign_key"),
        ("customers", "city_id", "cities", "city_id", not unsafe_city, "bridge" if bridge else "foreign_key"),
    ):
        common = {
            "authoritative_from_table": child,
            "authoritative_from_column": child_col,
            "authoritative_to_table": parent,
            "authoritative_to_column": parent_col,
            "relationship_type": rel_type,
            "source": "database_metadata",
            "confidence": 1.0,
            "safe_for_planner": safe,
            "is_inferred": False,
            "is_fallback": False,
            "evidence": ["fk"],
            "evidence_reasons": ["metadata"],
        }
        graph[child]["edges"].append({"to_table": parent, "from_column": child_col, "to_column": parent_col, **common})
        graph[parent]["edges"].append({"to_table": child, "from_column": parent_col, "to_column": child_col, **common})
    if extra:
        graph["orders"]["edges"].append({
            "to_table": "cities",
            "from_column": "customer_id",
            "to_column": "city_id",
            "authoritative_from_table": "orders",
            "authoritative_from_column": "customer_id",
            "authoritative_to_table": "cities",
            "authoritative_to_column": "city_id",
            "relationship_type": "foreign_key",
            "source": "database_metadata",
            "confidence": 1.0,
            "safe_for_planner": False,
        })
    return graph


def _path(*tables: str) -> dict:
    graph = _graph()
    edges = []
    for left, right in zip(tables, tables[1:]):
        edge = next(item for item in graph[left]["edges"] if item["to_table"] == right)
        edges.append({
            "from_table": left,
            "from_column": edge["from_column"],
            "to_table": right,
            "to_column": edge["to_column"],
            "relationship_type": edge["relationship_type"],
            "safe_for_planner": edge["safe_for_planner"],
        })
    return {"base_table": tables[0], "joined_tables": list(tables[1:]), "edges": edges, "path_source": "relationship_graph", "ambiguity_status": "resolved"}


def _bfs(graph: dict, base="orders", target="cities") -> dict:
    return resolve_safe_multi_hop_path(
        base_table=base,
        target_table=target,
        relationship_graph=graph,
        schema=_schema(),
        max_depth=2,
    )


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


def test_relationship_path_cache_miss_hit_orientation_and_fingerprint_miss():
    store = MemoryCacheStore(CacheConfig(backend="memory"))
    graph = _graph()
    schema = _schema()
    calls = {"count": 0}

    def compute():
        calls["count"] += 1
        return _bfs(graph)

    first = cached_multi_hop_path(
        cache_store=store,
        database_identity=DB_ID,
        knowledge_base=schema,
        relationship_graph=graph,
        base_table="orders",
        target_table="cities",
        max_depth=2,
        compute=compute,
        candidate_tables=["orders", "cities"],
    )
    second = cached_multi_hop_path(
        cache_store=store,
        database_identity=DB_ID,
        knowledge_base=schema,
        relationship_graph=graph,
        base_table="orders",
        target_table="cities",
        max_depth=2,
        compute=compute,
        candidate_tables=["orders", "cities"],
    )

    assert first == second
    assert calls["count"] == 1
    assert store.stats()["artifacts"][RELATIONSHIP_PATH_ARTIFACT_TYPE]["hit"] == 1

    reverse = cached_multi_hop_path(
        cache_store=store,
        database_identity=DB_ID,
        knowledge_base=schema,
        relationship_graph=graph,
        base_table="cities",
        target_table="orders",
        max_depth=2,
        compute=lambda: _bfs(graph, "cities", "orders"),
        candidate_tables=["cities", "orders"],
    )
    assert reverse["tables"] == ["cities", "customers", "orders"]

    mutated_graph = _graph(extra=True)
    cached_multi_hop_path(
        cache_store=store,
        database_identity=DB_ID,
        knowledge_base=schema,
        relationship_graph=mutated_graph,
        base_table="orders",
        target_table="cities",
        max_depth=2,
        compute=lambda: _bfs(mutated_graph),
        candidate_tables=["orders", "cities"],
    )
    assert store.stats()["artifacts"][RELATIONSHIP_PATH_ARTIFACT_TYPE]["miss"] >= 3


def test_direct_cache_prefers_direct_result_and_separates_candidate_sets():
    store = MemoryCacheStore(CacheConfig(backend="memory"))
    graph = _graph()
    schema = _schema()

    direct = cached_direct_join_relationships(
        cache_store=store,
        database_identity=DB_ID,
        knowledge_base=schema,
        relationship_graph=graph,
        source_table="orders",
        target_table="customers",
        candidate_tables=["orders", "customers"],
    )
    cached = cached_direct_join_relationships(
        cache_store=store,
        database_identity=DB_ID,
        knowledge_base=schema,
        relationship_graph=graph,
        source_table="orders",
        target_table="customers",
        candidate_tables=["orders", "customers"],
    )
    different_candidates = cached_direct_join_relationships(
        cache_store=store,
        database_identity=DB_ID,
        knowledge_base=schema,
        relationship_graph=graph,
        source_table="orders",
        target_table="customers",
        candidate_tables=["orders", "customers", "cities"],
    )
    different_options = cached_direct_join_relationships(
        cache_store=store,
        database_identity=DB_ID,
        knowledge_base=schema,
        relationship_graph=graph,
        source_table="orders",
        target_table="customers",
        candidate_tables=["orders", "customers"],
        planner_options={"max_edges": 2},
    )

    assert len(direct) == 1
    assert cached == direct
    assert different_candidates == direct
    assert different_options == direct
    assert store.stats()["artifacts"][RELATIONSHIP_PATH_ARTIFACT_TYPE]["hit"] == 1
    assert store.stats()["artifacts"][RELATIONSHIP_PATH_ARTIFACT_TYPE]["miss"] == 3


def test_relationship_path_cache_degrades_on_redis_failure():
    store = RedisCacheStore(CacheConfig(backend="redis", redis_url="redis://localhost:6379/0"), client=FailingRedis())
    graph = _graph()
    calls = {"count": 0}

    def compute():
        calls["count"] += 1
        return _bfs(graph)

    result = cached_multi_hop_path(
        cache_store=store,
        database_identity=DB_ID,
        knowledge_base=_schema(),
        relationship_graph=graph,
        base_table="orders",
        target_table="cities",
        max_depth=2,
        compute=compute,
    )

    assert result["status"] == "unique_safe_path"
    assert calls["count"] == 1


def test_memory_and_redis_path_artifacts_return_same_result():
    graph = _graph()
    schema = _schema()
    memory = MemoryCacheStore(CacheConfig(backend="memory"))
    redis = RedisCacheStore(CacheConfig(backend="redis", redis_url="redis://localhost:6379/0"), client=DictRedis())

    def run(store):
        return cached_multi_hop_path(
            cache_store=store,
            database_identity=DB_ID,
            knowledge_base=schema,
            relationship_graph=graph,
            base_table="orders",
            target_table="cities",
            max_depth=2,
            compute=lambda: _bfs(graph),
        )

    assert run(memory) == run(redis)
    assert run(memory) == run(redis)
    assert memory.stats()["artifacts"][RELATIONSHIP_PATH_ARTIFACT_TYPE]["hit"] == 1
    assert redis.stats()["artifacts"][RELATIONSHIP_PATH_ARTIFACT_TYPE]["hit"] == 1


def test_grain_cache_hit_key_separation_rejections_and_disabled_equivalence():
    store = MemoryCacheStore(CacheConfig(backend="memory"))
    graph = _graph()
    schema = _schema()
    path = _path("orders", "customers", "cities")

    first = cached_grain_analysis(
        cache_store=store,
        database_identity=DB_ID,
        knowledge_base=schema,
        relationship_graph=graph,
        metric_base_table="orders",
        selected_join_path=path,
        aggregate_function="sum",
        count_base_table=None,
        metric_column="amount",
        dimension_table="cities",
        dimension_column="city_name",
    )
    second = cached_grain_analysis(
        cache_store=store,
        database_identity=DB_ID,
        knowledge_base=schema,
        relationship_graph=graph,
        metric_base_table="orders",
        selected_join_path=path,
        aggregate_function="sum",
        count_base_table=None,
        metric_column="amount",
        dimension_table="cities",
        dimension_column="city_name",
    )
    count_result = cached_grain_analysis(
        cache_store=store,
        database_identity=DB_ID,
        knowledge_base=schema,
        relationship_graph=graph,
        metric_base_table="orders",
        selected_join_path=path,
        aggregate_function="count",
        count_base_table="orders",
        dimension_table="cities",
        dimension_column="city_name",
    )
    disabled = cached_grain_analysis(
        cache_store=None,
        database_identity=DB_ID,
        knowledge_base=schema,
        relationship_graph=graph,
        metric_base_table="orders",
        selected_join_path=path,
        aggregate_function="sum",
        count_base_table=None,
        metric_column="amount",
        dimension_table="cities",
        dimension_column="city_name",
    )

    assert first == second == disabled
    assert first["status"] == "grain_preserved"
    assert count_result["status"] == "grain_preserved"
    assert store.stats()["artifacts"][GRAIN_ANALYSIS_ARTIFACT_TYPE]["hit"] == 1
    assert store.stats()["artifacts"][GRAIN_ANALYSIS_ARTIFACT_TYPE]["miss"] == 2

    parent_to_child = cached_grain_analysis(
        cache_store=store,
        database_identity=DB_ID,
        knowledge_base=schema,
        relationship_graph=graph,
        metric_base_table="customers",
        selected_join_path=_path("customers", "orders"),
        aggregate_function="sum",
        count_base_table=None,
        metric_column="customer_id",
        dimension_table="orders",
        dimension_column="order_id",
    )
    bridge = cached_grain_analysis(
        cache_store=store,
        database_identity=DB_ID,
        knowledge_base=schema,
        relationship_graph=_graph(bridge=True),
        metric_base_table="orders",
        selected_join_path=path,
        aggregate_function="sum",
        count_base_table=None,
        metric_column="amount",
        dimension_table="cities",
        dimension_column="city_name",
    )

    assert parent_to_child["status"] == "row_multiplication_risk"
    assert bridge["status"] == "unsupported_bridge_path"
    assert first == analyze_selected_path_grain(
        metric_base_table="orders",
        selected_join_path=path,
        relationship_graph=graph,
        schema=schema,
        aggregate_function="sum",
        count_base_table=None,
    )


def test_corrupt_grain_artifact_recomputes_without_changing_output():
    store = MemoryCacheStore(CacheConfig(backend="memory"))
    graph = _graph()
    schema = _schema()
    path = _path("orders", "customers", "cities")
    expected = cached_grain_analysis(
        cache_store=store,
        database_identity=DB_ID,
        knowledge_base=schema,
        relationship_graph=graph,
        metric_base_table="orders",
        selected_join_path=path,
        aggregate_function="sum",
        count_base_table=None,
        metric_column="amount",
        dimension_table="cities",
        dimension_column="city_name",
    )

    corrupted = deepcopy(store._entries)
    key_hash = next(iter(corrupted))
    store._entries[key_hash] = "not-json"
    recomputed = cached_grain_analysis(
        cache_store=store,
        database_identity=DB_ID,
        knowledge_base=schema,
        relationship_graph=graph,
        metric_base_table="orders",
        selected_join_path=path,
        aggregate_function="sum",
        count_base_table=None,
        metric_column="amount",
        dimension_table="cities",
        dimension_column="city_name",
    )

    assert recomputed == expected
    assert store.stats()["artifacts"][GRAIN_ANALYSIS_ARTIFACT_TYPE]["corrupt"] == 1
