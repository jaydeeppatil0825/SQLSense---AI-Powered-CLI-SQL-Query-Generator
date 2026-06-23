"""
test_question_service_no_ai_runtime.py
======================================
Test to protect Phase 2 changes - ensures QuestionService does not import or call AI SQL generator.
"""

from pathlib import Path

import pytest

from core.insight_service import InsightService
from sql_pipeline.question_service import QuestionService
from sql_pipeline.sql_generator import _call_ai_backend, generate_sql, generate_sql_with_retry


RELATIONSHIP_KB = {
    "clients": {
        "columns": [
            {"name": "client_id", "type": "INTEGER", "nullable": False, "semantic_type": "id"},
            {"name": "client_name", "type": "VARCHAR(100)", "nullable": False, "semantic_type": "name"},
        ],
        "primary_keys": ["client_id"],
        "foreign_keys": [],
        "relationships": [],
    },
    "agreements": {
        "columns": [
            {"name": "agreement_id", "type": "INTEGER", "nullable": False, "semantic_type": "id"},
            {"name": "client_id", "type": "INTEGER", "nullable": False, "semantic_type": "id"},
        ],
        "primary_keys": ["agreement_id"],
        "foreign_keys": [
            {"column": "client_id", "referenced_table": "clients", "referenced_column": "client_id"},
        ],
        "relationships": [],
    },
    "invoices": {
        "columns": [
            {"name": "invoice_id", "type": "INTEGER", "nullable": False, "semantic_type": "id"},
            {"name": "client_id", "type": "INTEGER", "nullable": False, "semantic_type": "id"},
        ],
        "primary_keys": ["invoice_id"],
        "foreign_keys": [
            {"column": "client_id", "referenced_table": "clients", "referenced_column": "client_id"},
        ],
        "relationships": [],
    },
}


def test_question_service_does_not_import_ai_sql_generator():
    """
    Verify that question_service.py does not import or call AI SQL generator functions.
    
    This test protects Phase 2 changes by ensuring:
    - generate_sql is not imported or called
    - generate_sql_with_retry is not imported or called
    - _call_ai_backend is not called
    - call_ai_backend is not called
    
    These functions are blocked in sql_generator.py with RuntimeError wrappers,
    but QuestionService should never call them at runtime.
    """
    root = Path(__file__).resolve().parents[1]
    text = (root / "sql_pipeline" / "question_service.py").read_text(
        encoding="utf-8",
        errors="ignore",
    )

    forbidden = [
        "generate_sql(",
        "generate_sql_with_retry(",
        "_call_ai_backend",
        "call_ai_backend",
    ]

    for pattern in forbidden:
        assert pattern not in text, f"Found forbidden pattern '{pattern}' in question_service.py"


def test_show_all_client_singular_uses_rule_based_without_joins():
    service = QuestionService()

    success, message, sql, error = service.process_question("show all client", RELATIONSHIP_KB, ai_backend="local")

    assert success is True
    assert error is None
    assert "FROM clients" in sql
    assert "JOIN" not in sql.upper()
    context = service.get_last_query_context()
    assert context["selected_table_names"] == ["clients"]
    assert context["join_paths"] == []
    assert context["route_used"] == "rule-based"


def test_count_client_singular_uses_rule_based_without_joins():
    service = QuestionService()

    success, message, sql, error = service.process_question("count client", RELATIONSHIP_KB, ai_backend="local")

    assert success is True
    assert error is None
    assert sql == "SELECT COUNT(*) AS total_clients FROM clients;"
    context = service.get_last_query_context()
    assert context["selected_table_names"] == ["clients"]
    assert context["join_paths"] == []
    assert context["route_used"] == "rule-based"


def test_runtime_ai_sql_helpers_are_blocked():
    with pytest.raises(RuntimeError):
        _call_ai_backend([], backend="local")
    with pytest.raises(RuntimeError):
        generate_sql()
    with pytest.raises(RuntimeError):
        generate_sql_with_retry()


def test_runtime_insight_generation_is_disabled():
    service = InsightService()

    success, message, insights = service.generate_insights(
        user_question="show all client",
        sql="SELECT client_id, client_name FROM clients;",
        rows=[{"client_id": 1, "client_name": "Alpha"}],
        knowledge_base=RELATIONSHIP_KB,
        ai_backend="local",
    )

    assert success is False
    assert insights is None
    assert "disabled" in message.lower()
    assert service.get_last_insights() is None
    assert service.get_last_insights_skipped() is True
