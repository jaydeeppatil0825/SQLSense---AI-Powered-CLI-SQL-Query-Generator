from copy import deepcopy

import pytest

from sql_pipeline.sql_validator import validate_sql_structure


def _kb(*, stale_second=False, unsafe_second=False):
    customers_fk = [] if stale_second else [{"column": "region_id", "referenced_table": "regions", "referenced_column": "region_id"}]
    relationships = []
    if unsafe_second:
        customers_fk = []
        relationships = [
            {
                "from_table": "customers",
                "from_column": "region_id",
                "to_table": "regions",
                "to_column": "region_id",
                "relationship_type": "inferred",
                "source": "kb_build_inference",
                "confidence": 0.95,
                "safe_for_planner": False,
                "evidence": ["test"],
                "evidence_reasons": ["unsafe test edge"],
            }
        ]
    return {
        "orders": {
            "columns": [
                {"name": "order_id", "type": "INTEGER"},
                {"name": "customer_id", "type": "INTEGER"},
                {"name": "order_status", "type": "VARCHAR"},
            ],
            "foreign_keys": [{"column": "customer_id", "referenced_table": "customers", "referenced_column": "customer_id"}],
        },
        "customers": {
            "columns": [
                {"name": "customer_id", "type": "INTEGER"},
                {"name": "region_id", "type": "INTEGER"},
                {"name": "customer_name", "type": "VARCHAR"},
            ],
            "foreign_keys": customers_fk,
            "relationships": relationships,
        },
        "regions": {
            "columns": [
                {"name": "region_id", "type": "INTEGER"},
                {"name": "region_name", "type": "VARCHAR"},
            ],
            "foreign_keys": [],
        },
        "payments": {
            "columns": [
                {"name": "payment_id", "type": "INTEGER"},
                {"name": "order_id", "type": "INTEGER"},
            ],
            "foreign_keys": [{"column": "order_id", "referenced_table": "orders", "referenced_column": "order_id"}],
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


def _path():
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


def _sql(on1="orders.customer_id = customers.customer_id", on2="customers.region_id = regions.region_id"):
    return (
        "SELECT orders.order_id AS orders__order_id, regions.region_name AS regions__region_name "
        f"FROM orders INNER JOIN customers ON {on1} "
        f"INNER JOIN regions ON {on2} LIMIT 50;"
    )


def test_valid_ordered_two_edge_sql_passes_with_selected_path():
    assert validate_sql_structure(_sql(), _kb(), selected_join_path=_path())[0] is True


def test_reversed_on_equalities_pass():
    sql = _sql(
        "customers.customer_id = orders.customer_id",
        "regions.region_id = customers.region_id",
    )

    assert validate_sql_structure(sql, _kb(), selected_join_path=_path())[0] is True


def test_direct_one_edge_sql_remains_valid_without_selected_path():
    sql = (
        "SELECT orders.order_id AS orders__order_id, customers.customer_name AS customers__customer_name "
        "FROM orders INNER JOIN customers ON orders.customer_id = customers.customer_id LIMIT 50;"
    )

    assert validate_sql_structure(sql, _kb())[0] is True


def test_multi_hop_without_selected_path_fails():
    ok, reason = validate_sql_structure(_sql(), _kb())

    assert ok is False
    assert "selected" in reason.lower()


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT orders.order_id AS orders__order_id FROM orders INNER JOIN customers ON orders.customer_id = customers.customer_id LIMIT 50;",
        _sql()[:-1] + " INNER JOIN payments ON payments.order_id = orders.order_id LIMIT 50;",
        (
            "SELECT orders.order_id AS orders__order_id FROM orders "
            "INNER JOIN regions ON customers.region_id = regions.region_id "
            "INNER JOIN customers ON orders.customer_id = customers.customer_id LIMIT 50;"
        ),
    ],
)
def test_missing_extra_or_reordered_join_fails(sql):
    assert validate_sql_structure(sql, _kb(), selected_join_path=_path())[0] is False


@pytest.mark.parametrize(
    "sql",
    [
        _sql("orders.order_id = customers.customer_id"),
        _sql("orders.customer_id = customers.customer_id", "orders.customer_id = regions.region_id"),
    ],
)
def test_altered_or_disconnected_on_column_fails(sql):
    assert validate_sql_structure(sql, _kb(), selected_join_path=_path())[0] is False


def test_cyclic_selected_path_fails():
    path = _path()
    path["joined_tables"] = ["customers", "orders"]

    assert validate_sql_structure(_sql(), _kb(), selected_join_path=path)[0] is False


@pytest.mark.parametrize("kb", [_kb(stale_second=True), _kb(unsafe_second=True)])
def test_stale_or_unsafe_graph_edge_fails(kb):
    assert validate_sql_structure(_sql(), kb, selected_join_path=_path())[0] is False


@pytest.mark.parametrize(
    "sql",
    [
        _sql("o.customer_id = customers.customer_id"),
        _sql("orders.missing_id = customers.customer_id"),
        "SELECT orders.order_id FROM orders INNER JOIN missing ON orders.customer_id = missing.customer_id INNER JOIN regions ON missing.region_id = regions.region_id;",
    ],
)
def test_unknown_alias_table_or_column_fails(sql):
    assert validate_sql_structure(sql, _kb(), selected_join_path=_path())[0] is False


@pytest.mark.parametrize(
    "sql",
    [
        _sql().replace("INNER JOIN customers", "LEFT JOIN customers"),
        _sql().replace("ON orders.customer_id = customers.customer_id", "USING (customer_id)"),
        "SELECT orders.order_id FROM orders, customers, regions;",
    ],
)
def test_non_inner_using_or_comma_join_fails(sql):
    assert validate_sql_structure(sql, _kb(), selected_join_path=_path())[0] is False


def test_select_star_fails():
    assert validate_sql_structure("SELECT * FROM orders INNER JOIN customers ON orders.customer_id = customers.customer_id INNER JOIN regions ON customers.region_id = regions.region_id;", _kb(), selected_join_path=_path())[0] is False


def test_three_or_more_joins_fail():
    path = deepcopy(_path())
    path["joined_tables"].append("payments")
    path["edges"].append(_edge("regions", "region_id", "payments", "payment_id"))
    sql = _sql()[:-1] + " INNER JOIN payments ON payments.order_id = orders.order_id LIMIT 50;"

    assert validate_sql_structure(sql, _kb(), selected_join_path=path)[0] is False
