from datetime import date

from query_pipeline.intent_builder import build_intent
from query_pipeline.query_planner import build_query_context
from query_pipeline.planner.role_resolver import rank_role_candidates
from sql_pipeline.deterministic_sql_generator import generate_deterministic_sql


KB = {
    "orders": {
        "columns": [
            {"name": "order_id", "type": "INT", "semantic_type": "id"},
            {"name": "customer_id", "type": "INT", "semantic_type": "id", "is_foreign_key": True},
            {"name": "order_status", "type": "VARCHAR(30)", "semantic_type": "status", "sample_values": ["Delivered", "Pending"]},
            {"name": "order_date", "type": "DATE", "semantic_type": "date"},
            {"name": "total_amount", "type": "DECIMAL(12,2)", "semantic_type": "money", "is_measure": True},
        ],
        "primary_keys": ["order_id"],
        "foreign_keys": [{"column": "customer_id", "referenced_table": "customers", "referenced_column": "customer_id"}],
    },
    "products": {
        "columns": [
            {"name": "product_id", "type": "INT", "semantic_type": "id"},
            {"name": "product_status", "type": "VARCHAR(30)", "semantic_type": "status", "sample_values": ["Active", "Inactive"]},
            {"name": "stock_quantity", "type": "INT", "semantic_type": "quantity", "is_measure": True},
            {"name": "unit_price", "type": "DECIMAL(12,2)", "semantic_type": "money", "is_measure": True},
        ]
    },
    "categories": {
        "columns": [
            {"name": "category_id", "type": "INT", "semantic_type": "id"},
            {"name": "category_name", "type": "VARCHAR(80)", "semantic_type": "name"},
            {"name": "category_status", "type": "VARCHAR(30)", "semantic_type": "status"},
        ]
    },
    "payments": {
        "columns": [
            {"name": "payment_id", "type": "INT", "semantic_type": "id"},
            {"name": "payment_date", "type": "DATE", "semantic_type": "date"},
            {"name": "payment_method", "type": "VARCHAR(30)", "semantic_type": "method"},
            {"name": "payment_status", "type": "VARCHAR(30)", "semantic_type": "status", "sample_values": ["Completed", "Failed"]},
            {"name": "payment_amount", "type": "DECIMAL(12,2)", "semantic_type": "money", "is_measure": True},
        ]
    },
    "customers": {
        "columns": [
            {"name": "customer_id", "type": "INT", "semantic_type": "id"},
            {"name": "customer_name", "type": "VARCHAR(80)", "semantic_type": "name"},
            {"name": "city", "type": "VARCHAR(30)", "semantic_type": "city"},
            {"name": "region", "type": "VARCHAR(30)", "semantic_type": "region"},
        ],
        "primary_keys": ["customer_id"],
    },
    "suppliers": {
        "columns": [
            {"name": "supplier_id", "type": "INT", "semantic_type": "id"},
            {"name": "city", "type": "VARCHAR(30)", "semantic_type": "city"},
            {"name": "rating", "type": "DECIMAL(3,2)", "semantic_type": "quantity", "is_measure": True},
        ]
    },
}


def _candidate(table, column, *, semantic_type, score=0.99, terms=None, is_measure=False, is_dimension=False):
    return {
        "table": table,
        "column": column,
        "semantic_type": semantic_type,
        "type": next(col["type"] for col in KB[table]["columns"] if col["name"] == column),
        "is_measure": is_measure,
        "is_dimension": is_dimension,
        "score": score,
        "matched_terms": terms or [column.replace("_", " ")],
    }


def _evidence(*, table, metrics=(), dimensions=(), filters=()):
    columns = [*metrics, *dimensions, *filters]
    return {
        "matched_tables": [{"table": table, "score": 0.99, "matched_terms": [table]}],
        "matched_columns": list(columns),
        "measure_candidates": list(metrics),
        "dimension_candidates": list(dimensions),
        "filter_candidates": list(filters),
        "matched_relationships": [],
        "possible_join_paths": [],
        "retrieval_sources": ["test"],
        "evidence_scores": {"overall": 0.99},
        "normalized_package_used": True,
        "confidence": 0.99,
    }


def _plan(question, evidence):
    return build_query_context(question, KB, intent=build_intent(question, today=date(2026, 7, 23)), retrieved_context=evidence)


def test_grouped_metric_phrase_is_not_reused_as_status_filter():
    metric = _candidate("orders", "total_amount", semantic_type="money", terms=["order amount"], is_measure=True)
    dimension = _candidate("orders", "order_status", semantic_type="status", terms=["order status"], is_dimension=True)
    context = _plan(
        "total order amount by order status",
        _evidence(table="orders", metrics=[metric], dimensions=[dimension], filters=[dimension]),
    )
    result = generate_deterministic_sql(query_context=context, knowledge_base=KB)

    assert result.status == "generated"
    assert "WHERE" not in result.sql
    assert context["selected_filters"] == []


def test_ranking_metric_phrase_is_not_reused_as_filter_value():
    metric = _candidate("payments", "payment_amount", semantic_type="money", terms=["total payment amount"], is_measure=True)
    dimension = _candidate("payments", "payment_method", semantic_type="method", terms=["payment methods"], is_dimension=True)
    context = _plan(
        "top 3 payment methods by total payment amount",
        _evidence(table="payments", metrics=[metric], dimensions=[dimension], filters=[]),
    )
    result = generate_deterministic_sql(query_context=context, knowledge_base=KB)

    assert result.status == "generated"
    assert "payment_status = 'total amount'" not in result.sql.lower()
    assert context["selected_filters"] == []


def test_distinct_control_word_fails_closed():
    metric = _candidate("orders", "total_amount", semantic_type="money", terms=["order amount"], is_measure=True)
    dimension = _candidate("customers", "city", semantic_type="city", terms=["customer city"], is_dimension=True)
    context = _plan(
        "count distinct orders by customer city",
        _evidence(table="orders", metrics=[metric], dimensions=[dimension], filters=[]),
    )

    assert context["route"] == "cannot_plan_safely"
    assert "unsupported_intent" in context["missing_evidence"]


def test_mutation_words_do_not_become_status_filters():
    context = _plan(
        "change inactive products to active",
        _evidence(table="products", filters=[_candidate("products", "product_status", semantic_type="status", is_dimension=True)]),
    )

    assert context["query_shape"] == "blocked_unsafe"
    assert context["route_recommendation"] == "blocked_unsafe"
    assert context["selected_filters"] == []


def test_average_text_dimension_does_not_fallback_to_numeric_metric():
    rating = _candidate("suppliers", "rating", semantic_type="quantity", is_measure=True)
    context = _plan("average supplier city", _evidence(table="suppliers", metrics=[rating]))

    assert context["route"] == "cannot_plan_safely"
    assert context["selected_metric"] is None
    assert context["missing_evidence_flags"]["invalid_metric_type"] is True


def test_status_modifier_and_numeric_filter_are_both_preserved():
    status = _candidate("products", "product_status", semantic_type="status", terms=["status"], is_dimension=True)
    quantity = _candidate("products", "stock_quantity", semantic_type="quantity", terms=["stock quantity"], is_measure=True)
    context = _plan(
        "show active products with stock quantity below 50",
        _evidence(table="products", metrics=[quantity], dimensions=[], filters=[status, quantity]),
    )

    assert [(entry["column"], entry["operator"]) for entry in context["selected_filters"]] == [
        ("product_status", "eq"),
        ("stock_quantity", "lt"),
    ]


def test_status_modifier_and_month_interval_are_both_preserved():
    status = _candidate("orders", "order_status", semantic_type="status", terms=["status"], is_dimension=True)
    context = _plan(
        "show delivered orders from January 2026",
        _evidence(table="orders", filters=[status]),
    )

    assert [(entry["column"], entry.get("filter_kind")) for entry in context["selected_filters"]] == [
        ("order_status", None),
        ("order_date", "date_interval"),
    ]


def test_status_modifier_does_not_break_join_lookup_base_entity():
    status = _candidate("orders", "order_status", semantic_type="status", terms=["status"], is_dimension=True)
    customer_name = _candidate("customers", "customer_name", semantic_type="name", terms=["customer details"], is_dimension=True)
    evidence = _evidence(table="orders", filters=[status], dimensions=[customer_name])
    evidence["matched_tables"].append({"table": "customers", "score": 0.97, "matched_terms": ["customer"]})
    evidence["matched_relationships"] = [
        {
            "from_table": "orders",
            "from_column": "customer_id",
            "to_table": "customers",
            "to_column": "customer_id",
            "join_condition": "orders.customer_id = customers.customer_id",
            "source": "fk_relationship",
        }
    ]
    evidence["possible_join_paths"] = [
        {
            "from_table": "orders",
            "to_table": "customers",
            "path": [
                {
                    "from_table": "orders",
                    "from_column": "customer_id",
                    "to_table": "customers",
                    "to_column": "customer_id",
                    "join_condition": "orders.customer_id = customers.customer_id",
                }
            ],
            "length": 1,
        }
    ]

    context = _plan("show delivered orders with customer details", evidence)

    assert context["query_shape"] == "joined_lookup"
    assert context["route"] == "deterministic_sql_required"
    assert context["selected_join_path"]
    assert [(entry["table"], entry["column"], entry["operator"]) for entry in context["selected_filters"]] == [
        ("orders", "order_status", "eq"),
    ]


def test_human_date_after_phrase_resolves_date_filter():
    context = _plan("show payments after April 1 2026", _evidence(table="payments"))

    assert [(entry["column"], entry.get("operator"), entry.get("value")) for entry in context["selected_filters"]] == [
        ("payment_date", "after", "2026-04-01"),
    ]


def test_count_having_with_status_modifier_preserves_filter_boundary():
    intent = build_intent("customer regions with more than 2 active customers", today=date(2026, 7, 23))

    assert intent["intent_type"] == "grouped_summary"
    assert intent["aggregate_function"] == "count"
    assert intent["requested_dimensions"] == ["customer regions"]
    assert intent["structured_having"][0]["aggregate_function"] == "count"
    assert intent["structured_having"][0]["entity_phrase"] == "active customers"
    assert intent["structured_filters"] == [
        {
            "raw_phrase": "active",
            "field": "status",
            "field_phrase": "status",
            "operator": "eq",
            "value": "active",
            "value_phrase": "active",
            "values": ["active"],
            "conjunction": None,
        }
    ]


def test_supplied_by_phrase_is_joined_lookup_not_grouped_aggregate():
    intent = build_intent("show order items for products supplied by suppliers in Pune")

    assert intent["intent_type"] == "list"
    assert intent["requested_dimensions"] == []
    assert intent["join_lookup_request"] == {
        "requested": True,
        "base_entity_phrase": "order items",
        "related_request_phrase": "suppliers",
        "requested_output_fields": [],
        "projection_mode": "broad_related",
    }
    assert intent["shape_decision"]["selected_shape"] == "joined_lookup"


def test_product_category_prefers_entity_name_over_status_noise():
    candidates = [
        _candidate("categories", "category_name", semantic_type="name", is_dimension=True),
        _candidate("categories", "category_status", semantic_type="status", is_dimension=True),
        _candidate("products", "product_status", semantic_type="status", is_dimension=True),
    ]

    result = rank_role_candidates("product category", candidates, role="dimension")

    assert result["status"] == "resolved"
    selected = result["selected"]["candidate"]
    assert (selected["table"], selected["column"]) == ("categories", "category_name")
