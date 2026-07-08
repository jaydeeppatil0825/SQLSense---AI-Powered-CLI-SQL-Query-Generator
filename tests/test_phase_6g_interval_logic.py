from datetime import date

from query_pipeline.intent_builder import build_intent
from query_pipeline.query_planner import build_query_context
from sql_pipeline.deterministic_sql_generator import generate_deterministic_sql
from sql_pipeline.sql_validator import validate_sql_structure


FIXED_TODAY = date(2026, 7, 8)


def _orders_kb(*, extra_date=False):
    columns = [
        {"name": "order_id", "type": "INTEGER", "semantic_type": "id"},
        {"name": "customer_id", "type": "INTEGER", "semantic_type": "id"},
        {"name": "order_date", "type": "DATE", "semantic_type": "date", "is_date": True},
        {"name": "order_status", "type": "VARCHAR(30)", "semantic_type": "status", "is_dimension": True, "sample_values": ["Delivered", "Pending"]},
        {"name": "total_amount", "type": "DECIMAL(12,2)", "semantic_type": "money", "is_measure": True},
    ]
    if extra_date:
        columns.append({"name": "delivery_date", "type": "DATE", "semantic_type": "date", "is_date": True})
    return {
        "service_orders": {
            "columns": columns,
            "primary_keys": ["order_id"],
            "foreign_keys": [
                {"column": "customer_id", "referenced_table": "customers", "referenced_column": "customer_id"}
            ],
            "relationships": [],
        },
        "customers": {
            "columns": [
                {"name": "customer_id", "type": "INTEGER", "semantic_type": "id"},
                {"name": "city", "type": "VARCHAR(100)", "semantic_type": "text", "is_dimension": True},
                {"name": "customer_segment", "type": "VARCHAR(100)", "semantic_type": "text", "is_dimension": True},
            ],
            "primary_keys": ["customer_id"],
            "foreign_keys": [],
            "relationships": [],
        },
    }


def _candidate(table, column, *, role, terms):
    return {
        "table": table,
        "column": column,
        "semantic_type": "money" if role == "metric" else "text",
        "is_measure": role == "metric",
        "is_dimension": role in {"dimension", "filter"},
        "score": 0.99,
        "matched_terms": list(terms),
        "source": "phase6g_test_evidence",
    }


def _context(question, kb, *, metric=False, dimension=False):
    intent = build_intent(question, today=FIXED_TODAY)
    columns = [
        _candidate("service_orders", "order_id", role="dimension", terms=["orders"]),
        _candidate("service_orders", "order_date", role="filter", terms=["order date"]),
    ]
    if metric:
        columns.append(_candidate("service_orders", "total_amount", role="metric", terms=["total amount", "order amount"]))
    if dimension:
        columns.append(_candidate("customers", "city", role="dimension", terms=["customer city"]))
    evidence = {
        "query_terms": [],
        "matched_tables": [
            {"table": "service_orders", "score": 0.99, "matched_terms": ["service orders", "orders"], "source": "test"},
            {"table": "customers", "score": 0.9, "matched_terms": ["customers"], "source": "test"} if dimension else {},
        ],
        "matched_columns": [entry for entry in columns if entry],
        "matched_glossary_terms": [],
        "matched_relationships": [],
        "possible_join_paths": [],
        "measure_candidates": [entry for entry in columns if entry.get("is_measure")],
        "dimension_candidates": [entry for entry in columns if entry.get("table") == "customers"],
        "filter_candidates": [],
        "date_candidates": [],
        "retrieval_sources": ["phase6g_test_evidence"],
        "confidence": 0.92,
        "evidence_scores": {},
        "ambiguity_candidates": {},
        "missing_evidence_indicators": {},
        "source_metadata": {},
    }
    return build_query_context(
        question,
        kb,
        intent=intent,
        retrieved_context=evidence,
    )


def test_after_date_resolves_single_scoped_date_column_and_generates_sql():
    kb = _orders_kb()
    context = _context("show orders after 2026-01-01", kb)

    assert context["selected_filters"][0]["column"] == "order_date"
    result = generate_deterministic_sql(query_context=context, knowledge_base=kb)

    assert result.status == "generated"
    assert "WHERE order_date > '2026-01-01'" in result.sql
    assert validate_sql_structure(result.sql, kb)[0] is True


def test_between_date_is_inclusive():
    kb = _orders_kb()
    context = _context("show orders between 2026-01-01 and 2026-01-31", kb)
    result = generate_deterministic_sql(query_context=context, knowledge_base=kb)

    assert result.status == "generated"
    assert "order_date BETWEEN '2026-01-01' AND '2026-01-31'" in result.sql


def test_joined_aggregate_month_interval_uses_base_table_date_where():
    kb = _orders_kb()
    context = _context("show total order amount by customer city in January 2026", kb, metric=True, dimension=True)
    result = generate_deterministic_sql(query_context=context, knowledge_base=kb)

    assert context["query_shape"] == "joined_aggregate"
    assert result.status == "generated"
    assert "INNER JOIN customers ON service_orders.customer_id = customers.customer_id" in result.sql
    assert "WHERE service_orders.order_date BETWEEN '2026-01-01' AND '2026-01-31'" in result.sql
    assert validate_sql_structure(result.sql, kb, selected_join_path=context["selected_join_path"])[0] is True


def test_relative_last_30_days_uses_fixed_clock():
    intent = build_intent("show delivered orders from last 30 days", today=FIXED_TODAY)

    assert intent["structured_intervals"][0]["values"] == ["2026-06-09", "2026-07-08"]


def test_on_date_and_this_year_are_normalized():
    on_intent = build_intent("show orders on 2026-01-15", today=FIXED_TODAY)
    year_intent = build_intent("count orders by customer segment this year", today=FIXED_TODAY)

    assert on_intent["structured_intervals"][0]["operator"] == "eq"
    assert on_intent["structured_intervals"][0]["value"] == "2026-01-15"
    assert year_intent["structured_intervals"][0]["values"] == ["2026-01-01", "2026-12-31"]


def test_ambiguous_date_column_fails_closed_without_date_role():
    kb = _orders_kb(extra_date=True)
    context = _context("show orders last month", kb)

    assert context["route_recommendation"] == "cannot_plan_safely"
    assert context["selected_filters"] == []


def test_validator_rejects_date_literal_on_non_date_column():
    kb = _orders_kb()
    sql = "SELECT order_id FROM service_orders WHERE order_status = '2026-01-01' LIMIT 50"

    assert validate_sql_structure(sql, kb)[0] is False


def test_validator_rejects_invalid_date_range_order():
    kb = _orders_kb()
    sql = "SELECT order_id FROM service_orders WHERE order_date BETWEEN '2026-02-01' AND '2026-01-01' LIMIT 50"

    assert validate_sql_structure(sql, kb)[0] is False


def test_invalid_between_phrase_stays_unresolved():
    intent = build_intent("show orders between January and pending", today=FIXED_TODAY)

    assert intent["structured_intervals"][0]["operator"] == "unknown"
