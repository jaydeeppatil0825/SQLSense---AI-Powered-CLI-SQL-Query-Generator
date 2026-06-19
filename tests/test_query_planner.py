"""Tests for dynamic query planner behavior."""

from core.query_planner import build_query_context
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

    assert set(context["selected_table_names"]) == {"alpha_records", "beta_events"}
    assert context["confidence"] == 0.55
    assert context["plan"]["metric"] is None


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

    assert set(context["selected_table_names"]) == {"alpha_records", "beta_records"}
    assert context["confidence"] == 0.55
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
    assert "Requested metric remains unresolved in dynamic context." in context["warnings"]


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
