"""
Tests for dynamic business glossary generation and search.
"""

import tempfile
from pathlib import Path

from semantic.business_glossary import (
    generate_business_glossary,
    save_business_glossary,
    load_business_glossary,
    search_business_glossary,
)


def test_generate_business_glossary_from_kb():
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
    assert "money" in glossary
    assert any(mapping["column"] == "total_due" for mapping in glossary["money"]["mapped_columns"])


def test_generate_business_glossary_from_ai_enriched_kb():
    knowledge_base = {
        "invoice_headers": {
            "columns": [
                {
                    "name": "total_due",
                    "type": "DECIMAL(10,2)",
                    "semantic_type": "money",
                    "business_terms": ["payables", "amount due"],
                    "business_description": "Open amount due",
                }
            ]
        }
    }

    glossary = generate_business_glossary(knowledge_base, use_ai_enrichment=True)

    assert "payables" in glossary
    assert "amount due" in glossary
    assert glossary["payables"]["mapped_columns"][0]["table"] == "invoice_headers"


def test_save_and_load_business_glossary():
    glossary = {
        "money": {
            "description": "Monetary values",
            "mapped_columns": [{"table": "invoice_headers", "column": "total_due", "confidence": "high"}],
            "example_questions": ["Show total amount"],
            "business_terms": ["amount"],
        }
    }

    with tempfile.TemporaryDirectory() as tmpdir:
        glossary_path = Path(tmpdir) / "business_glossary.json"
        save_business_glossary(glossary, str(glossary_path))
        loaded_glossary = load_business_glossary(str(glossary_path))
        assert loaded_glossary == glossary


def test_search_business_glossary():
    glossary = {
        "payables": {
            "description": "Open payable amount",
            "mapped_columns": [{"table": "invoice_headers", "column": "total_due", "confidence": "high"}],
            "example_questions": ["Show current payables"],
            "business_terms": ["amount due"],
        },
        "client name": {
            "description": "Client label",
            "mapped_columns": [{"table": "client_directory", "column": "client_name", "confidence": "high"}],
            "example_questions": ["Show client names"],
            "business_terms": ["name"],
        },
    }

    assert "payables" in search_business_glossary("payables", glossary)
    assert "payables" in search_business_glossary("amount due", glossary)
    assert "payables" in search_business_glossary("total_due", glossary)
    assert "payables" in search_business_glossary("invoice_headers", glossary)
    assert search_business_glossary("nonexistent", glossary) == {}


def test_load_business_glossary_missing_file_uses_generic_fallback():
    glossary = load_business_glossary("nonexistent_path.json")
    assert "money" in glossary
    assert "quantity" in glossary


def test_load_business_glossary_invalid_json_falls_back(tmp_path):
    invalid_path = tmp_path / "business_glossary.json"
    invalid_path.write_text("{not valid json", encoding="utf-8")

    glossary = load_business_glossary(str(invalid_path))

    assert "date" in glossary
    assert "status" in glossary


def test_load_business_glossary_unreadable_falls_back(monkeypatch):
    def fake_load_json(path):
        raise OSError("Permission denied")

    monkeypatch.setattr("utils.file_utils.load_json", fake_load_json)
    glossary = load_business_glossary("semantic/business_glossary.json")

    assert "name" in glossary
    assert "code" in glossary


def test_generate_glossary_with_empty_knowledge_base():
    assert generate_business_glossary({}, use_ai_enrichment=False) == {}
