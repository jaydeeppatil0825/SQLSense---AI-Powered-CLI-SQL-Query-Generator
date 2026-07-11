from copy import deepcopy

from query_pipeline.planner.phase7_bfs_join_resolver import resolve_safe_multi_hop_path


def _schema(*tables: str) -> dict:
    return {
        table: {
            "columns": [
                {"name": "id"},
                {"name": f"{table}_id"},
                {"name": "next_id"},
                {"name": "customer_id"},
                {"name": "city_id"},
            ]
        }
        for table in tables
    }


def _graph(*edges: tuple[str, str, str, str, bool]) -> dict:
    tables = sorted({table for edge in edges for table in (edge[0], edge[2])})
    graph = {table: {"table_name": table, "edges": []} for table in tables}
    for from_table, from_column, to_table, to_column, safe in edges:
        common = {
            "authoritative_from_table": from_table,
            "authoritative_from_column": from_column,
            "authoritative_to_table": to_table,
            "authoritative_to_column": to_column,
            "relationship_type": "foreign_key",
            "source": "database_metadata",
            "confidence": 1.0,
            "safe_for_planner": safe,
            "evidence": ["fk"],
            "evidence_reasons": ["database metadata"],
        }
        graph[from_table]["edges"].append({"to_table": to_table, "from_column": from_column, "to_column": to_column, **common})
        graph[to_table]["edges"].append({"to_table": from_table, "from_column": to_column, "to_column": from_column, **common})
    return graph


def _resolve(base: str, target: str, graph: dict, schema: dict, max_depth: int = 2) -> dict:
    return resolve_safe_multi_hop_path(
        base_table=base,
        target_table=target,
        relationship_graph=graph,
        schema=schema,
        max_depth=max_depth,
    )


def test_unique_two_edge_path():
    result = _resolve(
        "orders",
        "cities",
        _graph(("orders", "customer_id", "customers", "id", True), ("customers", "city_id", "cities", "id", True)),
        _schema("orders", "customers", "cities"),
    )

    assert result["status"] == "unique_safe_path"
    assert result["tables"] == ["orders", "customers", "cities"]
    assert [edge["from_table"] for edge in result["path"]] == ["orders", "customers"]


def test_reversed_edge_traversal_preserves_column_ownership():
    result = _resolve(
        "customers",
        "orders",
        _graph(("orders", "customer_id", "customers", "id", True)),
        _schema("orders", "customers"),
    )

    assert result["status"] == "unique_safe_path"
    assert result["path"][0]["from_table"] == "customers"
    assert result["path"][0]["from_column"] == "id"
    assert result["path"][0]["to_table"] == "orders"
    assert result["path"][0]["to_column"] == "customer_id"
    assert result["path"][0]["authoritative_from_table"] == "orders"


def test_direct_edge_path():
    result = _resolve(
        "orders",
        "customers",
        _graph(("orders", "customer_id", "customers", "id", True)),
        _schema("orders", "customers"),
    )

    assert result["status"] == "unique_safe_path"
    assert result["edge_count"] == 1


def test_disconnected_tables_fail_closed():
    result = _resolve("orders", "products", _graph(("orders", "customer_id", "customers", "id", True)), _schema("orders", "customers", "products"))

    assert result["status"] == "no_safe_path"


def test_unsafe_intermediate_edge_is_not_used():
    result = _resolve(
        "orders",
        "cities",
        _graph(("orders", "customer_id", "customers", "id", True), ("customers", "city_id", "cities", "id", False)),
        _schema("orders", "customers", "cities"),
    )

    assert result["status"] == "no_safe_path"


def test_missing_table_or_join_column_is_invalid_graph_edge():
    schema = _schema("orders", "customers")
    schema["orders"]["columns"] = [{"name": "id"}]

    result = _resolve(
        "orders",
        "customers",
        _graph(("orders", "customer_id", "customers", "id", True)),
        schema,
    )

    assert result["status"] == "invalid_graph_edge"


def test_cycle_prevention_does_not_loop_forever():
    result = _resolve(
        "orders",
        "products",
        _graph(
            ("orders", "customer_id", "customers", "id", True),
            ("customers", "city_id", "cities", "id", True),
            ("cities", "next_id", "orders", "id", True),
        ),
        _schema("orders", "customers", "cities", "products"),
    )

    assert result["status"] == "no_safe_path"


def test_depth_limit_reports_unsupported_depth():
    result = _resolve(
        "a",
        "d",
        _graph(("a", "next_id", "b", "id", True), ("b", "next_id", "c", "id", True), ("c", "next_id", "d", "id", True)),
        _schema("a", "b", "c", "d"),
        max_depth=2,
    )

    assert result["status"] == "unsupported_depth"


def test_two_equal_safe_paths_fail_ambiguous():
    result = _resolve(
        "orders",
        "regions",
        _graph(
            ("orders", "customer_id", "customers", "id", True),
            ("customers", "next_id", "regions", "id", True),
            ("orders", "next_id", "stores", "id", True),
            ("stores", "next_id", "regions", "id", True),
        ),
        _schema("orders", "customers", "stores", "regions"),
    )

    assert result["status"] == "ambiguous_path"


def test_output_is_deterministic_when_edge_order_changes():
    graph = _graph(("orders", "customer_id", "customers", "id", True), ("orders", "next_id", "noise", "id", False))
    reversed_graph = deepcopy(graph)
    reversed_graph["orders"]["edges"] = list(reversed(reversed_graph["orders"]["edges"]))

    first = _resolve("orders", "customers", graph, _schema("orders", "customers", "noise"))
    second = _resolve("orders", "customers", reversed_graph, _schema("orders", "customers", "noise"))

    assert first == second
