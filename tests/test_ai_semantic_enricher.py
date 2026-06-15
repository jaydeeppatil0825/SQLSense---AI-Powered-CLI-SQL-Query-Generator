"""
tests/test_ai_semantic_enricher.py
==================================
Tests for AI semantic enrichment of the knowledge base.
"""

import json
import logging

import pytest

from semantic.ai_semantic_enricher import (
    enrich_knowledge_base_with_ai,
    _clean_ai_response,
    get_last_enrichment_report,
)


def test_clean_ai_response_removes_markdown():
    """Test that markdown code fences are removed from AI response."""
    response = """```json
{
  "tables": {},
  "columns": {}
}
```"""
    cleaned = _clean_ai_response(response)
    assert "```json" not in cleaned
    assert "```" not in cleaned
    assert '"tables"' in cleaned


def test_clean_ai_response_removes_extra_text():
    """Test that extra text before/after JSON is removed."""
    response = """Here is the JSON:
{
  "tables": {},
  "columns": {}
}
That's it!"""
    cleaned = _clean_ai_response(response)
    assert "Here is the JSON:" not in cleaned
    assert "That's it!" not in cleaned
    assert '"tables"' in cleaned


def test_enrich_knowledge_base_with_ai_fallback_on_invalid_json(monkeypatch):
    """Test that invalid JSON response falls back to original KB."""
    knowledge_base = {
        "orders": {
            "columns": [
                {"name": "final_amount", "type": "DECIMAL(10,2)", "semantic_type": "value"}
            ]
        }
    }
    
    monkeypatch.setattr(
        "semantic.ai_semantic_enricher._call_ai_backend",
        lambda messages, backend, response_format=None: "{not valid json",
    )

    enriched = enrich_knowledge_base_with_ai(knowledge_base, backend="local")
    
    # Should return original KB when AI fails
    assert enriched == knowledge_base


def test_enrich_knowledge_base_with_ai_fallback_on_missing_structure(monkeypatch):
    """Test that response missing required structure falls back to original KB."""
    knowledge_base = {
        "orders": {
            "columns": [
                {"name": "final_amount", "type": "DECIMAL(10,2)", "semantic_type": "value"}
            ]
        }
    }
    
    monkeypatch.setattr(
        "semantic.ai_semantic_enricher._call_ai_backend",
        lambda messages, backend, response_format=None: json.dumps({"wrong": "shape"}),
    )

    enriched = enrich_knowledge_base_with_ai(knowledge_base, backend="local")
    
    # Should return original KB when structure is invalid
    assert enriched == knowledge_base


def test_enrich_knowledge_base_sends_chat_messages(monkeypatch):
    """AI enrichment must call the backend with compact system+user messages."""
    knowledge_base = {
        "orders": {
            "columns": [
                {"name": "final_amount", "type": "DECIMAL(10,2)", "semantic_type": "value"}
            ]
        }
    }
    captured = {}

    def fake_call_ai_backend(messages, backend, response_format=None):
        captured["messages"] = messages
        captured["backend"] = backend
        captured["response_format"] = response_format
        if "required" in response_format and "q" in response_format["required"]:
            return json.dumps({
                "d": "Customer orders",
                "p": "Tracks sales",
                "q": ["Show total sales"],
            })
        return json.dumps({
            "c": {
                "final_amount": {
                    "d": "Final amount",
                    "b": ["sales", "revenue"],
                    "m": "currency",
                    "me": True,
                    "di": False,
                    "dt": False,
                },
            },
        })

    monkeypatch.setattr("semantic.ai_semantic_enricher._call_ai_backend", fake_call_ai_backend)

    enriched = enrich_knowledge_base_with_ai(knowledge_base, backend="local")

    assert captured["backend"] == "local"
    assert isinstance(captured["messages"], list)
    assert captured["messages"][0]["role"] == "system"
    assert captured["messages"][1]["role"] == "user"
    assert "Table: orders" in captured["messages"][1]["content"]
    assert captured["response_format"]["type"] == "object"
    assert enriched["orders"]["business_description"] == "Customer orders"
    assert enriched["orders"]["columns"][0]["business_terms"] == ["sales", "revenue"]
    assert enriched["orders"]["business_purpose"] == "Tracks sales"


def test_enrich_timeout_fallback_log_is_sanitized(monkeypatch, caplog):
    """Timeout logs should be useful without dumping raw connection pool text."""
    knowledge_base = {
        "orders": {
            "columns": [
                {"name": "final_amount", "type": "DECIMAL(10,2)", "semantic_type": "value"}
            ]
        }
    }

    def fake_call_ai_backend(messages, backend, response_format=None):
        raise TimeoutError("Read timed out.")

    monkeypatch.setattr("semantic.ai_semantic_enricher._call_ai_backend", fake_call_ai_backend)
    caplog.set_level(logging.INFO, logger="aisqlqurrey")

    enriched = enrich_knowledge_base_with_ai(knowledge_base, backend="local")

    assert enriched == knowledge_base
    assert "Local AI timed out" in caplog.text
    assert "HTTPSConnectionPool" not in caplog.text


def test_enrich_knowledge_base_allows_partial_table_fallback(monkeypatch):
    knowledge_base = {
        "orders": {
            "columns": [
                {"name": "final_amount", "type": "DECIMAL(10,2)", "semantic_type": "value"}
            ]
        },
        "customers": {
            "columns": [
                {"name": "customer_name", "type": "VARCHAR(100)", "semantic_type": "customer"}
            ]
        },
    }
    calls = []

    def fake_call_ai_backend(messages, backend, response_format=None):
        table_line = next(line for line in messages[1]["content"].splitlines() if line.startswith("Table: "))
        table_name = table_line.split(": ", 1)[1]
        calls.append(table_name)
        if table_name == "customers":
            raise TimeoutError("Read timed out.")
        if "required" in response_format and "q" in response_format["required"]:
            return json.dumps({
                "d": "Order facts",
                "p": "Tracks order value",
                "q": ["Show total sales"],
            })
        return json.dumps({
            "c": {
                "final_amount": {
                    "d": "Final order amount",
                    "b": ["sales"],
                    "m": "currency",
                    "me": True,
                    "di": False,
                    "dt": False,
                },
            },
        })

    monkeypatch.setattr("semantic.ai_semantic_enricher._call_ai_backend", fake_call_ai_backend)

    enriched = enrich_knowledge_base_with_ai(knowledge_base, backend="local")
    enriched_tables, fallback_tables = get_last_enrichment_report()

    assert calls == ["orders", "orders", "customers"]
    assert enriched["orders"]["business_description"] == "Order facts"
    assert "business_description" not in enriched["customers"]
    assert enriched_tables == ["orders"]
    assert fallback_tables == {"customers": "Local AI timed out"}


def test_invalid_ai_business_purpose_uses_rule_based_fallback(monkeypatch):
    knowledge_base = {
        "vendor_payments": {
            "module": "finance",
            "columns": [
                {"name": "payment_amount", "type": "DECIMAL(10,2)", "semantic_type": "money"}
            ],
        }
    }

    def fake_call_ai_backend(messages, backend, response_format=None):
        if "required" in response_format and "q" in response_format["required"]:
            return json.dumps({
                "d": "Vendor payments",
                "p": ".99",
                "q": ["What is unpaid?"],
            })
        return json.dumps({
            "c": {
                "payment_amount": {
                    "d": "Paid amount",
                    "b": ["payment"],
                    "m": "money",
                    "me": True,
                    "di": False,
                    "dt": False,
                }
            }
        })

    monkeypatch.setattr("semantic.ai_semantic_enricher._call_ai_backend", fake_call_ai_backend)

    enriched = enrich_knowledge_base_with_ai(knowledge_base, backend="local")

    assert enriched["vendor_payments"]["business_purpose"] == "Stores vendor payment records for finance workflows."
    assert enriched["vendor_payments"]["possible_business_questions"] == ["What is unpaid?"]


def test_clean_ai_response_handles_empty_response():
    """Test that empty response is handled gracefully."""
    cleaned = _clean_ai_response("")
    assert cleaned == ""


def test_clean_ai_response_handles_response_without_braces():
    """Test that response without JSON braces is handled."""
    response = "This is not JSON at all"
    cleaned = _clean_ai_response(response)
    assert cleaned == response  # Returns as-is if no braces found
