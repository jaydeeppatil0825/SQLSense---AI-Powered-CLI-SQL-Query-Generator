import re
import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest
from hypothesis import given, settings, strategies as st
from sqlalchemy import Column, Integer, MetaData, String, Table, create_engine

import ai.sql_generator as sql_generator
import db.connection as connection_module
from ai.prompt_builder import build_sql_prompt
from ai.sql_generator import generate_sql
from db.data_profiler import profile_database_data
from db.query_executor import execute_query
from db.schema_reader import read_database_schema
from semantic.semantic_mapper import SEMANTIC_MAP, GENERIC_SEMANTIC_PATTERNS, add_semantic_mapping
from utils.file_utils import load_json, save_json
from utils.sql_validator import add_limit_if_missing, validate_sql


REQUIRED_DB_VARS = ("DB_HOST", "DB_USER", "DB_PASSWORD", "DB_NAME")
FORBIDDEN_KEYWORDS = ("DROP", "DELETE", "UPDATE", "INSERT", "ALTER", "TRUNCATE", "CREATE", "REPLACE")
IDENTIFIER = st.text(alphabet="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_", min_size=1, max_size=8)
JSON_SCALAR = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(min_value=-1000, max_value=1000),
    st.floats(allow_nan=False, allow_infinity=False, width=32),
    st.text(max_size=20),
)
JSON_VALUE = st.recursive(
    JSON_SCALAR,
    lambda children: st.lists(children, max_size=3) | st.dictionaries(st.text(max_size=10), children, max_size=3),
    max_leaves=10,
)


def _valid_env():
    return {
        "DB_HOST": "localhost",
        "DB_USER": "user",
        "DB_PASSWORD": "secret",
        "DB_NAME": "testdb",
        "LLM_BACKEND": "local",
    }


@given(st.frozensets(st.sampled_from(REQUIRED_DB_VARS), min_size=1))
@settings(max_examples=100)
def test_property_1_missing_required_env_vars_raise_value_error(blank_vars):
    # Feature: ai-sql-tool, Property 1: Missing required env variables always produce a ValueError
    env = _valid_env()
    for var in blank_vars:
        env[var] = " "
    create_engine_mock = MagicMock()

    with patch.dict(os.environ, env, clear=True):
        with patch.object(connection_module.sqlalchemy, "create_engine", create_engine_mock):
            with pytest.raises(ValueError) as exc_info:
                connection_module.get_engine()

    assert any(var in str(exc_info.value) for var in blank_vars)
    create_engine_mock.assert_not_called()


@given(st.integers(min_value=0, max_value=3))
@settings(max_examples=100)
def test_property_2_schema_extraction_completeness(table_count):
    # Feature: ai-sql-tool, Property 2: Schema extraction completeness
    engine = create_engine("sqlite:///:memory:")
    metadata = MetaData()
    for index in range(table_count):
        Table(
            f"table_{index}",
            metadata,
            Column("id", Integer, primary_key=True),
            Column(f"name_{index}", String(20)),
        )
    metadata.create_all(engine)

    schema = read_database_schema(engine)

    assert set(schema) == {f"table_{index}" for index in range(table_count)}
    for table_data in schema.values():
        assert set(table_data) == {"columns", "primary_keys", "foreign_keys"}


class FakeResult:
    def __init__(self, first_row=None, rows=None):
        self.first_row = first_row
        self.rows = rows or []

    def mappings(self):
        return self

    def first(self):
        return self.first_row

    def all(self):
        return self.rows


class FakeConnection:
    def __init__(self, fail_table: bool, fail_column: bool):
        self.fail_table = fail_table
        self.fail_column = fail_column

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def execute(self, statement):
        sql = str(statement)
        if self.fail_table and "COUNT(*)" in sql:
            raise RuntimeError("row count failed")
        if self.fail_column and "`bad_col`" in sql:
            raise RuntimeError("column failed")
        if "COUNT(*)" in sql:
            return FakeResult({"row_count": 1})
        if "sample_value" in sql:
            return FakeResult(rows=[{"sample_value": "sample"}])
        if "MIN(" in sql:
            return FakeResult({"min_value": 1, "max_value": 2})
        return FakeResult({"null_count": 0, "non_null_count": 1, "unique_count": 1})


class FakeEngine:
    def __init__(self, fail_table: bool, fail_column: bool):
        self.fail_table = fail_table
        self.fail_column = fail_column

    def connect(self):
        return FakeConnection(self.fail_table, self.fail_column)


@given(st.booleans(), st.booleans())
@settings(max_examples=100)
def test_property_3_profiling_errors_are_recorded_not_raised(fail_table, fail_column):
    # Feature: ai-sql-tool, Property 3: Profiling resilience - errors are recorded, not raised
    schema = {
        "users": {
            "columns": [{"name": "bad_col" if fail_column else "age", "type": "INTEGER", "nullable": True}],
            "primary_keys": [],
            "foreign_keys": [],
        }
    }

    result = profile_database_data(schema, FakeEngine(fail_table, fail_column))

    if fail_table:
        assert "row_count_error" in result["users"]
    if fail_column:
        assert "profile_error" in result["users"]["columns"][0]


@given(st.lists(IDENTIFIER, min_size=0, max_size=5))
@settings(max_examples=100)
def test_property_4_semantic_mapping_covers_every_column(column_names):
    # Feature: ai-sql-tool, Property 4: Semantic mapping covers every column with no gaps
    schema = {"table": {"columns": [{"name": name} for name in column_names]}}

    result = add_semantic_mapping(schema)

    valid_types = set(GENERIC_SEMANTIC_PATTERNS.values()) | {"general"}
    for column in result["table"]["columns"]:
        assert column["semantic_type"] in valid_types


@given(st.dictionaries(st.text(max_size=10), JSON_VALUE, max_size=5))
@settings(max_examples=100)
def test_property_5_json_persistence_round_trip(data):
    # Feature: ai-sql-tool, Property 5: JSON persistence round-trip
    with tempfile.TemporaryDirectory() as directory:
        path = os.path.join(directory, "data.json")
        save_json(data, path)
        assert load_json(path) == data


@given(st.text().filter(lambda value: bool(value.strip()) and not value.lstrip().upper().startswith("SELECT")))
@settings(max_examples=100)
def test_property_6_sql_validator_rejects_non_select_input(sql):
    # Feature: ai-sql-tool, Property 6: SQL validator rejects all non-SELECT input
    assert validate_sql(sql) == (False, "Only SELECT queries are allowed.")


@given(st.sampled_from(FORBIDDEN_KEYWORDS))
@settings(max_examples=100)
def test_property_7_sql_validator_detects_forbidden_keywords(keyword):
    # Feature: ai-sql-tool, Property 7: SQL validator detects forbidden keywords as whole words
    valid, message = validate_sql(f"SELECT {keyword} FROM users")

    assert valid is False
    assert message == f"Dangerous SQL command detected: {keyword}"


@given(st.text().map(lambda suffix: f"SELECT {suffix}"))
@settings(max_examples=100)
def test_property_8_add_limit_if_missing_is_idempotent(sql):
    # Feature: ai-sql-tool, Property 8: add_limit_if_missing is idempotent
    assert add_limit_if_missing(add_limit_if_missing(sql)) == add_limit_if_missing(sql)


@given(st.text().filter(lambda value: validate_sql(value)[0] is False))
@settings(max_examples=100)
def test_property_9_query_executor_validates_before_executing(sql):
    # Feature: ai-sql-tool, Property 9: Query executor always validates before executing
    engine = MagicMock()
    _, reason = validate_sql(sql)

    with pytest.raises(ValueError, match=re.escape(reason)):
        execute_query(sql, engine)

    engine.connect.assert_not_called()


@given(IDENTIFIER, IDENTIFIER, IDENTIFIER, IDENTIFIER)
@settings(max_examples=100)
def test_property_10_prompt_builder_includes_complete_schema_context(table, column, referenced_table, referenced_column):
    # Feature: ai-sql-tool, Property 10: Prompt builder includes complete schema context
    knowledge_base = {
        table: {
            "columns": [
                {
                    "name": column,
                    "type": "INTEGER",
                    "semantic_type": "general",
                    "sample_values": ["sample"],
                }
            ],
            "primary_keys": [column],
            "foreign_keys": [
                {
                    "column": column,
                    "referenced_table": referenced_table,
                    "referenced_column": referenced_column,
                }
            ],
        }
    }

    system_message = build_sql_prompt("show data", knowledge_base)[0]["content"]

    for expected in [table, column, "INTEGER", "general", "sample", referenced_table, referenced_column]:
        assert expected in system_message
