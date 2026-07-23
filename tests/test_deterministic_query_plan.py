import ast
from pathlib import Path

from sql_pipeline.deterministic_sql_generator import generate_deterministic_sql
from sql_pipeline.query_plan import (
    SQL_PLAN_CONTRACT_VERSION,
    build_deterministic_query_plan,
)


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
            "foreign_keys": [],
        },
        "customers": {
            "columns": [
                _column("customer_id", "INTEGER"),
                _column("customer_name"),
                _column("city"),
            ],
            "primary_keys": ["customer_id"],
            "foreign_keys": [],
        },
        "products": {
            "columns": [
                _column("product_id", "INTEGER"),
                _column("supplier_id", "INTEGER"),
                _column("product_name"),
                _column("category"),
            ],
            "primary_keys": ["product_id"],
            "foreign_keys": [],
        },
        "suppliers": {
            "columns": [
                _column("supplier_id", "INTEGER"),
                _column("supplier_name"),
                _column("city"),
            ],
            "primary_keys": ["supplier_id"],
            "foreign_keys": [],
        },
        "payments": {
            "columns": [
                _column("payment_id", "INTEGER"),
                _column("order_id", "INTEGER"),
                _column("payment_amount", "DECIMAL(12,2)"),
                _column("payment_status"),
            ],
            "primary_keys": ["payment_id"],
            "foreign_keys": [],
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


def _base_context(shape, table="orders"):
    return {
        "query_shape": shape,
        "route_recommendation": "deterministic_sql_required",
        "selected_tables": [{"table": table}],
        "selected_table_names": [table],
        "selected_output_columns": [_output(table, "order_id")],
        "selected_filters": [],
        "schema_fingerprint": "schema-1",
        "kb_fingerprint": "kb-1",
        "graph_fingerprint": "graph-1",
        "planner_contract_version": "planner-v1",
    }


def _aggregate_context(shape="single_table_aggregate"):
    context = _base_context(shape)
    context.update(
        {
            "aggregate_function": "sum",
            "selected_metric": {"table": "orders", "column": "total_amount"},
            "selected_output_columns": [
                {
                    "kind": "aggregate",
                    "table": "orders",
                    "column": "total_amount",
                    "expression": "SUM(total_amount)",
                    "alias": "sum_total_amount",
                }
            ],
        }
    )
    return context


def _grouped_context(*, having=False):
    context = _aggregate_context("grouped_aggregate")
    context["selected_dimensions"] = [{"table": "orders", "column": "order_status"}]
    context["selected_output_columns"] = [
        _output("orders", "order_status", kind="dimension"),
        {
            "kind": "aggregate",
            "table": "orders",
            "column": "total_amount",
            "aggregate_function": "sum",
            "expression": "SUM(total_amount)",
            "alias": "sum__orders__total_amount",
        },
    ]
    if having:
        context["selected_having"] = [
            {
                "table": "orders",
                "column": "total_amount",
                "aggregate_function": "sum",
                "operator": "gt",
                "value": 1000,
            }
        ]
    return context


def _joined_lookup_context(*tables):
    path = _path(*tables)
    outputs = [_output(tables[0], "order_id")]
    if "customers" in tables:
        outputs.append(_output("customers", "customer_name"))
    if "suppliers" in tables:
        outputs.append(_output("suppliers", "supplier_name"))
    return {
        "query_shape": "joined_lookup",
        "route_recommendation": "deterministic_sql_required",
        "selected_tables": [{"table": table} for table in tables],
        "selected_join_path": path,
        "selected_output_columns": outputs,
        "limit": 50,
    }


def _joined_aggregate_context():
    path = _path("payments", "orders", "customers")
    return {
        "query_shape": "joined_aggregate",
        "route_recommendation": "deterministic_sql_required",
        "aggregate_function": "sum",
        "selected_tables": [{"table": table} for table in ["payments", "orders", "customers"]],
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


def test_simple_lookup_plan_conversion():
    context = _base_context("single_table_list")
    plan = build_deterministic_query_plan(context)

    assert plan.contract_version == SQL_PLAN_CONTRACT_VERSION
    assert plan.query_shape == "single_table_list"
    assert plan.executable is True
    assert plan.base_table == "orders"
    assert plan.selected_columns == [_output("orders", "order_id")]


def test_filtered_lookup_plan_conversion():
    context = _base_context("filtered_query")
    context["selected_filters"] = [{"table": "orders", "column": "order_status", "operator": "eq", "value": "delivered"}]
    plan = build_deterministic_query_plan(context)

    assert plan.executable is True
    assert plan.where_filters == context["selected_filters"]


def test_aggregate_plan_conversion():
    plan = build_deterministic_query_plan(_aggregate_context())

    assert plan.aggregate_function == "sum"
    assert plan.aggregate_table == "orders"
    assert plan.aggregate_column == "total_amount"


def test_grouped_aggregate_and_having_plan_conversion():
    plan = build_deterministic_query_plan(_grouped_context(having=True))

    assert plan.group_by_columns == [{"table": "orders", "column": "order_status"}]
    assert plan.having_filters[0]["operator"] == "gt"


def test_row_ranking_and_aggregate_ranking_plan_conversion():
    row = _base_context("ranking_query")
    row.update(
        {
            "selected_metric": {"table": "orders", "column": "total_amount"},
            "selected_order_by": {"table": "orders", "column": "total_amount", "direction": "desc"},
            "ranking_decision": {
                "status": "resolved",
                "ranking_mode": "row_ranking",
                "selected_projection_mode": "row_projection",
            },
            "limit": 5,
        }
    )
    aggregate = _grouped_context()
    aggregate["query_shape"] = "ranking_query"
    aggregate["ranking_decision"] = {
        "status": "resolved",
        "ranking_mode": "grouped_aggregate_ranking",
        "selected_projection_mode": "grouped_aggregate_projection",
    }

    row_plan = build_deterministic_query_plan(row)
    aggregate_plan = build_deterministic_query_plan(aggregate)

    assert row_plan.ranking_decision["ranking_mode"] == "row_ranking"
    assert row_plan.limit == 5
    assert aggregate_plan.ranking_decision["ranking_mode"] == "grouped_aggregate_ranking"


def test_direct_and_bfs_joined_lookup_plan_conversion():
    direct = build_deterministic_query_plan(_joined_lookup_context("orders", "customers"))
    two_edge = build_deterministic_query_plan(_joined_lookup_context("orders", "products", "suppliers"))

    assert direct.selected_join_path["joined_tables"] == ["customers"]
    assert len(direct.join_edges) == 1
    assert two_edge.selected_join_path["joined_tables"] == ["products", "suppliers"]
    assert len(two_edge.join_edges) == 2


def test_direct_and_two_edge_joined_aggregate_plan_conversion_with_grain():
    direct = _joined_aggregate_context()
    direct["selected_join_path"] = _path("orders", "customers")
    direct["selected_tables"] = [{"table": "orders"}, {"table": "customers"}]
    direct["selected_metric"] = {"table": "orders", "column": "total_amount"}
    direct["selected_output_columns"][1].update(
        {
            "table": "orders",
            "column": "total_amount",
            "expression": "SUM(orders.total_amount)",
            "alias": "sum__orders__total_amount",
        }
    )
    direct_plan = build_deterministic_query_plan(direct)
    two_edge_plan = build_deterministic_query_plan(_joined_aggregate_context())

    assert direct_plan.executable is True
    assert two_edge_plan.executable is True
    assert two_edge_plan.grain_decision["grain_preserved"] is True


def test_fail_closed_and_missing_required_fields_are_non_executable():
    blocked = _base_context("filtered_query")
    blocked["route_recommendation"] = "cannot_plan_safely"
    missing = {"query_shape": "joined_lookup", "route_recommendation": "deterministic_sql_required"}

    blocked_plan = build_deterministic_query_plan(blocked)
    missing_plan = build_deterministic_query_plan(missing)

    assert blocked_plan.executable is False
    assert missing_plan.executable is False
    assert missing_plan.missing_required_fields == ["selected_join_path"]


def test_decision_contracts_identity_and_validation_metadata_are_preserved():
    context = _joined_aggregate_context()
    context.update(
        {
            "filter_decision": {"status": "resolved"},
            "metric_decision": {"status": "resolved", "table": "payments", "column": "payment_amount"},
            "dimension_decision": {"status": "resolved", "table": "customers", "column": "city"},
            "join_decision": {"status": "resolved"},
            "schema_fingerprint": "schema-1",
            "kb_fingerprint": "kb-1",
            "graph_fingerprint": "graph-1",
        }
    )
    plan = build_deterministic_query_plan(context)

    assert plan.filter_decision["status"] == "resolved"
    assert plan.metric_decision["column"] == "payment_amount"
    assert plan.dimension_decision["column"] == "city"
    assert plan.join_decision["status"] == "resolved"
    assert plan.schema_fingerprint == "schema-1"
    assert plan.kb_fingerprint == "kb-1"
    assert plan.graph_fingerprint == "graph-1"


def test_generator_uses_canonical_plan_without_recomputing_mutated_context():
    context = _aggregate_context()
    plan = build_deterministic_query_plan(context)
    context["selected_metric"] = {"table": "orders", "column": "order_id"}

    result = generate_deterministic_sql(
        query_context=context,
        knowledge_base=_kb(),
        deterministic_query_plan=plan,
    )

    assert result.status == "generated"
    assert result.sql == "SELECT SUM(total_amount) AS sum_total_amount FROM orders;"
    assert result.query_plan.aggregate_column == "total_amount"


def test_generated_sql_for_representative_case_is_unchanged():
    result = generate_deterministic_sql(query_context=_aggregate_context(), knowledge_base=_kb())

    assert result.status == "generated"
    assert result.sql == "SELECT SUM(total_amount) AS sum_total_amount FROM orders;"
    assert result.query_plan.to_dict()["sql_plan_contract_version"] == SQL_PLAN_CONTRACT_VERSION


def test_query_plan_architecture_imports_stay_one_way():
    query_plan_tree = ast.parse(Path("sql_pipeline/query_plan.py").read_text(encoding="utf-8"))
    generator_tree = ast.parse(Path("sql_pipeline/deterministic_sql_generator.py").read_text(encoding="utf-8"))

    query_plan_imports = {
        alias.name
        for node in ast.walk(query_plan_tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        node.module or ""
        for node in ast.walk(query_plan_tree)
        if isinstance(node, ast.ImportFrom)
    }
    generator_imports = {
        alias.name
        for node in ast.walk(generator_tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        node.module or ""
        for node in ast.walk(generator_tree)
        if isinstance(node, ast.ImportFrom)
    }

    assert "sql_pipeline.deterministic_sql_generator" not in query_plan_imports
    assert "query_pipeline.planner.role_resolver" not in generator_imports
    assert "query_pipeline.planner.phase7_bfs_join_resolver" not in generator_imports
    assert "query_pipeline.planner.grain_analyzer" not in generator_imports
    assert "query_pipeline.context_retriever" not in generator_imports
