"""Tests for AI SQL generation prompt assembly and retry context."""

from ai.sql_generator import generate_sql_with_retry


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
