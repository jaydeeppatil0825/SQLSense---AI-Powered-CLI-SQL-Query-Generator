"""
tests/test_ai_semantic_enricher.py
==================================
Tests for AI semantic enrichment of the knowledge base.
"""

import json
import logging

from semantic.ai_semantic_enricher import (
    enrich_knowledge_base_with_ai,
    _clean_ai_response,
    get_last_enrichment_report,
)


def test_clean_ai_response_removes_markdown():
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
    knowledge_base = {
        "orders": {
            "columns": [
                {"name": "final_amount", "type": "DECIMAL(10,2)", "semantic_type": "numeric_candidate"}
            ]
        }
    }

    monkeypatch.setattr(
        "semantic.ai_semantic_enricher._call_ai_backend",
        lambda messages, backend, response_format=None: "{not valid json",
    )

    enriched = enrich_knowledge_base_with_ai(knowledge_base, backend="local")
    assert enriched == knowledge_base


def test_candidate_prompt_contains_schema_and_profile_evidence(monkeypatch):
    knowledge_base = {
        "stock_positions": {
            "primary_keys": ["stock_id"],
            "foreign_keys": [
                {"column": "item_id", "referenced_table": "items", "referenced_column": "item_id"},
                {"column": "storage_id", "referenced_table": "storage_points", "referenced_column": "storage_id"},
            ],
            "relationships": [],
            "columns": [
                {"name": "stock_id", "type": "INTEGER", "semantic_type": "id"},
                {"name": "item_id", "type": "INTEGER", "semantic_type": "id"},
                {"name": "storage_id", "type": "INTEGER", "semantic_type": "id"},
                {
                    "name": "units_available",
                    "type": "INTEGER",
                    "semantic_type": "numeric_candidate",
                    "sample_values": [12, 7, 0],
                    "min_value": 0,
                    "max_value": 12,
                    "unique_count": 3,
                    "null_count": 0,
                },
                {"name": "checked_on", "type": "DATE", "semantic_type": "date"},
            ],
        }
    }
    captured_messages = []

    def fake_call_ai_backend(messages, backend, response_format=None):
        captured_messages.append(messages)
        if "q" in response_format.get("required", []):
            return json.dumps({"d": "Stock records", "p": "Tracks inventory", "q": ["Show stock"]})
        return json.dumps(
            {
                "c": {
                    "units_available": {
                        "d": "Available units",
                        "b": ["stock", "available"],
                        "s": "quantity",
                        "cf": 0.93,
                        "r": "samples and nearby columns indicate units on hand",
                        "me": True,
                        "di": False,
                        "dt": False,
                    }
                }
            }
        )

    monkeypatch.setattr("semantic.ai_semantic_enricher._call_ai_backend", fake_call_ai_backend)

    enriched = enrich_knowledge_base_with_ai(knowledge_base, backend="local")

    candidate_prompt = captured_messages[1][1]["content"]
    assert "candidate_type=numeric_candidate" in candidate_prompt
    assert "samples=['12', '7', '0']" in candidate_prompt
    assert "min=0" in candidate_prompt
    assert "max=12" in candidate_prompt
    assert "unique=3" in candidate_prompt
    assert "nulls=0" in candidate_prompt
    assert "nearby=['storage_id', 'checked_on']" in candidate_prompt or "nearby=['item_id', 'storage_id', 'checked_on']" in candidate_prompt
    assert "stock_positions.item_id -> items.item_id" in candidate_prompt
    assert "Do not copy names, labels, cities, dates, amounts, or codes from rows." in captured_messages[0][1]["content"]
    assert enriched["stock_positions"]["columns"][3]["semantic_type"] == "quantity"


def test_enrichment_applies_final_semantic_meaning_to_candidates_only(monkeypatch):
    knowledge_base = {
        "refund_logs": {
            "columns": [
                {"name": "refund_id", "type": "INTEGER", "semantic_type": "id"},
                {
                    "name": "refund_value",
                    "type": "DECIMAL(10,2)",
                    "semantic_type": "numeric_candidate",
                    "sample_values": [100.0, 35.5],
                },
                {
                    "name": "reason_text",
                    "type": "VARCHAR(100)",
                    "semantic_type": "text_candidate",
                    "sample_values": ["Damaged", "Expired"],
                },
            ]
        }
    }

    def fake_call_ai_backend(messages, backend, response_format=None):
        if "q" in response_format.get("required", []):
            return json.dumps({"d": "Refund events", "p": "Tracks refunds", "q": ["Show refunds"]})
        return json.dumps(
            {
                "c": {
                    "refund_value": {
                        "d": "Refund amount",
                        "b": ["refund", "amount"],
                        "s": "money",
                        "cf": 0.92,
                        "r": "numeric values and context indicate money",
                        "me": True,
                        "di": False,
                        "dt": False,
                    },
                    "reason_text": {
                        "d": "Refund reason",
                        "b": ["reason"],
                        "s": "text",
                        "cf": 0.84,
                        "r": "sample values look categorical text",
                        "me": False,
                        "di": True,
                        "dt": False,
                    },
                }
            }
        )

    monkeypatch.setattr("semantic.ai_semantic_enricher._call_ai_backend", fake_call_ai_backend)

    enriched = enrich_knowledge_base_with_ai(knowledge_base, backend="local")
    refund_value = enriched["refund_logs"]["columns"][1]
    reason_text = enriched["refund_logs"]["columns"][2]

    assert refund_value["semantic_type"] == "money"
    assert refund_value["is_measure"] is True
    assert refund_value["business_description"] == "Refund amount"
    assert "numeric values and context indicate money" in refund_value["reason"]
    assert reason_text["semantic_type"] == "text"
    assert reason_text["is_dimension"] is True
    assert reason_text["business_description"] == "Refund reason"


def test_reason_like_text_fields_normalize_name_to_text(monkeypatch):
    knowledge_base = {
        "refund_logs": {
            "columns": [
                {
                    "name": "reason_text",
                    "type": "VARCHAR(100)",
                    "semantic_type": "text_candidate",
                    "sample_values": ["Damaged", "Expired"],
                }
            ]
        }
    }

    def fake_call_ai_backend(messages, backend, response_format=None):
        if "q" in response_format.get("required", []):
            return json.dumps({"d": "Refund events", "p": "Tracks refunds", "q": ["Show refunds"]})
        return json.dumps(
            {
                "c": {
                    "reason_text": {
                        "d": "Refund reason",
                        "b": ["reason"],
                        "s": "name",
                        "cf": 0.81,
                        "r": "text values describe the reason",
                        "me": False,
                        "di": True,
                        "dt": False,
                    }
                }
            }
        )

    monkeypatch.setattr("semantic.ai_semantic_enricher._call_ai_backend", fake_call_ai_backend)

    enriched = enrich_knowledge_base_with_ai(knowledge_base, backend="local")
    reason_text = enriched["refund_logs"]["columns"][0]

    assert reason_text["semantic_type"] == "text"
    assert reason_text["is_dimension"] is True


def test_structural_facts_override_ai_semantic_changes(monkeypatch):
    knowledge_base = {
        "stock_positions": {
            "primary_keys": ["stock_id"],
            "foreign_keys": [{"column": "item_id", "referenced_table": "items", "referenced_column": "item_id"}],
            "columns": [
                {"name": "stock_id", "type": "INTEGER", "semantic_type": "id", "reason": "Structural fact: primary key."},
                {"name": "item_id", "type": "INTEGER", "semantic_type": "id", "reason": "Structural fact: foreign key."},
                {"name": "checked_on", "type": "DATE", "semantic_type": "date", "is_date": True, "reason": "Structural fact: date."},
                {"name": "units_available", "type": "INTEGER", "semantic_type": "numeric_candidate"},
            ],
        }
    }

    def fake_call_ai_backend(messages, backend, response_format=None):
        if "q" in response_format.get("required", []):
            return json.dumps({"d": "Stock records", "p": "Tracks inventory", "q": ["Show stock"]})
        return json.dumps(
            {
                "c": {
                    "stock_id": {
                        "d": "Stock money",
                        "b": ["amount"],
                        "s": "money",
                        "cf": 0.99,
                        "r": "wrong on purpose",
                        "me": True,
                        "di": False,
                        "dt": False,
                    },
                    "checked_on": {
                        "d": "Check status",
                        "b": ["status"],
                        "s": "status",
                        "cf": 0.91,
                        "r": "wrong on purpose",
                        "me": False,
                        "di": True,
                        "dt": False,
                    },
                    "units_available": {
                        "d": "Available units",
                        "b": ["stock"],
                        "s": "quantity",
                        "cf": 0.9,
                        "r": "values indicate countable units",
                        "me": True,
                        "di": False,
                        "dt": False,
                    },
                }
            }
        )

    monkeypatch.setattr("semantic.ai_semantic_enricher._call_ai_backend", fake_call_ai_backend)

    enriched = enrich_knowledge_base_with_ai(knowledge_base, backend="local")
    stock_id = enriched["stock_positions"]["columns"][0]
    item_id = enriched["stock_positions"]["columns"][1]
    checked_on = enriched["stock_positions"]["columns"][2]
    units_available = enriched["stock_positions"]["columns"][3]

    assert stock_id["semantic_type"] == "id"
    assert item_id["semantic_type"] == "id"
    assert checked_on["semantic_type"] == "date"
    assert checked_on["is_date"] is True
    assert units_available["semantic_type"] == "quantity"


def test_enrich_knowledge_base_allows_partial_table_fallback(monkeypatch):
    knowledge_base = {
        "orders": {
            "columns": [
                {"name": "final_amount", "type": "DECIMAL(10,2)", "semantic_type": "numeric_candidate"}
            ]
        },
        "customers": {
            "columns": [
                {"name": "customer_name", "type": "VARCHAR(100)", "semantic_type": "text_candidate"}
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
        if "q" in response_format.get("required", []):
            return json.dumps({"d": "Order facts", "p": "Tracks order value", "q": ["Show order value"]})
        return json.dumps(
            {
                "c": {
                    "final_amount": {
                        "d": "Final amount",
                        "b": ["sales"],
                        "s": "money",
                        "cf": 0.94,
                        "r": "numeric values indicate money",
                        "me": True,
                        "di": False,
                        "dt": False,
                    }
                }
            }
        )

    monkeypatch.setattr("semantic.ai_semantic_enricher._call_ai_backend", fake_call_ai_backend)

    enriched = enrich_knowledge_base_with_ai(knowledge_base, backend="local")
    enriched_tables, fallback_tables = get_last_enrichment_report()

    assert calls == ["orders", "orders", "customers"]
    assert enriched["orders"]["columns"][0]["semantic_type"] == "money"
    assert "business_description" not in enriched["customers"]
    assert enriched_tables == ["orders"]
    assert fallback_tables == {"customers": "Local AI timed out"}


def test_enrich_timeout_fallback_log_is_sanitized(monkeypatch, caplog):
    knowledge_base = {
        "orders": {
            "columns": [
                {"name": "final_amount", "type": "DECIMAL(10,2)", "semantic_type": "numeric_candidate"}
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


def test_ai_sample_value_echoes_are_replaced_with_neutral_metadata(monkeypatch):
    knowledge_base = {
        "items": {
            "columns": [
                {
                    "name": "item_group",
                    "type": "VARCHAR(100)",
                    "semantic_type": "category_candidate",
                    "sample_values": ["Furniture", "Appliances"],
                }
            ]
        },
        "accounts": {
            "columns": [
                {
                    "name": "account_label",
                    "type": "VARCHAR(100)",
                    "semantic_type": "text_candidate",
                    "sample_values": ["Nova Traders", "Prime Retail", "BlueLine Stores"],
                }
            ]
        },
        "stock_positions": {
            "columns": [
                {
                    "name": "stock_value",
                    "type": "DECIMAL(10,2)",
                    "semantic_type": "numeric_candidate",
                    "sample_values": [1200.0, 950.0],
                },
                {
                    "name": "item_label",
                    "type": "VARCHAR(100)",
                    "semantic_type": "text_candidate",
                    "sample_values": ["Office Chair", "Study Desk"],
                },
            ]
        },
    }

    def fake_call_ai_backend(messages, backend, response_format=None):
        table_line = next(line for line in messages[1]["content"].splitlines() if line.startswith("Table: "))
        table_name = table_line.split(": ", 1)[1]

        if "q" in response_format.get("required", []):
            if table_name == "items":
                return json.dumps({"d": "Furniture", "p": "Furniture", "q": []})
            if table_name == "accounts":
                return json.dumps({"d": "Nova Traders", "p": "Nova Traders", "q": []})
            return json.dumps({"d": "Stock", "p": "Stock", "q": ["What are my stocks worth?"]})

        if table_name == "accounts":
            return json.dumps(
                {
                    "c": {
                        "account_label": {
                            "d": "Nova Traders",
                            "b": ["Prime Retail", "BlueLine Stores"],
                            "s": "name",
                            "cf": 0.86,
                            "r": "text values identify the account label",
                            "me": False,
                            "di": True,
                            "dt": False,
                        }
                    }
                }
            )

        return json.dumps(
            {
                "c": {
                    "stock_value": {
                        "d": "Stock value",
                        "b": ["inventory value"],
                        "s": "money",
                        "cf": 0.82,
                        "r": "numeric values indicate stored value",
                        "me": True,
                        "di": False,
                        "dt": False,
                    },
                    "item_label": {
                        "d": "Office Chair",
                        "b": ["Office Chair", "Study Desk"],
                        "s": "name",
                        "cf": 0.8,
                        "r": "text values identify the item label",
                        "me": False,
                        "di": True,
                        "dt": False,
                    },
                }
            }
        )

    monkeypatch.setattr("semantic.ai_semantic_enricher._call_ai_backend", fake_call_ai_backend)

    enriched = enrich_knowledge_base_with_ai(knowledge_base, backend="local")

    assert enriched["items"]["business_purpose"] == "Stores records for item."
    assert enriched["accounts"]["business_purpose"] == "Stores records for account."
    assert enriched["stock_positions"]["business_purpose"] == "Stores records for stock position."
    assert enriched["stock_positions"]["possible_business_questions"] == []

    account_label = enriched["accounts"]["columns"][0]
    item_label = enriched["stock_positions"]["columns"][1]
    assert account_label["business_description"] != "Nova Traders"
    assert "Nova Traders" not in account_label["business_terms"]
    assert "Prime Retail" not in account_label["business_terms"]
    assert "BlueLine Stores" not in account_label["business_terms"]
    assert "account label" in account_label["business_terms"]
    assert item_label["business_description"] != "Office Chair"
    assert "Office Chair" not in item_label["business_terms"]


def test_ai_identifier_like_metadata_is_rewritten_into_useful_terms(monkeypatch):
    knowledge_base = {
        "deals": {
            "table_name": "deals",
            "primary_keys": ["deal_id"],
            "foreign_keys": [
                {"column": "account_id", "referenced_table": "accounts", "referenced_column": "account_id"},
            ],
            "columns": [
                {"name": "deal_id", "type": "INTEGER", "semantic_type": "id"},
                {"name": "account_id", "type": "INTEGER", "semantic_type": "id"},
                {
                    "name": "deal_value",
                    "type": "DECIMAL(10,2)",
                    "semantic_type": "numeric_candidate",
                    "reason": "Candidate evidence: numeric-like type or numeric-style column meaning.",
                },
            ],
        },
        "accounts": {
            "table_name": "accounts",
            "columns": [
                {
                    "name": "account_label",
                    "type": "VARCHAR(100)",
                    "semantic_type": "text_candidate",
                    "sample_values": ["Nova Traders", "Prime Retail"],
                    "reason": "Candidate evidence: text-like type or label-style column meaning.",
                }
            ],
        },
        "stock_positions": {
            "table_name": "stock_positions",
            "primary_keys": ["stock_id"],
            "foreign_keys": [
                {"column": "item_id", "referenced_table": "items", "referenced_column": "item_id"},
                {"column": "storage_id", "referenced_table": "storage_points", "referenced_column": "storage_id"},
            ],
            "columns": [
                {"name": "stock_id", "type": "INTEGER", "semantic_type": "id"},
                {"name": "item_id", "type": "INTEGER", "semantic_type": "id"},
                {"name": "storage_id", "type": "INTEGER", "semantic_type": "id"},
                {
                    "name": "units_available",
                    "type": "INTEGER",
                    "semantic_type": "numeric_candidate",
                    "reason": "Candidate evidence: numeric-like type or numeric-style column meaning.",
                },
                {"name": "checked_on", "type": "DATE", "semantic_type": "date"},
            ],
        },
    }

    def fake_call_ai_backend(messages, backend, response_format=None):
        table_line = next(line for line in messages[1]["content"].splitlines() if line.startswith("Table: "))
        table_name = table_line.split(": ", 1)[1]

        if "q" in response_format.get("required", []):
            if table_name == "stock_positions":
                return json.dumps({"d": "Storage ID", "p": "Storage ID", "q": []})
            if table_name == "accounts":
                return json.dumps({"d": "Nova Traders", "p": "Nova Traders", "q": []})
            return json.dumps({"d": "Deal details", "p": "Deal details", "q": []})

        if table_name == "deals":
            return json.dumps(
                {
                    "c": {
                        "deal_value": {
                            "d": "deal_date",
                            "b": ["deal_date", "item_id"],
                            "s": "money",
                            "cf": 0.9,
                            "r": "deal_date",
                            "me": True,
                            "di": False,
                            "dt": False,
                        }
                    }
                }
            )
        if table_name == "accounts":
            return json.dumps(
                {
                    "c": {
                        "account_label": {
                            "d": "Nova Traders",
                            "b": ["Prime Retail", "BlueLine Stores"],
                            "s": "name",
                            "cf": 0.87,
                            "r": "account_label",
                            "me": False,
                            "di": True,
                            "dt": False,
                        }
                    }
                }
            )
        return json.dumps(
            {
                "c": {
                    "units_available": {
                        "d": "Storage ID",
                        "b": ["storage_id", "checked_on"],
                        "s": "quantity",
                        "cf": 0.91,
                        "r": "checked_on",
                        "me": True,
                        "di": False,
                        "dt": False,
                    }
                }
            }
        )

    monkeypatch.setattr("semantic.ai_semantic_enricher._call_ai_backend", fake_call_ai_backend)

    enriched = enrich_knowledge_base_with_ai(knowledge_base, backend="local")

    deal_value = enriched["deals"]["columns"][2]
    account_label = enriched["accounts"]["columns"][0]
    units_available = enriched["stock_positions"]["columns"][3]

    assert enriched["stock_positions"]["business_purpose"] == "Stores stock position records linked to item and storage point."
    assert deal_value["business_description"] == "Description for deal value field."
    assert deal_value["business_terms"] == ["deal value"]
    assert deal_value["reason"] == "Candidate evidence: numeric-like type or numeric-style column meaning."
    assert deal_value["is_measure"] is True

    assert account_label["business_description"] == "Description for account label field."
    assert account_label["business_terms"] == ["account label"]
    assert account_label["is_dimension"] is True

    assert units_available["business_description"] == "Description for units available field."
    assert units_available["business_terms"] == ["units available"]
    assert units_available["reason"] == "Candidate evidence: numeric-like type or numeric-style column meaning."
    assert units_available["is_measure"] is True
