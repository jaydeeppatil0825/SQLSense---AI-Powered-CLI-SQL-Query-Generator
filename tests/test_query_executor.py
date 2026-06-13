from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine, text

from db.query_executor import execute_query


def test_invalid_sql_raises_value_error_before_connecting():
    engine = MagicMock()

    with pytest.raises(ValueError, match="Only SELECT queries are allowed"):
        execute_query("DELETE FROM users", engine)

    engine.connect.assert_not_called()


def test_execute_query_returns_list_of_dicts():
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE users (id INTEGER, name TEXT)"))
        connection.execute(text("INSERT INTO users VALUES (1, 'Asha'), (2, 'Ben')"))

    rows = execute_query("SELECT id, name FROM users ORDER BY id", engine)

    assert rows == [{"id": 1, "name": "Asha"}, {"id": 2, "name": "Ben"}]


def test_execute_query_returns_empty_list_for_zero_rows():
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE users (id INTEGER, name TEXT)"))

    assert execute_query("SELECT id, name FROM users", engine) == []


def test_database_exception_is_wrapped_in_runtime_error():
    engine = create_engine("sqlite:///:memory:")

    with pytest.raises(RuntimeError, match="Database query failed"):
        execute_query("SELECT * FROM missing_table", engine)
