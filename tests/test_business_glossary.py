"""
tests/test_business_glossary.py
================================
Tests for business glossary generation and search.
"""

import tempfile
from pathlib import Path

import pytest

from semantic.business_glossary import (
    generate_business_glossary,
    save_business_glossary,
    load_business_glossary,
    search_business_glossary,
)


def test_generate_business_glossary_from_rule_based_kb():
    """Test that glossary is generated from rule-based knowledge base."""
    knowledge_base = {
        "orders": {
            "columns": [
                {"name": "final_amount", "type": "DECIMAL(10,2)", "semantic_type": "value"},
                {"name": "order_date", "type": "DATE", "semantic_type": "date"},
            ]
        },
        "customers": {
            "columns": [
                {"name": "customer_name", "type": "VARCHAR(100)", "semantic_type": "name"},
                {"name": "customer_id", "type": "INTEGER", "semantic_type": "id"},
                {"name": "city", "type": "VARCHAR(50)", "semantic_type": "location"},
            ]
        },
    }
    
    glossary = generate_business_glossary(knowledge_base, use_ai_enrichment=False)
    
    assert isinstance(glossary, dict)
    assert len(glossary) > 0
    
    # Should map "sales" to amount columns
    if "sales" in glossary:
        assert "mapped_columns" in glossary["sales"]
        assert len(glossary["sales"]["mapped_columns"]) > 0
        assert glossary["sales"]["mapped_columns"][0] == {
            "table": "orders",
            "column": "final_amount",
            "type": "DECIMAL(10,2)",
            "confidence": "high",
        }

    assert "top customers" in glossary
    assert {
        "table": "customers",
        "column": "customer_name",
        "type": "VARCHAR(100)",
        "confidence": "high",
    } in glossary["top customers"]["mapped_columns"]


def test_generate_business_glossary_from_ai_enriched_kb():
    """Test that glossary uses AI enrichment when available."""
    knowledge_base = {
        "orders": {
            "columns": [
                {
                    "name": "final_amount",
                    "type": "DECIMAL(10,2)",
                    "semantic_type": "value",
                    "business_terms": ["sales", "revenue", "order value"],
                    "metric_type": "currency",
                    "is_measure": True,
                    "is_dimension": False,
                    "is_date": False,
                }
            ]
        }
    }
    
    glossary = generate_business_glossary(knowledge_base, use_ai_enrichment=True)
    
    assert isinstance(glossary, dict)
    # Should have terms from business_terms
    assert "sales" in glossary or "revenue" in glossary


def test_save_and_load_business_glossary():
    """Test that glossary can be saved and loaded."""
    glossary = {
        "sales": {
            "description": "Total revenue",
            "mapped_columns": [
                {"table": "orders", "column": "final_amount", "confidence": "high"}
            ],
            "example_questions": ["Show total sales"]
        }
    }
    
    with tempfile.TemporaryDirectory() as tmpdir:
        glossary_path = Path(tmpdir) / "business_glossary.json"
        
        save_business_glossary(glossary, str(glossary_path))
        
        loaded_glossary = load_business_glossary(str(glossary_path))
        
        assert loaded_glossary == glossary


def test_search_business_glossary():
    """Test that glossary search finds matching terms."""
    glossary = {
        "sales": {
            "description": "Total revenue",
            "mapped_columns": [
                {"table": "orders", "column": "final_amount", "confidence": "high"}
            ],
            "example_questions": ["Show total sales"]
        },
        "customer": {
            "description": "Person placing orders",
            "mapped_columns": [
                {"table": "customers", "column": "customer_name", "confidence": "high"}
            ],
            "example_questions": ["Show all customers"]
        }
    }
    
    # Search for "sales"
    results = search_business_glossary("sales", glossary)
    assert "sales" in results
    assert len(results) == 1
    
    # Search for "revenue" (should match sales description)
    results = search_business_glossary("revenue", glossary)
    assert "sales" in results
    
    # Search for non-existent term
    results = search_business_glossary("nonexistent", glossary)
    assert len(results) == 0


def test_search_business_glossary_in_mapped_columns():
    """Test that search finds matches in mapped column names."""
    glossary = {
        "sales": {
            "description": "Total revenue",
            "mapped_columns": [
                {"table": "orders", "column": "final_amount", "confidence": "high"}
            ],
            "example_questions": ["Show total sales"]
        }
    }
    
    # Search for "final_amount" (column name)
    results = search_business_glossary("final_amount", glossary)
    assert "sales" in results
    
    # Search for "orders" (table name)
    results = search_business_glossary("orders", glossary)
    assert "sales" in results


def test_load_business_glossary_missing_file():
    """Test that missing glossary file returns empty dict."""
    glossary = load_business_glossary("nonexistent_path.json")
    assert glossary == {}


def test_generate_glossary_with_empty_knowledge_base():
    """Test that empty knowledge base produces empty glossary."""
    glossary = generate_business_glossary({}, use_ai_enrichment=False)
    assert glossary == {}
