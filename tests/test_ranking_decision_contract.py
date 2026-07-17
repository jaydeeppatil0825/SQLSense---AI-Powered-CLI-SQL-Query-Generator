from query_pipeline.intent_builder import build_intent
from query_pipeline.query_planner import build_query_context


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


def _candidate(column, *, role, term=None, score=0.98):
    schema_column = next(
        item for item in RANKING_KB["service_invoices"]["columns"]
        if item["name"] == column
    )
    return {
        "table": "service_invoices",
        "column": column,
        "semantic_type": schema_column["semantic_type"],
        "is_measure": role == "metric",
        "is_dimension": role in {"dimension", "order"},
        "score": score,
        "matched_terms": [term or column.replace("_", " ")],
        "source": "ranking_contract_test_evidence",
    }


def _context(question, *, metrics=None, dimensions=None, order_columns=None):
    metrics = list(metrics or [])
    dimensions = list(dimensions or [])
    order_columns = list(order_columns or [])
    columns = metrics + dimensions + order_columns
    evidence = {
        "matched_tables": [
            {
                "table": "service_invoices",
                "score": 0.99,
                "matched_terms": ["service invoices"],
                "source": "ranking_contract_test_evidence",
            }
        ],
        "matched_columns": columns,
        "measure_candidates": metrics,
        "dimension_candidates": dimensions,
        "filter_candidates": [],
        "date_candidates": order_columns,
        "matched_relationships": [],
        "possible_join_paths": [],
        "retrieval_sources": ["ranking_contract_test_evidence"],
        "evidence_scores": {"overall": 0.98},
        "normalized_package_used": True,
        "confidence": 0.98,
    }
    return build_query_context(
        question,
        RANKING_KB,
        intent=build_intent(question),
        retrieved_context=evidence,
    )


def test_row_ranking_decision_contract_resolves_direction_limit_and_metric():
    context = _context(
        "top 5 service invoices by gross amount",
        metrics=[_candidate("gross_amount", role="metric")],
    )

    decision = context["ranking_decision"]

    assert context["route_recommendation"] == "deterministic_sql_required"
    assert decision["status"] == "resolved"
    assert decision["ranking_mode"] == "row_ranking"
    assert decision["target_table"] == "service_invoices"
    assert decision["ranking_metric"] == {"table": "service_invoices", "column": "gross_amount"}
    assert decision["direction"] == "DESC"
    assert decision["limit"] == 5
    assert decision["selected_projection_mode"] == "row_projection"


def test_bottom_row_ranking_decision_uses_ascending_direction():
    context = _context(
        "bottom 3 service invoices by gross amount",
        metrics=[_candidate("gross_amount", role="metric")],
    )

    decision = context["ranking_decision"]

    assert decision["status"] == "resolved"
    assert decision["ranking_mode"] == "row_ranking"
    assert decision["direction"] == "ASC"
    assert decision["limit"] == 3


def test_grouped_aggregate_ranking_decision_requires_metric_and_dimension():
    context = _context(
        "top invoice status by sum gross amount",
        metrics=[_candidate("gross_amount", role="metric")],
        dimensions=[_candidate("invoice_status", role="dimension")],
    )

    decision = context["ranking_decision"]

    assert context["route_recommendation"] == "deterministic_sql_required"
    assert decision["status"] == "resolved"
    assert decision["ranking_mode"] == "grouped_aggregate_ranking"
    assert decision["aggregate_function"] == "sum"
    assert decision["ranking_metric"] == {"table": "service_invoices", "column": "gross_amount"}
    assert decision["grouping_dimension"] == {"table": "service_invoices", "column": "invoice_status"}
    assert decision["selected_projection_mode"] == "grouped_aggregate_projection"


def test_ordered_list_decision_does_not_invent_limit_or_aggregation():
    context = _context(
        "show service invoices ordered by invoice date descending",
        order_columns=[_candidate("invoice_date", role="order")],
    )

    decision = context["ranking_decision"]

    assert context["route_recommendation"] == "deterministic_sql_required"
    assert decision["status"] == "resolved"
    assert decision["ranking_mode"] == "ordered_list"
    assert decision["aggregate_function"] is None
    assert decision["direction"] == "DESC"
    assert decision["limit"] is None


def test_conflicting_ranking_directions_fail_closed_before_generation():
    context = _context(
        "top and bottom service invoices by gross amount",
        metrics=[_candidate("gross_amount", role="metric")],
    )

    decision = context["ranking_decision"]

    assert context["route_recommendation"] == "cannot_plan_safely"
    assert context["selected_order_by"] is None
    assert decision["status"] == "ambiguous"
    assert decision["reason_code"] == "ranking_direction_conflict"
    assert decision["ambiguity_group_key"] == "ranking_direction"


def test_ambiguous_ranking_metric_reports_rejected_alternatives():
    context = _context(
        "top 5 service invoices by amount",
        metrics=[
            _candidate("gross_amount", role="metric", term="amount"),
            _candidate("received_amount", role="metric", term="amount"),
        ],
    )

    decision = context["ranking_decision"]

    assert context["route_recommendation"] == "cannot_plan_safely"
    assert decision["status"] == "ambiguous"
    assert decision["reason_code"] == "order_by_target_ambiguous"
    assert {
        (entry["table"], entry["column"])
        for entry in decision["rejected_alternatives"]
    } == {
        ("service_invoices", "gross_amount"),
        ("service_invoices", "received_amount"),
    }


def test_grouped_ranking_without_dimension_fails_closed():
    context = _context(
        "top unknown group by sum gross amount",
        metrics=[_candidate("gross_amount", role="metric")],
    )

    decision = context["ranking_decision"]

    assert context["route_recommendation"] == "cannot_plan_safely"
    assert decision["status"] == "unsupported"
    assert decision["reason_code"] == "ranking_grouping_dimension_missing"
