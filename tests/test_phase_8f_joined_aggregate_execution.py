from copy import deepcopy
from unittest.mock import MagicMock

from sqlalchemy import Column, Integer, MetaData, Numeric, String, Table, create_engine

from core.app_service import AppService
from sql_pipeline.query_executor import execute_query
from tests.test_phase_7f_multi_hop_execution import _app as _phase7_lookup_app
from tests.test_phase_7f_multi_hop_execution import QUESTION as PHASE7_QUESTION
from tests.test_phase_8c_joined_aggregate_generator import _context, _kb, _sql


QUESTION = "total payment amount by customer city"


def _engine():
    engine = create_engine("sqlite:///:memory:")
    metadata = MetaData()
    payments = Table(
        "payments",
        metadata,
        Column("payment_id", Integer, primary_key=True),
        Column("order_id", Integer),
        Column("payment_amount", Numeric),
        Column("payment_status", String),
    )
    orders = Table(
        "orders",
        metadata,
        Column("order_id", Integer, primary_key=True),
        Column("customer_id", Integer),
        Column("order_status", String),
    )
    customers = Table(
        "customers",
        metadata,
        Column("customer_id", Integer, primary_key=True),
        Column("city", String),
        Column("customer_status", String),
    )
    metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(
            customers.insert(),
            [
                {"customer_id": 1, "city": "Mumbai", "customer_status": "Active"},
                {"customer_id": 2, "city": "Pune", "customer_status": "Inactive"},
                {"customer_id": 3, "city": "Mumbai", "customer_status": "Active"},
            ],
        )
        conn.execute(
            orders.insert(),
            [
                {"order_id": 10, "customer_id": 1, "order_status": "Delivered"},
                {"order_id": 20, "customer_id": 2, "order_status": "Pending"},
                {"order_id": 30, "customer_id": 1, "order_status": "Delivered"},
                {"order_id": 40, "customer_id": 3, "order_status": "Delivered"},
            ],
        )
        conn.execute(
            payments.insert(),
            [
                {"payment_id": 1, "order_id": 10, "payment_amount": 100, "payment_status": "Paid"},
                {"payment_id": 2, "order_id": 20, "payment_amount": 200, "payment_status": "Pending"},
                {"payment_id": 3, "order_id": 30, "payment_amount": 300, "payment_status": "Paid"},
                {"payment_id": 4, "order_id": 40, "payment_amount": 400, "payment_status": "Paid"},
            ],
        )
    return engine


def _pipeline_context(context):
    return {
        "normalized_question": QUESTION,
        "query_context": deepcopy(context),
        "plan": {},
        "retrieved_context": {},
        "route_recommendation": context["route_recommendation"],
        "complex_sql_plan": {},
        "formula_evidence": [],
        "evidence_sources": [],
    }


def _app(context):
    service = AppService()
    service.database_service.engine = _engine()
    service.database_service.knowledge_base = _kb()
    service.database_ready = True
    service.query_pipeline.run = lambda **_: _pipeline_context(context)
    return service


def _run(context):
    service = _app(context)
    result = service.process_question(QUESTION, ai_backend="local")
    success, message, rows = service.execute_sql(service.get_last_sql(), revalidate=True)
    return result, success, message, rows


def test_executes_valid_two_edge_sum_avg_and_count():
    for aggregate, key in [
        ("sum", "sum__payments__payment_amount"),
        ("avg", "avg__payments__payment_amount"),
        ("count", "count__payments__rows"),
    ]:
        result, success, message, rows = _run(_context(aggregate=aggregate))

        assert result["success"] is True
        assert success is True
        assert message == "Query executed successfully"
        assert any(row["customers__city"] == "Mumbai" and float(row[key]) > 0 for row in rows)


def test_executes_where_having_order_limit_case():
    context = _context(
        filters=[
            {"table": "payments", "column": "payment_status", "operator": "eq", "value": "Paid"},
            {"table": "orders", "column": "order_status", "operator": "eq", "value": "Delivered"},
            {"table": "customers", "column": "customer_status", "operator": "eq", "value": "Active"},
        ],
        having=[{"aggregate_function": "sum", "table": "payments", "column": "payment_amount", "operator": "gt", "value": 100}],
        order=True,
        limit=1,
    )

    _, success, _, rows = _run(context)

    assert success is True
    assert rows == [{"customers__city": "Mumbai", "sum__payments__payment_amount": 800}]


def test_missing_query_context_fails_before_connect():
    context = _context()
    engine = MagicMock()

    try:
        execute_query(
            _sql(context),
            engine,
            knowledge_base=_kb(),
            selected_join_path=context["selected_join_path"],
        )
    except ValueError as exc:
        assert "structure invalid" in str(exc)
    else:
        raise AssertionError("expected validation failure")

    engine.connect.assert_not_called()


def test_altered_sql_and_missing_path_fail_before_connect():
    service = _app(_context())
    service.process_question(QUESTION, ai_backend="local")
    service.database_service.engine = MagicMock()

    success, message, rows = service.execute_sql(service.get_last_sql() + " ", revalidate=True)

    assert success is False
    assert "Execution failed" in message
    assert rows is None
    service.database_service.engine.connect.assert_not_called()


def test_false_grain_or_mismatched_context_fails_before_connect():
    context = _context()
    bad = deepcopy(context)
    bad["phase8a_grain_analysis"] = {"grain_preserved": False, "status": "row_multiplication_risk"}
    engine = MagicMock()

    try:
        execute_query(
            _sql(context),
            engine,
            knowledge_base=_kb(),
            selected_join_path=context["selected_join_path"],
            query_context=bad,
        )
    except ValueError as exc:
        assert "structure invalid" in str(exc)
    else:
        raise AssertionError("expected validation failure")

    engine.connect.assert_not_called()


def test_stale_graph_edge_fails_before_connect():
    context = _context()
    stale_kb = _kb()
    stale_kb["orders"]["foreign_keys"] = []
    engine = MagicMock()

    try:
        execute_query(
            _sql(context),
            engine,
            knowledge_base=stale_kb,
            selected_join_path=context["selected_join_path"],
            query_context=context,
        )
    except ValueError as exc:
        assert "structure invalid" in str(exc)
    else:
        raise AssertionError("expected validation failure")

    engine.connect.assert_not_called()


def test_phase7_lookup_execution_still_works(monkeypatch):
    service, _ = _phase7_lookup_app(monkeypatch)

    service.process_question(PHASE7_QUESTION, ai_backend="local")
    success, _, rows = service.execute_sql(service.get_last_sql(), revalidate=True)

    assert success is True
    assert rows
