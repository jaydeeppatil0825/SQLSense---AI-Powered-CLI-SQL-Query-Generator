from unittest.mock import MagicMock

import pytest
from sqlalchemy import Column, Integer, MetaData, String, Table, create_engine

from core.app_service import AppService
from kb_pipeline.relationship_graph import build_relationship_graph, find_safe_direct_join_relationships
from sql_pipeline.query_executor import execute_query

from tests.test_phase_7b_planner_multi_hop import _context, _kb


QUESTION = "show orders with region details"


def _pipeline_result(question, context):
    return {
        "normalized_question": question,
        "query_context": context,
        "plan": context["plan"],
        "retrieved_context": context["retrieved_context"],
        "route_recommendation": context["route_recommendation"],
        "complex_sql_plan": context["complex_sql_plan"],
        "formula_evidence": [],
        "evidence_sources": context["evidence_sources"],
    }


def _engine():
    engine = create_engine("sqlite:///:memory:")
    metadata = MetaData()
    orders = Table(
        "orders",
        metadata,
        Column("order_id", Integer, primary_key=True),
        Column("customer_id", Integer),
        Column("region_id", Integer),
        Column("order_amount", Integer),
    )
    customers = Table(
        "customers",
        metadata,
        Column("customer_id", Integer, primary_key=True),
        Column("region_id", Integer),
        Column("customer_segment", String),
    )
    regions = Table(
        "regions",
        metadata,
        Column("region_id", Integer, primary_key=True),
        Column("region_name", String),
    )
    metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(regions.insert(), [{"region_id": 10, "region_name": "West"}])
        conn.execute(customers.insert(), [{"customer_id": 1, "region_id": 10, "customer_segment": "Retail"}])
        conn.execute(orders.insert(), [{"order_id": 100, "customer_id": 1, "region_id": 10, "order_amount": 500}])
    return engine


def _app(monkeypatch):
    kb = _kb()
    context = _context(QUESTION, kb)
    service = AppService()
    service.database_service.engine = _engine()
    service.database_service.knowledge_base = kb
    service.database_ready = True
    monkeypatch.setattr(service.query_pipeline, "run", lambda **_: _pipeline_result(QUESTION, context))
    return service, context


def test_valid_two_edge_lookup_executes_with_stored_path(monkeypatch):
    service, context = _app(monkeypatch)

    result = service.process_question(QUESTION, ai_backend="local")
    success, message, rows = service.execute_sql(service.get_last_sql(), revalidate=True)

    assert result["success"] is True
    assert result["validation_result"]["is_valid"] is True
    assert service.result_service.get_last_selected_join_path() == context["selected_join_path"]
    assert success is True
    assert message == "Query executed successfully"
    assert rows[0]["regions__region_name"] == "West"


def test_missing_path_fails_before_db_execution():
    engine = MagicMock()
    sql = (
        "SELECT orders.order_id AS orders__order_id FROM orders "
        "INNER JOIN customers ON orders.customer_id = customers.customer_id "
        "INNER JOIN regions ON customers.region_id = regions.region_id LIMIT 50;"
    )

    with pytest.raises(ValueError, match="selected"):
        execute_query(sql, engine, knowledge_base=_kb())

    engine.connect.assert_not_called()


def test_mismatched_or_altered_path_fails_before_db_execution():
    engine = MagicMock()
    context = _context(QUESTION, _kb())
    path = context["selected_join_path"]
    sql = (
        "SELECT orders.order_id AS orders__order_id FROM orders "
        "INNER JOIN customers ON orders.order_id = customers.customer_id "
        "INNER JOIN regions ON customers.region_id = regions.region_id LIMIT 50;"
    )

    with pytest.raises(ValueError, match="structure invalid"):
        execute_query(sql, engine, knowledge_base=_kb(), selected_join_path=path)

    engine.connect.assert_not_called()


def test_stale_graph_edge_fails_before_db_execution():
    engine = MagicMock()
    context = _context(QUESTION, _kb())
    stale_kb = _kb()
    stale_kb["customers"]["foreign_keys"] = []
    sql = (
        "SELECT orders.order_id AS orders__order_id FROM orders "
        "INNER JOIN customers ON orders.customer_id = customers.customer_id "
        "INNER JOIN regions ON customers.region_id = regions.region_id LIMIT 50;"
    )

    with pytest.raises(ValueError, match="structure invalid"):
        execute_query(sql, engine, knowledge_base=stale_kb, selected_join_path=context["selected_join_path"])

    engine.connect.assert_not_called()


def test_direct_execution_remains_unchanged():
    rows = execute_query(
        "SELECT orders.order_id AS orders__order_id, customers.customer_segment AS customers__customer_segment "
        "FROM orders INNER JOIN customers ON orders.customer_id = customers.customer_id LIMIT 50;",
        _engine(),
        knowledge_base=_kb(),
    )

    assert rows == [{"orders__order_id": 100, "customers__customer_segment": "Retail"}]


def test_kb_graph_path_reaches_execution(monkeypatch):
    kb = _kb()
    graph = build_relationship_graph(kb, infer_relationships=False)

    first = find_safe_direct_join_relationships(graph, "orders", "customers")
    second = find_safe_direct_join_relationships(graph, "customers", "regions")
    service, context = _app(monkeypatch)
    service.process_question(QUESTION, ai_backend="local")
    success, _, rows = service.execute_sql(service.get_last_sql(), revalidate=True)

    assert len(first) == len(second) == 1
    assert all(edge["source"] == "database_metadata" and edge["confidence"] == 1.0 for edge in [first[0], second[0]])
    assert len(context["selected_join_path"]["edges"]) == 2
    assert success is True
    assert rows


def test_multi_hop_aggregate_remains_blocked():
    context = _context("show total order amount by region name", _kb())

    assert context["route_recommendation"] == "cannot_plan_safely"
    assert context.get("selected_join_path") is None
