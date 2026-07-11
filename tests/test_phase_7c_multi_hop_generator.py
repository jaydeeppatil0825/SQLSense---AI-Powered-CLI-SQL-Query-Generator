from copy import deepcopy

import pytest

from sql_pipeline.deterministic_sql_generator import generate_deterministic_sql


def _kb():
    return {
        "orders": {
            "columns": [
                {"name": "order_id", "type": "INTEGER"},
                {"name": "customer_id", "type": "INTEGER"},
                {"name": "order_status", "type": "VARCHAR"},
            ]
        },
        "customers": {
            "columns": [
                {"name": "customer_id", "type": "INTEGER"},
                {"name": "region_id", "type": "INTEGER"},
                {"name": "customer_name", "type": "VARCHAR"},
            ]
        },
        "regions": {
            "columns": [
                {"name": "region_id", "type": "INTEGER"},
                {"name": "region_name", "type": "VARCHAR"},
            ]
        },
        "payments": {
            "columns": [
                {"name": "payment_id", "type": "INTEGER"},
                {"name": "order_id", "type": "INTEGER"},
            ]
        },
    }


def _edge(from_table, from_column, to_table, to_column, *, safe=True):
    return {
        "from_table": from_table,
        "from_column": from_column,
        "to_table": to_table,
        "to_column": to_column,
        "relationship_type": "foreign_key",
        "source": "database_metadata",
        "confidence": 1.0,
        "safe_for_planner": safe,
        "evidence": ["fk"],
        "evidence_reasons": ["database metadata"],
    }


def _output(table, column):
    return {
        "table": table,
        "column": column,
        "expression": f"{table}.{column}",
        "alias": f"{table}__{column}",
    }


def _context(path, outputs, *, filters=None, query_shape="joined_lookup"):
    return {
        "query_shape": query_shape,
        "intent": {},
        "selected_tables": [{"table": table} for table in [path["base_table"], *path["joined_tables"]]],
        "selected_join_path": path,
        "selected_output_columns": outputs,
        "selected_filters": filters or [],
        "limit": 50,
        "clause_plan": {
            "clause_shape": query_shape,
            "selected_join_path": path,
            "limit": 50,
            "requires": {"join": True, "limit": True},
            "decision_path": [{"node": "route", "status": "resolved", "reason": "test planner contract is resolved"}],
        },
    }


def _multi_path():
    return {
        "base_table": "orders",
        "joined_tables": ["customers", "regions"],
        "edges": [
            _edge("orders", "customer_id", "customers", "customer_id"),
            _edge("customers", "region_id", "regions", "region_id"),
        ],
        "path_source": "relationship_graph",
        "ambiguity_status": "resolved",
    }


def test_valid_two_edge_sql_outputs_all_three_tables():
    result = generate_deterministic_sql(
        query_context=_context(
            _multi_path(),
            [_output("orders", "order_id"), _output("customers", "customer_name"), _output("regions", "region_name")],
        ),
        knowledge_base=_kb(),
    )

    assert result.status == "generated"
    assert result.sql == (
        "SELECT orders.order_id AS orders__order_id, customers.customer_name AS customers__customer_name, "
        "regions.region_name AS regions__region_name FROM orders "
        "INNER JOIN customers ON orders.customer_id = customers.customer_id "
        "INNER JOIN regions ON customers.region_id = regions.region_id LIMIT 50;"
    )


def test_reversed_edge_orientation_renders_selected_direction():
    path = {
        "base_table": "customers",
        "joined_tables": ["orders", "payments"],
        "edges": [
            _edge("customers", "customer_id", "orders", "customer_id"),
            _edge("orders", "order_id", "payments", "order_id"),
        ],
        "path_source": "relationship_graph",
        "ambiguity_status": "resolved",
    }

    result = generate_deterministic_sql(
        query_context=_context(path, [_output("customers", "customer_name"), _output("payments", "payment_id")]),
        knowledge_base=_kb(),
    )

    assert result.status == "generated"
    assert "INNER JOIN orders ON customers.customer_id = orders.customer_id" in result.sql
    assert "INNER JOIN payments ON orders.order_id = payments.order_id" in result.sql


def test_filters_on_all_path_tables_are_rendered():
    result = generate_deterministic_sql(
        query_context=_context(
            _multi_path(),
            [_output("orders", "order_id")],
            filters=[
                {"table": "orders", "column": "order_status", "operator": "eq", "value": "Delivered"},
                {"table": "customers", "column": "customer_name", "operator": "contains", "value": "Acme"},
                {"table": "regions", "column": "region_name", "operator": "eq", "value": "West"},
            ],
        ),
        knowledge_base=_kb(),
    )

    assert result.status == "generated"
    assert "WHERE orders.order_status = 'Delivered'" in result.sql
    assert "AND customers.customer_name LIKE '%Acme%'" in result.sql
    assert "AND regions.region_name = 'West'" in result.sql


def test_deterministic_join_order_follows_selected_path():
    first = generate_deterministic_sql(query_context=_context(_multi_path(), [_output("regions", "region_name")]), knowledge_base=_kb())
    second_path = deepcopy(_multi_path())
    second = generate_deterministic_sql(query_context=_context(second_path, [_output("regions", "region_name")]), knowledge_base=_kb())

    assert first.sql == second.sql


@pytest.mark.parametrize(
    "mutate, reason",
    [
        (lambda path: path["edges"][0].update({"safe_for_planner": False}), "selected_join_path_not_authorized"),
        (lambda path: path["edges"][0].update({"to_table": "regions"}), "selected_join_path_order_invalid"),
        (lambda path: path["edges"][1].update({"from_table": "orders"}), "selected_join_path_order_invalid"),
        (lambda path: path["edges"][1].update({"to_column": "missing_id"}), "join_edge_not_in_schema"),
        (lambda path: path.update({"joined_tables": ["customers", "orders"]}), "selected_join_path_invalid"),
        (lambda path: path["joined_tables"].append("payments"), "selected_join_path_invalid"),
        (lambda path: (path["joined_tables"].append("payments"), path["edges"].append(_edge("regions", "region_id", "payments", "payment_id"))), "selected_join_path_invalid"),
    ],
)
def test_invalid_multi_hop_paths_are_rejected(mutate, reason):
    path = _multi_path()
    mutate(path)

    result = generate_deterministic_sql(
        query_context=_context(path, [_output("orders", "order_id")]),
        knowledge_base=_kb(),
    )

    assert result.status == "cannot_plan_safely"
    assert result.sql is None
    assert result.reason == reason


def test_direct_lookup_regression_unchanged():
    path = {
        "base_table": "orders",
        "joined_tables": ["customers"],
        "edges": [_edge("orders", "customer_id", "customers", "customer_id")],
        "path_source": "relationship_graph",
        "ambiguity_status": "resolved",
    }
    result = generate_deterministic_sql(
        query_context=_context(path, [_output("orders", "order_id"), _output("customers", "customer_name")]),
        knowledge_base=_kb(),
    )

    assert result.status == "generated"
    assert result.sql == (
        "SELECT orders.order_id AS orders__order_id, customers.customer_name AS customers__customer_name "
        "FROM orders INNER JOIN customers ON orders.customer_id = customers.customer_id LIMIT 50;"
    )


def test_multi_hop_aggregate_remains_blocked():
    result = generate_deterministic_sql(
        query_context=_context(_multi_path(), [_output("regions", "region_name")], query_shape="joined_aggregate"),
        knowledge_base=_kb(),
    )

    assert result.status == "cannot_plan_safely"
    assert result.sql is None
