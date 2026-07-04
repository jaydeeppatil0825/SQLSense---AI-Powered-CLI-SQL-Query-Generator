import pytest
from unittest.mock import MagicMock

from query_pipeline.intent_builder import build_intent
from query_pipeline.query_planner import build_query_context
from sql_pipeline.deterministic_sql_generator import (
    build_deterministic_sql_plan,
    generate_deterministic_sql,
)
from sql_pipeline.question_service import QuestionService
from sql_pipeline.query_executor import execute_query
from sql_pipeline.sql_validator import validate_sql_structure


INVOICE_KB = {
    "service_invoices": {
        "columns": [
            {"name": "invoice_id", "type": "INTEGER", "semantic_type": "id"},
            {"name": "gross_amount", "type": "DECIMAL(12,2)", "semantic_type": "money", "is_measure": True},
            {"name": "received_amount", "type": "DECIMAL(12,2)", "semantic_type": "money", "is_measure": True},
            {"name": "invoice_status", "type": "VARCHAR(30)", "semantic_type": "status", "is_dimension": True},
            {"name": "customer_name", "type": "VARCHAR(100)", "semantic_type": "name", "is_dimension": True},
        ],
        "primary_keys": ["invoice_id"],
        "foreign_keys": [],
        "relationships": [],
    }
}


def _candidate(column, *, role, term, score=0.98):
    schema_column = next(
        item for item in INVOICE_KB["service_invoices"]["columns"]
        if item["name"] == column
    )
    return {
        "table": "service_invoices",
        "column": column,
        "semantic_type": schema_column["semantic_type"],
        "is_measure": role == "metric",
        "is_dimension": role == "dimension",
        "score": score,
        "matched_terms": [term],
        "source": "normalized_test_evidence",
    }


def _context(
    question,
    *,
    metric=None,
    dimension=None,
    row_filter=False,
    filter_column=None,
    filter_columns=None,
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
    resolved_filter_columns = list(filter_columns or [])
    resolved_filter_column = filter_column or ("invoice_status" if row_filter else None)
    if resolved_filter_column and resolved_filter_column not in resolved_filter_columns:
        resolved_filter_columns.append(resolved_filter_column)
    for resolved_filter_column in resolved_filter_columns:
        filters.append(
            _candidate(
                resolved_filter_column,
                role="filter",
                term=resolved_filter_column.replace("_", " "),
            )
        )
    columns = metrics + dimensions + filters
    evidence = {
        "query_terms": [],
        "matched_tables": [
            {
                "table": "service_invoices",
                "score": 0.99,
                "matched_terms": ["service invoices"],
                "source": "normalized_test_evidence",
            }
        ],
        "matched_columns": columns,
        "matched_glossary_terms": [],
        "matched_relationships": [],
        "possible_join_paths": [],
        "measure_candidates": metrics,
        "dimension_candidates": dimensions,
        "filter_candidates": filters,
        "date_candidates": [],
        "retrieval_sources": ["normalized_test_evidence"],
        "evidence_scores": {"overall": 0.98},
        "source_metadata": {"backend": "test"},
        "ambiguity_candidates": {},
        "missing_evidence_indicators": {},
        "normalized_package_used": True,
        "confidence": 0.98,
    }
    return build_query_context(
        question,
        INVOICE_KB,
        intent=intent,
        retrieved_context=evidence,
    )


def _pipeline_context(question, context):
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


@pytest.mark.parametrize(
    ("question", "context_kwargs", "clause_shape", "expected_statuses"),
    [
        (
            "show service invoices where invoice status is paid",
            {"filter_column": "invoice_status"},
            "where_only",
            ("resolved", "resolved", "resolved", "not_required", "not_required", "not_required", "resolved", "not_required", "not_required", "resolved", "resolved", "resolved"),
        ),
        (
            "show sum gross amount from service invoices",
            {"metric": "gross_amount"},
            "aggregate_only",
            ("resolved", "resolved", "resolved", "resolved", "resolved", "not_required", "not_required", "not_required", "not_required", "not_required", "resolved", "resolved"),
        ),
        (
            "show sum gross amount from service invoices where invoice status is paid",
            {"metric": "gross_amount", "filter_column": "invoice_status"},
            "aggregate_where",
            ("resolved", "resolved", "resolved", "resolved", "resolved", "not_required", "resolved", "not_required", "not_required", "not_required", "resolved", "resolved"),
        ),
        (
            "show sum gross amount by invoice status from service invoices",
            {"metric": "gross_amount", "dimension": "invoice_status"},
            "group_by",
            ("resolved", "resolved", "resolved", "resolved", "resolved", "resolved", "not_required", "not_required", "not_required", "not_required", "resolved", "resolved"),
        ),
        (
            "show sum gross amount from service invoices where invoice status is paid group by customer name",
            {"metric": "gross_amount", "dimension": "customer_name", "filter_column": "invoice_status"},
            "where_group_by",
            ("resolved", "resolved", "resolved", "resolved", "resolved", "resolved", "resolved", "not_required", "not_required", "not_required", "resolved", "resolved"),
        ),
        (
            "show invoice status where sum gross amount is greater than 10000 from service invoices",
            {"metric": "gross_amount", "dimension": "invoice_status"},
            "group_by_having",
            ("resolved", "resolved", "resolved", "resolved", "resolved", "resolved", "not_required", "resolved", "not_required", "not_required", "resolved", "resolved"),
        ),
        (
            "show sum gross amount from service invoices where invoice status is paid group by customer name having sum gross amount greater than 10000",
            {"metric": "gross_amount", "dimension": "customer_name", "filter_column": "invoice_status"},
            "where_group_by_having",
            ("resolved", "resolved", "resolved", "resolved", "resolved", "resolved", "resolved", "resolved", "not_required", "not_required", "resolved", "resolved"),
        ),
    ],
)
def test_clause_plan_exposes_ordered_deterministic_decision_tree(
    question,
    context_kwargs,
    clause_shape,
    expected_statuses,
):
    context = _context(question, **context_kwargs)
    clause_plan = context["clause_plan"]

    assert clause_plan["clause_shape"] == clause_shape
    assert [entry["node"] for entry in clause_plan["decision_path"]] == [
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
    assert tuple(entry["status"] for entry in clause_plan["decision_path"]) == expected_statuses

    sql_plan = build_deterministic_sql_plan(
        query_context=context,
        knowledge_base=INVOICE_KB,
    )
    assert sql_plan.clause_shape == clause_shape
    assert sql_plan.decision_path == clause_plan["decision_path"]
    assert sql_plan.status == "ready"


def test_generator_rejects_planner_clause_shape_mismatch():
    question = "show sum gross amount by invoice status from service invoices"
    context = _context(question, metric="gross_amount", dimension="invoice_status")
    context["clause_plan"] = dict(context["clause_plan"])
    context["clause_plan"]["clause_shape"] = "where_group_by"

    result = generate_deterministic_sql(query_context=context, knowledge_base=INVOICE_KB)

    assert result.status == "cannot_plan_safely"
    assert result.sql is None
    assert result.plan is not None
    assert "clause_plan_mismatch" in result.plan.missing_evidence


@pytest.mark.parametrize(
    ("question", "metric", "dimension", "expected_sql"),
    [
        (
            "show sum gross amount by invoice status from service invoices",
            "gross_amount",
            "invoice_status",
            "SELECT invoice_status, SUM(gross_amount) AS sum_gross_amount FROM service_invoices GROUP BY invoice_status;",
        ),
        (
            "show average gross amount by invoice status from service invoices",
            "gross_amount",
            "invoice_status",
            "SELECT invoice_status, AVG(gross_amount) AS avg_gross_amount FROM service_invoices GROUP BY invoice_status;",
        ),
        (
            "show count service invoices by invoice status",
            None,
            "invoice_status",
            "SELECT invoice_status, COUNT(*) AS count_rows FROM service_invoices GROUP BY invoice_status;",
        ),
        (
            "show highest received amount by invoice status from service invoices",
            "received_amount",
            "invoice_status",
            "SELECT invoice_status, MAX(received_amount) AS max_received_amount FROM service_invoices GROUP BY invoice_status;",
        ),
        (
            "show lowest gross amount by invoice status from service invoices",
            "gross_amount",
            "invoice_status",
            "SELECT invoice_status, MIN(gross_amount) AS min_gross_amount FROM service_invoices GROUP BY invoice_status;",
        ),
    ],
)
def test_grouped_aggregate_success(question, metric, dimension, expected_sql):
    context = _context(question, metric=metric, dimension=dimension)

    assert context["route_recommendation"] == "deterministic_sql_required"
    assert context["query_shape"] == "grouped_aggregate"
    result = generate_deterministic_sql(query_context=context, knowledge_base=INVOICE_KB)
    assert result.status == "generated"
    assert result.sql == expected_sql


def test_filtered_grouped_aggregate_success():
    question = "show sum gross amount from service invoices where invoice status is paid group by customer name"
    context = _context(question, metric="gross_amount", dimension="customer_name", row_filter=True)

    assert context["query_shape"] == "grouped_aggregate"
    result = generate_deterministic_sql(query_context=context, knowledge_base=INVOICE_KB)
    assert result.status == "generated"
    assert result.sql == (
        "SELECT customer_name, SUM(gross_amount) AS sum_gross_amount FROM service_invoices "
        "WHERE invoice_status = 'paid' GROUP BY customer_name;"
    )


@pytest.mark.parametrize(
    ("question", "metric", "dimension", "row_filter", "expected_sql"),
    [
        (
            "show invoice status where sum gross amount is greater than 10000 from service invoices",
            "gross_amount",
            "invoice_status",
            False,
            "SELECT invoice_status, SUM(gross_amount) AS sum_gross_amount FROM service_invoices "
            "GROUP BY invoice_status HAVING SUM(gross_amount) > 10000;",
        ),
        (
            "show invoice status where average gross amount is greater than 5000 from service invoices",
            "gross_amount",
            "invoice_status",
            False,
            "SELECT invoice_status, AVG(gross_amount) AS avg_gross_amount FROM service_invoices "
            "GROUP BY invoice_status HAVING AVG(gross_amount) > 5000;",
        ),
        (
            "show invoice status where count is greater than 5 from service invoices",
            None,
            "invoice_status",
            False,
            "SELECT invoice_status, COUNT(*) AS count_rows FROM service_invoices "
            "GROUP BY invoice_status HAVING COUNT(*) > 5;",
        ),
        (
            "show customer name with sum received amount greater than 10000 from service invoices",
            "received_amount",
            "customer_name",
            False,
            "SELECT customer_name, SUM(received_amount) AS sum_received_amount FROM service_invoices "
            "GROUP BY customer_name HAVING SUM(received_amount) > 10000;",
        ),
        (
            "show customer name with count greater than 2 from service invoices",
            None,
            "customer_name",
            False,
            "SELECT customer_name, COUNT(*) AS count_rows FROM service_invoices "
            "GROUP BY customer_name HAVING COUNT(*) > 2;",
        ),
        (
            "show customer name from service invoices where invoice status is paid having sum gross amount greater than 10000",
            "gross_amount",
            "customer_name",
            True,
            "SELECT customer_name, SUM(gross_amount) AS sum_gross_amount FROM service_invoices "
            "WHERE invoice_status = 'paid' GROUP BY customer_name HAVING SUM(gross_amount) > 10000;",
        ),
    ],
)
def test_having_success(question, metric, dimension, row_filter, expected_sql):
    context = _context(question, metric=metric, dimension=dimension, row_filter=row_filter)

    assert context["route_recommendation"] == "deterministic_sql_required"
    assert context["query_shape"] == "grouped_aggregate"
    assert context["intent"]["structured_having"]
    result = generate_deterministic_sql(query_context=context, knowledge_base=INVOICE_KB)
    assert result.status == "generated"
    assert result.sql == expected_sql


@pytest.mark.parametrize(
    ("question", "metric", "dimension", "row_filter", "expected_fragment"),
    [
        (
            "show sum gross amount from service invoices where invoice status is paid group by customer name having sum gross amount greater than 10000",
            "gross_amount",
            "customer_name",
            True,
            "WHERE invoice_status = 'paid' GROUP BY customer_name HAVING SUM(gross_amount) > 10000",
        ),
        (
            "show count service invoices where invoice status is pending group by customer name having count greater than 1",
            None,
            "customer_name",
            True,
            "WHERE invoice_status = 'pending' GROUP BY customer_name HAVING COUNT(*) > 1",
        ),
    ],
)
def test_where_group_by_having_success(question, metric, dimension, row_filter, expected_fragment):
    context = _context(question, metric=metric, dimension=dimension, row_filter=row_filter)
    result = generate_deterministic_sql(query_context=context, knowledge_base=INVOICE_KB)

    assert result.status == "generated"
    assert expected_fragment in result.sql


def test_grouped_generic_metric_fails_closed():
    question = "show sum amount by invoice status from service invoices"
    extra = [_candidate("received_amount", role="metric", term="amount", score=0.97)]
    context = _context(
        question,
        metric="gross_amount",
        dimension="invoice_status",
        extra_metrics=extra,
    )

    assert context["route_recommendation"] == "cannot_plan_safely"
    assert "metric_selection" in context["ambiguities"]
    assert generate_deterministic_sql(query_context=context, knowledge_base=INVOICE_KB).sql is None


@pytest.mark.parametrize(
    "question",
    [
        "show sum gross amount by unknown field from service invoices",
        "show sum gross amount by amount from service invoices",
    ],
)
def test_unknown_or_unsafe_group_dimension_fails_closed(question):
    context = _context(question, metric="gross_amount")

    assert context["route_recommendation"] == "cannot_plan_safely"
    assert generate_deterministic_sql(query_context=context, knowledge_base=INVOICE_KB).sql is None


@pytest.mark.parametrize(
    "question",
    [
        "show invoice status where unknown metric is greater than 10000 from service invoices",
        "show invoice status having unknown metric greater than 10000 from service invoices",
        "show service invoices having gross amount greater than 10000",
        "show invoice status where customer name is John having gross amount greater than 10000 from service invoices",
    ],
)
def test_unknown_or_unaggregated_having_fails_closed(question):
    context = _context(question, dimension="invoice_status")

    assert context["route_recommendation"] == "cannot_plan_safely"
    assert generate_deterministic_sql(query_context=context, knowledge_base=INVOICE_KB).sql is None


def test_having_generic_metric_fails_closed():
    question = "show invoice status where sum amount is greater than 10000 from service invoices"
    context = _context(
        question,
        metric="gross_amount",
        dimension="invoice_status",
        extra_metrics=[_candidate("received_amount", role="metric", term="amount", score=0.97)],
    )

    assert context["route_recommendation"] == "cannot_plan_safely"
    assert "metric_selection" in context["ambiguities"]


def test_having_on_a_different_metric_fails_closed_as_multi_metric():
    question = (
        "show sum gross amount by invoice status from service invoices "
        "having sum received amount greater than 10000"
    )
    context = _context(question, metric="received_amount", dimension="invoice_status")

    assert context["route_recommendation"] == "cannot_plan_safely"
    assert "multi_metric_having_not_supported" in context["intent"]["unsupported_constructs"]
    assert generate_deterministic_sql(query_context=context, knowledge_base=INVOICE_KB).sql is None


def test_grouped_multi_metric_output_fails_closed():
    question = "show sum gross amount and received amount by invoice status from service invoices"
    context = _context(
        question,
        metric="gross_amount",
        dimension="invoice_status",
        extra_metrics=[_candidate("received_amount", role="metric", term="received amount", score=0.97)],
    )

    assert context["query_shape"] == "multi_metric_aggregate"
    assert context["route_recommendation"] == "cannot_plan_safely"
    assert context["clause_plan"]["clause_shape"] == "unsupported"
    assert generate_deterministic_sql(query_context=context, knowledge_base=INVOICE_KB).sql is None


def test_unsafe_grouped_request_stays_blocked():
    context = _context("delete service invoices by invoice status", dimension="invoice_status")

    assert context["route_recommendation"] == "blocked_unsafe"
    assert generate_deterministic_sql(query_context=context, knowledge_base=INVOICE_KB).sql is None


def test_question_service_dispatches_grouped_aggregate_without_runtime_ai():
    question = "show sum gross amount by invoice status from service invoices"
    context = _context(question, metric="gross_amount", dimension="invoice_status")
    pipeline_context = _pipeline_context(question, context)
    service = QuestionService()

    success, message, sql, error = service.process_question(
        question,
        INVOICE_KB,
        pipeline_context=pipeline_context,
    )

    assert success is True
    assert error is None
    assert "deterministic" in message.lower()
    assert sql == (
        "SELECT invoice_status, SUM(gross_amount) AS sum_gross_amount "
        "FROM service_invoices GROUP BY invoice_status;"
    )
    assert "grouped_aggregate/group_by" in service.get_last_query_context()["route_reason"]


@pytest.mark.parametrize(
    ("question", "metric", "filter_column", "expected_fragments"),
    [
        ("show all service invoices", None, None, ("SELECT", "FROM service_invoices")),
        ("count service invoices", None, None, ("COUNT(*)", "FROM service_invoices")),
        (
            "show sum gross amount from service invoices",
            "gross_amount",
            None,
            ("SUM(gross_amount)", "FROM service_invoices"),
        ),
        (
            "show service invoices where invoice status is pending and received amount equals 0",
            None,
            "invoice_status",
            ("WHERE invoice_status = 'pending'", "received_amount = 0"),
        ),
        (
            "show service invoices where gross amount between 1000 and 5000",
            None,
            "gross_amount",
            ("WHERE gross_amount BETWEEN 1000 AND 5000",),
        ),
        (
            "show sum gross amount from service invoices where invoice status is paid",
            "gross_amount",
            "invoice_status",
            ("SUM(gross_amount)", "WHERE invoice_status = 'paid'"),
        ),
    ],
)
def test_phase_1e_and_phase_2_old_regression_smoke(
    question,
    metric,
    filter_column,
    expected_fragments,
):
    context = _context(
        question,
        metric=metric,
        filter_column=filter_column,
        filter_columns=(
            ["invoice_status", "received_amount"]
            if "and received amount" in question
            else None
        ),
    )
    success, _, sql, error = QuestionService().process_question(
        question,
        INVOICE_KB,
        pipeline_context=_pipeline_context(question, context),
    )

    assert success is True, error
    assert sql is not None
    for fragment in expected_fragments:
        assert fragment in sql


def test_validator_accepts_safe_grouped_having_sql():
    sql = (
        "SELECT invoice_status, SUM(gross_amount) AS sum_gross_amount FROM service_invoices "
        "WHERE invoice_status <> 'void' GROUP BY invoice_status HAVING SUM(gross_amount) > 10000;"
    )

    assert validate_sql_structure(sql, INVOICE_KB)[0] is True


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT invoice_status, SUM(gross_amount) FROM service_invoices WHERE SUM(gross_amount) > 10000 GROUP BY invoice_status;",
        "SELECT invoice_status, SUM(gross_amount) FROM service_invoices GROUP BY invoice_status HAVING gross_amount > 10000;",
        "SELECT invoice_status, SUM(gross_amount) FROM service_invoices HAVING SUM(gross_amount) > 10000;",
        "SELECT invoice_status, gross_amount FROM service_invoices GROUP BY invoice_status;",
        "SELECT invoice_status, MEDIAN(gross_amount) FROM service_invoices GROUP BY invoice_status;",
    ],
)
def test_validator_rejects_invalid_grouped_having_semantics(sql):
    assert validate_sql_structure(sql, INVOICE_KB)[0] is False


def test_executor_revalidates_invalid_grouped_sql_before_connecting():
    engine = MagicMock()
    sql = (
        "SELECT invoice_status, SUM(gross_amount) FROM service_invoices "
        "WHERE SUM(gross_amount) > 10000 GROUP BY invoice_status;"
    )

    with pytest.raises(ValueError, match="Aggregate conditions must use HAVING"):
        execute_query(sql, engine, knowledge_base=INVOICE_KB)

    engine.connect.assert_not_called()
