"""Tests for generic SQL structure validation."""

import pytest

from utils.sql_validator import validate_sql_structure

RUNTIME_SCHEMA = {
    "alpha_records": {
        "columns": [
            {"name": "record_id", "type": "INTEGER", "nullable": False},
            {"name": "record_name", "type": "VARCHAR(100)", "nullable": True},
            {"name": "owner_id", "type": "INTEGER", "nullable": True},
        ],
        "primary_keys": ["record_id"],
        "foreign_keys": [],
    },
    "beta_events": {
        "columns": [
            {"name": "event_id", "type": "INTEGER", "nullable": False},
            {"name": "owner_id", "type": "INTEGER", "nullable": True},
            {"name": "event_total", "type": "DECIMAL(12,2)", "nullable": True},
        ],
        "primary_keys": ["event_id"],
        "foreign_keys": [],
    },
}


def test_accepts_valid_join_sql():
    sql = (
        "SELECT a.record_name, SUM(b.event_total) AS total_amount "
        "FROM alpha_records a "
        "JOIN beta_events b ON a.owner_id = b.owner_id "
        "GROUP BY a.record_name "
        "ORDER BY total_amount DESC LIMIT 50"
    )
    ok, reason = validate_sql_structure(sql, RUNTIME_SCHEMA)
    assert ok is True
    assert reason == "SQL structure is valid"


def test_accepts_valid_group_by_sql():
    sql = "SELECT owner_id, COUNT(*) AS total_count FROM beta_events GROUP BY owner_id LIMIT 50"
    ok, reason = validate_sql_structure(sql, RUNTIME_SCHEMA)
    assert ok is True


@pytest.mark.parametrize(
    "sql, expected",
    [
        ("SELECT record_name FROM LIMIT 50", "missing a valid table name after FROM"),
        ("SELECT record_name FROM WHERE owner_id = 1", "missing a valid table name after FROM"),
        ("SELECT record_name FROM alpha_records JOIN LIMIT 50", "missing a valid table name after JOIN"),
        ("SELECT record_name FROM alpha_records JOIN beta_events", "JOIN without an ON or USING condition"),
        ("SELECT record_name FROM alpha_records JOIN beta_events ON", "incomplete ON clause"),
    ],
)
def test_rejects_invalid_from_or_join_shapes(sql, expected):
    ok, reason = validate_sql_structure(sql, RUNTIME_SCHEMA)
    assert ok is False
    assert expected.lower() in reason.lower()


def test_rejects_unknown_table():
    ok, reason = validate_sql_structure("SELECT record_name FROM gamma_unknown LIMIT 50", RUNTIME_SCHEMA)
    assert ok is False
    assert "gamma_unknown" in reason


def test_rejects_unknown_column():
    ok, reason = validate_sql_structure("SELECT missing_field FROM alpha_records LIMIT 50", RUNTIME_SCHEMA)
    assert ok is False
    assert "missing_field" in reason


def test_rejects_unknown_qualified_column():
    sql = (
        "SELECT a.record_name, b.missing_total "
        "FROM alpha_records a JOIN beta_events b ON a.owner_id = b.owner_id LIMIT 50"
    )
    ok, reason = validate_sql_structure(sql, RUNTIME_SCHEMA)
    assert ok is False
    assert "b.missing_total" in reason


def test_rejects_invalid_alias_usage():
    sql = "SELECT z.record_name FROM alpha_records a LIMIT 50"
    ok, reason = validate_sql_structure(sql, RUNTIME_SCHEMA)
    assert ok is False
    assert "Alias or table 'z'" in reason


def test_rejects_markdown_and_preamble_after_cleanup_if_sql_missing():
    sql = "```sql\nSELECT record_name FROM LIMIT 50\n```"
    ok, reason = validate_sql_structure(sql, RUNTIME_SCHEMA)
    assert ok is False
    assert "non-SQL content outside the SELECT" in reason


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT record_name, FROM alpha_records LIMIT 50",
        "SELECT record_name FROM alpha_records WHERE owner_id = 1,",
    ],
)
def test_rejects_dangling_comma(sql):
    ok, reason = validate_sql_structure(sql, RUNTIME_SCHEMA)
    assert ok is False
    assert "dangling comma" in reason.lower() or "incomplete" in reason.lower()
