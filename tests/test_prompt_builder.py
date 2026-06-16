import pytest

from ai.prompt_builder import build_sql_prompt, _get_relevant_glossary_terms


def _knowledge_base():
    return {
        "client_directory": {
            "primary_keys": ["client_id"],
            "foreign_keys": [],
            "columns": [
                {
                    "name": "client_id",
                    "type": "INTEGER",
                    "nullable": False,
                    "semantic_type": "id",
                    "sample_values": [1, 2],
                },
                {
                    "name": "client_name",
                    "type": "VARCHAR(100)",
                    "nullable": False,
                    "semantic_type": "name",
                    "sample_values": ["Acme", "Globex"],
                },
            ],
        },
        "invoice_headers": {
            "primary_keys": ["invoice_id"],
            "foreign_keys": [
                {
                    "column": "client_id",
                    "referenced_table": "client_directory",
                    "referenced_column": "client_id",
                }
            ],
            "columns": [
                {
                    "name": "invoice_id",
                    "type": "INTEGER",
                    "nullable": False,
                    "semantic_type": "id",
                    "sample_values": [1, 2],
                },
                {
                    "name": "client_id",
                    "type": "INTEGER",
                    "nullable": False,
                    "semantic_type": "id",
                    "sample_values": [1, 2],
                },
                {
                    "name": "invoice_date",
                    "type": "DATE",
                    "nullable": True,
                    "semantic_type": "date",
                    "sample_values": ["2026-01-01", "2026-01-02"],
                },
                {
                    "name": "total_due",
                    "type": "DECIMAL(10,2)",
                    "nullable": True,
                    "semantic_type": "money",
                    "sample_values": [100.0, 250.5],
                },
                {
                    "name": "workflow_status",
                    "type": "VARCHAR(20)",
                    "nullable": True,
                    "semantic_type": "status",
                    "sample_values": ["Pending", "Paid"],
                },
            ],
        },
    }


def _glossary():
    return {
        "payables": {
            "description": "Open amount due from invoice headers.",
            "mapped_columns": [{"table": "invoice_headers", "column": "total_due", "confidence": "high"}],
            "example_questions": ["Show current payables"],
            "business_terms": ["amount due"],
        }
    }


def test_missing_knowledge_base_raises_value_error():
    with pytest.raises(ValueError, match="Knowledge base is missing"):
        build_sql_prompt("show invoices", {})


def test_prompt_builder_returns_system_and_user_messages_with_full_context():
    messages = build_sql_prompt("show recent invoices", _knowledge_base(), business_glossary=_glossary())
    system_message = messages[0]["content"]

    assert messages[0]["role"] == "system"
    assert messages[1] == {"role": "user", "content": "show recent invoices"}
    for expected in ["invoice_headers", "invoice_date", "total_due", "workflow_status", "Pending", "Paid"]:
        assert expected in system_message
    assert "invoice_headers.client_id references client_directory.client_id" in system_message
    assert "JOIN client_directory ON invoice_headers.client_id = client_directory.client_id" in system_message
    assert "Do not add LIMIT unless the question explicitly requests a row count." in system_message


def test_prompt_omits_default_limit_instruction_when_question_has_numeric_qualifier():
    messages = build_sql_prompt("show top 10 invoices", _knowledge_base())
    assert "Do not add LIMIT unless the question explicitly requests a row count." not in messages[0]["content"]
    assert "Use LIMIT 10 in your query." in messages[0]["content"]


def test_get_relevant_glossary_terms_uses_generic_fallback_when_file_missing():
    glossary_section = _get_relevant_glossary_terms(
        "show balances",
        _knowledge_base(),
        glossary_path="nonexistent_path.json",
    )
    assert "Business glossary" in glossary_section


def test_get_relevant_glossary_terms_uses_supplied_glossary():
    glossary_section = _get_relevant_glossary_terms(
        "show current payables",
        _knowledge_base(),
        glossary=_glossary(),
    )
    assert "TERM: payables" in glossary_section
    assert "invoice_headers.total_due" in glossary_section


def test_prompt_includes_cli_safety_and_dynamic_relationship_guidance():
    messages = build_sql_prompt("show current payables", _knowledge_base(), business_glossary=_glossary())
    system_message = messages[0]["content"]

    assert "Do NOT include markdown fences" in system_message
    assert "Detected schema relationships to prefer for JOINs" in system_message
    assert "invoice_headers.client_id = client_directory.client_id" in system_message
    assert "PCSoft" not in system_message


def test_prompt_includes_selected_columns_and_plan_execution_rules():
    selected_tables = [
        {
            "table": "invoice_headers",
            "confidence": 0.92,
            "reason": "matched money and date context",
            "selected_columns": [
                {"column": "invoice_date", "semantic_type": "date"},
                {"column": "total_due", "semantic_type": "money"},
            ],
        }
    ]
    query_plan = {
        "intent": "total",
        "metric": "money",
        "dimension": None,
        "filters": [{"type": "status", "value": "Pending"}],
        "date_range": None,
        "grouping": [],
        "sorting": None,
        "limit": 50,
        "semantic_hints": {"money", "status"},
        "matched_glossary_terms": ["payables"],
    }

    messages = build_sql_prompt(
        "show current payables",
        _knowledge_base(),
        query_plan=query_plan,
        selected_tables=selected_tables,
        business_glossary=_glossary(),
        join_paths=[
            {
                "from_table": "invoice_headers",
                "to_table": "client_directory",
                "path": [
                    {
                        "from_table": "invoice_headers",
                        "from_column": "client_id",
                        "to_table": "client_directory",
                        "to_column": "client_id",
                        "join_condition": "invoice_headers.client_id = client_directory.client_id",
                    }
                ],
                "length": 1,
            }
        ],
    )
    system_message = messages[0]["content"]

    assert "Treat the structured query plan as authoritative" in system_message
    assert "AI target for this question:" in system_message
    assert "Use the metric 'money' as the main measure hint." in system_message
    assert "selected columns: invoice_date[date], total_due[money]" in system_message
    assert "semantic_type=money" in system_message
    assert "Computed join paths between selected tables:" in system_message
    assert "invoice_headers.client_id = client_directory.client_id" in system_message
