"""Tests for dynamic business glossary generation and search."""

import tempfile
from pathlib import Path

from semantic.business_glossary import (
    generate_business_glossary,
    load_business_glossary,
    save_business_glossary,
    search_business_glossary,
)


def test_generate_business_glossary_from_schema_facts_only():
    knowledge_base = {
        "invoice_headers": {
            "business_description": "Invoice header records",
            "columns": [
                {"name": "invoice_id", "type": "INTEGER", "semantic_type": "id"},
                {"name": "total_due", "type": "DECIMAL(10,2)", "semantic_type": "money"},
                {"name": "invoice_date", "type": "DATE", "semantic_type": "date"},
            ],
        },
        "client_directory": {
            "foreign_keys": [],
            "relationships": [],
            "columns": [
                {"name": "client_name", "type": "VARCHAR(100)", "semantic_type": "name"},
                {"name": "client_code", "type": "VARCHAR(20)", "semantic_type": "code"},
            ],
        },
    }

    glossary = generate_business_glossary(knowledge_base, use_ai_enrichment=False)

    assert "invoice headers" in glossary
    assert "invoice header" in glossary
    assert "total due" in glossary
    assert glossary["total due"]["mapped_columns"][0]["table"] == "invoice_headers"
    assert glossary["total due"]["mapped_columns"][0]["column"] == "total_due"
    assert glossary["total due"]["sources"] == ["schema_identifier", "semantic_metadata"]
    assert glossary["total due"]["target_type"] == "column"
    assert glossary["total due"]["usage_scope"] == "column_lookup"
    assert isinstance(glossary["total due"]["confidence"], float)
    assert glossary["total due"]["primary_terms"] == ["total due"]
    assert glossary["total due"]["business_terms"] == ["total due"]
    assert glossary["total due"]["related_terms"] == []
    assert "tax" not in glossary
    assert "gst" not in glossary
    assert "vat" not in glossary
    assert "outstanding" not in glossary


def test_generate_business_glossary_from_ai_metadata_only_when_present():
    knowledge_base = {
        "invoice_headers": {
            "relationships": [],
            "columns": [
                {
                    "name": "total_due",
                    "type": "DECIMAL(10,2)",
                    "semantic_type": "money",
                    "business_terms": ["payables open", "amount due current"],
                    "business_description": "Open amount due",
                }
            ],
        }
    }

    glossary = generate_business_glossary(knowledge_base, use_ai_enrichment=True)

    assert "payables open" in glossary
    assert "amount due current" in glossary
    assert glossary["payables open"]["mapped_columns"][0]["table"] == "invoice_headers"
    assert "ai_semantic_metadata" in glossary["payables open"]["sources"]
    assert glossary["payables open"]["business_terms"] == glossary["payables open"]["primary_terms"]


def test_generate_business_glossary_preserves_profile_and_sample_evidence_without_promoting_values():
    knowledge_base = {
        "event_records": {
            "relationships": [],
            "columns": [
                {
                    "name": "event_state",
                    "type": "VARCHAR(20)",
                    "semantic_type": "category_candidate",
                    "profile_facts": {
                        "null_count": 1,
                        "non_null_count": 4,
                        "unique_count": 2,
                        "sample_values": ["Open", "Closed"],
                        "min": None,
                        "max": None,
                    },
                    "planner_roles": {
                        "dimension_candidate": True,
                        "filter_candidate": True,
                    },
                }
            ],
        }
    }

    glossary = generate_business_glossary(knowledge_base, use_ai_enrichment=False)
    entry = glossary["event state"]
    mapping = entry["mapped_columns"][0]

    assert mapping["sample_values"] == ["Open", "Closed"]
    assert mapping["profile_facts"]["unique_count"] == 2
    assert mapping["planner_roles"]["dimension_candidate"] is True
    assert "profiling" in entry["sources"]
    assert "sample_values" in entry["sources"]
    assert "Open" not in entry["primary_terms"]
    assert "Closed" not in entry["related_terms"]


def test_generate_business_glossary_uses_relationship_context_without_inventing_aliases():
    knowledge_base = {
        "stock_positions": {
            "foreign_keys": [
                {"column": "item_id", "referenced_table": "items", "referenced_column": "item_id"},
                {"column": "storage_id", "referenced_table": "storage_points", "referenced_column": "storage_id"},
            ],
            "relationships": [],
            "columns": [
                {"name": "item_id", "type": "INTEGER", "semantic_type": "id", "is_foreign_key": True},
                {"name": "storage_id", "type": "INTEGER", "semantic_type": "id", "is_foreign_key": True},
            ],
        }
    }

    glossary = generate_business_glossary(knowledge_base, use_ai_enrichment=False)

    assert "stock positions" in glossary
    assert "relationship_context" in glossary["stock positions"]["sources"]
    assert "items" in glossary["item id"]["related_terms"]
    assert "storage point" in glossary["storage id"]["related_terms"]
    assert "items" not in glossary["item id"]["business_terms"]
    assert "storage point" not in glossary["storage id"]["business_terms"]
    assert "pending stock positions" not in glossary


def test_generate_business_glossary_separates_primary_and_related_terms():
    knowledge_base = {
        "bills": {
            "business_description": "Bill details",
            "foreign_keys": [
                {"column": "partner_id", "referenced_table": "partners", "referenced_column": "partner_id"},
            ],
            "relationships": [],
            "columns": [
                {"name": "bill_id", "type": "INTEGER", "semantic_type": "id"},
                {"name": "partner_id", "type": "INTEGER", "semantic_type": "id", "is_foreign_key": True},
            ],
        }
    }

    glossary = generate_business_glossary(knowledge_base, use_ai_enrichment=False)

    assert glossary["bills"]["primary_terms"] == ["bills", "bill"]
    assert glossary["bills"]["business_terms"] == ["bills", "bill"]
    assert glossary["bills"]["related_terms"] == ["partners", "partner"]
    assert glossary["bills"]["mapped_tables"] == ["bills"]
    assert glossary["bills"]["related_tables"] == ["partners"]
    assert "foreign_key" in glossary["bills"]["relationship_sources"]
    assert glossary["bills"]["target_type"] == "table"
    assert glossary["bills"]["usage_scope"] == "table_lookup"
    assert isinstance(glossary["bills"]["confidence"], float)
    assert glossary["bill id"]["primary_terms"] == ["bill id"]
    assert glossary["bill id"]["related_terms"] == []


def test_save_and_load_business_glossary():
    glossary = {
        "invoice headers": {
            "description": "Schema table: invoice headers.",
            "target_type": "table",
            "mapped_tables": ["invoice_headers"],
            "mapped_columns": [{"table": "invoice_headers", "column": "total_due", "confidence": "high"}],
            "example_questions": [],
            "primary_terms": ["invoice headers"],
            "related_terms": [],
            "related_tables": [],
            "business_terms": ["invoice headers"],
            "usage_scope": "table_lookup",
            "confidence": 0.99,
            "sources": ["schema_identifier"],
            "relationship_sources": [],
        }
    }

    with tempfile.TemporaryDirectory() as tmpdir:
        glossary_path = Path(tmpdir) / "business_glossary.json"
        save_business_glossary(glossary, str(glossary_path))
        loaded_glossary = load_business_glossary(str(glossary_path))
        assert loaded_glossary["invoice headers"]["primary_terms"] == ["invoice headers"]
        assert loaded_glossary["invoice headers"]["business_terms"] == ["invoice headers"]
        assert loaded_glossary["invoice headers"]["related_terms"] == []
        assert loaded_glossary["invoice headers"]["mapped_tables"] == ["invoice_headers"]
        assert loaded_glossary["invoice headers"]["target_type"] == "table"
        assert loaded_glossary["invoice headers"]["usage_scope"] == "table_lookup"
        assert isinstance(loaded_glossary["invoice headers"]["confidence"], float)


def test_search_business_glossary():
    glossary = {
        "payables open": {
            "description": "Open payable amount",
            "mapped_columns": [{"table": "invoice_headers", "column": "total_due", "confidence": "high"}],
            "example_questions": [],
            "business_terms": ["amount due current"],
            "sources": ["schema_identifier", "ai_semantic_metadata"],
        },
        "client name": {
            "description": "Client label",
            "mapped_columns": [{"table": "client_directory", "column": "client_name", "confidence": "high"}],
            "example_questions": [],
            "business_terms": ["client name"],
            "sources": ["schema_identifier"],
        },
    }

    assert "payables open" in search_business_glossary("payables open", glossary)
    assert "payables open" in search_business_glossary("amount due current", glossary)
    assert "payables open" in search_business_glossary("total_due", glossary)
    assert "payables open" in search_business_glossary("invoice_headers", glossary)
    assert search_business_glossary("nonexistent", glossary) == {}


def test_load_business_glossary_missing_file_uses_empty_fallback():
    glossary = load_business_glossary("nonexistent_path.json")
    assert glossary == {}


def test_load_business_glossary_invalid_json_falls_back(tmp_path):
    invalid_path = tmp_path / "business_glossary.json"
    invalid_path.write_text("{not valid json", encoding="utf-8")

    glossary = load_business_glossary(str(invalid_path))

    assert glossary == {}


def test_load_business_glossary_unreadable_falls_back(monkeypatch):
    def fake_load_json(path):
        raise OSError("Permission denied")

    monkeypatch.setattr("utils.file_utils.load_json", fake_load_json)
    glossary = load_business_glossary("semantic/business_glossary.json")

    assert glossary == {}


def test_generate_glossary_with_empty_knowledge_base():
    assert generate_business_glossary({}, use_ai_enrichment=False) == {}
