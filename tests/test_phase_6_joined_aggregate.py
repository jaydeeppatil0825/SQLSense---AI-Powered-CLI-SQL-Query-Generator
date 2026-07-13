from copy import deepcopy
from unittest.mock import MagicMock

import pytest

from query_pipeline.intent_builder import build_intent
from query_pipeline.query_planner import build_query_context
from sql_pipeline.deterministic_sql_generator import generate_deterministic_sql
from sql_pipeline.query_executor import execute_query
from sql_pipeline.question_service import QuestionService
from sql_pipeline.sql_validator import validate_sql_structure


def _knowledge_base(*, second_fk=False, fallback=False, no_customer_edge=False):
    order_columns = [
        {"name": "order_id", "type": "INTEGER", "semantic_type": "id"},
        {"name": "customer_id", "type": "INTEGER", "semantic_type": "id"},
        {"name": "payment_status", "type": "VARCHAR(30)", "semantic_type": "status", "is_dimension": True},
        {
            "name": "order_status",
            "type": "VARCHAR(30)",
            "semantic_type": "status",
            "is_dimension": True,
            "sample_values": ["Delivered", "Pending"],
        },
        {"name": "total_amount", "type": "DECIMAL(12,2)", "semantic_type": "money", "is_measure": True},
        {"name": "paid_amount", "type": "DECIMAL(12,2)", "semantic_type": "money", "is_measure": True},
    ]
    foreign_keys = [] if fallback or no_customer_edge else [
        {"column": "customer_id", "referenced_table": "customers", "referenced_column": "customer_id"}
    ]
    if second_fk:
        order_columns.append({"name": "billing_customer_id", "type": "INTEGER", "semantic_type": "id"})
        foreign_keys.append(
            {"column": "billing_customer_id", "referenced_table": "customers", "referenced_column": "customer_id"}
        )
    relationships = []
    if fallback:
        relationships.append(
            {
                "from_table": "service_orders",
                "from_column": "customer_id",
                "to_table": "customers",
                "to_column": "customer_id",
                "relationship_type": "inferred",
                "source": "kb_build_inference",
                "confidence": 0.9,
                "safe_for_planner": True,
                "is_inferred": True,
                "is_fallback": True,
                "evidence": ["naming_pattern", "compatible_data_type"],
                "evidence_reasons": ["Names align", "Types align"],
                "reason": "Persisted KB-build relationship evidence.",
            }
        )
    return {
        "service_orders": {
            "columns": order_columns,
            "primary_keys": ["order_id"],
            "foreign_keys": foreign_keys,
            "relationships": relationships,
        },
        "customers": {
            "columns": [
                {"name": "customer_id", "type": "INTEGER", "semantic_type": "id"},
                {"name": "customer_name", "type": "VARCHAR(100)", "semantic_type": "name", "is_dimension": True},
                {"name": "city", "type": "VARCHAR(100)", "semantic_type": "text", "is_dimension": True},
                {"name": "customer_type", "type": "VARCHAR(30)", "semantic_type": "text", "is_dimension": True},
                {"name": "customer_segment", "type": "VARCHAR(30)", "semantic_type": "text", "is_dimension": True},
                {"name": "customer_status", "type": "VARCHAR(30)", "semantic_type": "status", "is_dimension": True},
                {"name": "region_id", "type": "INTEGER", "semantic_type": "id"},
            ],
            "primary_keys": ["customer_id"],
            "foreign_keys": [
                {"column": "region_id", "referenced_table": "regions", "referenced_column": "region_id"}
            ],
            "relationships": [],
        },
        "regions": {
            "columns": [
                {"name": "region_id", "type": "INTEGER", "semantic_type": "id"},
                {"name": "region_name", "type": "VARCHAR(100)", "semantic_type": "text", "is_dimension": True},
            ],
            "primary_keys": ["region_id"],
            "foreign_keys": [],
            "relationships": [],
        },
        "suppliers": {
            "columns": [
                {"name": "supplier_id", "type": "INTEGER", "semantic_type": "id"},
                {"name": "city", "type": "VARCHAR(100)", "semantic_type": "text", "is_dimension": True},
            ],
            "primary_keys": ["supplier_id"],
            "foreign_keys": [],
            "relationships": [],
        },
        "order_items": {
            "columns": [
                {"name": "item_id", "type": "INTEGER", "semantic_type": "id"},
                {"name": "product_id", "type": "INTEGER", "semantic_type": "id"},
                {"name": "quantity", "type": "INTEGER", "semantic_type": "quantity", "is_measure": True},
                {"name": "line_total", "type": "DECIMAL(12,2)", "semantic_type": "money", "is_measure": True},
            ],
            "primary_keys": ["item_id"],
            "foreign_keys": [
                {"column": "product_id", "referenced_table": "products", "referenced_column": "product_id"}
            ],
            "relationships": [],
        },
        "products": {
            "columns": [
                {"name": "product_id", "type": "INTEGER", "semantic_type": "id"},
                {"name": "product_name", "type": "VARCHAR(100)", "semantic_type": "name", "is_dimension": True},
                {"name": "category", "type": "VARCHAR(100)", "semantic_type": "text", "is_dimension": True},
                {"name": "brand", "type": "VARCHAR(100)", "semantic_type": "text", "is_dimension": True},
                {
                    "name": "product_status",
                    "type": "VARCHAR(30)",
                    "semantic_type": "status",
                    "is_dimension": True,
                    "sample_values": ["Active", "Inactive"],
                },
            ],
            "primary_keys": ["product_id"],
            "foreign_keys": [],
            "relationships": [],
        },
    }


def _candidate(table, column, *, role, terms, score=0.99):
    return {
        "table": table,
        "column": column,
        "semantic_type": "money" if role == "metric" else "text",
        "is_measure": role == "metric",
        "is_dimension": role in {"dimension", "filter"},
        "score": score,
        "matched_terms": list(terms),
        "source": "phase6_normalized_evidence",
    }


def _evidence(question, *, formula=False):
    intent = build_intent(question)
    text = question.lower()
    metrics = []
    if "profit" not in text and (intent.get("requested_metrics") or intent.get("metric_phrase")):
        total = _candidate("service_orders", "total_amount", role="metric", terms=["total amount", "order amount", "amount"])
        paid = _candidate("service_orders", "paid_amount", role="metric", terms=["paid amount", "amount"], score=0.98)
        quantity = _candidate("order_items", "quantity", role="metric", terms=["quantity", "units ordered"])
        line_total = _candidate("order_items", "line_total", role="metric", terms=["line total", "item sales", "total line total"])
        if "line total" in text or "item sales" in text:
            metrics = [line_total]
        elif "quantity" in text:
            metrics = [quantity]
        elif "paid amount" in text and "total amount" not in text:
            metrics = [paid]
        elif " amount" in text and "total amount" not in text and "paid amount" not in text:
            metrics = [total, paid]
        else:
            metrics = [total]

    dimensions = []
    if "customer city and customer type" in text:
        dimensions = [
            _candidate("customers", "city", role="dimension", terms=["customer city"]),
            _candidate("customers", "customer_type", role="dimension", terms=["customer type"]),
        ]
    elif "supplier city" in text:
        dimensions = [_candidate("suppliers", "city", role="dimension", terms=["supplier city"])]
    elif "customer city" in text:
        dimensions = [_candidate("customers", "city", role="dimension", terms=["customer city"])]
    elif "customer type" in text:
        dimensions = [_candidate("customers", "customer_type", role="dimension", terms=["customer type"])]
    elif "customer segment" in text:
        dimensions = [_candidate("customers", "customer_segment", role="dimension", terms=["customer segment"])]
    elif "top customers" in text:
        dimensions = [_candidate("customers", "customer_name", role="dimension", terms=["customers"])]
    elif "product category" in text:
        dimensions = [_candidate("products", "category", role="dimension", terms=["product category"])]
    elif "product brand" in text:
        dimensions = [_candidate("products", "brand", role="dimension", terms=["product brand"])]
    elif "top 4 products" in text:
        dimensions = [_candidate("products", "product_name", role="dimension", terms=["products"])]
    elif " by status" in text:
        dimensions = [
            _candidate("service_orders", "order_status", role="dimension", terms=["status"]),
            _candidate("customers", "customer_status", role="dimension", terms=["status"], score=0.98),
        ]

    filters = []
    for clause in intent.get("structured_filters") or []:
        field = str(clause.get("field_phrase") or "")
        if field == "payment status":
            filters = [_candidate("service_orders", "payment_status", role="filter", terms=[field])]
        elif field == "customer status":
            filters = [_candidate("customers", "customer_status", role="filter", terms=[field])]
        elif field == "status":
            filters = [
                _candidate("service_orders", "order_status", role="filter", terms=[field]),
                _candidate("customers", "customer_status", role="filter", terms=[field], score=0.98),
            ]

    columns = [*metrics, *dimensions, *filters]
    return {
        "query_terms": [],
        "matched_tables": [
            {"table": table, "score": 0.99, "matched_terms": [table.replace("_", " ")], "source": "test"}
            for table in {entry["table"] for entry in columns} | {"service_orders"}
        ],
        "matched_columns": columns,
        "matched_glossary_terms": [],
        "matched_relationships": [],
        "possible_join_paths": [
            {
                "from_table": "service_orders",
                "to_table": "customers",
                "path": [{"join_condition": "service_orders.order_id = customers.customer_id"}],
                "length": 1,
            }
        ],
        "measure_candidates": metrics,
        "dimension_candidates": dimensions,
        "filter_candidates": filters,
        "date_candidates": [],
        "formula_evidence": [{"expression": "total_amount - paid_amount"}] if formula else [],
        "retrieval_sources": ["phase6_normalized_evidence"],
        "ambiguity_candidates": {},
        "missing_evidence_indicators": {},
        "confidence": 0.99,
    }


def _context(question, *, kb=None, formula=False):
    knowledge_base = kb or _knowledge_base()
    return build_query_context(
        question,
        knowledge_base,
        intent=build_intent(question),
        retrieved_context=_evidence(question, formula=formula),
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
        "formula_evidence": context.get("formula_evidence") or [],
        "evidence_sources": context["evidence_sources"],
    }


@pytest.mark.parametrize(
    ("question", "aggregate", "dimension", "extra"),
    [
        ("show sum total amount by customer city from service orders", "SUM(service_orders.total_amount)", "customers.city", ()),
        ("show average total amount by customer type from service orders", "AVG(service_orders.total_amount)", "customers.customer_type", ()),
        ("count service orders by customer city", "COUNT(*)", "customers.city", ()),
        ("show minimum total amount by customer city from service orders", "MIN(service_orders.total_amount)", "customers.city", ()),
        ("show maximum total amount by customer city from service orders", "MAX(service_orders.total_amount)", "customers.city", ()),
        (
            "show sum total amount by customer city from service orders where payment status is paid",
            "SUM(service_orders.total_amount)",
            "customers.city",
            ("WHERE service_orders.payment_status = 'paid'",),
        ),
        (
            "show sum total amount by customer city from service orders where customer status is active",
            "SUM(service_orders.total_amount)",
            "customers.city",
            ("WHERE customers.customer_status = 'active'",),
        ),
        (
            "show customer city where sum total amount greater than 50000 from service orders",
            "SUM(service_orders.total_amount)",
            "customers.city",
            ("HAVING SUM(service_orders.total_amount) > 50000",),
        ),
        (
            "top customers by total amount",
            "SUM(service_orders.total_amount)",
            "customers.customer_name",
            ("ORDER BY sum__service_orders__total_amount DESC", "LIMIT 50"),
        ),
        (
            "top 3 customer city by sum total amount from service orders",
            "SUM(service_orders.total_amount)",
            "customers.city",
            ("ORDER BY sum__service_orders__total_amount DESC", "LIMIT 3"),
        ),
    ],
)
def test_joined_aggregate_positive_shapes(question, aggregate, dimension, extra):
    kb = _knowledge_base()
    context = _context(question, kb=kb)
    result = generate_deterministic_sql(query_context=context, knowledge_base=kb)

    assert context["route_recommendation"] == "deterministic_sql_required"
    assert context["query_shape"] == "joined_aggregate"
    assert len(context["selected_join_path"]["edges"]) == 1
    assert result.status == "generated"
    assert f"{dimension} AS {dimension.replace('.', '__')}" in result.sql
    assert aggregate in result.sql
    assert "FROM service_orders INNER JOIN customers ON service_orders.customer_id = customers.customer_id" in result.sql
    assert f"GROUP BY {dimension}" in result.sql
    assert all(fragment in result.sql for fragment in extra)
    assert "SELECT *" not in result.sql
    valid, reason = validate_sql_structure(
        result.sql,
        kb,
        selected_join_path=context["selected_join_path"],
    )
    assert valid is True, reason


def test_joined_aggregate_contract_has_exact_decision_path():
    context = _context("show sum total amount by customer city from service orders")
    path = context["clause_plan"]["decision_path"]

    assert [entry["node"] for entry in path] == [
        "unsafe_check", "table_scope", "query_shape", "aggregate", "metric", "dimension",
        "join_need", "relationship_graph_lookup", "safe_join_path", "ambiguity_check",
        "where", "having", "order_by", "limit", "clause_shape", "route",
    ]
    statuses = {entry["node"]: entry["status"] for entry in path}
    assert statuses["where"] == "not_required"
    assert statuses["having"] == "not_required"
    assert statuses["order_by"] == "not_required"
    assert statuses["limit"] == "not_required"
    assert statuses["route"] == "resolved"


def test_count_contract_does_not_require_metric():
    context = _context("count service orders by customer city")

    assert context["selected_metric"] is None
    assert context["clause_plan"]["requires"]["metric"] is False
    metric_node = next(entry for entry in context["clause_plan"]["decision_path"] if entry["node"] == "metric")
    assert metric_node["status"] == "not_required"


def test_count_orders_uses_graph_to_disambiguate_base_table():
    kb = _knowledge_base()
    context = _context("count orders by customer segment", kb=kb)
    result = generate_deterministic_sql(query_context=context, knowledge_base=kb)

    assert context["route_recommendation"] == "deterministic_sql_required"
    assert context["selected_join_path"]["base_table"] == "service_orders"
    assert context["selected_dimensions"][0]["table"] == "customers"
    assert context["selected_dimensions"][0]["column"] == "customer_segment"
    assert "COUNT(*) AS count__service_orders__rows" in result.sql
    assert "GROUP BY customers.customer_segment" in result.sql


def test_metric_modifier_becomes_sample_backed_base_filter():
    kb = _knowledge_base()
    context = _context("show total delivered order amount by customer city", kb=kb)
    result = generate_deterministic_sql(query_context=context, knowledge_base=kb)

    assert context["route_recommendation"] == "deterministic_sql_required"
    assert context["selected_metric"]["table"] == "service_orders"
    assert context["selected_metric"]["column"] == "total_amount"
    assert context["selected_filters"][0]["table"] == "service_orders"
    assert context["selected_filters"][0]["column"] == "order_status"
    assert "WHERE service_orders.order_status = 'Delivered'" in result.sql


def test_metric_modifier_can_resolve_related_table_filter():
    kb = _knowledge_base()
    context = _context("show active item sales by product category", kb=kb)
    result = generate_deterministic_sql(query_context=context, knowledge_base=kb)

    assert context["route_recommendation"] == "deterministic_sql_required"
    assert context["selected_metric"]["table"] == "order_items"
    assert context["selected_metric"]["column"] == "line_total"
    assert context["selected_filters"][0]["table"] == "products"
    assert context["selected_filters"][0]["column"] == "product_status"
    assert "WHERE products.product_status = 'Active'" in result.sql


def test_source_scope_value_owner_becomes_related_filter():
    kb = _knowledge_base()
    context = _context("total quantity by product category for active products", kb=kb)
    result = generate_deterministic_sql(query_context=context, knowledge_base=kb)

    assert context["route_recommendation"] == "deterministic_sql_required"
    assert context["selected_metric"]["table"] == "order_items"
    assert context["selected_metric"]["column"] == "quantity"
    assert context["selected_dimensions"][0]["table"] == "products"
    assert context["selected_dimensions"][0]["column"] == "category"
    assert context["selected_filters"][0]["table"] == "products"
    assert context["selected_filters"][0]["column"] == "product_status"
    assert "SUM(order_items.quantity)" in result.sql
    assert "WHERE products.product_status = 'Active'" in result.sql


def test_product_joined_aggregate_and_entity_ranking_questions():
    kb = _knowledge_base()

    item_sales = _context("show total item sales by product category", kb=kb)
    item_sales_sql = generate_deterministic_sql(query_context=item_sales, knowledge_base=kb).sql
    assert item_sales["route_recommendation"] == "deterministic_sql_required"
    assert item_sales["selected_metric"]["table"] == "order_items"
    assert item_sales["selected_metric"]["column"] == "line_total"
    assert item_sales["selected_dimensions"][0]["table"] == "products"
    assert item_sales["selected_dimensions"][0]["column"] == "category"
    assert "SUM(order_items.line_total)" in item_sales_sql

    top_products = _context("top 4 products by total line total", kb=kb)
    top_products_sql = generate_deterministic_sql(query_context=top_products, knowledge_base=kb).sql
    assert top_products["route_recommendation"] == "deterministic_sql_required"
    assert top_products["selected_dimensions"][0]["table"] == "products"
    assert top_products["selected_dimensions"][0]["column"] == "product_name"
    assert top_products["selected_order_by"]["aggregate_function"] == "sum"
    assert "ORDER BY sum__order_items__line_total DESC" in top_products_sql
    assert "LIMIT 4" in top_products_sql

    brand_count = _context("count order items by product brand", kb=kb)
    brand_count_sql = generate_deterministic_sql(query_context=brand_count, knowledge_base=kb).sql
    assert brand_count["route_recommendation"] == "deterministic_sql_required"
    assert brand_count["selected_dimensions"][0]["table"] == "products"
    assert brand_count["selected_dimensions"][0]["column"] == "brand"
    assert "COUNT(*) AS count__order_items__rows" in brand_count_sql
    assert "GROUP BY products.brand" in brand_count_sql


def test_joined_aggregate_selected_evidence_lifts_exact_graph_confidence():
    question = "show total order amount by customer city"
    kb = _knowledge_base()
    intent = build_intent(question)
    evidence = _evidence(question)
    evidence["confidence"] = 0.2

    context = build_query_context(question, kb, intent=intent, retrieved_context=evidence)

    assert context["route_recommendation"] == "deterministic_sql_required"
    assert context["confidence"] >= 0.86
    assert "Retrieved context is weak; planner confidence is low." not in context["warnings"]
    assert context["selected_evidence"]["metric"]["tier"] in {"exact_normalized_column", "owner_qualified_exact", "kb_glossary_semantic"}
    assert context["selected_evidence"]["dimension"]["tier"] == "owner_qualified_exact"
    assert context["selected_evidence"]["relationship_graph"]["tier"] == "selected_join_path_agreement"


def test_joined_aggregate_fallback_metric_keeps_weak_context_warning():
    question = "show total item sales by product category"
    kb = _knowledge_base()
    intent = build_intent(question)
    product_category = _candidate("products", "category", role="dimension", terms=["product category"])
    evidence = _evidence(question)
    evidence["confidence"] = 0.2
    evidence["measure_candidates"] = []
    evidence["matched_columns"] = [product_category]
    evidence["dimension_candidates"] = [product_category]

    context = build_query_context(question, kb, intent=intent, retrieved_context=evidence)

    assert context["route_recommendation"] == "deterministic_sql_required"
    assert context["selected_evidence"]["metric"]["tier"] == "numeric_metric_eligible"
    assert "fallback-only" in " ".join(context["selected_evidence"]["metric"]["reasons"])
    assert "Retrieved context is weak; planner confidence is low." in context["warnings"]


def test_status_column_marked_measure_cannot_pollute_metric_candidates():
    question = "show sum payment status by customer city from service orders"
    kb = _knowledge_base()
    intent = build_intent(question)
    bad_status_metric = {
        "table": "service_orders",
        "column": "payment_status",
        "semantic_type": "status",
        "core_semantic_type": "status",
        "data_type": "VARCHAR(30)",
        "is_measure": True,
        "is_dimension": True,
        "score": 0.99,
        "matched_terms": ["payment status"],
        "source": "test_bad_metric",
    }
    customer_city = _candidate("customers", "city", role="dimension", terms=["customer city"])
    evidence = _evidence(question)
    evidence["measure_candidates"] = [bad_status_metric]
    evidence["matched_columns"] = [bad_status_metric, customer_city]
    evidence["dimension_candidates"] = [customer_city]

    context = build_query_context(question, kb, intent=intent, retrieved_context=evidence)

    assert context["route_recommendation"] == "cannot_plan_safely"
    assert context["query_shape"] == "joined_aggregate"
    assert context["selected_join_path"] is None


def test_joined_aggregate_resolves_unique_owner_table_monetary_metric_without_retrieval_mapping():
    question = "show total item sales by product category"
    kb = _knowledge_base()
    intent = build_intent(question)
    product_category = _candidate("products", "category", role="dimension", terms=["product category"])
    evidence = _evidence(question)
    evidence["measure_candidates"] = []
    evidence["matched_columns"] = [product_category]
    evidence["dimension_candidates"] = [product_category]

    context = build_query_context(
        question,
        kb,
        intent=intent,
        retrieved_context=evidence,
    )

    assert context["route_recommendation"] == "deterministic_sql_required"
    assert context["query_shape"] == "joined_aggregate"
    assert context["selected_metric"]["table"] == "order_items"
    assert context["selected_metric"]["column"] == "line_total"
    assert context["selected_metric"]["source"] == "kb_schema_profile"
    assert context["plan"]["unresolved_metrics"] == []


def test_question_service_dispatches_joined_aggregate():
    question = "show sum total amount by customer city from service orders"
    kb = _knowledge_base()
    context = _context(question, kb=kb)

    success, _, sql, error = QuestionService().process_question(
        question,
        kb,
        pipeline_context=_pipeline_context(question, context),
    )

    assert success is True
    assert error is None
    assert "SUM(service_orders.total_amount)" in sql


def test_persisted_safe_fallback_relationship_authorizes_joined_aggregate():
    kb = _knowledge_base(fallback=True)
    context = _context("show sum total amount by customer city from service orders", kb=kb)

    assert context["route_recommendation"] == "deterministic_sql_required"
    edge = context["selected_join_path"]["edges"][0]
    assert edge["relationship_type"] == "inferred"
    assert edge["safe_for_planner"] is True
    assert edge["evidence"]
    assert edge["evidence_reasons"]


def test_generator_rejects_planner_contract_mismatch():
    kb = _knowledge_base()
    context = _context("show sum total amount by customer city from service orders", kb=kb)
    context["clause_plan"] = deepcopy(context["clause_plan"])
    context["clause_plan"]["selected_join_path"] = None

    result = generate_deterministic_sql(query_context=context, knowledge_base=kb)

    assert result.status == "cannot_plan_safely"
    assert result.sql is None
    assert "clause" in result.reason


def test_generator_rejects_planner_selected_alias_mismatch():
    kb = _knowledge_base()
    context = _context("show sum total amount by customer city from service orders", kb=kb)
    context["selected_output_columns"] = deepcopy(context["selected_output_columns"])
    context["selected_output_columns"][1]["alias"] = "different_aggregate_alias"

    result = generate_deterministic_sql(query_context=context, knowledge_base=kb)

    assert result.status == "cannot_plan_safely"
    assert result.sql is None
    assert "output" in result.reason


def test_validator_rejects_selected_graph_path_mismatch():
    kb = _knowledge_base()
    context = _context("show sum total amount by customer city from service orders", kb=kb)
    sql = generate_deterministic_sql(query_context=context, knowledge_base=kb).sql
    selected_path = deepcopy(context["selected_join_path"])
    selected_path["edges"][0]["from_column"] = "order_id"

    valid, reason = validate_sql_structure(sql, kb, selected_join_path=selected_path)

    assert valid is False
    assert "selected_join_path" in reason


@pytest.mark.parametrize(
    "question",
    [
        "show sum total amount and paid amount by customer city from service orders",
        "show sum total amount by customer city and customer type from service orders",
        "show profit by customer city from service orders",
        "show sum total amount by supplier city from service orders",
        "show sum total amount by customer city from service orders using left join",
        "show sum total amount by customer city from service orders via region",
        "show sum total amount by customer city from service orders and products",
        "show sum amount by customer city from service orders",
        "show sum total amount by status from service orders",
    ],
)
def test_unsupported_or_ambiguous_joined_aggregates_fail_closed(question):
    kb = _knowledge_base()
    context = _context(question, kb=kb)
    success, _, sql, _ = QuestionService().process_question(
        question,
        kb,
        pipeline_context=_pipeline_context(question, context),
    )

    assert context["route_recommendation"] == "cannot_plan_safely"
    assert context.get("selected_join_path") is None
    assert success is False
    assert sql is None


def test_unsafe_joined_aggregate_is_blocked():
    context = _context("delete service orders by customer city")

    assert context["route_recommendation"] == "blocked_unsafe"
    assert context.get("selected_join_path") is None


def test_ambiguous_relationship_edge_fails_closed():
    kb = _knowledge_base(second_fk=True)
    context = _context("show sum total amount by customer city from service orders", kb=kb)

    assert context["route_recommendation"] == "cannot_plan_safely"
    assert context.get("selected_join_path") is None
    assert "multiple distinct" in context["route_reason"]


def test_formula_joined_aggregate_fails_closed():
    question = "show total amount minus paid amount by customer city from service orders"
    context = _context(question, formula=True)

    assert context["route_recommendation"] == "cannot_plan_safely"
    assert context.get("selected_join_path") is None


def test_having_aggregate_mismatch_fails_closed():
    question = "show sum total amount by customer city from service orders having avg total amount greater than 50000"
    context = _context(question)

    assert context["route_recommendation"] == "cannot_plan_safely"
    assert context.get("selected_join_path") is None
    assert "HAVING" in context["route_reason"]


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT customers.city, SUM(service_orders.total_amount) FROM service_orders LEFT JOIN customers ON service_orders.customer_id = customers.customer_id GROUP BY customers.city;",
        "SELECT customers.city, SUM(service_orders.total_amount) FROM service_orders INNER JOIN customers ON service_orders.order_id = customers.customer_id GROUP BY customers.city;",
        "SELECT *, SUM(service_orders.total_amount) FROM service_orders INNER JOIN customers ON service_orders.customer_id = customers.customer_id GROUP BY customers.city;",
        "SELECT customers.*, SUM(service_orders.total_amount) FROM service_orders INNER JOIN customers ON service_orders.customer_id = customers.customer_id GROUP BY customers.city;",
        "SELECT customers.city AS customers__city, SUM(service_orders.total_amount) AS sum__service_orders__total_amount FROM service_orders INNER JOIN customers ON service_orders.customer_id = customers.customer_id GROUP BY customers.city ORDER BY customers__city DESC;",
        "SELECT customers.city AS customers__city, SUM(service_orders.total_amount) AS sum__service_orders__total_amount FROM service_orders INNER JOIN customers ON service_orders.customer_id = customers.customer_id GROUP BY customers.city HAVING AVG(service_orders.total_amount) > 5;",
    ],
)
def test_validator_rejects_invalid_joined_aggregate_sql(sql):
    valid, _ = validate_sql_structure(sql, _knowledge_base())

    assert valid is False


def test_executor_rejects_non_graph_join_before_connect():
    engine = MagicMock()
    sql = (
        "SELECT customers.city AS customers__city, "
        "SUM(service_orders.total_amount) AS sum__service_orders__total_amount "
        "FROM service_orders INNER JOIN customers "
        "ON service_orders.order_id = customers.customer_id "
        "GROUP BY customers.city;"
    )

    with pytest.raises(ValueError, match="structure invalid"):
        execute_query(sql, engine, knowledge_base=_knowledge_base())

    engine.connect.assert_not_called()
