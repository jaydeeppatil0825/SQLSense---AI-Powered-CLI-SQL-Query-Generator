from copy import deepcopy

from query_pipeline.intent_builder import build_intent
from query_pipeline.query_planner import build_query_context
from sql_pipeline.deterministic_sql_generator import generate_deterministic_sql
from sql_pipeline.sql_validator import validate_sql_structure


def _column(name, type_="VARCHAR", semantic_type="text", *, measure=False, dimension=False):
    return {
        "name": name,
        "type": type_,
        "semantic_type": semantic_type,
        "is_measure": measure,
        "is_dimension": dimension,
    }


def _kb(*, direct_order_customer=False, ambiguous=False, unknown_cardinality=False, long_path=False):
    orders_fks = [] if unknown_cardinality else [
        {"column": "customer_id", "referenced_table": "customers", "referenced_column": "customer_id"}
    ]
    kb = {
        "payments": {
            "columns": [
                _column("payment_id", "INTEGER", "id"),
                _column("order_id", "INTEGER", "id"),
                _column("payment_amount", "DECIMAL(12,2)", "money", measure=True),
                _column("payment_status", "VARCHAR(30)", "status", dimension=True),
            ],
            "primary_keys": ["payment_id"],
            "foreign_keys": [
                {"column": "order_id", "referenced_table": "orders", "referenced_column": "order_id"}
            ],
            "relationships": [],
        },
        "orders": {
            "columns": [
                _column("order_id", "INTEGER", "id"),
                _column("customer_id", "INTEGER", "id"),
                _column("order_amount", "DECIMAL(12,2)", "money", measure=True),
            ],
            "primary_keys": ["order_id"],
            "foreign_keys": orders_fks,
            "relationships": [],
        },
        "customers": {
            "columns": [
                _column("customer_id", "INTEGER", "id"),
                _column("city", "VARCHAR(100)", "text", dimension=True),
                _column("customer_status", "VARCHAR(30)", "status", dimension=True),
            ],
            "primary_keys": ["customer_id"],
            "foreign_keys": [],
            "relationships": [],
        },
    }
    if direct_order_customer:
        kb["orders"]["foreign_keys"] = [
            {"column": "customer_id", "referenced_table": "customers", "referenced_column": "customer_id"}
        ]
    if ambiguous:
        kb["invoices"] = {
            "columns": [
                _column("invoice_id", "INTEGER", "id"),
                _column("payment_id", "INTEGER", "id"),
                _column("customer_id", "INTEGER", "id"),
            ],
            "primary_keys": ["invoice_id"],
            "foreign_keys": [
                {"column": "payment_id", "referenced_table": "payments", "referenced_column": "payment_id"},
                {"column": "customer_id", "referenced_table": "customers", "referenced_column": "customer_id"},
            ],
            "relationships": [],
        }
    if long_path:
        kb["customers"]["columns"].append(_column("city_id", "INTEGER", "id"))
        kb["customers"]["foreign_keys"] = [
            {"column": "city_id", "referenced_table": "cities", "referenced_column": "city_id"}
        ]
        kb["cities"] = {
            "columns": [_column("city_id", "INTEGER", "id"), _column("city_name", "VARCHAR(100)", "text", dimension=True)],
            "primary_keys": ["city_id"],
            "foreign_keys": [],
            "relationships": [],
        }
    return kb


def _candidate(table, column, *, role, terms):
    return {
        "table": table,
        "column": column,
        "semantic_type": "money" if role == "metric" else "text",
        "is_measure": role == "metric",
        "is_dimension": role == "dimension",
        "score": 0.99,
        "matched_terms": terms,
        "source": "phase8b_test",
    }


def _evidence(question, *, metric_table="payments", metric_column="payment_amount", dimension_table="customers", dimension_column="city"):
    metric = _candidate(metric_table, metric_column, role="metric", terms=["payment amount", "amount"])
    dimension = _candidate(
        dimension_table,
        dimension_column,
        role="dimension",
        terms=[f"{dimension_table.rstrip('s')} {dimension_column}", dimension_column],
    )
    return {
        "query_terms": [],
        "matched_tables": [
            {"table": table, "score": 0.99, "matched_terms": [table.replace("_", " ")]}
            for table in {metric_table, dimension_table, "orders", "payments", "customers"}
        ],
        "matched_columns": [metric, dimension],
        "measure_candidates": [] if question.startswith("count ") else [metric],
        "dimension_candidates": [dimension],
        "filter_candidates": [],
        "date_candidates": [],
        "matched_glossary_terms": [],
        "matched_relationships": [],
        "possible_join_paths": [],
        "retrieval_sources": ["phase8b_test"],
        "ambiguity_candidates": {},
        "missing_evidence_indicators": {},
        "confidence": 0.99,
    }


def _context(question, kb=None, **evidence_kwargs):
    return build_query_context(
        question,
        kb or _kb(),
        intent=build_intent(question),
        retrieved_context=_evidence(question, **evidence_kwargs),
    )


def test_safe_sum_builds_two_edge_joined_aggregate_contract():
    context = _context("total payment amount by customer city")

    assert context["route_recommendation"] == "deterministic_sql_required"
    assert context["query_shape"] == "joined_aggregate"
    assert context["selected_join_path"]["base_table"] == "payments"
    assert context["selected_join_path"]["joined_tables"] == ["orders", "customers"]
    assert len(context["selected_join_path"]["edges"]) == 2
    assert context["phase8a_grain_analysis"]["status"] == "grain_preserved"


def test_safe_avg_and_count_use_preserved_metric_grain():
    avg_context = _context(
        "average payment amount by customer status",
        dimension_column="customer_status",
    )
    count_context = _context("count payments by customer city")

    assert avg_context["route_recommendation"] == "deterministic_sql_required"
    assert avg_context["aggregate_function"] == "avg"
    assert avg_context["phase8a_grain_analysis"]["status"] == "grain_preserved"
    assert count_context["route_recommendation"] == "deterministic_sql_required"
    assert count_context["selected_metric"] is None
    assert count_context["phase8a_grain_analysis"]["status"] == "grain_preserved"


def test_parent_to_child_count_fails_closed_for_row_multiplication():
    context = _context(
        "count customers by payment status",
        metric_table="customers",
        metric_column="customer_id",
        dimension_table="payments",
        dimension_column="payment_status",
    )

    assert context["route_recommendation"] == "cannot_plan_safely"
    assert context["selected_join_path"] is None
    assert "row_multiplication_risk" in context["route_reason"]


def test_parent_metric_row_multiplication_fails_closed():
    kb = _kb()
    kb["customers"]["columns"].append(_column("customer_value", "DECIMAL(12,2)", "money", measure=True))
    context = _context(
        "total customer value by payment status",
        kb=kb,
        metric_table="customers",
        metric_column="customer_value",
        dimension_table="payments",
        dimension_column="payment_status",
    )

    assert context["route_recommendation"] == "cannot_plan_safely"
    assert "row_multiplication_risk" in context["route_reason"]


def test_unknown_cardinality_ambiguous_path_and_depth_fail_closed():
    unknown = _context("total payment amount by customer city", kb=_kb(unknown_cardinality=True))
    ambiguous = _context("total payment amount by customer city", kb=_kb(ambiguous=True))
    too_deep = _context(
        "total payment amount by city name",
        kb=_kb(long_path=True),
        dimension_table="cities",
        dimension_column="city_name",
    )

    assert unknown["route_recommendation"] == "cannot_plan_safely"
    assert "no_safe_path" in unknown["route_reason"]
    assert ambiguous["route_recommendation"] == "cannot_plan_safely"
    assert "ambiguous_path" in ambiguous["route_reason"]
    assert too_deep["route_recommendation"] == "cannot_plan_safely"
    assert "unsupported_depth" in too_deep["route_reason"]


def test_direct_joined_aggregate_and_two_edge_lookup_remain_unchanged():
    direct_kb = {
        "orders": deepcopy(_kb()["orders"]),
        "customers": deepcopy(_kb()["customers"]),
    }
    direct_kb["orders"]["foreign_keys"] = [
        {"column": "customer_id", "referenced_table": "customers", "referenced_column": "customer_id"}
    ]
    direct = _context(
        "total order amount by customer city",
        kb=direct_kb,
        metric_table="orders",
        metric_column="order_amount",
    )

    assert direct["route_recommendation"] == "deterministic_sql_required"
    assert len(direct["selected_join_path"]["edges"]) == 1
    assert direct.get("phase8a_grain_analysis") is None


def test_source_scope_filter_does_not_override_explicit_metric_owner():
    kb = _kb()
    kb["orders"]["columns"].append(
        {
            **_column("order_status", "VARCHAR(30)", "status", dimension=True),
            "sample_values": ["Delivered", "Pending"],
        }
    )
    question = "show total payment amount by customer city for delivered orders"
    metric = _candidate("orders", "order_amount", role="metric", terms=["amount"])
    dimension = _candidate("customers", "city", role="dimension", terms=["customer city"])
    context = build_query_context(
        question,
        kb,
        intent=build_intent(question),
        retrieved_context={
            "query_terms": [],
            "matched_tables": [
                {"table": "payments", "score": 0.9, "matched_terms": ["payment"]},
                {"table": "orders", "score": 0.99, "matched_terms": ["delivered orders"]},
                {"table": "customers", "score": 0.9, "matched_terms": ["customer"]},
            ],
            "matched_columns": [metric, dimension],
            "measure_candidates": [metric],
            "dimension_candidates": [dimension],
            "filter_candidates": [],
            "date_candidates": [],
            "matched_glossary_terms": [],
            "matched_relationships": [],
            "possible_join_paths": [],
            "retrieval_sources": ["phase8g_test"],
            "ambiguity_candidates": {},
            "missing_evidence_indicators": {},
            "confidence": 0.99,
        },
    )
    result = generate_deterministic_sql(query_context=context, knowledge_base=kb)

    assert context["route_recommendation"] == "deterministic_sql_required"
    assert context["selected_metric"]["table"] == "payments"
    assert context["selected_metric"]["column"] == "payment_amount"
    assert context["selected_filters"][0]["table"] == "orders"
    assert context["selected_filters"][0]["column"] == "order_status"
    assert context["selected_filters"][0]["value"] == "Delivered"
    assert result.status == "generated"
    assert "SUM(payments.payment_amount)" in result.sql
    assert "WHERE orders.order_status = 'Delivered'" in result.sql


def test_safe_phase8b_contract_generates_but_remains_validator_blocked():
    kb = _kb()
    context = _context("total payment amount by customer city", kb=kb)
    result = generate_deterministic_sql(query_context=context, knowledge_base=kb)

    assert context["route_recommendation"] == "deterministic_sql_required"
    assert len(context["selected_join_path"]["edges"]) == 2
    assert result.status == "generated"
    assert validate_sql_structure(result.sql, kb, selected_join_path=context["selected_join_path"])[0] is False
