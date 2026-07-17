from query_pipeline.planner.filter_resolver import _detect_runtime_filters
from sql_pipeline.deterministic_sql_generator import _resolve_filter_clauses


def test_runtime_filter_values_match_whole_tokens_only():
    kb = {
        "orders": {
            "columns": [
                {
                    "name": "payment_status",
                    "type": "VARCHAR(20)",
                    "semantic_type": "status",
                    "sample_values": ["paid", "unpaid"],
                }
            ]
        },
        "customers": {
            "columns": [
                {
                    "name": "customer_status",
                    "type": "VARCHAR(20)",
                    "semantic_type": "status",
                    "sample_values": ["active", "inactive"],
                }
            ]
        },
    }

    unpaid = _detect_runtime_filters("show unpaid orders", {"orders": kb["orders"]})
    inactive = _detect_runtime_filters("show inactive customers", {"customers": kb["customers"]})

    assert [entry["value"] for entry in unpaid] == ["unpaid"]
    assert [entry["value"] for entry in inactive] == ["inactive"]


def test_filter_clause_resolution_allows_extra_selected_filters():
    table_data = {
        "columns": [
            {"name": "city", "type": "VARCHAR(100)", "semantic_type": "location"},
            {"name": "customer_segment", "type": "VARCHAR(50)", "semantic_type": "category"},
        ]
    }
    query_context = {
        "selected_filters": [
            {
                "table": "customers",
                "column": "city",
                "operator": "eq",
                "value": "Pune",
                "values": ["Pune"],
                "conjunction": "",
            },
            {
                "table": "customers",
                "column": "customer_segment",
                "operator": "eq",
                "value": "enterprise",
                "values": ["enterprise"],
                "conjunction": "and",
            },
        ],
        "intent": {
            "structured_filters": [
                {
                    "table": "customers",
                    "column": "city",
                    "operator": "eq",
                    "value": "Pune",
                    "values": ["Pune"],
                }
            ]
        },
    }

    clauses, conjunctions, columns, reason = _resolve_filter_clauses(
        query_context=query_context,
        table_name="customers",
        table_data=table_data,
    )

    assert reason == ""
    assert clauses == ["city = 'Pune'", "customer_segment = 'enterprise'"]
    assert conjunctions == ["", "and"]
    assert columns == ["city", "customer_segment"]
