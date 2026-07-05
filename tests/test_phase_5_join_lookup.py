from copy import deepcopy
from unittest.mock import MagicMock

import pytest

from kb_pipeline.relationship_graph import (
    build_relationship_graph,
    find_safe_direct_join_relationships,
)
from query_pipeline.intent_builder import build_intent
from query_pipeline.query_planner import build_query_context
from sql_pipeline.deterministic_sql_generator import generate_deterministic_sql
from sql_pipeline.query_executor import execute_query
from sql_pipeline.question_service import QuestionService
from sql_pipeline.sql_validator import validate_sql_structure


def _orders_kb(*, second_fk=False):
    order_columns = [
        {"name": "order_id", "type": "INTEGER", "semantic_type": "id"},
        {"name": "customer_id", "type": "INTEGER", "semantic_type": "id"},
        {"name": "order_status", "type": "VARCHAR(30)", "semantic_type": "status"},
    ]
    foreign_keys = [
        {"column": "customer_id", "referenced_table": "customers", "referenced_column": "customer_id"}
    ]
    if second_fk:
        order_columns.append({"name": "billing_customer_id", "type": "INTEGER", "semantic_type": "id"})
        foreign_keys.append(
            {"column": "billing_customer_id", "referenced_table": "customers", "referenced_column": "customer_id"}
        )
    return {
        "orders": {
            "columns": order_columns,
            "primary_keys": ["order_id"],
            "foreign_keys": foreign_keys,
            "relationships": [],
        },
        "customers": {
            "columns": [
                {"name": "customer_id", "type": "INTEGER", "semantic_type": "id"},
                {"name": "name", "type": "VARCHAR(100)", "semantic_type": "name"},
                {"name": "city", "type": "VARCHAR(100)", "semantic_type": "text"},
            ],
            "primary_keys": ["customer_id"],
            "foreign_keys": [],
            "relationships": [],
        },
    }


def _fallback_relationship(*, reverse=False, safe=True, evidence=True):
    relationship = {
        "from_table": "orders",
        "from_column": "customer_id",
        "to_table": "customers",
        "to_column": "customer_id",
        "direction": "many-to-one",
        "relationship_type": "inferred",
        "confidence": 0.9,
        "reason": "Persisted KB-build relationship evidence.",
        "evidence": ["naming_pattern", "compatible_data_type"] if evidence else [],
        "evidence_reasons": ["Names align", "Types align"] if evidence else [],
        "safe_for_planner": safe,
        "is_inferred": True,
        "is_fallback": True,
        "source": "kb_build_inference",
    }
    if reverse:
        relationship.update(
            {
                "from_table": "customers",
                "from_column": "customer_id",
                "to_table": "orders",
                "to_column": "customer_id",
                "direction": "one-to-many",
            }
        )
    return relationship


def _evidence(*, include_name=True, include_city=False):
    columns = []
    filters = []
    if include_name:
        columns.append(
            {
                "table": "customers",
                "column": "name",
                "semantic_type": "name",
                "score": 0.99,
                "matched_terms": ["customer name"],
                "source": "normalized_test_evidence",
            }
        )
    if include_city:
        city = {
            "table": "customers",
            "column": "city",
            "semantic_type": "text",
            "score": 0.99,
            "matched_terms": ["customer city"],
            "source": "normalized_test_evidence",
        }
        columns.append(city)
        filters.append(city)
    return {
        "query_terms": [],
        "matched_tables": [
            {"table": "orders", "score": 0.99, "matched_terms": ["orders"], "source": "test"},
            {"table": "customers", "score": 0.97, "matched_terms": ["customers"], "source": "test"},
        ],
        "matched_columns": columns,
        "matched_glossary_terms": [],
        "matched_relationships": [],
        # Retrieval paths are intentionally wrong; only Relationship Graph may authorize SQL.
        "possible_join_paths": [
            {
                "from_table": "orders",
                "to_table": "customers",
                "path": [{"join_condition": "orders.order_id = customers.customer_id"}],
                "length": 1,
            }
        ],
        "measure_candidates": [],
        "dimension_candidates": [],
        "filter_candidates": filters,
        "date_candidates": [],
        "retrieval_sources": ["normalized_test_evidence"],
        "ambiguity_candidates": {},
        "missing_evidence_indicators": {},
        "confidence": 0.98,
    }


def _context(question, *, kb=None, include_name=True, include_city=False):
    knowledge_base = kb or _orders_kb()
    return build_query_context(
        question,
        knowledge_base,
        intent=build_intent(question),
        retrieved_context=_evidence(include_name=include_name, include_city=include_city),
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
    ("question", "mode", "base", "fields"),
    [
        ("show orders with customer details", "broad_related", "orders", []),
        ("show orders with customer name", "base_plus_related_fields", "orders", ["customer name"]),
        ("show order id and customer name", "explicit_fields_only", "", ["order id", "customer name"]),
    ],
)
def test_join_lookup_intent_projection_modes(question, mode, base, fields):
    request = build_intent(question)["join_lookup_request"]

    assert request["requested"] is True
    assert request["projection_mode"] == mode
    assert request["base_entity_phrase"] == base
    assert request["requested_output_fields"] == fields


def test_relationship_graph_loads_real_fk_without_runtime_inference():
    graph = build_relationship_graph(_orders_kb(), infer_relationships=False)
    edges = find_safe_direct_join_relationships(graph, "orders", "customers")

    assert len(edges) == 1
    assert edges[0]["relationship_type"] == "foreign_key"
    assert edges[0]["source"] == "database_metadata"
    assert edges[0]["confidence"] == 1.0


def test_relationship_graph_loads_only_complete_safe_persisted_fallbacks():
    kb = _orders_kb()
    kb["orders"]["foreign_keys"] = []
    kb["orders"]["relationships"] = [_fallback_relationship()]
    graph = build_relationship_graph(kb, infer_relationships=False)
    assert len(find_safe_direct_join_relationships(graph, "orders", "customers")) == 1

    kb["orders"]["relationships"] = [_fallback_relationship(evidence=False)]
    graph = build_relationship_graph(kb, infer_relationships=False)
    assert find_safe_direct_join_relationships(graph, "orders", "customers") == []


def test_relationship_graph_deduplicates_reversed_copy_of_same_edge():
    kb = _orders_kb()
    kb["orders"]["foreign_keys"] = []
    kb["orders"]["relationships"] = [_fallback_relationship()]
    kb["customers"]["relationships"] = [_fallback_relationship(reverse=True)]

    graph = build_relationship_graph(kb, infer_relationships=False)

    edges = find_safe_direct_join_relationships(graph, "orders", "customers")
    assert len(edges) == 1
    assert edges[0]["from_table"] == "orders"
    assert edges[0]["to_table"] == "customers"


def test_planner_builds_graph_authorized_join_and_exact_decision_tree():
    context = _context("show orders with customer name")

    assert context["route_recommendation"] == "deterministic_sql_required"
    assert context["query_shape"] == "joined_lookup"
    assert context["selected_join_path"]["path_source"] == "relationship_graph"
    assert context["selected_join_path"]["edges"][0]["from_column"] == "customer_id"
    assert [node["node"] for node in context["clause_plan"]["decision_path"]] == [
        "unsafe_check",
        "table_scope",
        "requested_fields",
        "join_need",
        "relationship_graph_lookup",
        "safe_join_path",
        "ambiguity_check",
        "where",
        "selected_output_columns",
        "route",
    ]
    assert all(node["status"] != "blocked" for node in context["clause_plan"]["decision_path"])


def test_planner_projection_modes_are_qualified_and_aliased():
    broad = _context("show orders with customer details")
    related = _context("show orders with customer name")
    fields_only = _context("show order id and customer name")

    assert len(broad["selected_output_columns"]) == 6
    assert [entry["expression"] for entry in related["selected_output_columns"]][-1] == "customers.name"
    assert [entry["expression"] for entry in fields_only["selected_output_columns"]] == [
        "orders.order_id",
        "customers.name",
    ]
    for context in (broad, related, fields_only):
        assert all(entry["alias"] == f"{entry['table']}__{entry['column']}" for entry in context["selected_output_columns"])


def test_explicit_fields_only_uses_authoritative_graph_orientation():
    context = _context("show order id and customer name")

    assert context["selected_join_path"]["base_table"] == "orders"
    assert context["selected_join_path"]["joined_tables"] == ["customers"]


def test_explicit_fields_only_honors_explicit_source_scope_base():
    context = _context("show order id and customer name from orders")

    assert context["route_recommendation"] == "deterministic_sql_required"
    assert context["selected_join_path"]["base_table"] == "orders"


def test_related_table_filter_resolves_join_and_base_only_projection():
    context = _context(
        "show orders where customer city is Pune",
        include_name=False,
        include_city=True,
    )

    assert context["query_shape"] == "joined_lookup"
    assert context["route_recommendation"] == "deterministic_sql_required"
    assert context["selected_filters"][0]["table"] == "customers"
    assert {entry["table"] for entry in context["selected_output_columns"]} == {"orders"}


def test_planner_fails_closed_for_genuinely_distinct_graph_edges():
    context = _context("show orders with customer name", kb=_orders_kb(second_fk=True))

    assert context["route_recommendation"] == "cannot_plan_safely"
    assert context["selected_join_path"] is None
    assert context["clause_plan"]["decision_path"][6]["status"] == "blocked"


def test_generator_renders_only_selected_graph_path_and_projection():
    context = _context("show orders with customer name")
    result = generate_deterministic_sql(query_context=context, knowledge_base=_orders_kb())

    assert result.status == "generated"
    assert result.sql == (
        "SELECT orders.order_id AS orders__order_id, "
        "orders.customer_id AS orders__customer_id, "
        "orders.order_status AS orders__order_status, "
        "customers.name AS customers__name FROM orders "
        "INNER JOIN customers ON orders.customer_id = customers.customer_id LIMIT 50;"
    )
    assert "orders.order_id = customers.customer_id" not in result.sql


def test_generator_qualifies_related_where_filter():
    context = _context(
        "show orders where customer city is Pune",
        include_name=False,
        include_city=True,
    )
    result = generate_deterministic_sql(query_context=context, knowledge_base=_orders_kb())

    assert result.status == "generated"
    assert "WHERE customers.city = 'Pune'" in result.sql


def test_generator_rejects_join_path_contract_mismatch():
    context = _context("show orders with customer name")
    context["selected_join_path"] = deepcopy(context["selected_join_path"])
    context["selected_join_path"]["edges"][0]["from_column"] = "order_id"

    result = generate_deterministic_sql(query_context=context, knowledge_base=_orders_kb())

    assert result.status == "cannot_plan_safely"
    assert result.sql is None


def test_validator_accepts_reversed_graph_equality():
    sql = (
        "SELECT orders.order_id AS orders__order_id, customers.name AS customers__name "
        "FROM orders INNER JOIN customers "
        "ON customers.customer_id = orders.customer_id LIMIT 50;"
    )

    assert validate_sql_structure(sql, _orders_kb())[0] is True


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT orders.order_id FROM orders CROSS JOIN customers;",
        "SELECT orders.order_id FROM orders, customers WHERE orders.customer_id = customers.customer_id;",
        "SELECT orders.order_id FROM orders, customers;",
        "SELECT orders.order_id FROM orders INNER JOIN customers USING (customer_id);",
        "SELECT orders.order_id FROM orders LEFT JOIN customers ON orders.customer_id = customers.customer_id;",
        "SELECT orders.order_id FROM orders INNER JOIN customers ON orders.order_id = customers.customer_id;",
    ],
)
def test_validator_rejects_unsupported_or_non_graph_join(sql):
    assert validate_sql_structure(sql, _orders_kb())[0] is False


def test_question_service_dispatches_joined_lookup_deterministically():
    question = "show orders with customer name"
    context = _context(question)
    success, _, sql, error = QuestionService().process_question(
        question,
        _orders_kb(),
        pipeline_context=_pipeline_context(question, context),
    )

    assert success is True
    assert error is None
    assert "INNER JOIN customers ON orders.customer_id = customers.customer_id" in sql


def test_joined_aggregate_fails_closed_without_sql():
    question = "show sum order id with customer name"
    context = _context(question)
    success, _, sql, _ = QuestionService().process_question(
        question,
        _orders_kb(),
        pipeline_context=_pipeline_context(question, context),
    )

    assert success is False
    assert sql is None


def test_executor_rejects_non_graph_join_before_connection_use():
    engine = MagicMock()
    sql = (
        "SELECT orders.order_id AS orders__order_id, customers.name AS customers__name "
        "FROM orders INNER JOIN customers ON orders.order_id = customers.customer_id;"
    )

    with pytest.raises(ValueError, match="Relationship Graph"):
        execute_query(sql, engine, knowledge_base=_orders_kb())

    engine.connect.assert_not_called()
