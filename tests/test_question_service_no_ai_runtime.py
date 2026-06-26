"""
test_question_service_no_ai_runtime.py
======================================
Test to protect Phase 2 changes - ensures QuestionService does not import or call AI SQL generator.
"""

from pathlib import Path
from types import SimpleNamespace

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
    assert context["route_used"] == "deterministic_sql_required"


def test_count_client_singular_uses_rule_based_without_joins():
    service = QuestionService()

    success, message, sql, error = service.process_question("count client", RELATIONSHIP_KB, ai_backend="local")

    assert success is True
    assert error is None
    assert sql == "SELECT COUNT(*) AS total_clients FROM clients;"
    context = service.get_last_query_context()
    assert context["selected_table_names"] == ["clients"]
    assert context["join_paths"] == []
    assert context["route_used"] == "deterministic_sql_required"


def test_single_table_total_uses_deterministic_aggregate_without_runtime_ai(monkeypatch):
    knowledge_base = {
        "bills": {
            "columns": [
                {"name": "bill_id", "type": "INTEGER", "nullable": False, "semantic_type": "id"},
                {"name": "amount_total", "type": "DECIMAL(12,2)", "nullable": True, "semantic_type": "numeric_candidate"},
            ],
            "primary_keys": ["bill_id"],
            "foreign_keys": [],
            "relationships": [],
        }
    }
    service = QuestionService()

    monkeypatch.setattr(
        "core.question_service.generate_sql",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("Runtime AI SQL generation must remain disabled")),
    )
    monkeypatch.setattr(
        "core.question_service.generate_sql_with_retry",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("Runtime AI SQL retry must remain disabled")),
    )

    success, message, sql, error = service.process_question("show total amount from bills", knowledge_base, ai_backend="local")

    assert success is True
    assert sql == "SELECT SUM(amount_total) AS sum_amount_total FROM bills;"
    assert service.get_last_query_context()["route_used"] == "deterministic_sql_required"


@pytest.mark.parametrize(
    ("question", "expected_sql"),
    [
        ("show sum amount from bills", "SELECT SUM(amount_total) AS sum_amount_total FROM bills;"),
        ("show average amount from bills", "SELECT AVG(amount_total) AS avg_amount_total FROM bills;"),
        ("show highest amount from bills", "SELECT MAX(amount_total) AS max_amount_total FROM bills;"),
        ("show maximum amount from bills", "SELECT MAX(amount_total) AS max_amount_total FROM bills;"),
        ("show lowest amount from bills", "SELECT MIN(amount_total) AS min_amount_total FROM bills;"),
        ("show minimum amount from bills", "SELECT MIN(amount_total) AS min_amount_total FROM bills;"),
    ],
)
def test_single_table_aggregate_variants_use_deterministic_aggregate_without_runtime_ai(monkeypatch, question, expected_sql):
    knowledge_base = {
        "bills": {
            "columns": [
                {"name": "bill_id", "type": "INTEGER", "nullable": False, "semantic_type": "id"},
                {"name": "amount_total", "type": "DECIMAL(12,2)", "nullable": True, "semantic_type": "numeric_candidate"},
                {"name": "tax_total", "type": "DECIMAL(12,2)", "nullable": True, "semantic_type": "numeric_candidate"},
            ],
            "primary_keys": ["bill_id"],
            "foreign_keys": [],
            "relationships": [],
        }
    }
    service = QuestionService()

    monkeypatch.setattr(
        "core.question_service.generate_sql",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("Runtime AI SQL generation must remain disabled")),
    )
    monkeypatch.setattr(
        "core.question_service.generate_sql_with_retry",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("Runtime AI SQL retry must remain disabled")),
    )

    success, message, sql, error = service.process_question(question, knowledge_base, ai_backend="local")

    assert success is True
    assert sql == expected_sql
    assert service.get_last_query_context()["route_used"] == "deterministic_sql_required"


def test_single_table_aggregate_metric_ambiguity_returns_cannot_plan_safely(monkeypatch):
    knowledge_base = {
        "bills": {
            "columns": [
                {"name": "bill_id", "type": "INTEGER", "nullable": False, "semantic_type": "id"},
                {"name": "amount_total", "type": "DECIMAL(12,2)", "nullable": True, "semantic_type": "numeric_candidate"},
                {"name": "tax_total", "type": "DECIMAL(12,2)", "nullable": True, "semantic_type": "numeric_candidate"},
            ],
            "primary_keys": ["bill_id"],
            "foreign_keys": [],
            "relationships": [],
        }
    }
    service = QuestionService()

    monkeypatch.setattr(
        "core.question_service.generate_sql",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("Runtime AI SQL generation must remain disabled")),
    )
    monkeypatch.setattr(
        "core.question_service.generate_sql_with_retry",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("Runtime AI SQL retry must remain disabled")),
    )

    success, message, sql, error = service.process_question("show total from bills", knowledge_base, ai_backend="local")

    assert success is False
    assert sql is None
    assert "cannot choose metric safely" in message.lower()
    assert "amount_total" in message
    assert "tax_total" in message
    assert service.get_last_query_context()["route_used"] == "cannot_plan_safely"
    assert service.get_last_query_context()["route_reason"] == "metric_ambiguous"


def test_pipeline_single_table_aggregate_dispatch_uses_deterministic_generator(monkeypatch):
    service = QuestionService()
    pipeline_context = {
        "normalized_question": "show total amount from bills",
        "route_recommendation": "deterministic_sql_required",
        "query_context": {
            "route_recommendation": "deterministic_sql_required",
            "query_shape": "single_table_aggregate",
            "can_plan": True,
            "missing_evidence": [],
            "plan": {
                "question": "show total amount from bills",
                "intent": "total",
            },
            "selected_tables": [
                {
                    "table": "bills",
                    "confidence": 0.9,
                    "selected_columns": [
                        {"column": "amount_total", "confidence": 0.8, "semantic_type": "numeric_candidate"}
                    ],
                }
            ],
            "selected_table_names": ["bills"],
            "selected_columns": [
                {"table": "bills", "column": "amount_total", "confidence": 0.8, "semantic_type": "numeric_candidate"}
            ],
            "metric_candidates": [
                {"table": "bills", "column": "amount_total", "score": 0.92, "reason": "metric candidate"}
            ],
            "join_paths": [],
            "complex_sql_plan": {
                "query_shape": "single_table_aggregate",
            },
        },
        "plan": {
            "question": "show total amount from bills",
            "intent": "total",
        },
        "retrieved_context": {},
        "formula_evidence": [],
        "evidence_sources": [],
    }

    monkeypatch.setattr(
        "sql_pipeline.question_service.generate_simple_sql",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("Simple generator should not run for single-table aggregates")),
    )
    monkeypatch.setattr(
        "sql_pipeline.question_service.generate_single_table_aggregate_sql",
        lambda *args, **kwargs: SimpleNamespace(
            status="generated",
            sql="SELECT SUM(amount_total) AS sum_amount_total FROM bills;",
            reason="single-table aggregate generated deterministically",
        ),
    )

    success, message, sql, error = service.process_question(
        "show total amount from bills",
        {
            "bills": {
                "columns": [
                    {"name": "bill_id", "type": "INTEGER", "nullable": False, "semantic_type": "id"},
                    {"name": "amount_total", "type": "DECIMAL(12,2)", "nullable": True, "semantic_type": "numeric_candidate"},
                ],
                "primary_keys": ["bill_id"],
                "foreign_keys": [],
                "relationships": [],
            }
        },
        ai_backend="local",
        pipeline_context=pipeline_context,
    )

    assert success is True
    assert sql == "SELECT SUM(amount_total) AS sum_amount_total FROM bills;"
    assert service.get_last_query_context()["route_used"] == "deterministic_sql_required"


def test_pipeline_blocked_unsafe_route_blocks_sql_generation(monkeypatch):
    service = QuestionService()
    pipeline_context = {
        "normalized_question": "delete all bills",
        "route_recommendation": "blocked_unsafe",
        "query_context": {
            "route_recommendation": "blocked_unsafe",
            "query_shape": "blocked_unsafe",
            "can_plan": False,
            "missing_evidence": [],
            "plan": {"question": "delete all bills", "intent": "list"},
            "selected_tables": [{"table": "bills", "confidence": 0.8}],
            "selected_table_names": ["bills"],
            "selected_columns": [],
            "join_paths": [],
            "complex_sql_plan": {},
        },
        "plan": {"question": "delete all bills", "intent": "list"},
        "retrieved_context": {},
        "formula_evidence": [],
        "evidence_sources": [],
    }

    monkeypatch.setattr(
        "sql_pipeline.question_service.generate_simple_sql",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("Simple generator must not run for blocked unsafe routes")),
    )
    monkeypatch.setattr(
        "sql_pipeline.question_service.generate_single_table_aggregate_sql",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("Aggregate generator must not run for blocked unsafe routes")),
    )

    success, message, sql, error = service.process_question(
        "delete all bills",
        {
            "bills": {
                "columns": [{"name": "bill_id", "type": "INTEGER", "nullable": False, "semantic_type": "id"}],
                "primary_keys": ["bill_id"],
                "foreign_keys": [],
                "relationships": [],
            }
        },
        ai_backend="local",
        pipeline_context=pipeline_context,
    )

    assert success is False
    assert sql is None
    assert "unsafe request blocked" in message.lower()
    assert service.get_last_query_context()["route_used"] == "blocked_unsafe"


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


@pytest.mark.parametrize(
    ("question", "expected_sql"),
    [
        ("show sum billed value from bills", "SELECT SUM(billed_value) AS sum_billed_value FROM bills;"),
        ("show total billed value from bills", "SELECT SUM(billed_value) AS sum_billed_value FROM bills;"),
        ("show sum paid value from bills", "SELECT SUM(paid_value) AS sum_paid_value FROM bills;"),
        ("average billed value from bills", "SELECT AVG(billed_value) AS avg_billed_value FROM bills;"),
        ("highest paid value from bills", "SELECT MAX(paid_value) AS max_paid_value FROM bills;"),
        ("lowest billed value from bills", "SELECT MIN(billed_value) AS min_billed_value FROM bills;"),
    ],
)
def test_clear_metric_single_table_aggregates_dispatch_deterministically(question, expected_sql):
    knowledge_base = {
        "bills": {
            "columns": [
                {"name": "bill_id", "type": "INTEGER", "nullable": False, "semantic_type": "id"},
                {"name": "billed_value", "type": "DECIMAL(12,2)", "nullable": True, "semantic_type": "numeric_candidate"},
                {"name": "paid_value", "type": "DECIMAL(12,2)", "nullable": True, "semantic_type": "numeric_candidate"},
            ],
            "primary_keys": ["bill_id"],
            "foreign_keys": [],
            "relationships": [],
        }
    }
    service = QuestionService()

    success, message, sql, error = service.process_question(question, knowledge_base, ai_backend="local")

    assert success is True
    assert sql == expected_sql
    assert service.get_last_query_context()["route_used"] == "deterministic_sql_required"


def test_sum_amount_from_bills_stays_ambiguous_without_sql():
    knowledge_base = {
        "bills": {
            "columns": [
                {"name": "bill_id", "type": "INTEGER", "nullable": False, "semantic_type": "id"},
                {"name": "billed_value", "type": "DECIMAL(12,2)", "nullable": True, "semantic_type": "numeric_candidate"},
                {"name": "paid_value", "type": "DECIMAL(12,2)", "nullable": True, "semantic_type": "numeric_candidate"},
            ],
            "primary_keys": ["bill_id"],
            "foreign_keys": [],
            "relationships": [],
        }
    }
    service = QuestionService()

    success, message, sql, error = service.process_question("show sum amount from bills", knowledge_base, ai_backend="local")

    assert success is False
    assert sql is None
    assert "cannot choose metric safely" in message.lower()
    assert "billed_value" in message
    assert "paid_value" in message
    assert service.get_last_query_context()["route_used"] == "cannot_plan_safely"


def test_grouped_aggregate_shape_returns_not_implemented_capability_message():
    knowledge_base = {
        "partners": {
            "columns": [
                {"name": "partner_id", "type": "INTEGER", "nullable": False, "semantic_type": "id"},
                {"name": "partner_name", "type": "VARCHAR(100)", "nullable": False, "semantic_type": "name"},
            ],
            "primary_keys": ["partner_id"],
            "foreign_keys": [],
            "relationships": [],
        },
        "bills": {
            "columns": [
                {"name": "bill_id", "type": "INTEGER", "nullable": False, "semantic_type": "id"},
                {"name": "partner_id", "type": "INTEGER", "nullable": False, "semantic_type": "id"},
                {"name": "billed_value", "type": "DECIMAL(12,2)", "nullable": True, "semantic_type": "numeric_candidate"},
            ],
            "primary_keys": ["bill_id"],
            "foreign_keys": [
                {"column": "partner_id", "referenced_table": "partners", "referenced_column": "partner_id"},
            ],
            "relationships": [],
        },
    }
    service = QuestionService()

    success, message, sql, error = service.process_question(
        "total billed value by partner",
        knowledge_base,
        ai_backend="local",
    )

    assert success is False
    assert sql is None
    assert message == (
        "This query was understood, but deterministic SQL generation for this query shape "
        "is not implemented yet: grouped_aggregate."
    )
    assert service.get_last_query_context()["route_recommendation"] == "deterministic_sql_required"
    assert service.get_last_query_context()["query_shape"] == "grouped_aggregate"
    assert service.get_last_query_context()["route_used"] == "deterministic_sql_required"
