"""Tests for SQL structure validation."""

import pytest
from utils.sql_validator import validate_sql_structure

DEMO_KB = {
    "customers": {"columns": [{"name": "customer_id", "type": "INTEGER", "nullable": False}], "primary_keys": ["customer_id"], "foreign_keys": []},
    "orders": {"columns": [{"name": "order_id", "type": "INTEGER", "nullable": False}], "primary_keys": ["order_id"], "foreign_keys": []},
}


def test_accepts_valid_sql():
    ok, reason = validate_sql_structure("SELECT * FROM customers LIMIT 50", DEMO_KB)
    assert ok is True


def test_rejects_natural_language_preamble():
    bad = "SELECT SQL statement to show all customers: LIMIT 50"
    ok, reason = validate_sql_structure(bad, DEMO_KB)
    assert ok is False
    assert "natural language" in reason.lower() or "SQL statement" in reason.lower() or "pattern" in reason.lower()


def test_rejects_markdown_fences():
    bad = "```sql\nSELECT * FROM customers\n```"
    ok, reason = validate_sql_structure(bad, DEMO_KB)
    assert ok is False


def test_rejects_unknown_table():
    bad = "SELECT * FROM nonexistent_table LIMIT 50"
    ok, reason = validate_sql_structure(bad, DEMO_KB)
    assert ok is False
    assert "nonexistent_table" in reason


def test_rejects_missing_from():
    bad = "SELECT 1 + 1"
    ok, reason = validate_sql_structure(bad, DEMO_KB)
    assert ok is False


def test_rejects_non_select():
    bad = "INSERT INTO customers VALUES (1)"
    ok, reason = validate_sql_structure(bad, DEMO_KB)
    assert ok is False


def test_accepts_join_sql():
    sql = "SELECT c.customer_name FROM customers c JOIN orders o ON c.customer_id = o.customer_id LIMIT 50"
    ok, reason = validate_sql_structure(sql, DEMO_KB)
    assert ok is True


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM customers -- comment",
        "SELECT * FROM customers # comment",
        "SELECT /*+ hint */ * FROM customers",
        "SELECT /*!50000 1 */ FROM customers",
    ],
)
def test_rejects_sql_comments(sql):
    ok, reason = validate_sql_structure(sql, DEMO_KB)

    assert ok is False
    assert "comment" in reason.lower()


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM customers ORDER BY LIMIT 50",
        "SELECT * FROM customers ORDER BY;",
        "SELECT * FROM customers ORDER BY",
    ],
)
def test_rejects_incomplete_order_by(sql):
    ok, reason = validate_sql_structure(sql, DEMO_KB)

    assert ok is False
    assert "order by" in reason.lower()
