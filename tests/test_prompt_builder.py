import pytest

from ai.prompt_builder import build_sql_prompt, _get_relevant_glossary_terms


def _knowledge_base():
    return {
        "customers": {
            "primary_keys": ["id"],
            "foreign_keys": [],
            "columns": [
                {
                    "name": "id",
                    "type": "INTEGER",
                    "nullable": False,
                    "semantic_type": "customer",
                    "sample_values": [1, 2],
                },
                {
                    "name": "customer_name",
                    "type": "VARCHAR(100)",
                    "nullable": False,
                    "semantic_type": "customer",
                    "sample_values": ["Acme", "Globex"],
                },
            ],
        },
        "orders": {
            "primary_keys": ["id"],
            "foreign_keys": [
                {
                    "column": "customer_id",
                    "referenced_table": "customers",
                    "referenced_column": "id",
                }
            ],
            "columns": [
                {
                    "name": "id",
                    "type": "INTEGER",
                    "nullable": False,
                    "semantic_type": "general",
                    "sample_values": [1, 2],
                },
                {
                    "name": "status",
                    "type": "VARCHAR(20)",
                    "nullable": True,
                    "semantic_type": "status",
                    "sample_values": ["paid", "pending"],
                },
            ],
        }
    }


def test_missing_knowledge_base_raises_value_error():
    with pytest.raises(ValueError, match="Knowledge base is missing"):
        build_sql_prompt("show orders", {})


def test_prompt_builder_returns_system_and_user_messages_with_full_context():
    messages = build_sql_prompt("show recent orders", _knowledge_base())
    system_message = messages[0]["content"]

    assert messages[0]["role"] == "system"
    assert messages[1] == {"role": "user", "content": "show recent orders"}
    for expected in ["orders", "id", "INTEGER", "status", "VARCHAR(20)", "paid", "pending"]:
        assert expected in system_message
    assert "orders.customer_id references customers.id" in system_message
    assert "JOIN customers ON orders.customer_id = customers.id" in system_message
    assert "LIMIT 50" in system_message


def test_prompt_omits_limit_instruction_when_question_has_numeric_qualifier():
    messages = build_sql_prompt("show top 10 orders", _knowledge_base())

    assert "Append LIMIT 50" not in messages[0]["content"]


def test_get_relevant_glossary_terms_fallback_to_hardcoded():
    """Test that glossary falls back to hardcoded when business_glossary.json not found."""
    # Use a non-existent path to force fallback
    glossary_section = _get_relevant_glossary_terms("show sales", _knowledge_base(), glossary_path="nonexistent_path.json")
    
    # Should return the hardcoded glossary when file not found
    assert "Business term glossary" in glossary_section
    assert "SALES" in glossary_section or "REVENUE" in glossary_section


def test_get_relevant_glossary_terms_uses_real_glossary_when_available():
    """Test that the real business_glossary.json is used when it exists."""
    # Use the default path (semantic/business_glossary.json) which exists in the project
    glossary_section = _get_relevant_glossary_terms("show sales", _knowledge_base())
    
    # Should contain the real glossary content from business_glossary.json
    assert "Business term glossary" in glossary_section
    assert "SALES" in glossary_section
    assert "Maps to:" in glossary_section


def test_prompt_includes_cli_safety_and_pcsoft_relationship_guidance():
    messages = build_sql_prompt("show payment details with customer names", _knowledge_base())
    system_message = messages[0]["content"]

    assert "Do NOT include SQL comments" in system_message
    assert "orders.order_id = payments.order_id" in system_message
    assert "customers.customer_id = support_tickets.customer_id" in system_message


def test_prompt_includes_selected_columns_and_plan_execution_rules():
    selected_tables = [
        {
            "table": "orders",
            "confidence": 0.92,
            "reason": "matched sales metric",
            "selected_columns": [
                {"column": "order_date", "semantic_type": "date"},
                {"column": "status", "semantic_type": "status"},
            ],
        }
    ]
    query_plan = {
        "intent": "trend",
        "metric": "sales",
        "dimension": "month",
        "filters": [],
        "date_range": None,
        "grouping": ["month"],
        "sorting": {"direction": "asc", "by": "date"},
        "limit": 50,
    }

    messages = build_sql_prompt(
        "show monthly sales",
        _knowledge_base(),
        query_plan=query_plan,
        selected_tables=selected_tables,
    )
    system_message = messages[0]["content"]

    assert "Treat the structured query plan as authoritative" in system_message
    assert "AI target for this question:" in system_message
    assert "Use the metric 'sales' as the main business measure." in system_message
    assert "selected columns: order_date[date], status[status]" in system_message
    assert "Detected ERP relationships to prefer for JOINs" in system_message
