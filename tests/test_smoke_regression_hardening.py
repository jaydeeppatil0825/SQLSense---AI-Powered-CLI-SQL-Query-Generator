import pytest

from query_pipeline.intent_builder import build_intent
from query_pipeline.query_planner import build_query_context
from sql_pipeline.question_service import QuestionService


SMOKE_KB = {
    "service_orders": {
        "columns": [
            {"name": "order_id", "type": "INTEGER", "semantic_type": "id"},
            {"name": "customer_id", "type": "INTEGER", "semantic_type": "id"},
            {"name": "order_status", "type": "VARCHAR(30)", "semantic_type": "status", "is_dimension": True},
            {"name": "payment_status", "type": "VARCHAR(30)", "semantic_type": "status", "is_dimension": True},
            {"name": "total_amount", "type": "DECIMAL(12,2)", "semantic_type": "money", "is_measure": True},
            {"name": "paid_amount", "type": "DECIMAL(12,2)", "semantic_type": "money", "is_measure": True},
        ],
        "primary_keys": ["order_id"],
        "foreign_keys": [
            {"column": "customer_id", "referenced_table": "customers", "referenced_column": "customer_id"}
        ],
        "relationships": [],
    },
    "customers": {
        "columns": [
            {"name": "customer_id", "type": "INTEGER", "semantic_type": "id"},
            {"name": "customer_name", "type": "VARCHAR(100)", "semantic_type": "name", "is_dimension": True},
            {"name": "city", "type": "VARCHAR(100)", "semantic_type": "text", "is_dimension": True},
            {"name": "customer_status", "type": "VARCHAR(30)", "semantic_type": "status", "is_dimension": True},
        ],
        "primary_keys": ["customer_id"],
        "foreign_keys": [],
        "relationships": [],
    },
}


def _candidate(table, column, *, role, score, matched_terms):
    schema_column = next(entry for entry in SMOKE_KB[table]["columns"] if entry["name"] == column)
    return {
        "table": table,
        "column": column,
        "semantic_type": schema_column["semantic_type"],
        "is_measure": role == "metric",
        "is_dimension": role in {"dimension", "filter"},
        "score": score,
        "matched_terms": list(matched_terms),
        "source": "normalized_smoke_evidence",
    }


def _evidence(question):
    intent = build_intent(question)
    total = _candidate("service_orders", "total_amount", role="metric", score=0.98, matched_terms=["total amount"])
    paid = _candidate("service_orders", "paid_amount", role="metric", score=0.99, matched_terms=["amount", "paid amount"])
    payment = _candidate("service_orders", "payment_status", role="dimension", score=0.98, matched_terms=["payment status"])
    order = _candidate("service_orders", "order_status", role="dimension", score=0.99, matched_terms=["status", "order status"])
    customer_status = _candidate("customers", "customer_status", role="dimension", score=0.97, matched_terms=["status", "customer status"])
    city = _candidate("customers", "city", role="filter", score=0.96, matched_terms=["customer city"])
    customer_name = _candidate("customers", "customer_name", role="dimension", score=0.99, matched_terms=["customers"])
    metrics = [paid, total] if intent.get("requested_metrics") else []
    dimensions = [order, payment, customer_status, city] if intent.get("requested_dimensions") else []
    if question.lower().startswith("top customers by"):
        dimensions = [customer_name]
    filters = []
    for clause in intent.get("structured_filters") or []:
        field = str(clause.get("field_phrase") or "")
        if "customer city" in field:
            filters.append(city)
        elif "payment status" in field:
            filters.extend([payment, order, customer_status])
        elif "order status" in field:
            filters.extend([order, payment, customer_status])
        elif "total amount" in field or field == "amount":
            filters.extend([total, paid])
    columns = [*metrics, *dimensions, *filters]
    return {
        "query_terms": [],
        "matched_tables": [
            {"table": "service_orders", "score": 0.99, "matched_terms": ["service orders"], "source": "test"},
            {"table": "customers", "score": 0.97, "matched_terms": ["customers"], "source": "test"},
        ],
        "matched_columns": columns,
        "matched_glossary_terms": [],
        "matched_relationships": [],
        "possible_join_paths": [],
        "measure_candidates": metrics,
        "dimension_candidates": dimensions,
        "filter_candidates": filters,
        "date_candidates": [],
        "retrieval_sources": ["normalized_smoke_evidence"],
        "evidence_scores": {"overall": 0.98},
        "source_metadata": {"backend": "test"},
        "ambiguity_candidates": {},
        "missing_evidence_indicators": {},
        "normalized_package_used": True,
        "confidence": 0.98,
    }


def _context(question):
    return build_query_context(
        question,
        SMOKE_KB,
        intent=build_intent(question),
        retrieved_context=_evidence(question),
    )


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
    ("question", "aggregate", "metric", "mode", "base"),
    [
        ("show sum paid amount from service orders", "sum", "paid amount", None, "service orders"),
        ("show highest total amount from service orders", "max", "total amount", None, "service orders"),
        ("show lowest total amount from service orders", "min", "total amount", None, "service orders"),
        ("top 3 service orders by total amount", None, "total amount", "row", "service orders"),
        ("top 2 payment status by sum total amount", "sum", "total amount", "grouped_aggregate", "payment status"),
    ],
)
def test_intent_preserves_compound_metric_and_ranking_mode(question, aggregate, metric, mode, base):
    intent = build_intent(question)

    assert intent["aggregate_function"] == aggregate
    assert intent["metric_phrase"] == metric
    assert intent["target_entity_phrase"] == base
    if mode:
        assert intent["ranking_diagnostics"]["mode_hint"] == mode


def test_between_field_is_not_misclassified_as_aggregate():
    intent = build_intent("show service orders where total amount between 10000 and 30000")

    assert intent["intent_type"] == "filter"
    assert intent["aggregate_function"] is None
    assert intent["needs_aggregation"] is False


def test_grouped_count_keeps_base_entity_separate_from_dimension():
    intent = build_intent("show count service orders by payment status")

    assert intent["target_entity_phrase"] == "service orders"
    assert intent["requested_dimensions"] == ["payment status"]


def test_with_having_clause_does_not_pollute_source_scope():
    intent = build_intent("show payment status from service orders with sum total amount greater than 50000")

    assert intent["source_scope"] == ["service orders"]
    assert intent["join_lookup_request"]["requested"] is False


@pytest.mark.parametrize(
    ("question", "shape", "sql_fragments", "forbidden"),
    [
        ("show average total amount from service orders", "single_table_aggregate", ("AVG(total_amount)",), ()),
        ("show sum paid amount from service orders", "single_table_aggregate", ("SUM(paid_amount)",), ()),
        ("show highest total amount from service orders", "single_table_aggregate", ("MAX(total_amount)",), ()),
        ("show lowest total amount from service orders", "single_table_aggregate", ("MIN(total_amount)",), ()),
        (
            "show service orders where total amount between 10000 and 30000",
            "filtered_query",
            ("WHERE total_amount BETWEEN 10000 AND 30000",),
            ("SUM(", "AVG(", "MIN(", "MAX("),
        ),
        (
            "show sum total amount by payment status from service orders",
            "grouped_aggregate",
            ("SUM(total_amount)", "GROUP BY payment_status"),
            (),
        ),
        (
            "show count service orders by payment status",
            "grouped_aggregate",
            ("COUNT(*)", "GROUP BY payment_status"),
            (),
        ),
        (
            "show count service orders by order status",
            "grouped_aggregate",
            ("COUNT(*)", "GROUP BY order_status"),
            (),
        ),
        (
            "show payment status from service orders with sum total amount greater than 50000",
            "grouped_aggregate",
            ("GROUP BY payment_status", "HAVING SUM(total_amount) > 50000"),
            (),
        ),
        (
            "top 3 service orders by total amount",
            "ranking_query",
            ("ORDER BY total_amount DESC", "LIMIT 3"),
            ("GROUP BY",),
        ),
        (
            "top 2 payment status by sum total amount",
            "ranking_query",
            ("GROUP BY payment_status", "ORDER BY SUM(total_amount) DESC", "LIMIT 2"),
            (),
        ),
    ],
)
def test_exact_compounds_generate_expected_deterministic_sql(question, shape, sql_fragments, forbidden):
    context = _context(question)
    success, _, sql, error = QuestionService().process_question(
        question,
        SMOKE_KB,
        pipeline_context=_pipeline_context(question, context),
    )

    assert context["route_recommendation"] == "deterministic_sql_required"
    assert context["query_shape"] == shape
    assert success is True
    assert error is None
    assert all(fragment in sql for fragment in sql_fragments)
    assert all(fragment not in sql for fragment in forbidden)


@pytest.mark.parametrize(
    "question",
    [
        "show sum amount from service orders",
        "show count service orders by status",
    ],
)
def test_generic_metric_or_dimension_remains_ambiguous(question):
    context = _context(question)

    assert context["route_recommendation"] == "cannot_plan_safely"
    assert context.get("selected_join_path") is None


@pytest.mark.parametrize(
    ("question", "expected_route", "expected_shape"),
    [
        ("show service orders with customer details", "deterministic_sql_required", "joined_lookup"),
        ("show service orders where customer city is Pune", "deterministic_sql_required", "joined_lookup"),
        ("show customers with their service orders", "deterministic_sql_required", "joined_lookup"),
        ("show sum total amount by customer city from service orders", "deterministic_sql_required", "joined_aggregate"),
        ("top customers by total amount", "deterministic_sql_required", "joined_aggregate"),
    ],
)
def test_single_table_cleanup_does_not_weaken_join_boundaries(question, expected_route, expected_shape):
    context = _context(question)

    assert context["route_recommendation"] == expected_route
    assert context["query_shape"] == expected_shape
    if expected_route == "deterministic_sql_required":
        assert context["selected_join_path"]["path_source"] == "relationship_graph"
    else:
        assert context.get("selected_join_path") is None
