from query_pipeline.intent_builder import build_intent
from query_pipeline.query_planner import build_query_context
from query_pipeline.planner.filter_resolver import build_filter_decision_contract


KB = {
    "orders": {
        "columns": [
            {"name": "order_id", "type": "INTEGER", "semantic_type": "id"},
            {"name": "order_status", "type": "VARCHAR(30)", "semantic_type": "status", "sample_values": ["Delivered", "Unpaid"]},
            {"name": "order_date", "type": "DATE", "semantic_type": "date"},
            {"name": "customer_id", "type": "INTEGER", "semantic_type": "id"},
        ]
    },
    "customers": {
        "columns": [
            {"name": "customer_id", "type": "INTEGER", "semantic_type": "id"},
            {"name": "city", "type": "VARCHAR(50)", "semantic_type": "city", "sample_values": ["Pune"]},
        ]
    },
    "payments": {
        "columns": [
            {"name": "payment_id", "type": "INTEGER", "semantic_type": "id"},
            {"name": "payment_status", "type": "VARCHAR(30)", "semantic_type": "status", "sample_values": ["Completed"]},
            {"name": "payment_method", "type": "VARCHAR(30)", "semantic_type": "method", "sample_values": ["Bank"]},
        ]
    },
}


def test_filter_decision_resolves_path_compatible_where_filter():
    decision = build_filter_decision_contract(
        selected_filters=[
            {
                "table": "payments",
                "column": "payment_status",
                "operator": "eq",
                "value": "Completed",
                "value_phrase": "completed",
                "source": "source_scope_value_filter",
            }
        ],
        selected_having=[],
        knowledge_base=KB,
        selected_tables=[{"table": "payments"}],
    )

    assert decision["overall_status"] == "resolved"
    assert decision["resolved_where_filters"][0]["table"] == "payments"
    assert decision["resolved_where_filters"][0]["column"] == "payment_status"
    assert decision["resolved_where_filters"][0]["selected_path_compatible"] is True


def test_filter_decision_rejects_filter_outside_selected_path():
    decision = build_filter_decision_contract(
        selected_filters=[
            {
                "table": "payments",
                "column": "payment_status",
                "operator": "eq",
                "value": "Completed",
            }
        ],
        selected_having=[],
        knowledge_base=KB,
        selected_tables=[{"table": "orders"}, {"table": "customers"}],
        selected_join_path={"base_table": "orders", "joined_tables": ["customers"], "edges": []},
    )

    assert decision["overall_status"] == "unsupported"
    assert decision["unresolved_filters"][0]["reason_code"] == "filter_table_outside_selected_path"


def test_filter_decision_rejects_unknown_column():
    decision = build_filter_decision_contract(
        selected_filters=[
            {
                "table": "orders",
                "column": "missing_status",
                "operator": "eq",
                "value": "Delivered",
            }
        ],
        selected_having=[],
        knowledge_base=KB,
        selected_tables=[{"table": "orders"}],
    )

    assert decision["overall_status"] == "unsupported"
    assert decision["unresolved_filters"][0]["reason_code"] == "filter_column_not_in_schema"


def test_relative_interval_survives_runtime_status_filter():
    evidence = {
        "matched_tables": [{"table": "orders", "score": 0.99, "matched_terms": ["orders"]}],
        "matched_columns": [],
        "measure_candidates": [],
        "dimension_candidates": [],
        "filter_candidates": [],
        "matched_relationships": [],
        "possible_join_paths": [],
        "retrieval_sources": ["test"],
        "evidence_scores": {"overall": 0.99},
        "normalized_package_used": True,
        "confidence": 0.99,
    }
    context = build_query_context(
        "show delivered orders from last 30 days",
        KB,
        intent=build_intent("show delivered orders from last 30 days"),
        retrieved_context=evidence,
    )

    assert [(entry["column"], entry.get("filter_kind")) for entry in context["selected_filters"]] == [
        ("order_status", None),
        ("order_date", "date_interval"),
    ]
    assert context["filter_decision"]["overall_status"] == "resolved"
