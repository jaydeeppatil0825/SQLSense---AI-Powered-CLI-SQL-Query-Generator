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
