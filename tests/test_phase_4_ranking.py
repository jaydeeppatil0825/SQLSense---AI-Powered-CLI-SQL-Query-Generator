from copy import deepcopy
from unittest.mock import MagicMock

import pytest

from query_pipeline.intent_builder import build_intent
from query_pipeline.query_planner import build_query_context
from sql_pipeline.deterministic_sql_generator import generate_deterministic_sql
from sql_pipeline.question_service import QuestionService
from sql_pipeline.query_executor import execute_query
from sql_pipeline.sql_validator import validate_sql_structure


RANKING_KB = {
    "service_invoices": {
        "columns": [
            {"name": "invoice_id", "type": "INTEGER", "semantic_type": "id"},
            {"name": "gross_amount", "type": "DECIMAL(12,2)", "semantic_type": "money", "is_measure": True},
            {"name": "received_amount", "type": "DECIMAL(12,2)", "semantic_type": "money", "is_measure": True},
            {"name": "invoice_status", "type": "VARCHAR(30)", "semantic_type": "status", "is_dimension": True},
            {"name": "customer_name", "type": "VARCHAR(100)", "semantic_type": "name", "is_dimension": True},
            {"name": "invoice_date", "type": "DATE", "semantic_type": "date", "is_dimension": True},
        ],
        "primary_keys": ["invoice_id"],
        "foreign_keys": [],
        "relationships": [],
    }
}


def _candidate(column, *, role, term, score=0.98, table="service_invoices"):
    schema_column = next(
        item for item in RANKING_KB["service_invoices"]["columns"]
        if item["name"] == column
    )
    return {
        "table": table,
        "column": column,
        "semantic_type": schema_column["semantic_type"],
        "is_measure": role == "metric",
        "is_dimension": role in {"dimension", "order"},
        "score": score,
        "matched_terms": [term],
        "source": "phase_4_test_evidence",
    }


def _context(
    question,
    *,
    metric=None,
    dimension=None,
    order_column=None,
    filter_column=None,
    extra_metrics=None,
):
    intent = build_intent(question)
    metrics = []
    if metric:
        metrics.append(_candidate(metric, role="metric", term=metric.replace("_", " ")))
    metrics.extend(extra_metrics or [])
    dimensions = []
    if dimension:
        dimensions.append(_candidate(dimension, role="dimension", term=dimension.replace("_", " ")))
    filters = []
    if filter_column:
        filters.append(_candidate(filter_column, role="filter", term=filter_column.replace("_", " ")))
    order_candidates = []
    if order_column and order_column not in {metric, dimension, filter_column}:
        order_candidates.append(_candidate(order_column, role="order", term=order_column.replace("_", " ")))
    columns = metrics + dimensions + filters + order_candidates
    evidence = {
        "query_terms": [],
        "matched_tables": [
            {
                "table": "service_invoices",
                "score": 0.99,
                "matched_terms": ["service invoices"],
                "source": "phase_4_test_evidence",
            }
        ],
        "matched_columns": columns,
        "matched_glossary_terms": [],
        "matched_relationships": [],
        "possible_join_paths": [],
        "measure_candidates": metrics,
        "dimension_candidates": dimensions,
        "filter_candidates": filters,
        "date_candidates": order_candidates if order_column == "invoice_date" else [],
        "retrieval_sources": ["phase_4_test_evidence"],
        "evidence_scores": {"overall": 0.98},
        "source_metadata": {"backend": "test"},
        "ambiguity_candidates": {},
        "missing_evidence_indicators": {},
        "normalized_package_used": True,
        "confidence": 0.98,
    }
    return build_query_context(question, RANKING_KB, intent=intent, retrieved_context=evidence)


def _pipeline_context(question, context):
    return {
        "normalized_question": question,
        "query_context": context,
        "plan": context["plan"],
        "retrieved_context": context["retrieved_context"],
        "route_recommendation": context["route_recommendation"],
        "clause_plan": context["clause_plan"],
        "complex_sql_plan": context["complex_sql_plan"],
        "formula_evidence": [],
        "evidence_sources": context["evidence_sources"],
    }


@pytest.mark.parametrize(
    ("question", "mode", "direction", "limit", "aggregate"),
    [
        ("top 5 service invoices by gross amount", "row", "desc", 5, None),
        ("highest 10 service invoices by received amount", "row", "desc", 10, None),
        ("lowest 5 service invoices by gross amount", "row", "asc", 5, None),
        ("bottom 3 service invoices by invoice date", "row", "asc", 3, None),
        ("show service invoices ordered by gross amount", "ordered_list", "asc", None, None),
        ("top invoice status by sum gross amount", "grouped_aggregate", "desc", 50, "sum"),
        ("lowest invoice status by average gross amount", "grouped_aggregate", "asc", 50, "avg"),
    ],
)
def test_ranking_intent_contract(question, mode, direction, limit, aggregate):
    intent = build_intent(question)

    assert intent["requested_sort"]["direction"] == direction
    assert intent["limit"] == limit
    assert intent["aggregate_function"] == aggregate
    assert intent["ranking_diagnostics"]["mode_hint"] == mode


@pytest.mark.parametrize(
    ("question", "kwargs", "clause_shape", "limit_status"),
    [
        ("top 5 service invoices by gross amount", {"metric": "gross_amount"}, "list_only", "resolved"),
        ("show service invoices ordered by gross amount", {"metric": "gross_amount"}, "list_only", "not_required"),
        (
            "show service invoices where invoice status is paid order by gross amount descending",
            {"metric": "gross_amount", "filter_column": "invoice_status"},
            "where_only",
            "not_required",
        ),
        (
            "top 3 customer name by sum received amount",
            {"metric": "received_amount", "dimension": "customer_name"},
            "group_by",
            "resolved",
        ),
    ],
)
def test_ranking_planner_exposes_complete_clause_tree(question, kwargs, clause_shape, limit_status):
    context = _context(question, **kwargs)
    path = context["clause_plan"]["decision_path"]

    assert context["query_shape"] == "ranking_query"
    assert context["route_recommendation"] == "deterministic_sql_required"
    assert context["clause_plan"]["clause_shape"] == clause_shape
    assert [node["node"] for node in path] == [
        "unsafe_check",
        "table_scope",
        "query_shape",
        "aggregate",
        "metric",
        "dimension",
        "where",
        "having",
        "order_by",
        "limit",
        "clause_shape",
        "route",
    ]
    assert next(node for node in path if node["node"] == "order_by")["status"] == "resolved"
    assert next(node for node in path if node["node"] == "limit")["status"] == limit_status


@pytest.mark.parametrize(
    ("question", "kwargs", "expected_sql"),
    [
        (
            "top 5 service invoices by gross amount",
            {"metric": "gross_amount"},
            "SELECT invoice_id, gross_amount, received_amount, invoice_status, customer_name, invoice_date "
            "FROM service_invoices ORDER BY gross_amount DESC LIMIT 5;",
        ),
        (
            "highest 10 service invoices by received amount",
            {"metric": "received_amount"},
            "SELECT invoice_id, gross_amount, received_amount, invoice_status, customer_name, invoice_date "
            "FROM service_invoices ORDER BY received_amount DESC LIMIT 10;",
        ),
        (
            "lowest 5 service invoices by gross amount",
            {"metric": "gross_amount"},
            "SELECT invoice_id, gross_amount, received_amount, invoice_status, customer_name, invoice_date "
            "FROM service_invoices ORDER BY gross_amount ASC LIMIT 5;",
        ),
        (
            "bottom 3 service invoices by invoice date",
            {"order_column": "invoice_date"},
            "SELECT invoice_id, gross_amount, received_amount, invoice_status, customer_name, invoice_date "
            "FROM service_invoices ORDER BY invoice_date ASC LIMIT 3;",
        ),
        (
            "show service invoices ordered by gross amount",
            {"metric": "gross_amount"},
            "SELECT invoice_id, gross_amount, received_amount, invoice_status, customer_name, invoice_date "
            "FROM service_invoices ORDER BY gross_amount ASC;",
        ),
        (
            "show service invoices order by invoice date descending",
            {"order_column": "invoice_date"},
            "SELECT invoice_id, gross_amount, received_amount, invoice_status, customer_name, invoice_date "
            "FROM service_invoices ORDER BY invoice_date DESC;",
        ),
        (
            "show sum gross amount by invoice status order by sum gross amount descending",
            {"metric": "gross_amount", "dimension": "invoice_status"},
            "SELECT invoice_status, SUM(gross_amount) AS sum_gross_amount FROM service_invoices "
            "GROUP BY invoice_status ORDER BY SUM(gross_amount) DESC;",
        ),
        (
            "top 3 customer name by sum received amount",
            {"metric": "received_amount", "dimension": "customer_name"},
            "SELECT customer_name, SUM(received_amount) AS sum_received_amount FROM service_invoices "
            "GROUP BY customer_name ORDER BY SUM(received_amount) DESC LIMIT 3;",
        ),
        (
            "top invoice status by sum gross amount",
            {"metric": "gross_amount", "dimension": "invoice_status"},
            "SELECT invoice_status, SUM(gross_amount) AS sum_gross_amount FROM service_invoices "
            "GROUP BY invoice_status ORDER BY SUM(gross_amount) DESC LIMIT 50;",
        ),
        (
            "lowest invoice status by average gross amount",
            {"metric": "gross_amount", "dimension": "invoice_status"},
            "SELECT invoice_status, AVG(gross_amount) AS avg_gross_amount FROM service_invoices "
            "GROUP BY invoice_status ORDER BY AVG(gross_amount) ASC LIMIT 50;",
        ),
        (
            "show service invoices where invoice status is paid order by gross amount descending",
            {"metric": "gross_amount", "filter_column": "invoice_status"},
            "SELECT invoice_id, gross_amount, received_amount, invoice_status, customer_name, invoice_date "
            "FROM service_invoices WHERE invoice_status = 'paid' ORDER BY gross_amount DESC;",
        ),
        (
            "show sum gross amount from service invoices where invoice status is paid group by customer name "
            "having sum gross amount greater than 10000 order by sum gross amount descending limit 5",
            {"metric": "gross_amount", "dimension": "customer_name", "filter_column": "invoice_status"},
            "SELECT customer_name, SUM(gross_amount) AS sum_gross_amount FROM service_invoices "
            "WHERE invoice_status = 'paid' GROUP BY customer_name HAVING SUM(gross_amount) > 10000 "
            "ORDER BY SUM(gross_amount) DESC LIMIT 5;",
        ),
    ],
)
def test_ranking_sql_generation(question, kwargs, expected_sql):
    context = _context(question, **kwargs)
    result = generate_deterministic_sql(query_context=context, knowledge_base=RANKING_KB)

    assert result.status == "generated"
    assert result.sql == expected_sql
    assert validate_sql_structure(result.sql, RANKING_KB)[0] is True


def test_ambiguous_ranking_target_fails_closed():
    context = _context(
        "top 5 service invoices by amount",
        metric="gross_amount",
        extra_metrics=[_candidate("received_amount", role="metric", term="amount", score=0.97)],
    )

    assert context["route_recommendation"] == "cannot_plan_safely"
    assert generate_deterministic_sql(query_context=context, knowledge_base=RANKING_KB).sql is None


@pytest.mark.parametrize(
    "question",
    [
        "show service invoices ordered by unknown field",
        "order service invoices by unknown field",
    ],
)
def test_unknown_ranking_target_fails_closed(question):
    context = _context(question)

    assert context["route_recommendation"] == "cannot_plan_safely"
    assert context["selected_order_by"] is None


def test_multi_metric_ranking_fails_closed():
    context = _context(
        "top 5 service invoices by gross amount and received amount",
        metric="gross_amount",
        extra_metrics=[_candidate("received_amount", role="metric", term="received amount")],
    )

    assert context["route_recommendation"] == "cannot_plan_safely"
    assert generate_deterministic_sql(query_context=context, knowledge_base=RANKING_KB).sql is None


def test_multi_table_ranking_fails_closed():
    context = deepcopy(_context("top 5 service invoices by gross amount", metric="gross_amount"))
    context["selected_tables"].append({"table": "another_table", "confidence": 0.91})

    result = generate_deterministic_sql(query_context=context, knowledge_base=RANKING_KB)

    assert result.status == "cannot_plan_safely"
    assert result.sql is None


def test_invalid_ranking_direction_fails_closed_in_planner():
    base = _context("show service invoices ordered by gross amount", metric="gross_amount")
    intent = deepcopy(base["intent"])
    intent["requested_sort"]["direction"] = "sideways"

    context = build_query_context(
        "show service invoices ordered by gross amount",
        RANKING_KB,
        intent=intent,
        retrieved_context=base["retrieved_context"],
    )

    assert context["route_recommendation"] == "cannot_plan_safely"
    assert context["selected_order_by"] is None


def test_formula_ranking_fails_closed():
    context = deepcopy(_context("top 5 service invoices by gross amount", metric="gross_amount"))
    context["formula_evidence"] = [{"expression": "gross_amount - received_amount"}]

    result = generate_deterministic_sql(query_context=context, knowledge_base=RANKING_KB)

    assert result.status == "cannot_plan_safely"
    assert result.sql is None


def test_unsafe_ranking_stays_blocked():
    context = _context("delete top 5 service invoices by gross amount", metric="gross_amount")

    assert context["route_recommendation"] == "blocked_unsafe"
    assert generate_deterministic_sql(query_context=context, knowledge_base=RANKING_KB).sql is None


def test_unsafe_ranking_limit_fails_closed():
    context = _context("top 1001 service invoices by gross amount", metric="gross_amount")

    assert context["route_recommendation"] == "cannot_plan_safely"
    assert context["limit"] is None


def test_generator_rejects_order_by_contract_mismatch():
    context = _context("top 5 service invoices by gross amount", metric="gross_amount")
    context = deepcopy(context)
    context["clause_plan"]["requires"]["order_by"] = False

    result = generate_deterministic_sql(query_context=context, knowledge_base=RANKING_KB)

    assert result.status == "cannot_plan_safely"
    assert result.sql is None
    assert "clause_plan_mismatch" in result.plan.missing_evidence


def test_generator_rejects_limit_contract_mismatch():
    context = _context("top 5 service invoices by gross amount", metric="gross_amount")
    context = deepcopy(context)
    context["limit"] = 6

    result = generate_deterministic_sql(query_context=context, knowledge_base=RANKING_KB)

    assert result.status == "cannot_plan_safely"
    assert result.sql is None
    assert "clause_plan_mismatch" in result.plan.missing_evidence


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT invoice_id FROM service_invoices LIMIT 5 ORDER BY gross_amount DESC;",
        "SELECT invoice_id FROM service_invoices ORDER BY gross_amount SIDEWAYS;",
        "SELECT invoice_status, SUM(gross_amount) FROM service_invoices GROUP BY invoice_status ORDER BY AVG(gross_amount) DESC;",
        "SELECT invoice_id FROM service_invoices ORDER BY gross_amount DESC LIMIT 0;",
        "SELECT invoice_id FROM service_invoices ORDER BY gross_amount DESC LIMIT -1;",
        "SELECT invoice_id FROM service_invoices ORDER BY gross_amount DESC LIMIT 1001;",
        "SELECT invoice_id FROM service_invoices ORDER BY gross_amount DESC LIMIT five;",
        "SELECT invoice_id FROM service_invoices ORDER BY gross_amount DESC LIMIT 5 HAVING COUNT(*) > 1;",
    ],
)
def test_validator_rejects_invalid_ranking_sql(sql):
    assert validate_sql_structure(sql, RANKING_KB)[0] is False


def test_question_service_dispatches_ranking_without_runtime_ai():
    question = "top 3 customer name by sum received amount"
    context = _context(question, metric="received_amount", dimension="customer_name")

    success, message, sql, error = QuestionService().process_question(
        question,
        RANKING_KB,
        pipeline_context=_pipeline_context(question, context),
    )

    assert success is True
    assert error is None
    assert message == "SQL generated successfully (deterministic)"
    assert "ORDER BY SUM(received_amount) DESC LIMIT 3" in sql


def test_executor_rejects_invalid_limit_before_connecting():
    engine = MagicMock()
    sql = "SELECT invoice_id FROM service_invoices ORDER BY gross_amount DESC LIMIT 1001;"

    with pytest.raises(ValueError, match="LIMIT must be between 1 and 1000"):
        execute_query(sql, engine, knowledge_base=RANKING_KB)

    engine.connect.assert_not_called()
