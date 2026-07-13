from copy import deepcopy
from unittest.mock import MagicMock

import pytest

from sql_pipeline.query_executor import execute_query
from sql_pipeline.sql_validator import validate_sql_structure
from tests.test_phase_8c_joined_aggregate_generator import _context, _edge, _kb, _sql


def _valid(context=None):
    context = context or _context()
    return _validate(_sql(context), context)


def _validate(sql, context):
    return validate_sql_structure(
        sql,
        _kb(),
        selected_join_path=context["selected_join_path"],
        query_context=context,
    )


def test_accepts_valid_two_edge_sum_avg_count_and_reversed_on():
    assert _valid(_context())[0] is True
    assert _valid(_context(aggregate="avg"))[0] is True
    assert _valid(_context(aggregate="count"))[0] is True

    context = _context()
    sql = _sql(context).replace(
        "payments.order_id = orders.order_id",
        "orders.order_id = payments.order_id",
    ).replace(
        "orders.customer_id = customers.customer_id",
        "customers.customer_id = orders.customer_id",
    )

    assert validate_sql_structure(sql, _kb(), selected_join_path=context["selected_join_path"], query_context=context)[0] is True


def test_accepts_filters_having_order_and_limit_when_planner_selected():
    context = _context(
        filters=[
            {"table": "payments", "column": "payment_status", "operator": "eq", "value": "Paid"},
            {"table": "orders", "column": "order_status", "operator": "eq", "value": "Delivered"},
            {"table": "customers", "column": "customer_status", "operator": "eq", "value": "Active"},
        ],
        having=[{"aggregate_function": "sum", "table": "payments", "column": "payment_amount", "operator": "gt", "value": 1000}],
        order=True,
        limit=3,
    )

    assert _valid(context)[0] is True


@pytest.mark.parametrize(
    "mutate",
    [
        lambda context: context.pop("phase8a_grain_analysis"),
        lambda context: context.update({"phase8a_grain_analysis": {"grain_preserved": False}}),
        lambda context: context.update({"query_shape": "joined_lookup"}),
    ],
)
def test_rejects_missing_false_or_wrong_planner_evidence(mutate):
    sql = _sql(_context())
    context = _context()
    mutate(context)

    assert _validate(sql, context)[0] is False


def test_rejects_manual_sql_without_planner_context_and_executor_before_connect():
    context = _context()
    sql = _sql(context)
    engine = MagicMock()

    assert validate_sql_structure(sql, _kb(), selected_join_path=context["selected_join_path"])[0] is False
    with pytest.raises(ValueError, match="SQL structure invalid"):
        execute_query(sql, engine, knowledge_base=_kb(), selected_join_path=context["selected_join_path"])
    engine.connect.assert_not_called()


@pytest.mark.parametrize(
    "mutate",
    [
        lambda context: context["selected_join_path"]["edges"].reverse(),
        lambda context: context["selected_join_path"]["edges"].pop(),
        lambda context: context["selected_join_path"]["edges"].append(_edge("customers", "customer_id", "payments", "payment_id")),
        lambda context: context["selected_join_path"].update({"joined_tables": ["orders", "payments"]}),
        lambda context: context["selected_join_path"]["edges"][1].update({"from_table": "payments"}),
        lambda context: context["selected_join_path"]["edges"][1].update({"safe_for_planner": False}),
    ],
)
def test_rejects_altered_missing_extra_disconnected_cyclic_or_unsafe_path(mutate):
    sql = _sql(_context())
    context = _context()
    context["selected_join_path"] = deepcopy(context["selected_join_path"])
    context["clause_plan"]["selected_join_path"] = context["selected_join_path"]
    mutate(context)

    assert _validate(sql, context)[0] is False


@pytest.mark.parametrize(
    "sql_replace",
    [
        ("SUM(payments.payment_amount)", "SUM(orders.order_id)"),
        ("customers.city AS customers__city", "orders.order_status AS orders__order_status"),
        ("GROUP BY customers.city", "GROUP BY customers.customer_status"),
        ("COUNT(*)", "COUNT(DISTINCT payments.payment_id)"),
    ],
)
def test_rejects_metric_dimension_group_by_projection_or_count_mismatch(sql_replace):
    context = _context(aggregate="count") if "COUNT" in sql_replace[0] else _context()
    sql = _sql(context).replace(*sql_replace)

    assert validate_sql_structure(sql, _kb(), selected_join_path=context["selected_join_path"], query_context=context)[0] is False


@pytest.mark.parametrize(
    "sql_replace",
    [
        ("INNER JOIN orders", "LEFT JOIN orders"),
        ("ON payments.order_id = orders.order_id", "USING (order_id)"),
        ("SELECT customers.city", "SELECT *"),
    ],
)
def test_rejects_non_inner_using_and_select_star(sql_replace):
    context = _context()
    sql = _sql(context).replace(*sql_replace)

    assert validate_sql_structure(sql, _kb(), selected_join_path=context["selected_join_path"], query_context=context)[0] is False


def test_rejects_extra_filter_not_in_planner_contract():
    context = _context(filters=[{"table": "payments", "column": "payment_status", "operator": "eq", "value": "Paid"}])
    sql = _sql(context).replace(" GROUP BY", " AND orders.order_status = 'Delivered' GROUP BY")

    assert validate_sql_structure(sql, _kb(), selected_join_path=context["selected_join_path"], query_context=context)[0] is False
