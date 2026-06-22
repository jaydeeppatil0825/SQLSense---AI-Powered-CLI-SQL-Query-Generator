"""Tests for AI SQL generation prompt assembly and retry context."""

import pytest

from ai.sql_generator import generate_sql, generate_sql_with_retry


def test_generate_sql_prompt_uses_pipeline_context(monkeypatch):
    captured = {}

    def fake_call_ai_backend(messages, backend, response_format=None):
        captured["messages"] = messages
        captured["backend"] = backend
        return "SELECT a.account_label FROM accounts a;"

    monkeypatch.setattr("ai.sql_generator._call_ai_backend", fake_call_ai_backend)

    knowledge_base = {
        "accounts": {
            "columns": [
                {"name": "account_id", "type": "INTEGER", "semantic_type": "id"},
                {"name": "account_label", "type": "VARCHAR(100)", "semantic_type": "name"},
            ],
            "primary_keys": ["account_id"],
            "foreign_keys": [],
        },
        "deals": {
            "columns": [
                {"name": "deal_id", "type": "INTEGER", "semantic_type": "id"},
                {"name": "account_id", "type": "INTEGER", "semantic_type": "id"},
                {"name": "deal_value", "type": "DECIMAL(12,2)", "semantic_type": "money"},
            ],
            "primary_keys": ["deal_id"],
            "foreign_keys": [
                {"column": "account_id", "referenced_table": "accounts", "referenced_column": "account_id"},
            ],
        },
    }

    sql = generate_sql(
        user_question="top 5 accounts by deal value",
        knowledge_base=knowledge_base,
        backend="local",
        normalized_question="top 5 accounts by deal value",
        intent={
            "intent_type": "ranking",
            "requested_metrics": ["deal value"],
            "requested_dimensions": ["accounts"],
            "needs_grouping": True,
            "needs_aggregation": True,
            "needs_join": True,
            "limit": 5,
        },
        retrieved_context={
            "matched_tables": [{"table": "accounts", "score": 0.95}, {"table": "deals", "score": 0.93}],
            "matched_columns": [{"table": "deals", "column": "deal_value", "score": 0.96}],
            "possible_join_paths": [
                {
                    "from_table": "accounts",
                    "to_table": "deals",
                    "path": [
                        {
                            "from_table": "accounts",
                            "from_column": "account_id",
                            "to_table": "deals",
                            "to_column": "account_id",
                            "join_condition": "accounts.account_id = deals.account_id",
                        }
                    ],
                }
            ],
            "retrieval_sources": ["kb_identifier", "relationship_context"],
            "confidence": 0.94,
        },
        query_plan={"intent": "top_n", "grouping": ["accounts"], "limit": 5},
        selected_tables=[{"table": "accounts", "confidence": 0.95}, {"table": "deals", "confidence": 0.93}],
        selected_columns=[
            {"table": "accounts", "column": "account_label", "semantic_type": "name"},
            {"table": "deals", "column": "deal_value", "semantic_type": "money"},
        ],
        measure_candidates=[{"table": "deals", "column": "deal_value", "semantic_type": "money"}],
        dimension_candidates=[{"table": "accounts", "column": "account_label", "semantic_type": "name"}],
        join_paths=[
            {
                "from_table": "accounts",
                "to_table": "deals",
                "path": [
                    {
                        "from_table": "accounts",
                        "from_column": "account_id",
                        "to_table": "deals",
                        "to_column": "account_id",
                        "join_condition": "accounts.account_id = deals.account_id",
                    }
                ],
            }
        ],
        evidence_sources=["kb_identifier", "relationship_context"],
        route_recommendation="ai_sql_required",
    )

    assert sql == "SELECT a.account_label FROM accounts a;"
    system_prompt = captured["messages"][0]["content"]
    assert "Normalized question: top 5 accounts by deal value" in system_prompt
    assert "Route recommendation: ai_sql_required" in system_prompt
    assert "Structured intent from pipeline:" in system_prompt
    assert "Retrieved dynamic context from pipeline:" in system_prompt
    assert "possible_join_paths" in system_prompt
    assert "accounts.account_id = deals.account_id" in system_prompt
    assert "Use ONLY the provided selected tables, selected columns, runtime candidates, and supplied join paths." in system_prompt
    assert "Allowed SQL generation context:" in system_prompt
    assert "allowed_tables:" in system_prompt
    assert "allowed_columns:" in system_prompt
    assert "allowed_joins:" in system_prompt
    assert "Query shape: ranking_grouped_aggregate" in system_prompt
    assert "SELECT <dimension_column>, SUM(<measure_column>) AS <result_alias>" in system_prompt
    assert "TABLE: accounts" in system_prompt
    assert "TABLE: deals" in system_prompt


def test_generate_sql_prompt_scopes_schema_to_selected_tables(monkeypatch):
    captured = {}

    def fake_call_ai_backend(messages, backend, response_format=None):
        captured["messages"] = messages
        return "SELECT a.account_label FROM accounts a;"

    monkeypatch.setattr("ai.sql_generator._call_ai_backend", fake_call_ai_backend)

    knowledge_base = {
        "accounts": {
            "columns": [{"name": "account_label", "type": "VARCHAR(100)", "semantic_type": "name"}],
            "primary_keys": ["account_id"],
            "foreign_keys": [],
        },
        "hidden_table": {
            "columns": [{"name": "hidden_value", "type": "INTEGER", "semantic_type": "numeric_candidate"}],
            "primary_keys": ["hidden_id"],
            "foreign_keys": [],
        },
    }

    generate_sql(
        user_question="show all accounts",
        knowledge_base=knowledge_base,
        backend="local",
        normalized_question="show all accounts",
        intent={"intent_type": "list"},
        retrieved_context={},
        query_plan={"intent": "list"},
        selected_tables=[{"table": "accounts", "confidence": 0.95}],
        selected_columns=[{"table": "accounts", "column": "account_label", "semantic_type": "name"}],
    )

    system_prompt = captured["messages"][0]["content"]
    assert "TABLE: accounts" in system_prompt
    assert "TABLE: hidden_table" not in system_prompt


def test_generate_sql_blocks_backend_for_clarification_route(monkeypatch):
    monkeypatch.setattr(
        "ai.sql_generator._call_ai_backend",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("AI backend should not be called for blocked route")),
    )

    with pytest.raises(ValueError, match="needs_clarification"):
        generate_sql(
            user_question="show entries",
            knowledge_base={"entries": {"columns": [{"name": "entry_id", "type": "INTEGER"}]}},
            backend="local",
            route_recommendation="needs_clarification",
        )


def test_generate_sql_safely_cleans_markdown_wrapped_select(monkeypatch):
    monkeypatch.setattr(
        "ai.sql_generator._call_ai_backend",
        lambda messages, backend, response_format=None: "```sql\nSELECT id FROM alpha_records;\n```",
    )

    sql = generate_sql(
        user_question="show records",
        knowledge_base={"alpha_records": {"columns": [{"name": "id", "type": "INTEGER"}]}},
        backend="local",
    )

    assert sql == "SELECT id FROM alpha_records;"


def test_generate_sql_rejects_explanation_after_sql(monkeypatch):
    monkeypatch.setattr(
        "ai.sql_generator._call_ai_backend",
        lambda messages, backend, response_format=None: "SELECT id FROM alpha_records;\nThis query lists rows.",
    )

    sql = generate_sql(
        user_question="show records",
        knowledge_base={"alpha_records": {"columns": [{"name": "id", "type": "INTEGER"}]}},
        backend="local",
    )

    assert sql == "SELECT id FROM alpha_records;\nThis query lists rows."


def test_generate_sql_rejects_multiple_statements(monkeypatch):
    monkeypatch.setattr(
        "ai.sql_generator._call_ai_backend",
        lambda messages, backend, response_format=None: "SELECT id FROM alpha_records; DELETE FROM alpha_records;",
    )

    sql = generate_sql(
        user_question="show records",
        knowledge_base={"alpha_records": {"columns": [{"name": "id", "type": "INTEGER"}]}},
        backend="local",
    )

    assert sql == "SELECT id FROM alpha_records; DELETE FROM alpha_records;"


def test_retry_prompt_includes_validation_error_and_join_context(monkeypatch):
    captured = {}

    def fake_call_ai_backend(messages, backend, response_format=None):
        captured["messages"] = messages
        captured["backend"] = backend
        return "SELECT a.record_name FROM alpha_records a;"

    monkeypatch.setattr("ai.sql_generator._call_ai_backend", fake_call_ai_backend)

    knowledge_base = {
        "alpha_records": {
            "columns": [{"name": "record_name", "type": "VARCHAR(100)", "semantic_type": "name"}],
            "primary_keys": ["record_name"],
            "foreign_keys": [],
        },
        "beta_events": {
            "columns": [{"name": "owner_id", "type": "INTEGER", "semantic_type": "id"}],
            "primary_keys": ["owner_id"],
            "foreign_keys": [],
        },
    }
    validation_context = {
        "selected_tables": [{"table": "alpha_records", "confidence": 0.91, "reason": "best match"}],
        "selected_columns": [{"table": "alpha_records", "column": "record_name", "confidence": 0.9, "reason": "selected"}],
        "vector_tables": ["alpha_records", "beta_events"],
        "fk_relationships": [{"table": "beta_events", "foreign_keys": [{"column": "owner_id", "referenced_table": "alpha_records", "referenced_column": "record_name"}]}],
        "join_paths": [{"from_table": "alpha_records", "to_table": "beta_events", "path": [{"from_table": "alpha_records", "to_table": "beta_events", "from_column": "record_name", "to_column": "owner_id"}]}],
        "join_conditions": ["alpha_records.record_name = beta_events.owner_id"],
        "join_skeletons": ["FROM alpha_records JOIN beta_events ON alpha_records.record_name = beta_events.owner_id"],
    }
    join_paths = validation_context["join_paths"]

    sql = generate_sql_with_retry(
        user_question="tell me pending payment by customer",
        knowledge_base=knowledge_base,
        backend="local",
        first_attempt_sql="SELECT record_name FROM LIMIT 50",
        validation_reason="SQL is missing a valid table name after FROM. Found 'LIMIT' instead.",
        validation_context=validation_context,
        join_paths=join_paths,
    )

    assert sql == "SELECT a.record_name FROM alpha_records a;"
    assert captured["backend"] == "local"
    correction_user = captured["messages"][1]["content"]
    assert "Rejected SQL" in correction_user
    assert "SELECT record_name FROM LIMIT 50" in correction_user
    assert "Validation failure" in correction_user
    assert "missing a valid table name after FROM" in correction_user
    assert "Relationship/join paths" in correction_user
    assert "Join predicates to use" in correction_user
    assert "alpha_records.record_name = beta_events.owner_id" in correction_user
    assert "FROM/JOIN candidates to use" in correction_user
    assert "FROM alpha_records JOIN beta_events ON alpha_records.record_name = beta_events.owner_id" in correction_user
    assert "alpha_records" in correction_user
    assert "Use only allowed tables and columns" in correction_user
    assert "output complete sql only" in correction_user.lower()


def test_retry_prompt_mentions_formula_constraint_when_formula_is_missing(monkeypatch):
    captured = {}

    def fake_call_ai_backend(messages, backend, response_format=None):
        captured["messages"] = messages
        return "SELECT a.account_label FROM accounts a;"

    monkeypatch.setattr("ai.sql_generator._call_ai_backend", fake_call_ai_backend)

    generate_sql_with_retry(
        user_question="pending billed amount by account",
        knowledge_base={"accounts": {"columns": [{"name": "account_label", "type": "VARCHAR(100)"}]}},
        backend="local",
        first_attempt_sql="SELECT something FROM",
        validation_reason="SQL is missing a table name after FROM.",
        formula_evidence=[],
    )

    correction_user = captured["messages"][1]["content"]
    assert "If no formula evidence is provided, do not invent a derived expression." in correction_user
