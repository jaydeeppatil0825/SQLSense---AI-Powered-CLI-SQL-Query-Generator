import ast
from pathlib import Path

from sql_pipeline.query_plan import build_deterministic_query_plan
from sql_pipeline.sql_validator import validate_sql_contract
from sql_pipeline.validation_result import SQL_VALIDATOR_CONTRACT_VERSION


def _column(name, type_="VARCHAR"):
    return {"name": name, "type": type_}


def _kb():
    return {
        "orders": {
            "columns": [
                _column("order_id", "INTEGER"),
                _column("customer_id", "INTEGER"),
                _column("product_id", "INTEGER"),
                _column("total_amount", "DECIMAL(12,2)"),
                _column("order_status", "VARCHAR(30)"),
            ],
            "primary_keys": ["order_id"],
            "foreign_keys": [
                {"column": "customer_id", "referenced_table": "customers", "referenced_column": "customer_id"},
                {"column": "product_id", "referenced_table": "products", "referenced_column": "product_id"},
            ],
        },
        "customers": {
            "columns": [_column("customer_id", "INTEGER"), _column("customer_name"), _column("city")],
            "primary_keys": ["customer_id"],
            "foreign_keys": [],
        },
        "products": {
            "columns": [_column("product_id", "INTEGER"), _column("supplier_id", "INTEGER"), _column("product_name"), _column("category")],
            "primary_keys": ["product_id"],
            "foreign_keys": [
                {"column": "supplier_id", "referenced_table": "suppliers", "referenced_column": "supplier_id"},
            ],
        },
        "suppliers": {
            "columns": [_column("supplier_id", "INTEGER"), _column("supplier_name"), _column("city")],
            "primary_keys": ["supplier_id"],
            "foreign_keys": [],
        },
        "payments": {
            "columns": [_column("payment_id", "INTEGER"), _column("order_id", "INTEGER"), _column("payment_amount", "DECIMAL(12,2)"), _column("payment_status")],
            "primary_keys": ["payment_id"],
            "foreign_keys": [
                {"column": "order_id", "referenced_table": "orders", "referenced_column": "order_id"},
            ],
        },
    }


def _edge(from_table, from_column, to_table, to_column):
    return {
        "from_table": from_table,
        "from_column": from_column,
        "to_table": to_table,
        "to_column": to_column,
        "relationship_type": "foreign_key",
        "source": "database_metadata",
        "safe_for_planner": True,
    }


def _path(*tables):
    edges = {
        ("orders", "customers"): _edge("orders", "customer_id", "customers", "customer_id"),
        ("orders", "products"): _edge("orders", "product_id", "products", "product_id"),
        ("products", "suppliers"): _edge("products", "supplier_id", "suppliers", "supplier_id"),
        ("payments", "orders"): _edge("payments", "order_id", "orders", "order_id"),
    }
    return {
        "base_table": tables[0],
        "joined_tables": list(tables[1:]),
        "edges": [edges[(left, right)] for left, right in zip(tables, tables[1:])],
        "path_source": "relationship_graph",
        "ambiguity_status": "resolved",
    }


def _output(table, column, *, kind="column"):
    return {
        "kind": kind,
        "table": table,
        "column": column,
        "expression": f"{table}.{column}",
        "alias": f"{table}__{column}",
    }


def _base_context(shape="single_table_list"):
    return {
        "query_shape": shape,
        "route_recommendation": "deterministic_sql_required",
        "selected_tables": [{"table": "orders"}],
        "selected_table_names": ["orders"],
        "selected_output_columns": [_output("orders", "order_id")],
        "schema_fingerprint": "schema-1",
        "graph_fingerprint": "graph-1",
        "planner_contract_version": "planner-v1",
    }


def _plan(context):
    return build_deterministic_query_plan(context)


def test_sql_validation_result_contract_fields_for_simple_lookup():
    result = validate_sql_contract(
        "SELECT orders.order_id AS orders__order_id FROM orders LIMIT 50",
        _kb(),
        deterministic_query_plan=_plan(_base_context()),
    )

    assert result.valid is True
    assert result.validator_contract_version == SQL_VALIDATOR_CONTRACT_VERSION
    assert result.status == "passed"
    assert result.reason_code == "validation_passed"
    assert result.query_shape == "single_table_list"
    assert result.route == "deterministic_sql_required"
    assert result.schema_fingerprint == "schema-1"
    assert result.graph_fingerprint == "graph-1"
    assert result.validated_tables == ["orders"]
    assert result.to_dict()["sql_hash"]


def test_valid_filtered_lookup_with_plan():
    context = _base_context("filtered_query")
    context["selected_filters"] = [{"table": "orders", "column": "order_status", "operator": "=", "value": "delivered"}]
    sql = "SELECT orders.order_id AS orders__order_id FROM orders WHERE order_status = 'delivered' LIMIT 50"
    assert validate_sql_contract(sql, _kb(), deterministic_query_plan=_plan(context)).valid is True


def test_valid_simple_aggregate_with_plan():
    context = _base_context("single_table_aggregate")
    context.update(
        {
            "aggregate_function": "sum",
            "selected_metric": {"table": "orders", "column": "total_amount"},
            "selected_output_columns": [
                {"kind": "aggregate", "table": "orders", "column": "total_amount", "expression": "SUM(total_amount)", "alias": "sum_total_amount"}
            ],
        }
    )
    sql = "SELECT SUM(total_amount) AS sum_total_amount FROM orders"
    assert validate_sql_contract(sql, _kb(), deterministic_query_plan=_plan(context)).valid is True


def test_valid_grouped_aggregate_and_having_with_plan():
    context = _base_context("grouped_aggregate")
    context.update(
        {
            "aggregate_function": "count",
            "selected_dimensions": [{"table": "orders", "column": "order_status"}],
            "selected_output_columns": [
                _output("orders", "order_status", kind="dimension"),
                {"kind": "aggregate", "table": "orders", "column": "*", "expression": "COUNT(*)", "alias": "order_count"},
            ],
            "selected_having": [{"aggregate_function": "count", "operator": ">", "value": 5}],
        }
    )
    sql = "SELECT order_status, COUNT(*) AS order_count FROM orders GROUP BY order_status HAVING COUNT(*) > 5"
    assert validate_sql_contract(sql, _kb(), deterministic_query_plan=_plan(context)).valid is True


def test_valid_row_ranking_with_plan():
    context = _base_context("ranking_query")
    context.update({"selected_order_by": {"table": "orders", "column": "total_amount", "direction": "DESC"}, "limit": 5})
    sql = "SELECT orders.order_id AS orders__order_id FROM orders ORDER BY total_amount DESC LIMIT 5"
    assert validate_sql_contract(sql, _kb(), deterministic_query_plan=_plan(context)).valid is True


def test_valid_direct_join_lookup_with_plan():
    path = _path("orders", "customers")
    context = {
        "query_shape": "joined_lookup",
        "route_recommendation": "deterministic_sql_required",
        "selected_tables": [{"table": "orders"}, {"table": "customers"}],
        "selected_join_path": path,
        "selected_output_columns": [_output("orders", "order_id"), _output("customers", "customer_name")],
    }
    sql = (
        "SELECT orders.order_id AS orders__order_id, customers.customer_name AS customers__customer_name "
        "FROM orders INNER JOIN customers ON orders.customer_id = customers.customer_id"
    )
    assert validate_sql_contract(sql, _kb(), deterministic_query_plan=_plan(context)).valid is True


def test_valid_two_edge_joined_aggregate_with_grain_plan():
    path = _path("payments", "orders", "customers")
    context = {
        "query_shape": "joined_aggregate",
        "route_recommendation": "deterministic_sql_required",
        "aggregate_function": "sum",
        "selected_tables": [{"table": "payments"}, {"table": "orders"}, {"table": "customers"}],
        "selected_join_path": path,
        "phase8a_grain_analysis": {"status": "grain_preserved", "grain_preserved": True},
        "selected_metric": {"table": "payments", "column": "payment_amount"},
        "selected_dimensions": [{"table": "customers", "column": "city"}],
        "selected_output_columns": [
            _output("customers", "city", kind="dimension"),
            {
                "kind": "aggregate",
                "table": "payments",
                "column": "payment_amount",
                "aggregate_function": "sum",
                "expression": "SUM(payments.payment_amount)",
                "alias": "sum__payments__payment_amount",
            },
        ],
    }
    sql = (
        "SELECT customers.city AS customers__city, SUM(payments.payment_amount) AS sum__payments__payment_amount "
        "FROM payments INNER JOIN orders ON payments.order_id = orders.order_id "
        "INNER JOIN customers ON orders.customer_id = customers.customer_id GROUP BY customers.city"
    )
    result = validate_sql_contract(sql, _kb(), deterministic_query_plan=_plan(context))
    assert result.valid is True
    assert result.selected_join_path_verified is True
    assert result.grain_verified is True


def test_rejects_non_executable_plan_with_sql():
    context = _base_context()
    context["route_recommendation"] = "cannot_plan_safely"
    result = validate_sql_contract("SELECT orders.order_id FROM orders", _kb(), deterministic_query_plan=_plan(context))
    assert result.valid is False
    assert result.reason_code == "non_executable_deterministic_query_plan_cannot_validate_executable_sql"


def test_rejects_unsupported_sql_constructs_with_plan():
    plan = _plan(_base_context())
    for sql in [
        "SELECT orders.order_id FROM orders UNION SELECT orders.order_id FROM orders",
        "WITH x AS (SELECT orders.order_id FROM orders) SELECT * FROM x",
        "SELECT COUNT(DISTINCT order_id) FROM orders",
        "SELECT orders.order_id FROM orders LIMIT 10 OFFSET 5",
        "SELECT orders.order_id FROM (SELECT * FROM orders) x",
    ]:
        assert validate_sql_contract(sql, _kb(), deterministic_query_plan=plan).valid is False


def test_rejects_extra_table_and_wrong_join_for_plan():
    path = _path("orders", "customers")
    context = {
        "query_shape": "joined_lookup",
        "route_recommendation": "deterministic_sql_required",
        "selected_tables": [{"table": "orders"}, {"table": "customers"}],
        "selected_join_path": path,
        "selected_output_columns": [_output("orders", "order_id"), _output("customers", "customer_name")],
    }
    plan = _plan(context)
    extra_join = (
        "SELECT orders.order_id AS orders__order_id, customers.customer_name AS customers__customer_name "
        "FROM orders INNER JOIN customers ON orders.customer_id = customers.customer_id "
        "INNER JOIN products ON orders.product_id = products.product_id"
    )
    wrong_on = (
        "SELECT orders.order_id AS orders__order_id, customers.customer_name AS customers__customer_name "
        "FROM orders INNER JOIN customers ON orders.order_id = customers.customer_id"
    )
    assert validate_sql_contract(extra_join, _kb(), deterministic_query_plan=plan).valid is False
    assert validate_sql_contract(wrong_on, _kb(), deterministic_query_plan=plan).valid is False


def test_rejects_selected_column_not_approved_by_plan():
    sql = "SELECT orders.order_id AS orders__order_id, orders.total_amount AS orders__total_amount FROM orders LIMIT 50"
    result = validate_sql_contract(sql, _kb(), deterministic_query_plan=_plan(_base_context()))
    assert result.valid is False
    assert "selected_column" in result.reason_code


def test_sql_validator_import_boundaries():
    source = Path("sql_pipeline/sql_validator.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)

    forbidden = {
        "query_pipeline.context_retriever",
        "query_pipeline.planner.role_resolver",
        "query_pipeline.planner.filter_resolver",
        "query_pipeline.planner.join_resolver",
        "query_pipeline.planner.phase7_bfs_join_resolver",
        "query_pipeline.planner.grain_analyzer",
        "kb_pipeline.semantic_providers.provider_chain",
        "sql_pipeline.deterministic_sql_generator",
        "sql_pipeline.query_executor",
    }
    assert not (imports & forbidden)

    query_plan_imports = set()
    query_plan_tree = ast.parse(Path("sql_pipeline/query_plan.py").read_text(encoding="utf-8"))
    for node in ast.walk(query_plan_tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            query_plan_imports.add(node.module)
    assert "sql_pipeline.sql_validator" not in query_plan_imports
