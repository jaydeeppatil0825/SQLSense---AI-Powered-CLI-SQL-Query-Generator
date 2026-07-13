from sql_pipeline.deterministic_sql_generator import generate_deterministic_sql

from query_pipeline.intent_builder import build_intent
from query_pipeline.query_planner import build_query_context


def _kb():
    return {
        "order_items": {
            "columns": [
                {"name": "order_item_id", "type": "INTEGER"},
                {"name": "order_id", "type": "INTEGER"},
                {"name": "product_id", "type": "INTEGER"},
            ],
            "foreign_keys": [
                {"column": "order_id", "referenced_table": "orders", "referenced_column": "order_id"},
            ],
        },
        "orders": {
            "columns": [
                {"name": "order_id", "type": "INTEGER"},
                {"name": "customer_id", "type": "INTEGER"},
            ],
            "foreign_keys": [
                {"column": "customer_id", "referenced_table": "customers", "referenced_column": "customer_id"},
            ],
        },
        "customers": {
            "columns": [
                {"name": "customer_id", "type": "INTEGER"},
                {"name": "customer_name", "type": "VARCHAR"},
            ],
            "foreign_keys": [],
        },
        "payments": {
            "columns": [
                {"name": "payment_id", "type": "INTEGER"},
                {"name": "order_id", "type": "INTEGER"},
            ],
            "foreign_keys": [
                {"column": "order_id", "referenced_table": "orders", "referenced_column": "order_id"},
            ],
        },
        "products": {
            "columns": [
                {"name": "product_id", "type": "INTEGER"},
                {"name": "product_name", "type": "VARCHAR"},
            ],
            "foreign_keys": [],
        },
    }


def _evidence():
    return {
        "matched_tables": [
            {"table": "order_items", "score": 0.99, "matched_terms": ["order items", "order item"]},
            {"table": "orders", "score": 0.98, "matched_terms": ["orders"]},
            {"table": "customers", "score": 0.97, "matched_terms": ["customers", "customer"]},
            {"table": "payments", "score": 0.96, "matched_terms": ["payments", "payment"]},
            {"table": "products", "score": 0.95, "matched_terms": ["products", "product"]},
        ],
        "matched_columns": [],
        "matched_glossary_terms": [],
        "matched_relationships": [],
        "possible_join_paths": [],
        "measure_candidates": [],
        "dimension_candidates": [],
        "filter_candidates": [],
        "date_candidates": [],
        "retrieval_sources": ["phase_7g_test"],
        "ambiguity_candidates": {},
        "missing_evidence_indicators": {},
        "confidence": 0.99,
    }


def _context(question):
    return build_query_context(question, _kb(), intent=build_intent(question), retrieved_context=_evidence())


def _sql(question):
    context = _context(question)
    result = generate_deterministic_sql(query_context=context, knowledge_base=_kb())
    return context, result.sql


def test_equivalent_order_item_customer_phrases_resolve_same_path_and_sql():
    questions = [
        "show order items with customer details",
        "show me order items and their customer details",
        "list order item records with customer info",
    ]
    results = [_sql(question) for question in questions]

    assert {context["query_shape"] for context, _ in results} == {"joined_lookup"}
    assert {context["route_recommendation"] for context, _ in results} == {"deterministic_sql_required"}
    assert {tuple(context["selected_join_path"]["joined_tables"]) for context, _ in results} == {("orders", "customers")}
    assert {len(context["selected_join_path"]["edges"]) for context, _ in results} == {2}
    assert {tuple((f["table"], f["column"]) for f in context.get("selected_filters") or []) for context, _ in results} == {()}
    assert len({sql for _, sql in results}) == 1


def test_equivalent_payment_customer_phrases_resolve_same_path_and_sql():
    questions = [
        "can you show payments along with customer details",
        "list payment records with customer information",
    ]
    results = [_sql(question) for question in questions]

    assert {tuple(context["selected_join_path"]["joined_tables"]) for context, _ in results} == {("orders", "customers")}
    assert {len(context["selected_join_path"]["edges"]) for context, _ in results} == {2}
    assert len({sql for _, sql in results}) == 1


def test_equivalent_customer_payment_phrases_resolve_same_path_and_sql():
    questions = [
        "show customers together with payment details",
        "can you list customers with payment information",
    ]
    results = [_sql(question) for question in questions]

    assert {tuple(context["selected_join_path"]["joined_tables"]) for context, _ in results} == {("orders", "payments")}
    assert {len(context["selected_join_path"]["edges"]) for context, _ in results} == {2}
    assert len({sql for _, sql in results}) == 1


def test_direct_join_control_remains_direct():
    context, sql = _sql("show me all orders with customer information")

    assert context["query_shape"] == "joined_lookup"
    assert context["selected_join_path"]["joined_tables"] == ["customers"]
    assert len(context["selected_join_path"]["edges"]) == 1
    assert "INNER JOIN customers" in sql


def test_expanded_connector_and_filler_words_keep_equivalent_direct_lookup_sql():
    questions = [
        "show orders including customer data",
        "display orders plus customer profile",
        "return orders linked to customer overview",
        "provide orders related to customer fields",
        "get orders associated with customer attributes",
        "fetch orders belonging to customer information",
        "view orders connected to customer info",
        "list orders combined with customer details",
    ]
    results = [_sql(question) for question in questions]

    assert {context["query_shape"] for context, _ in results} == {"joined_lookup"}
    assert {tuple(context["selected_join_path"]["joined_tables"]) for context, _ in results} == {("customers",)}
    assert {tuple((f["table"], f["column"]) for f in context.get("selected_filters") or []) for context, _ in results} == {()}
    assert len({sql for _, sql in results}) == 1


def test_unsupported_long_path_and_multi_hop_aggregate_fail_closed():
    long_path = _context("show payments with product details")
    aggregate = _context("show total payment amount by customer city")

    assert long_path["route_recommendation"] == "cannot_plan_safely"
    assert long_path.get("selected_join_path") is None
    assert aggregate["route_recommendation"] == "cannot_plan_safely"
    assert aggregate.get("selected_join_path") is None
