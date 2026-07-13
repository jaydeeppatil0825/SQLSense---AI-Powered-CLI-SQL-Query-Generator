from query_pipeline.intent_builder import build_intent
from query_pipeline.query_planner import build_query_context
from sql_pipeline.deterministic_sql_generator import generate_deterministic_sql


def _column(name, type_="VARCHAR(100)", semantic_type="text", *, measure=False, dimension=False, samples=None):
    return {
        "name": name,
        "type": type_,
        "semantic_type": semantic_type,
        "is_measure": measure,
        "is_dimension": dimension,
        "sample_values": list(samples or []),
    }


def _kb():
    return {
        "payments": {
            "columns": [
                _column("payment_id", "INTEGER", "id"),
                _column("order_id", "INTEGER", "id"),
                _column("payment_amount", "DECIMAL(12,2)", "money", measure=True),
                _column("payment_status", "VARCHAR(30)", "status", dimension=True, samples=["completed"]),
            ],
            "primary_keys": ["payment_id"],
            "foreign_keys": [{"column": "order_id", "referenced_table": "orders", "referenced_column": "order_id"}],
        },
        "orders": {
            "columns": [
                _column("order_id", "INTEGER", "id"),
                _column("order_no", "VARCHAR(30)", "code", dimension=True),
                _column("customer_id", "INTEGER", "id"),
            ],
            "primary_keys": ["order_id"],
            "foreign_keys": [{"column": "customer_id", "referenced_table": "customers", "referenced_column": "customer_id"}],
        },
        "order_items": {
            "columns": [
                _column("order_item_id", "INTEGER", "id"),
                _column("order_id", "INTEGER", "id"),
                _column("line_total", "DECIMAL(12,2)", "money", measure=True),
            ],
            "primary_keys": ["order_item_id"],
            "foreign_keys": [{"column": "order_id", "referenced_table": "orders", "referenced_column": "order_id"}],
        },
        "customers": {
            "columns": [
                _column("customer_id", "INTEGER", "id"),
                _column("city", "VARCHAR(100)", "text", dimension=True),
                _column("customer_name", "VARCHAR(100)", "text", dimension=True),
            ],
            "primary_keys": ["customer_id"],
            "foreign_keys": [],
        },
    }


def _candidate(table, column, *, role, terms):
    return {
        "table": table,
        "column": column,
        "semantic_type": "money" if role == "metric" else "text",
        "is_measure": role == "metric",
        "is_dimension": role == "dimension",
        "score": 0.99,
        "matched_terms": terms,
        "source": "phase_1_to_8_regression",
    }


def _context(question, *, metric_table, metric_column, dimension_column="city"):
    metric = _candidate(metric_table, metric_column, role="metric", terms=[metric_column.replace("_", " ")])
    dimension = _candidate("customers", dimension_column, role="dimension", terms=["customer city", "city"])
    return build_query_context(
        question,
        _kb(),
        intent=build_intent(question),
        retrieved_context={
            "query_terms": [],
            "matched_tables": [
                {"table": table, "score": 0.99, "matched_terms": [table.replace("_", " ")]}
                for table in {"payments", "orders", "order_items", "customers"}
            ],
            "matched_columns": [metric, dimension],
            "measure_candidates": [metric],
            "dimension_candidates": [dimension],
            "filter_candidates": [],
            "date_candidates": [],
            "matched_glossary_terms": [],
            "matched_relationships": [],
            "possible_join_paths": [],
            "retrieval_sources": ["phase_1_to_8_regression"],
            "ambiguity_candidates": {},
            "missing_evidence_indicators": {},
            "confidence": 0.99,
        },
    )


def test_line_total_in_explicit_field_list_is_not_aggregate_intent():
    intent = build_intent("show order item id, line total, order number, customer name, and customer city")

    assert intent["intent_type"] == "list"
    assert intent["aggregate_function"] is None
    assert intent["join_lookup_request"]["projection_mode"] == "explicit_fields_only"
    assert "line total" in intent["join_lookup_request"]["requested_output_fields"]


def test_owner_prefix_in_metric_phrase_is_not_a_where_filter():
    context = _context(
        "show total order item line total by customer city",
        metric_table="order_items",
        metric_column="line_total",
    )

    assert context["route_recommendation"] == "deterministic_sql_required"
    assert context["selected_metric"]["table"] == "order_items"
    assert context["selected_filters"] == []


def test_grouped_ranking_strips_leading_aggregate_word_for_metric_resolution():
    context = _context(
        "show top 5 customer cities by total payment amount",
        metric_table="payments",
        metric_column="payment_amount",
    )
    result = generate_deterministic_sql(query_context=context, knowledge_base=_kb())

    assert context["route_recommendation"] == "deterministic_sql_required"
    assert context["selected_metric"]["table"] == "payments"
    assert result.status == "generated"
    assert "ORDER BY sum__payments__payment_amount DESC" in result.sql
    assert "LIMIT 5" in result.sql


def test_sample_backed_metric_modifier_becomes_base_table_filter():
    context = _context(
        "show total completed payment amount by customer city",
        metric_table="payments",
        metric_column="payment_amount",
    )

    assert context["route_recommendation"] == "deterministic_sql_required"
    assert context["selected_filters"][0]["table"] == "payments"
    assert context["selected_filters"][0]["column"] == "payment_status"
    assert context["selected_filters"][0]["value"] == "completed"
