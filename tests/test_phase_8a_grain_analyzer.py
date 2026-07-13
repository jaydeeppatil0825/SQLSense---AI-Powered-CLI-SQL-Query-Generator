from copy import deepcopy

from query_pipeline.planner.phase8a_grain_analyzer import analyze_selected_path_grain


def _schema() -> dict:
    return {
        "orders": {
            "primary_keys": ["order_id"],
            "foreign_keys": [{"column": "customer_id", "referenced_table": "customers", "referenced_column": "customer_id"}],
            "columns": [{"name": "order_id"}, {"name": "customer_id"}],
        },
        "customers": {
            "primary_keys": ["customer_id"],
            "foreign_keys": [{"column": "city_id", "referenced_table": "cities", "referenced_column": "city_id"}],
            "columns": [{"name": "customer_id"}, {"name": "city_id"}],
        },
        "cities": {
            "primary_keys": ["city_id"],
            "foreign_keys": [],
            "columns": [{"name": "city_id"}],
        },
    }


def _edge(child: str, child_col: str, parent: str, parent_col: str, *, safe=True, rel_type="foreign_key", source="database_metadata") -> dict:
    return {
        "authoritative_from_table": child,
        "authoritative_from_column": child_col,
        "authoritative_to_table": parent,
        "authoritative_to_column": parent_col,
        "relationship_type": rel_type,
        "source": source,
        "confidence": 1.0,
        "safe_for_planner": safe,
        "evidence": ["fk"],
        "evidence_reasons": ["metadata"],
    }


def _graph() -> dict:
    graph = {table: {"table_name": table, "edges": []} for table in _schema()}
    for child, child_col, parent, parent_col in (
        ("orders", "customer_id", "customers", "customer_id"),
        ("customers", "city_id", "cities", "city_id"),
    ):
        common = _edge(child, child_col, parent, parent_col)
        graph[child]["edges"].append({"to_table": parent, "from_column": child_col, "to_column": parent_col, **common})
        graph[parent]["edges"].append({"to_table": child, "from_column": parent_col, "to_column": child_col, **common})
    return graph


def _path(*tables: str) -> dict:
    graph = _graph()
    edges = []
    for left, right in zip(tables, tables[1:]):
        edge = next(item for item in graph[left]["edges"] if item["to_table"] == right)
        edges.append({"from_table": left, "from_column": edge["from_column"], "to_table": right, "to_column": edge["to_column"]})
    return {"base_table": tables[0], "joined_tables": list(tables[1:]), "edges": edges}


def _analyze(path: dict, *, graph=None, schema=None, base="orders", aggregate=None, count_base=None) -> dict:
    return analyze_selected_path_grain(
        metric_base_table=base,
        selected_join_path=path,
        relationship_graph=graph or _graph(),
        schema=schema or _schema(),
        aggregate_function=aggregate,
        count_base_table=count_base,
    )


def test_safe_two_edge_many_to_one_path_preserves_grain():
    result = _analyze(_path("orders", "customers", "cities"))

    assert result["status"] == "grain_preserved"
    assert [edge["traversal_direction"] for edge in result["edge_diagnostics"]] == ["many_to_one", "many_to_one"]


def test_reversed_stored_edge_orientation_detects_multiplication_risk():
    result = _analyze(_path("customers", "orders"), base="customers")

    assert result["status"] == "row_multiplication_risk"
    assert result["edge_diagnostics"][0]["traversal_direction"] == "one_to_many"


def test_one_to_many_first_edge_is_multiplication_risk():
    result = _analyze(_path("customers", "orders"), base="customers")

    assert result["row_multiplication_risk"] is True


def test_mixed_safe_and_unsafe_path_is_unknown_cardinality():
    graph = _graph()
    next(edge for edge in graph["customers"]["edges"] if edge["to_table"] == "cities")["safe_for_planner"] = False

    result = _analyze(_path("orders", "customers", "cities"), graph=graph)

    assert result["status"] == "unknown_cardinality"


def test_unknown_inferred_cardinality_fails_closed():
    graph = _graph()
    graph["orders"]["edges"][0]["source"] = "semantic_inference"
    graph["orders"]["edges"][0]["relationship_type"] = "inferred"

    result = _analyze(_path("orders", "customers"), graph=graph)

    assert result["status"] == "unknown_cardinality"


def test_bridge_relationship_is_rejected():
    graph = _graph()
    graph["orders"]["edges"][0]["relationship_type"] = "bridge"

    result = _analyze(_path("orders", "customers"), graph=graph)

    assert result["status"] == "unsupported_bridge_path"


def test_missing_pk_fk_evidence_fails_closed():
    schema = _schema()
    schema["customers"]["primary_keys"] = []

    result = _analyze(_path("orders", "customers"), schema=schema)

    assert result["status"] == "unknown_cardinality"


def test_cycle_or_repeated_table_rejected():
    result = _analyze({"base_table": "orders", "joined_tables": ["customers", "orders"], "edges": [{}, {}]})

    assert result["status"] == "unsupported_bridge_path"


def test_more_than_two_edges_rejected():
    result = _analyze({"base_table": "orders", "joined_tables": ["customers", "cities", "regions"], "edges": [{}, {}, {}]})

    assert result["status"] == "unsupported_bridge_path"


def test_count_requires_explicit_base_grain():
    result = _analyze(_path("orders", "customers"), aggregate="count", count_base=None)

    assert result["status"] == "unknown_cardinality"


def test_deterministic_result_when_graph_edge_order_changes():
    graph = _graph()
    reversed_graph = deepcopy(graph)
    reversed_graph["orders"]["edges"] = list(reversed(reversed_graph["orders"]["edges"]))

    assert _analyze(_path("orders", "customers"), graph=graph) == _analyze(_path("orders", "customers"), graph=reversed_graph)


def test_direct_one_edge_path_still_analyzes_without_runtime_changes():
    result = _analyze(_path("orders", "customers"))

    assert result["status"] == "grain_preserved"
