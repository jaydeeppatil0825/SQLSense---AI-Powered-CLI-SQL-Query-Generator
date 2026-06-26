"""Tests for dynamic query planner behavior."""

from core.query_planner import build_query_context, build_intent
from semantic.business_glossary import generate_business_glossary


def test_query_planner_uses_dynamic_glossary_and_kb_metadata():
    knowledge_base = {
        "entity_groups": {
            "table_name": "entity_groups",
            "business_description": "Stores entity group records",
            "business_purpose": "Stores entity group records linked to deals.",
            "columns": [
                {
                    "name": "group_id",
                    "type": "INTEGER",
                    "semantic_type": "id",
                },
                {
                    "name": "display_name",
                    "type": "VARCHAR(100)",
                    "semantic_type": "name",
                    "business_description": "Display label or name for the entity group.",
                    "business_terms": ["account label", "account name"],
                    "is_dimension": True,
                },
            ],
            "primary_keys": ["group_id"],
            "foreign_keys": [],
            "relationships": [],
        },
        "measure_events": {
            "table_name": "measure_events",
            "columns": [
                {
                    "name": "event_id",
                    "type": "INTEGER",
                    "semantic_type": "id",
                },
                {
                    "name": "group_id",
                    "type": "INTEGER",
                    "semantic_type": "id",
                    "is_foreign_key": True,
                },
            ],
            "primary_keys": ["event_id"],
            "foreign_keys": [{"column": "group_id", "referenced_table": "entity_groups", "referenced_column": "group_id"}],
            "relationships": [],
        },
    }
    glossary = generate_business_glossary(knowledge_base, use_ai_enrichment=True)

    context = build_query_context(
        "show account label",
        knowledge_base,
        business_glossary=glossary,
        use_vector_retrieval=False,
    )

    assert context["selected_table_names"][0] == "entity_groups"
    assert all("module" not in entry for entry in context["selected_tables"])
    selected_columns = context["selected_tables"][0]["selected_columns"]
    assert any(column["column"] == "display_name" for column in selected_columns)
    assert context["confidence"] >= 0.6


def test_query_planner_returns_low_confidence_when_business_context_is_weak():
    knowledge_base = {
        "alpha_records": {
            "columns": [
                {"name": "alpha_id", "type": "INTEGER", "semantic_type": "id"},
                {"name": "label_text", "type": "VARCHAR(100)", "semantic_type": "text_candidate"},
            ],
            "primary_keys": ["alpha_id"],
            "foreign_keys": [],
            "relationships": [],
        },
        "beta_events": {
            "columns": [
                {"name": "beta_id", "type": "INTEGER", "semantic_type": "id"},
                {"name": "event_text", "type": "VARCHAR(100)", "semantic_type": "text_candidate"},
            ],
            "primary_keys": ["beta_id"],
            "foreign_keys": [],
            "relationships": [],
        },
    }

    context = build_query_context(
        "show pending payables",
        knowledge_base,
        business_glossary={},
        use_vector_retrieval=False,
    )

    assert context["selected_table_names"] == []
    assert context["route_recommendation"] == "cannot_plan_safely"
    assert "missing_table" in context["missing_evidence"]
    assert context["confidence"] <= 0.55
    assert context["plan"]["metric"] is None


def test_query_planner_prefers_unique_direct_singular_table_match_for_simple_browse():
    knowledge_base = {
        "clients": {
            "columns": [
                {"name": "client_id", "type": "INTEGER", "semantic_type": "id"},
                {"name": "client_name", "type": "VARCHAR(100)", "semantic_type": "text_candidate"},
            ],
            "primary_keys": ["client_id"],
            "foreign_keys": [],
            "relationships": [],
        },
        "agreements": {
            "columns": [
                {"name": "agreement_id", "type": "INTEGER", "semantic_type": "id"},
                {"name": "client_id", "type": "INTEGER", "semantic_type": "id"},
            ],
            "primary_keys": ["agreement_id"],
            "foreign_keys": [
                {"column": "client_id", "referenced_table": "clients", "referenced_column": "client_id"},
            ],
            "relationships": [],
        },
        "invoices": {
            "columns": [
                {"name": "invoice_id", "type": "INTEGER", "semantic_type": "id"},
                {"name": "client_id", "type": "INTEGER", "semantic_type": "id"},
            ],
            "primary_keys": ["invoice_id"],
            "foreign_keys": [
                {"column": "client_id", "referenced_table": "clients", "referenced_column": "client_id"},
            ],
            "relationships": [],
        },
    }

    context = build_query_context(
        "show all client",
        knowledge_base,
        business_glossary={},
        use_vector_retrieval=False,
    )

    assert context["selected_table_names"] == ["clients"]
    assert context["join_paths"] == []
    assert context["route_recommendation"] == "simple_rule_based"
    assert context["query_shape"] == "single_table_list"
    assert context["complex_sql_plan"] == {}


def test_qp2_single_table_aggregate_contract_preserves_metric_evidence():
    knowledge_base = {
        "partners": {
            "columns": [
                {"name": "partner_id", "type": "INTEGER", "semantic_type": "id"},
                {"name": "partner_name", "type": "VARCHAR(100)", "semantic_type": "text_candidate"},
            ],
            "primary_keys": ["partner_id"],
            "foreign_keys": [],
            "relationships": [],
        },
        "bills": {
            "columns": [
                {"name": "bill_id", "type": "INTEGER", "semantic_type": "id"},
                {"name": "partner_id", "type": "INTEGER", "semantic_type": "id"},
                {"name": "amount_total", "type": "DECIMAL(12,2)", "semantic_type": "numeric_candidate", "is_measure": True},
            ],
            "primary_keys": ["bill_id"],
            "foreign_keys": [{"column": "partner_id", "referenced_table": "partners", "referenced_column": "partner_id"}],
            "relationships": [],
        },
    }
    glossary = generate_business_glossary(knowledge_base, use_ai_enrichment=True)

    context = build_query_context(
        "show total amount from bills",
        knowledge_base,
        business_glossary=glossary,
        use_vector_retrieval=False,
    )

    assert context["query_shape"] == "single_table_aggregate"
    assert context["route_recommendation"] == "deterministic_sql_required"
    assert context["selected_table_names"] == ["bills"]
    assert context["join_paths"] == []
    assert context["required_joins"] == []
    assert any(candidate["column"] == "amount_total" for candidate in context["metric_candidates"])
    metric_entry = next(candidate for candidate in context["metric_candidates"] if candidate["column"] == "amount_total")
    assert "metric" in metric_entry["reason"].lower()
    assert context["vector_results"] == {}


def test_qp6_show_sum_amount_from_bills_routes_to_deterministic_sql():
    knowledge_base = {
        "bills": {
            "columns": [
                {"name": "bill_id", "type": "INTEGER", "semantic_type": "id"},
                {"name": "amount_total", "type": "DECIMAL(12,2)", "semantic_type": "numeric_candidate", "is_measure": True},
            ],
            "primary_keys": ["bill_id"],
            "foreign_keys": [],
            "relationships": [],
        },
    }
    glossary = generate_business_glossary(knowledge_base, use_ai_enrichment=True)

    context = build_query_context(
        "show sum amount from bills",
        knowledge_base,
        business_glossary=glossary,
        use_vector_retrieval=False,
    )

    assert context["query_shape"] == "single_table_aggregate"
    assert context["route_recommendation"] == "deterministic_sql_required"


def test_qp6_metric_fallback_from_selected_columns_keeps_aggregate_route_safe():
    knowledge_base = {
        "bills": {
            "columns": [
                {"name": "bill_id", "type": "INTEGER", "semantic_type": "id"},
                {
                    "name": "amount_total",
                    "type": "DECIMAL(12,2)",
                    "semantic_type": "numeric_candidate",
                    "planner_roles": {
                        "measure_candidate": True,
                        "dimension_candidate": False,
                        "filter_candidate": False,
                        "join_candidate": False,
                        "date_candidate": False,
                        "sort_candidate": True,
                    },
                },
            ],
            "primary_keys": ["bill_id"],
            "foreign_keys": [],
            "relationships": [],
        },
    }
    intent = {
        "user_goal": "show sum amount from bills",
        "intent_type": "aggregate",
        "business_operation": "summarize",
        "requested_metrics": ["amount"],
        "requested_dimensions": [],
        "requested_filters": [],
        "requested_sort": {},
        "aggregate_function": "sum",
        "source_scope": ["bills"],
        "limit": None,
        "needs_grouping": False,
        "needs_aggregation": True,
        "needs_join": False,
        "raw_business_terms": ["amount", "bills"],
        "confidence": 0.72,
        "source": "fallback",
    }
    retrieved_context = {
        "query_terms": ["amount", "bills"],
        "matched_tables": [{"table": "bills", "score": 1.0, "matched_terms": ["bills"], "source": "kb_identifier"}],
        "matched_columns": [
            {
                "table": "bills",
                "column": "amount_total",
                "semantic_type": "numeric_candidate",
                "core_semantic_type": "numeric_candidate",
                "is_measure": True,
                "is_dimension": False,
                "is_date": False,
                "score": 0.91,
                "matched_terms": ["amount"],
                "evidence_sources": ["runtime_column_name"],
                "source": "kb_identifier",
            }
        ],
        "matched_glossary_terms": [],
        "matched_relationships": [],
        "possible_join_paths": [],
        "measure_candidates": [],
        "dimension_candidates": [],
        "filter_candidates": [],
        "retrieval_sources": ["kb_identifier"],
        "confidence": 0.91,
    }

    context = build_query_context(
        "show sum amount from bills",
        knowledge_base,
        business_glossary={},
        use_vector_retrieval=False,
        intent=intent,
        retrieved_context=retrieved_context,
    )

    assert context["selected_table_names"] == ["bills"]
    assert context["query_shape"] == "single_table_aggregate"
    assert context["route_recommendation"] == "deterministic_sql_required"
    assert context["can_plan"] is True
    assert context["missing_evidence"] == []
    assert any(candidate["column"] == "amount_total" for candidate in context["metric_candidates"])


def test_qp6_show_sum_billed_value_from_bills_keeps_single_table_aggregate_contract():
    knowledge_base = {
        "bills": {
            "columns": [
                {"name": "bill_id", "type": "INTEGER", "semantic_type": "id"},
                {"name": "billed_value", "type": "DECIMAL(12,2)", "semantic_type": "numeric_candidate"},
                {"name": "paid_value", "type": "DECIMAL(12,2)", "semantic_type": "numeric_candidate"},
            ],
            "primary_keys": ["bill_id"],
            "foreign_keys": [],
            "relationships": [],
        },
    }
    glossary = generate_business_glossary(knowledge_base, use_ai_enrichment=True)

    context = build_query_context(
        "show sum billed value from bills",
        knowledge_base,
        business_glossary=glossary,
        use_vector_retrieval=False,
    )

    assert context["query_shape"] == "single_table_aggregate"
    assert context["route_recommendation"] == "deterministic_sql_required"
    assert context["can_plan"] is True
    assert context["selected_table_names"] == ["bills"]
    assert context["missing_evidence"] == []
    assert any(candidate["column"] == "billed_value" for candidate in context["metric_candidates"])
    assert context["route_reason"]


def test_qp3_multi_metric_aggregate_classification():
    knowledge_base = {
        "bills": {
            "columns": [
                {"name": "bill_id", "type": "INTEGER", "semantic_type": "id"},
                {"name": "amount_total", "type": "DECIMAL(12,2)", "semantic_type": "numeric_candidate", "is_measure": True},
                {"name": "tax_total", "type": "DECIMAL(12,2)", "semantic_type": "numeric_candidate", "is_measure": True},
            ],
            "primary_keys": ["bill_id"],
            "foreign_keys": [],
            "relationships": [],
        },
    }
    glossary = generate_business_glossary(knowledge_base, use_ai_enrichment=True)

    context = build_query_context(
        "show total amount and tax from bills",
        knowledge_base,
        business_glossary=glossary,
        use_vector_retrieval=False,
    )

    assert context["query_shape"] == "multi_metric_aggregate"
    assert context["route_recommendation"] == "deterministic_sql_required"
    metric_names = {candidate["column"] for candidate in context["metric_candidates"]}
    assert {"amount_total", "tax_total"} <= metric_names


def test_qp2_joined_lookup_exposes_join_candidates():
    knowledge_base = {
        "partners": {
            "columns": [
                {"name": "partner_id", "type": "INTEGER", "semantic_type": "id"},
                {"name": "partner_name", "type": "VARCHAR(100)", "semantic_type": "text_candidate"},
            ],
            "primary_keys": ["partner_id"],
            "foreign_keys": [],
            "relationships": [],
        },
        "bills": {
            "columns": [
                {"name": "bill_id", "type": "INTEGER", "semantic_type": "id"},
                {"name": "partner_id", "type": "INTEGER", "semantic_type": "id"},
            ],
            "primary_keys": ["bill_id"],
            "foreign_keys": [{"column": "partner_id", "referenced_table": "partners", "referenced_column": "partner_id"}],
            "relationships": [],
        },
    }
    intent = {
        "intent_type": "list",
        "requested_metrics": [],
        "requested_dimensions": [],
        "requested_filters": [],
        "requested_sort": {},
        "limit": None,
        "needs_grouping": False,
        "needs_aggregation": False,
        "needs_join": True,
    }
    retrieved_context = {
        "query_terms": ["bills", "partner", "name"],
        "matched_tables": [
            {"table": "bills", "score": 0.88, "matched_terms": ["bills"], "source": "kb_identifier"},
            {"table": "partners", "score": 0.85, "matched_terms": ["partner"], "source": "kb_identifier"},
        ],
        "matched_columns": [
            {"table": "partners", "column": "partner_name", "semantic_type": "text_candidate", "is_dimension": True, "score": 0.92, "matched_terms": ["partner name"], "source": "glossary"},
        ],
        "matched_glossary_terms": [{"term": "partner name", "score": 0.9, "source": "glossary"}],
        "matched_relationships": [
            {
                "from_table": "bills",
                "from_column": "partner_id",
                "to_table": "partners",
                "to_column": "partner_id",
                "join_condition": "bills.partner_id = partners.partner_id",
                "source": "fk_relationship",
            }
        ],
        "possible_join_paths": [
            {
                "from_table": "bills",
                "to_table": "partners",
                "path": [
                    {
                        "from_table": "bills",
                        "from_column": "partner_id",
                        "to_table": "partners",
                        "to_column": "partner_id",
                        "join_condition": "bills.partner_id = partners.partner_id",
                    }
                ],
                "length": 1,
            }
        ],
        "measure_candidates": [],
        "dimension_candidates": [
            {"table": "partners", "column": "partner_name", "semantic_type": "text_candidate", "is_dimension": True, "score": 0.92, "matched_terms": ["partner name"], "source": "glossary"},
        ],
        "filter_candidates": [],
        "retrieval_sources": ["kb_identifier", "glossary", "relationship_context"],
        "confidence": 0.89,
    }

    context = build_query_context(
        "show bills with partner name",
        knowledge_base,
        use_vector_retrieval=False,
        intent=intent,
        retrieved_context=retrieved_context,
    )

    assert context["query_shape"] == "joined_lookup"
    assert context["route_recommendation"] == "deterministic_sql_required"
    assert context["join_candidates"]
    assert context["required_joins"] == ["bills.partner_id = partners.partner_id"]


def test_qp3_filtered_query_classification():
    knowledge_base = {
        "bills": {
            "columns": [
                {"name": "bill_id", "type": "INTEGER", "semantic_type": "id"},
                {"name": "amount_total", "type": "DECIMAL(12,2)", "semantic_type": "numeric_candidate", "is_measure": True},
                {"name": "status", "type": "VARCHAR(20)", "semantic_type": "text_candidate"},
            ],
            "primary_keys": ["bill_id"],
            "foreign_keys": [],
            "relationships": [],
        },
    }
    intent = {
        "intent_type": "list",
        "requested_metrics": ["total amount"],
        "requested_dimensions": [],
        "requested_filters": ["status pending"],
        "requested_sort": {},
        "limit": None,
        "needs_grouping": False,
        "needs_aggregation": True,
        "needs_join": False,
    }
    retrieved_context = {
        "query_terms": ["total amount", "status", "pending"],
        "matched_tables": [
            {"table": "bills", "score": 0.91, "matched_terms": ["bills"], "source": "kb_identifier"},
        ],
        "matched_columns": [
            {"table": "bills", "column": "amount_total", "semantic_type": "numeric_candidate", "is_measure": True, "score": 0.95, "matched_terms": ["total amount"], "source": "glossary"},
            {"table": "bills", "column": "status", "semantic_type": "text_candidate", "is_dimension": True, "score": 0.9, "matched_terms": ["status", "pending"], "source": "glossary"},
        ],
        "matched_glossary_terms": [{"term": "total amount", "score": 0.94, "source": "glossary"}],
        "matched_relationships": [],
        "possible_join_paths": [],
        "measure_candidates": [
            {"table": "bills", "column": "amount_total", "semantic_type": "numeric_candidate", "is_measure": True, "score": 0.95, "matched_terms": ["total amount"], "source": "glossary"},
        ],
        "dimension_candidates": [],
        "filter_candidates": [
            {"table": "bills", "column": "status", "semantic_type": "text_candidate", "score": 0.9, "matched_terms": ["status", "pending"], "source": "glossary"},
        ],
        "retrieval_sources": ["kb_identifier", "glossary"],
        "confidence": 0.9,
    }

    context = build_query_context(
        "total amount from bills where status is pending",
        knowledge_base,
        use_vector_retrieval=False,
        intent=intent,
        retrieved_context=retrieved_context,
    )

    assert context["query_shape"] == "filtered_query"
    assert context["route_recommendation"] == "deterministic_sql_required"
    assert context["filter_candidates"]
    assert any("filter" in str(entry.get("reason") or "").lower() for entry in context["filter_candidates"])


def test_qp6_missing_filter_evidence_routes_to_cannot_plan_safely():
    knowledge_base = {
        "bills": {
            "columns": [
                {"name": "bill_id", "type": "INTEGER", "semantic_type": "id"},
                {"name": "amount_total", "type": "DECIMAL(12,2)", "semantic_type": "numeric_candidate", "is_measure": True},
            ],
            "primary_keys": ["bill_id"],
            "foreign_keys": [],
            "relationships": [],
        },
    }
    intent = {
        "intent_type": "aggregate",
        "requested_metrics": ["amount"],
        "requested_dimensions": [],
        "requested_filters": ["status pending"],
        "requested_sort": {},
        "limit": None,
        "needs_grouping": False,
        "needs_aggregation": True,
        "needs_join": False,
    }
    retrieved_context = {
        "query_terms": ["amount", "status", "pending", "bills"],
        "matched_tables": [
            {"table": "bills", "score": 0.91, "matched_terms": ["bills"], "source": "kb_identifier"},
        ],
        "matched_columns": [
            {"table": "bills", "column": "amount_total", "semantic_type": "numeric_candidate", "is_measure": True, "score": 0.95, "matched_terms": ["amount"], "source": "glossary"},
        ],
        "matched_glossary_terms": [],
        "matched_relationships": [],
        "possible_join_paths": [],
        "measure_candidates": [
            {"table": "bills", "column": "amount_total", "semantic_type": "numeric_candidate", "is_measure": True, "score": 0.95, "matched_terms": ["amount"], "source": "glossary"},
        ],
        "dimension_candidates": [],
        "filter_candidates": [],
        "retrieval_sources": ["kb_identifier", "glossary"],
        "confidence": 0.9,
    }

    context = build_query_context(
        "total amount from bills where status is pending",
        knowledge_base,
        use_vector_retrieval=False,
        intent=intent,
        retrieved_context=retrieved_context,
    )

    assert context["query_shape"] == "filtered_query"
    assert context["route_recommendation"] == "cannot_plan_safely"
    assert "missing_filter_column" in context["missing_evidence"]


def test_qp5_vague_metric_only_question_stays_unplanned_without_table_evidence():
    knowledge_base = {
        "bills": {
            "columns": [
                {"name": "bill_id", "type": "INTEGER", "semantic_type": "id"},
                {"name": "amount_total", "type": "DECIMAL(12,2)", "semantic_type": "numeric_candidate", "is_measure": True},
            ],
            "primary_keys": ["bill_id"],
            "foreign_keys": [],
            "relationships": [],
        },
        "invoices": {
            "columns": [
                {"name": "invoice_id", "type": "INTEGER", "semantic_type": "id"},
                {"name": "amount_total", "type": "DECIMAL(12,2)", "semantic_type": "numeric_candidate", "is_measure": True},
            ],
            "primary_keys": ["invoice_id"],
            "foreign_keys": [],
            "relationships": [],
        },
    }

    context = build_query_context(
        "show amount",
        knowledge_base,
        business_glossary={},
        use_vector_retrieval=False,
    )

    assert context["route_recommendation"] == "cannot_plan_safely"
    assert context["query_shape"] == "unknown"
    assert "missing_table" in context["missing_evidence"]
    assert context["route_reason"] == "table evidence is missing or ambiguous"
    assert "table_selection" in context["ambiguities"] or context["confidence"] <= 0.55


def test_qp6_blocked_unsafe_route():
    knowledge_base = {
        "bills": {
            "columns": [
                {"name": "bill_id", "type": "INTEGER", "semantic_type": "id"},
            ],
            "primary_keys": ["bill_id"],
            "foreign_keys": [],
            "relationships": [],
        },
    }

    context = build_query_context(
        "drop bills",
        knowledge_base,
        business_glossary={},
        use_vector_retrieval=False,
    )

    assert context["query_shape"] == "blocked_unsafe"
    assert context["route_recommendation"] == "blocked_unsafe"


def test_query_planner_does_not_use_module_field_for_scoring():
    knowledge_base = {
        "alpha_records": {
            "module": "transaction",
            "columns": [
                {"name": "alpha_id", "type": "INTEGER", "semantic_type": "id"},
            ],
            "primary_keys": ["alpha_id"],
            "foreign_keys": [],
            "relationships": [],
        },
        "beta_records": {
            "module": "reference",
            "columns": [
                {"name": "beta_id", "type": "INTEGER", "semantic_type": "id"},
            ],
            "primary_keys": ["beta_id"],
            "foreign_keys": [],
            "relationships": [],
        },
    }

    context = build_query_context(
        "show transaction",
        knowledge_base,
        business_glossary={},
        use_vector_retrieval=False,
    )

    assert context["selected_table_names"] == []
    assert context["route_recommendation"] == "cannot_plan_safely"
    assert "missing_table" in context["missing_evidence"]
    assert context["confidence"] <= 0.55
    assert all("module" not in entry for entry in context["selected_tables"])


def test_query_planner_uses_retrieved_context_for_ranking_query():
    knowledge_base = {
        "accounts": {
            "columns": [
                {"name": "account_id", "type": "INTEGER", "semantic_type": "id"},
                {"name": "account_label", "type": "VARCHAR(100)", "semantic_type": "name", "is_dimension": True},
            ],
            "primary_keys": ["account_id"],
            "foreign_keys": [],
            "relationships": [],
        },
        "deals": {
            "columns": [
                {"name": "deal_id", "type": "INTEGER", "semantic_type": "id"},
                {"name": "account_id", "type": "INTEGER", "semantic_type": "id", "is_foreign_key": True},
                {"name": "deal_value", "type": "DECIMAL(12,2)", "semantic_type": "money", "is_measure": True},
            ],
            "primary_keys": ["deal_id"],
            "foreign_keys": [{"column": "account_id", "referenced_table": "accounts", "referenced_column": "account_id"}],
            "relationships": [],
        },
    }
    intent = {
        "intent_type": "ranking",
        "requested_metrics": ["deal value"],
        "requested_dimensions": ["accounts"],
        "requested_filters": [],
        "requested_sort": {"direction": "desc", "terms": "deal value"},
        "limit": 5,
    }
    retrieved_context = {
        "query_terms": ["accounts", "deal value"],
        "matched_tables": [
            {"table": "accounts", "score": 0.88, "matched_terms": ["accounts"], "source": "kb_identifier"},
            {"table": "deals", "score": 0.84, "matched_terms": ["deal value"], "source": "glossary"},
        ],
        "matched_columns": [
            {
                "table": "accounts",
                "column": "account_label",
                "semantic_type": "name",
                "is_dimension": True,
                "score": 0.92,
                "matched_terms": ["accounts"],
                "source": "glossary",
            },
            {
                "table": "deals",
                "column": "deal_value",
                "semantic_type": "money",
                "is_measure": True,
                "score": 0.91,
                "matched_terms": ["deal value"],
                "source": "glossary",
            },
        ],
        "matched_glossary_terms": [{"term": "deal value", "score": 0.9, "source": "glossary"}],
        "matched_relationships": [
            {
                "from_table": "deals",
                "from_column": "account_id",
                "to_table": "accounts",
                "to_column": "account_id",
                "join_condition": "deals.account_id = accounts.account_id",
                "source": "fk_relationship",
            }
        ],
        "possible_join_paths": [
            {
                "from_table": "accounts",
                "to_table": "deals",
                "path": [
                    {
                        "from_table": "accounts",
                        "from_column": "account_id",
                        "to_table": "deals",
                        "to_column": "account_id",
                        "join_condition": "accounts.account_id = deals.account_id",
                    }
                ],
                "length": 1,
            }
        ],
        "measure_candidates": [
            {
                "table": "deals",
                "column": "deal_value",
                "semantic_type": "money",
                "is_measure": True,
                "score": 0.91,
                "matched_terms": ["deal value"],
                "source": "glossary",
            }
        ],
        "dimension_candidates": [
            {
                "table": "accounts",
                "column": "account_label",
                "semantic_type": "name",
                "is_dimension": True,
                "score": 0.92,
                "matched_terms": ["accounts"],
                "source": "glossary",
            }
        ],
        "filter_candidates": [],
        "retrieval_sources": ["kb_identifier", "glossary"],
        "confidence": 0.87,
    }

    context = build_query_context(
        "top 5 accounts by deal value",
        knowledge_base,
        use_vector_retrieval=False,
        intent=intent,
        retrieved_context=retrieved_context,
    )

    assert context["plan"]["intent"] == "top_n"
    assert context["plan"]["grouping"] == ["accounts"]
    assert context["plan"]["sorting"] == {"direction": "desc", "by": "deal value"}
    assert context["selected_table_names"][:2] == ["accounts", "deals"]
    assert any(column["column"] == "account_label" for column in context["selected_columns"])
    assert any(column["column"] == "deal_value" for column in context["selected_columns"])
    assert context["join_paths"] == retrieved_context["possible_join_paths"]
    assert context["measure_candidates"][0]["column"] == "deal_value"
    assert context["dimension_candidates"][0]["column"] == "account_label"
    assert context["evidence_sources"] == ["kb_identifier", "glossary"]
    assert context["query_shape"] == "ranking_query"
    assert context["route_recommendation"] == "deterministic_sql_required"
    assert context["complex_sql_plan"]["query_shape"] == "ranking_query"
    assert context["complex_sql_plan"]["aggregation_type"] == "sum"
    assert context["complex_sql_plan"]["required_joins"] == ["accounts.account_id = deals.account_id"]
    assert context["complex_sql_plan"]["limit"] == 5
    assert context["complex_sql_plan"]["sql_skeleton_type"] == "ranking_aggregation"


def test_query_planner_leaves_pending_billed_amount_unresolved_without_formula_evidence():
    knowledge_base = {
        "accounts": {
            "columns": [
                {"name": "account_id", "type": "INTEGER", "semantic_type": "id"},
                {"name": "account_label", "type": "VARCHAR(100)", "semantic_type": "name", "is_dimension": True},
            ],
            "primary_keys": ["account_id"],
            "foreign_keys": [],
            "relationships": [],
        },
        "billing_notes": {
            "columns": [
                {"name": "billing_note_id", "type": "INTEGER", "semantic_type": "id"},
                {"name": "billed_value", "type": "DECIMAL(12,2)", "semantic_type": "money", "is_measure": True},
                {"name": "settlement_state", "type": "VARCHAR(30)", "semantic_type": "status"},
            ],
            "primary_keys": ["billing_note_id"],
            "foreign_keys": [],
            "relationships": [],
        },
    }
    intent = {
        "intent_type": "grouped_summary",
        "requested_metrics": ["pending billed amount"],
        "requested_dimensions": ["account"],
        "requested_filters": [],
        "requested_sort": {},
        "limit": None,
    }
    retrieved_context = {
        "query_terms": ["pending billed amount", "account"],
        "matched_tables": [
            {"table": "accounts", "score": 0.82, "matched_terms": ["account"], "source": "kb_identifier"},
            {"table": "billing_notes", "score": 0.76, "matched_terms": ["pending billed amount"], "source": "glossary"},
        ],
        "matched_columns": [
            {
                "table": "accounts",
                "column": "account_label",
                "semantic_type": "name",
                "is_dimension": True,
                "score": 0.88,
                "matched_terms": ["account"],
                "source": "kb_identifier",
            }
        ],
        "matched_glossary_terms": [{"term": "pending billed amount", "score": 0.78, "source": "glossary"}],
        "matched_relationships": [],
        "possible_join_paths": [],
        "measure_candidates": [],
        "dimension_candidates": [
            {
                "table": "accounts",
                "column": "account_label",
                "semantic_type": "name",
                "is_dimension": True,
                "score": 0.88,
                "matched_terms": ["account"],
                "source": "kb_identifier",
            }
        ],
        "filter_candidates": [],
        "retrieval_sources": ["kb_identifier", "glossary"],
        "confidence": 0.49,
    }

    context = build_query_context(
        "pending billed amount by account",
        knowledge_base,
        use_vector_retrieval=False,
        intent=intent,
        retrieved_context=retrieved_context,
    )

    assert context["plan"]["requested_metrics"] == ["pending billed amount"]
    assert context["plan"]["unresolved_metrics"] == ["pending billed amount"]
    assert context["measure_candidates"] == []
    assert context["plan"]["metric"] is None
    assert context["confidence"] == 0.49
    assert context["query_shape"] == "formula_query"
    assert context["complex_sql_plan"]["query_shape"] == "formula_query"
    assert "missing_formula_evidence" in context["missing_evidence"]
    assert context["missing_evidence_flags"]["missing_formula_evidence"] is True
    assert context["route_recommendation"] == "cannot_plan_safely"
    assert "Requested metric remains unresolved in dynamic context." in context["warnings"]


def test_query_planner_builds_grouped_aggregation_complex_plan_from_dynamic_context():
    knowledge_base = {
        "clients": {
            "columns": [
                {"name": "client_id", "type": "INTEGER", "semantic_type": "id"},
                {"name": "client_name", "type": "VARCHAR(100)", "semantic_type": "name", "is_dimension": True},
            ],
            "primary_keys": ["client_id"],
            "foreign_keys": [],
            "relationships": [],
        },
        "agreements": {
            "columns": [
                {"name": "agreement_id", "type": "INTEGER", "semantic_type": "id"},
                {"name": "client_id", "type": "INTEGER", "semantic_type": "id", "is_foreign_key": True},
                {"name": "deal_value", "type": "DECIMAL(12,2)", "semantic_type": "money", "is_measure": True},
            ],
            "primary_keys": ["agreement_id"],
            "foreign_keys": [{"column": "client_id", "referenced_table": "clients", "referenced_column": "client_id"}],
            "relationships": [],
        },
    }
    intent = {
        "intent_type": "grouped_summary",
        "requested_metrics": ["deal value"],
        "requested_dimensions": ["client"],
        "requested_filters": [],
        "requested_sort": {},
        "limit": None,
        "needs_grouping": True,
        "needs_aggregation": True,
        "needs_join": True,
    }
    retrieved_context = {
        "query_terms": ["deal value", "client"],
        "matched_tables": [
            {"table": "clients", "score": 0.87, "matched_terms": ["client"], "source": "kb_identifier"},
            {"table": "agreements", "score": 0.85, "matched_terms": ["deal value"], "source": "glossary"},
        ],
        "matched_columns": [
            {"table": "clients", "column": "client_name", "semantic_type": "name", "is_dimension": True, "score": 0.92, "matched_terms": ["client"], "source": "glossary"},
            {"table": "agreements", "column": "deal_value", "semantic_type": "money", "is_measure": True, "score": 0.91, "matched_terms": ["deal value"], "source": "glossary"},
        ],
        "matched_glossary_terms": [{"term": "deal value", "score": 0.89, "source": "glossary"}],
        "matched_relationships": [
            {
                "from_table": "agreements",
                "from_column": "client_id",
                "to_table": "clients",
                "to_column": "client_id",
                "join_condition": "agreements.client_id = clients.client_id",
                "source": "fk_relationship",
            }
        ],
        "possible_join_paths": [
            {
                "from_table": "clients",
                "to_table": "agreements",
                "path": [
                    {
                        "from_table": "clients",
                        "from_column": "client_id",
                        "to_table": "agreements",
                        "to_column": "client_id",
                        "join_condition": "clients.client_id = agreements.client_id",
                    }
                ],
                "length": 1,
            }
        ],
        "measure_candidates": [
            {"table": "agreements", "column": "deal_value", "semantic_type": "money", "is_measure": True, "score": 0.91, "matched_terms": ["deal value"], "source": "glossary"},
        ],
        "dimension_candidates": [
            {"table": "clients", "column": "client_name", "semantic_type": "name", "is_dimension": True, "score": 0.92, "matched_terms": ["client"], "source": "glossary"},
        ],
        "filter_candidates": [],
        "retrieval_sources": ["kb_identifier", "glossary", "relationship_context"],
        "confidence": 0.88,
    }

    context = build_query_context(
        "deal value by client",
        knowledge_base,
        use_vector_retrieval=False,
        intent=intent,
        retrieved_context=retrieved_context,
    )

    assert context["route_recommendation"] == "deterministic_sql_required"
    assert context["query_shape"] == "grouped_aggregate"
    assert context["complex_sql_plan"]["query_shape"] == "grouped_aggregate"
    assert context["complex_sql_plan"]["aggregation_type"] == "sum"
    assert context["complex_sql_plan"]["required_joins"] == ["clients.client_id = agreements.client_id"]
    assert context["complex_sql_plan"]["missing_evidence"] == {}


def test_query_planner_does_not_use_fixed_aliases_in_structured_path():
    knowledge_base = {
        "accounts": {
            "columns": [
                {"name": "account_id", "type": "INTEGER", "semantic_type": "id"},
            ],
            "primary_keys": ["account_id"],
            "foreign_keys": [],
            "relationships": [],
        },
    }
    intent = {
        "intent_type": "list",
        "requested_metrics": [],
        "requested_dimensions": ["customers"],
        "requested_filters": [],
        "requested_sort": {},
        "limit": None,
    }
    retrieved_context = {
        "query_terms": ["customers"],
        "matched_tables": [],
        "matched_columns": [],
        "matched_glossary_terms": [],
        "matched_relationships": [],
        "possible_join_paths": [],
        "measure_candidates": [],
        "dimension_candidates": [],
        "filter_candidates": [],
        "retrieval_sources": [],
        "confidence": 0.22,
    }

    context = build_query_context(
        "show all customers",
        knowledge_base,
        use_vector_retrieval=False,
        intent=intent,
        retrieved_context=retrieved_context,
    )

    assert context["selected_table_names"] == []
    assert context["confidence"] == 0.22
    assert context["warnings"] == ["Retrieved context is weak; planner confidence is low."]


def test_qp1_structured_contract_includes_required_fields():
    """Test that the planner returns the structured contract with all required fields."""
    knowledge_base = {
        "accounts": {
            "columns": [
                {"name": "account_id", "type": "INTEGER", "semantic_type": "id"},
                {"name": "account_label", "type": "VARCHAR(100)", "semantic_type": "name"},
            ],
            "primary_keys": ["account_id"],
            "foreign_keys": [],
            "relationships": [],
        },
    }

    context = build_query_context(
        "show accounts",
        knowledge_base,
        business_glossary={},
        use_vector_retrieval=False,
    )

    # Required contract fields
    assert "normalized_question" in context
    assert "intent" in context
    assert "retrieved_context" in context
    assert "plan" in context
    assert "query_shape" in context
    assert "route_reason" in context
    assert "required_evidence" in context
    assert "missing_evidence" in context
    assert "confidence" in context
    assert "route_recommendation" in context
    assert "debug_trace" in context
    assert "vector_results" in context
    assert "vector_used" in context
    assert "join_candidates" in context
    assert "required_joins" in context
    assert "group_by_candidates" in context
    assert "order_by_candidates" in context
    assert "can_plan" in context
    assert "ambiguities" in context

    # Legacy fields for backward compatibility
    assert "selected_tables" in context
    assert "selected_columns" in context
    assert "selected_table_names" in context
    assert "complex_sql_plan" in context


def test_qp1_missing_evidence_detection():
    """Test that missing evidence is correctly detected and marked."""
    knowledge_base = {
        "accounts": {
            "columns": [
                {"name": "account_id", "type": "INTEGER", "semantic_type": "id"},
                {"name": "account_label", "type": "VARCHAR(100)", "semantic_type": "name"},
            ],
            "primary_keys": ["account_id"],
            "foreign_keys": [],
            "relationships": [],
        },
        "transactions": {
            "columns": [
                {"name": "transaction_id", "type": "INTEGER", "semantic_type": "id"},
                {"name": "account_id", "type": "INTEGER", "semantic_type": "id"},
            ],
            "primary_keys": ["transaction_id"],
            "foreign_keys": [{"column": "account_id", "referenced_table": "accounts", "referenced_column": "account_id"}],
            "relationships": [],
        },
    }

    # Test missing metric detection
    intent = {
        "intent_type": "grouped_summary",
        "requested_metrics": ["total amount"],
        "requested_dimensions": [],
        "requested_filters": [],
        "requested_sort": {},
        "limit": None,
    }
    retrieved_context = {
        "query_terms": ["total amount"],
        "matched_tables": [{"table": "accounts", "score": 0.7, "matched_terms": [], "source": "kb_identifier"}],
        "matched_columns": [],
        "matched_glossary_terms": [],
        "matched_relationships": [],
        "possible_join_paths": [],
        "measure_candidates": [],
        "dimension_candidates": [],
        "filter_candidates": [],
        "retrieval_sources": [],
        "confidence": 0.5,
    }

    context = build_query_context(
        "total amount",
        knowledge_base,
        use_vector_retrieval=False,
        intent=intent,
        retrieved_context=retrieved_context,
    )

    assert "missing_metric" in context["missing_evidence"]
    assert context["missing_evidence_flags"]["missing_metric"] is True
    assert context["missing_evidence_flags"]["missing_dimension"] is False
    assert context["missing_evidence_flags"]["missing_join_path"] is False


def test_qp1_missing_join_path_detection():
    """Test that missing join path is detected when multiple tables are selected without FK paths."""
    knowledge_base = {
        "accounts": {
            "columns": [{"name": "account_id", "type": "INTEGER", "semantic_type": "id"}],
            "primary_keys": ["account_id"],
            "foreign_keys": [],
            "relationships": [],
        },
        "transactions": {
            "columns": [{"name": "transaction_id", "type": "INTEGER", "semantic_type": "id"}],
            "primary_keys": ["transaction_id"],
            "foreign_keys": [],
            "relationships": [],
        },
    }

    intent = {
        "intent_type": "list",
        "requested_metrics": [],
        "requested_dimensions": [],
        "requested_filters": [],
        "requested_sort": {},
        "limit": None,
    }
    retrieved_context = {
        "query_terms": ["accounts", "transactions"],
        "matched_tables": [
            {"table": "accounts", "score": 0.7, "matched_terms": [], "source": "kb_identifier"},
            {"table": "transactions", "score": 0.7, "matched_terms": [], "source": "kb_identifier"},
        ],
        "matched_columns": [],
        "matched_glossary_terms": [],
        "matched_relationships": [],
        "possible_join_paths": [],  # No join paths available
        "measure_candidates": [],
        "dimension_candidates": [],
        "filter_candidates": [],
        "retrieval_sources": [],
        "confidence": 0.5,
    }

    context = build_query_context(
        "accounts and transactions",
        knowledge_base,
        use_vector_retrieval=False,
        intent=intent,
        retrieved_context=retrieved_context,
    )

    assert "missing_join_path" in context["missing_evidence"]
    assert context["missing_evidence_flags"]["missing_join_path"] is True


def test_qp1_missing_formula_evidence_detection_requires_dynamic_context():
    """Formula evidence should not be inferred from raw words alone in legacy planning mode."""
    knowledge_base = {
        "billing": {
            "columns": [
                {"name": "billing_id", "type": "INTEGER", "semantic_type": "id"},
                {"name": "billed_value", "type": "DECIMAL(12,2)", "semantic_type": "money"},
            ],
            "primary_keys": ["billing_id"],
            "foreign_keys": [],
            "relationships": [],
        },
    }

    context = build_query_context(
        "show pending billed amount",
        knowledge_base,
        business_glossary={},
        use_vector_retrieval=False,
    )

    assert context["missing_evidence_flags"]["missing_formula_evidence"] is False


def test_qp1_route_recommendation_simple_rule_based():
    """Test that simple_rule_based is recommended for strong evidence with single table."""
    knowledge_base = {
        "accounts": {
            "columns": [
                {"name": "account_id", "type": "INTEGER", "semantic_type": "id"},
                {"name": "account_label", "type": "VARCHAR(100)", "semantic_type": "name"},
                {"name": "balance", "type": "DECIMAL(12,2)", "semantic_type": "money"},
            ],
            "primary_keys": ["account_id"],
            "foreign_keys": [],
            "relationships": [],
        },
    }

    context = build_query_context(
        "show accounts",  # More specific question to get higher confidence
        knowledge_base,
        business_glossary={},
        use_vector_retrieval=False,
    )

    assert context["route_recommendation"] == "simple_rule_based"
    assert context["query_shape"] == "single_table_list"
    assert context["complex_sql_plan"] == {}


def test_qp1_route_recommendation_deterministic_sql_required():
    """Test that deterministic_sql_required is recommended for multi-table queries with join evidence."""
    knowledge_base = {
        "accounts": {
            "columns": [
                {"name": "account_id", "type": "INTEGER", "semantic_type": "id"},
                {"name": "account_label", "type": "VARCHAR(100)", "semantic_type": "name"},
            ],
            "primary_keys": ["account_id"],
            "foreign_keys": [],
            "relationships": [],
        },
        "transactions": {
            "columns": [
                {"name": "transaction_id", "type": "INTEGER", "semantic_type": "id"},
                {"name": "account_id", "type": "INTEGER", "semantic_type": "id"},
            ],
            "primary_keys": ["transaction_id"],
            "foreign_keys": [{"column": "account_id", "referenced_table": "accounts", "referenced_column": "account_id"}],
            "relationships": [],
        },
    }

    context = build_query_context(
        "deal value by account",
        knowledge_base,
        business_glossary={},
        use_vector_retrieval=False,
    )

    assert context["route_recommendation"] == "deterministic_sql_required"
    assert context["query_shape"] in {"joined_lookup", "grouped_aggregate"}


def test_qp1_route_recommendation_cannot_plan_for_weak_evidence():
    """Test that cannot_plan_safely is recommended for weak evidence."""
    knowledge_base = {
        "generic_table": {
            "columns": [
                {"name": "id", "type": "INTEGER", "semantic_type": "id"},
                {"name": "value", "type": "VARCHAR(100)", "semantic_type": "text_candidate"},
            ],
            "primary_keys": ["id"],
            "foreign_keys": [],
            "relationships": [],
        },
    }

    context = build_query_context(
        "show xyz",  # Very unrelated question to get low confidence
        knowledge_base,
        business_glossary={},
        use_vector_retrieval=False,
    )

    assert context["route_recommendation"] == "cannot_plan_safely"


def test_qp1_route_recommendation_cannot_plan_safely():
    """Test that cannot_plan_safely is recommended when critical evidence is missing."""
    knowledge_base = {
        "table_a": {
            "columns": [{"name": "id", "type": "INTEGER", "semantic_type": "id"}],
            "primary_keys": ["id"],
            "foreign_keys": [],
            "relationships": [],
        },
        "table_b": {
            "columns": [{"name": "id", "type": "INTEGER", "semantic_type": "id"}],
            "primary_keys": ["id"],
            "foreign_keys": [],
            "relationships": [],
        },
    }

    intent = {
        "intent_type": "list",
        "requested_metrics": [],
        "requested_dimensions": [],
        "requested_filters": [],
        "requested_sort": {},
        "limit": None,
    }
    retrieved_context = {
        "query_terms": ["table_a", "table_b"],
        "matched_tables": [
            {"table": "table_a", "score": 0.3, "matched_terms": [], "source": "kb_identifier"},
            {"table": "table_b", "score": 0.3, "matched_terms": [], "source": "kb_identifier"},
        ],
        "matched_columns": [],
        "matched_glossary_terms": [],
        "matched_relationships": [],
        "possible_join_paths": [],  # No join paths
        "measure_candidates": [],
        "dimension_candidates": [],
        "filter_candidates": [],
        "retrieval_sources": [],
        "confidence": 0.3,
    }

    context = build_query_context(
        "table_a and table_b",
        knowledge_base,
        use_vector_retrieval=False,
        intent=intent,
        retrieved_context=retrieved_context,
    )

    assert context["route_recommendation"] == "cannot_plan_safely"
    assert "missing_join_path" in context["missing_evidence"]
    assert context["missing_evidence_flags"]["missing_join_path"] is True


def test_qp1_debug_trace_structure():
    """Test that debug trace contains all required fields."""
    knowledge_base = {
        "accounts": {
            "columns": [
                {"name": "account_id", "type": "INTEGER", "semantic_type": "id"},
                {"name": "account_label", "type": "VARCHAR(100)", "semantic_type": "name"},
            ],
            "primary_keys": ["account_id"],
            "foreign_keys": [],
            "relationships": [],
        },
    }

    context = build_query_context(
        "show accounts",
        knowledge_base,
        business_glossary={},
        use_vector_retrieval=False,
    )

    debug_trace = context["debug_trace"]
    assert isinstance(debug_trace, list)
    stages = {entry["stage"] for entry in debug_trace}
    assert {"question", "intent", "query_shape", "selected_tables", "join_count", "route_recommendation", "route_reason"} <= stages
    assert isinstance(context["debug_trace_details"], dict)
    assert "question" in context["debug_trace_details"]
    assert "selected_table_names" in context["debug_trace_details"]


def test_qp1_planner_does_not_guess_when_context_is_weak():
    """Test that planner does not guess when context is weak by checking route recommendation."""
    knowledge_base = {
        "generic_table": {
            "columns": [
                {"name": "id", "type": "INTEGER", "semantic_type": "id"},
                {"name": "value", "type": "VARCHAR(100)", "semantic_type": "text_candidate"},
            ],
            "primary_keys": ["id"],
            "foreign_keys": [],
            "relationships": [],
        },
    }

    context = build_query_context(
        "show xyz",  # Very unrelated question to get low confidence
        knowledge_base,
        business_glossary={},
        use_vector_retrieval=False,
    )

    assert context["route_recommendation"] == "cannot_plan_safely"


def test_qp2_intent_builder_show_all_accounts():
    """Test intent builder for 'show all accounts' question."""
    intent = build_intent("show all accounts")
    
    assert intent["intent_type"] == "list"
    assert intent["user_goal"].startswith("show")
    assert "accounts" in intent["raw_business_terms"]
    assert intent["needs_aggregation"] is False
    assert intent["needs_grouping"] is False
    # Should not classify as metric/dimension without "by" pattern
    assert len(intent["requested_metrics"]) == 0
    assert len(intent["requested_dimensions"]) == 0


def test_qp2_intent_builder_count_accounts():
    """Test intent builder for 'count accounts' question."""
    intent = build_intent("count accounts")
    
    assert intent["intent_type"] == "count"
    assert intent["user_goal"] == "count accounts"
    assert "accounts" in intent["raw_business_terms"]
    assert intent["needs_aggregation"] is True
    assert intent["needs_grouping"] is False
    # Should not classify as metric/dimension without "by" pattern
    assert len(intent["requested_metrics"]) == 0
    assert len(intent["requested_dimensions"]) == 0


def test_qp2_intent_builder_top_5_accounts_by_deal_value():
    """Test intent builder for 'top 5 accounts by deal value' question."""
    intent = build_intent("top 5 accounts by deal value")
    
    assert intent["intent_type"] == "ranking"
    assert "accounts" in intent["raw_business_terms"]
    assert "deal" in intent["raw_business_terms"] or "value" in intent["raw_business_terms"]
    assert intent["limit"] == 5
    assert intent["needs_aggregation"] is True
    assert intent["needs_grouping"] is True
    assert intent["requested_sort"] == {"direction": "desc", "terms": "deal value"}
    # Should use phrase positions: "accounts" as dimension, "deal value" as metric
    assert "accounts" in intent["requested_dimensions"]
    assert "deal value" in intent["requested_metrics"]


def test_qp2_intent_builder_deal_value_by_account():
    """Test intent builder for 'deal value by account' question."""
    intent = build_intent("deal value by account")
    
    assert intent["intent_type"] == "grouped_summary"
    assert "deal" in intent["raw_business_terms"] or "value" in intent["raw_business_terms"]
    assert "account" in intent["raw_business_terms"]
    assert intent["needs_aggregation"] is True
    assert intent["needs_grouping"] is True
    # Should use phrase positions: "deal value" as metric, "account" as dimension
    assert "deal value" in intent["requested_metrics"]
    assert "account" in intent["requested_dimensions"]


def test_qp2_intent_builder_pending_billed_amount_by_account():
    """Test intent builder for 'pending billed amount by account' question."""
    intent = build_intent("pending billed amount by account")
    
    assert intent["intent_type"] == "grouped_summary"
    assert "pending" in intent["raw_business_terms"]
    assert "billed" in intent["raw_business_terms"] or "amount" in intent["raw_business_terms"]
    assert "account" in intent["raw_business_terms"]
    assert intent["needs_aggregation"] is True
    assert intent["needs_grouping"] is True
    # Should preserve formula-like term as raw business terms
    assert "pending" in intent["raw_business_terms"]
    # Should use phrase positions: "pending billed amount" as metric, "account" as dimension
    assert "pending billed amount" in intent["requested_metrics"]
    assert "account" in intent["requested_dimensions"]


def test_qp2_intent_builder_show_current_stock_by_storage_point():
    """Test intent builder for 'show current stock by storage point' question."""
    intent = build_intent("show current stock by storage point")
    
    assert intent["intent_type"] == "grouped_summary"
    assert "stock" in intent["raw_business_terms"]
    assert "storage" in intent["raw_business_terms"] or "point" in intent["raw_business_terms"]
    assert intent["needs_aggregation"] is True
    assert intent["needs_grouping"] is True
    # Should use phrase positions: "current stock" as metric, "storage point" as dimension
    assert "current stock" in intent["requested_metrics"]
    assert "storage point" in intent["requested_dimensions"]


def test_qp2_intent_builder_ambiguous_weak_question():
    """Test intent builder for ambiguous/weak questions."""
    intent = build_intent("show something")
    
    assert intent["intent_type"] == "list"
    assert intent["confidence"] < 0.5  # Lower confidence for vague questions
    assert len(intent["raw_business_terms"]) == 0 or len(intent["raw_business_terms"]) < 2


def test_qp2_intent_builder_schema_agnostic():
    """Test that intent builder does not map terms to real tables/columns."""
    intent = build_intent("show customer revenue")
    
    # Should preserve raw terms without mapping
    assert "customer" in intent["raw_business_terms"]
    assert "revenue" in intent["raw_business_terms"]
    
    # Should not contain hardcoded table/column mappings
    assert "accounts" not in intent["requested_dimensions"]
    assert "sales" not in intent["requested_metrics"]
    
    # Should be schema-agnostic
    assert intent["intent_type"] in {"list", "count", "aggregate", "ranking", "grouped_summary", "comparison", "filter", "sorted_list"}
    
    # Without "by" pattern, should not classify as metric/dimension
    assert len(intent["requested_metrics"]) == 0
    assert len(intent["requested_dimensions"]) == 0


def test_qp2_intent_builder_preserves_unresolved_terms():
    """Test that intent builder preserves unresolved business terms for context retrieval."""
    intent = build_intent("pending billed amount by account")
    
    # Should preserve the formula-like term as raw business terms
    assert "pending" in intent["raw_business_terms"]
    assert "billed" in intent["raw_business_terms"]
    assert "amount" in intent["raw_business_terms"]
    assert "account" in intent["raw_business_terms"]
    
    # These terms should be available for context retrieval
    assert len(intent["raw_business_terms"]) >= 3


def test_qp2_intent_builder_confidence_lower_for_vague_questions():
    """Test that intent confidence is lower for vague/incomplete questions."""
    clear_intent = build_intent("count accounts by region")
    vague_intent = build_intent("show something")
    short_intent = build_intent("show")
    
    assert clear_intent["confidence"] >= 0.6
    assert vague_intent["confidence"] < 0.5
    assert short_intent["confidence"] < 0.5


def test_qp2_intent_builder_all_required_fields_present():
    """Test that intent builder returns all required fields."""
    intent = build_intent("show accounts")
    
    required_fields = [
        "user_goal",
        "intent_type",
        "requested_metrics",
        "requested_dimensions",
        "requested_filters",
        "requested_sort",
        "aggregate_function",
        "source_scope",
        "limit",
        "needs_grouping",
        "needs_aggregation",
        "needs_join",
        "raw_business_terms",
        "confidence",
    ]
    
    for field in required_fields:
        assert field in intent, f"Missing required field: {field}"
