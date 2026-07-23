import ast
from pathlib import Path

from sql_pipeline.deterministic_sql_generator import DeterministicSqlPlan
from sql_pipeline.renderers.common import render_plan_in_canonical_order, render_predicates
from sql_pipeline.renderers.filter_renderer import dedupe_filters, filter_predicate


def test_common_renderer_keeps_lookup_join_group_having_order_limit_sql():
    plan = DeterministicSqlPlan(
        query_shape="joined_aggregate",
        base_table="orders",
        joins=[
            {
                "table": "customers",
                "edge": {
                    "from_table": "orders",
                    "from_column": "customer_id",
                    "to_table": "customers",
                    "to_column": "customer_id",
                },
            }
        ],
        select_items=[
            {"expression": "customers.city", "alias": "customers__city"},
            {"expression": "SUM(orders.total_amount)", "alias": "sum__orders__total_amount"},
        ],
        where_clauses=["orders.order_status = 'delivered'"],
        group_by=["customers.city"],
        having_clauses=["SUM(orders.total_amount) > 1000"],
        order_by=["sum__orders__total_amount DESC"],
        limit=5,
    )

    assert render_plan_in_canonical_order(plan) == (
        "SELECT customers.city AS customers__city, "
        "SUM(orders.total_amount) AS sum__orders__total_amount FROM orders "
        "INNER JOIN customers ON orders.customer_id = customers.customer_id "
        "WHERE orders.order_status = 'delivered' "
        "GROUP BY customers.city "
        "HAVING SUM(orders.total_amount) > 1000 "
        "ORDER BY sum__orders__total_amount DESC LIMIT 5;"
    )


def test_common_renderer_keeps_single_table_full_row_star_projection():
    plan = DeterministicSqlPlan(
        query_shape="filtered_query",
        base_table="customers",
        where_clauses=["city = 'Pune'"],
        limit=50,
        sql_skeleton_type="filtered_single_table_list",
        projection_mode="full_row",
    )

    assert render_plan_in_canonical_order(plan) == "SELECT * FROM customers WHERE city = 'Pune' LIMIT 50;"


def test_filter_renderer_keeps_operator_and_literal_formatting():
    predicate, reason = filter_predicate(
        "payment_date",
        {"name": "payment_date", "type": "DATE", "semantic_type": "date"},
        "between",
        {"values": ["2026-01-01", "2026-01-31"]},
    )
    assert reason == ""
    assert predicate == "payment_date BETWEEN '2026-01-01' AND '2026-01-31'"

    predicate, reason = filter_predicate(
        "total_amount",
        {"name": "total_amount", "type": "DECIMAL(12,2)", "semantic_type": "numeric_candidate"},
        "gt",
        {"value": "5000.00"},
    )
    assert reason == ""
    assert predicate == "total_amount > 5000.00"


def test_filter_renderer_dedupes_without_changing_first_order():
    filters = [
        {"table": "orders", "column": "status", "operator": "eq", "value": "paid"},
        {"table": "orders", "column": "status", "operator": "eq", "value": "paid"},
        {"table": "orders", "column": "city", "operator": "eq", "value": "Pune"},
    ]

    assert dedupe_filters(filters) == [filters[0], filters[2]]
    assert render_predicates(["a = 1", "b = 2"], ["", "or"]) == "a = 1 OR b = 2"


def test_renderers_do_not_import_planner_runtime_or_ai_modules():
    forbidden = (
        "query_pipeline",
        "sql_pipeline.question_service",
        "sql_pipeline.query_executor",
        "sql_pipeline.sql_validator",
        "kb_pipeline.semantic_providers",
        "core.ai_backend_service",
    )
    offenders = []
    for path in Path("sql_pipeline/renderers").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and any(str(node.module or "").startswith(name) for name in forbidden):
                offenders.append((str(path), node.module))
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if any(alias.name.startswith(name) for name in forbidden):
                        offenders.append((str(path), alias.name))

    assert offenders == []
