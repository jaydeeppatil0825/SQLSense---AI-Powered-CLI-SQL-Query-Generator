import ast
from pathlib import Path
from unittest.mock import MagicMock

from sqlalchemy import create_engine, text

from sql_pipeline.execution_artifact import (
    EXECUTION_ARTIFACT_CONTRACT_VERSION,
    ValidatedSQLArtifact,
    build_validated_sql_artifact,
)
from sql_pipeline.query_executor import execute_query, execute_validated_artifact
from sql_pipeline.query_plan import build_deterministic_query_plan
from sql_pipeline.sql_validator import validate_sql_contract


KB = {
    "users": {
        "columns": [
            {"name": "id", "type": "INTEGER"},
            {"name": "name", "type": "VARCHAR(50)"},
        ],
        "primary_keys": ["id"],
        "foreign_keys": [],
    }
}


def _context():
    return {
        "query_shape": "single_table_list",
        "route_recommendation": "deterministic_sql_required",
        "selected_tables": [{"table": "users"}],
        "selected_table_names": ["users"],
        "selected_output_columns": [
            {"kind": "column", "table": "users", "column": "id", "expression": "users.id", "alias": "users__id"},
            {"kind": "column", "table": "users", "column": "name", "expression": "users.name", "alias": "users__name"},
        ],
        "limit": 50,
        "schema_fingerprint": "schema-1",
        "graph_fingerprint": "graph-1",
    }


def _sql():
    return "SELECT users.id AS users__id, users.name AS users__name FROM users LIMIT 50"


def _artifact(sql=None):
    sql = sql or _sql()
    plan = build_deterministic_query_plan(_context())
    validation = validate_sql_contract(sql, KB, deterministic_query_plan=plan)
    return build_validated_sql_artifact(sql, plan, validation)


def _engine(row_count=3):
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE users (id INTEGER, name TEXT)"))
        connection.execute(
            text("INSERT INTO users VALUES (:id, :name)"),
            [{"id": index, "name": f"user-{index}"} for index in range(row_count)],
        )
    return engine


def test_validated_sql_artifact_contract_creation():
    artifact = _artifact()

    assert artifact.execution_artifact_contract_version == EXECUTION_ARTIFACT_CONTRACT_VERSION
    assert artifact.safe_for_execution is True
    assert artifact.executable is True
    assert artifact.query_shape == "single_table_list"
    assert artifact.route == "deterministic_sql_required"
    assert artifact.schema_fingerprint == "schema-1"
    assert artifact.sql_hash == artifact.validation_result["sql_hash"]
    assert "password" not in artifact.to_dict()


def test_artifact_rejects_validation_failure_and_non_executable_plan():
    plan = build_deterministic_query_plan({**_context(), "route_recommendation": "cannot_plan_safely"})
    validation = validate_sql_contract(_sql(), KB, deterministic_query_plan=plan)
    artifact = build_validated_sql_artifact(_sql(), plan, validation)

    assert artifact.safe_for_execution is False
    assert artifact.reason_code in {"validation_failed", "plan_not_executable"}


def test_artifact_rejects_sql_hash_mismatch():
    artifact = _artifact().to_dict()
    artifact["sql"] = "SELECT users.name AS users__name FROM users LIMIT 50"

    result = execute_validated_artifact(ValidatedSQLArtifact.from_dict(artifact), MagicMock())

    assert result.success is False
    assert result.reason_code == "artifact_hash_mismatch"


def test_execute_validated_artifact_succeeds_and_truncates_rows():
    result = execute_validated_artifact(_artifact(), _engine(row_count=3), options={"knowledge_base": KB, "max_rows": 2})

    assert result.success is True
    assert result.row_count == 3
    assert result.returned_row_count == 2
    assert result.truncated is True
    assert result.rows == [{"users__id": 0, "users__name": "user-0"}, {"users__id": 1, "users__name": "user-1"}]


def test_execute_validated_artifact_rejects_without_connecting():
    artifact = _artifact().to_dict()
    artifact["safe_for_execution"] = False
    engine = MagicMock()

    result = execute_validated_artifact(artifact, engine, options={"knowledge_base": KB})

    assert result.success is False
    engine.connect.assert_not_called()


def test_legacy_execute_query_uses_artifact_path_when_plan_is_supplied():
    rows = execute_query(
        _sql(),
        _engine(row_count=1),
        knowledge_base=KB,
        deterministic_query_plan=build_deterministic_query_plan(_context()),
    )

    assert rows == [{"users__id": 0, "users__name": "user-0"}]


def test_executor_import_boundaries():
    source = Path("sql_pipeline/query_executor.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)

    forbidden = {
        "query_pipeline.context_retriever",
        "query_pipeline.planner.role_resolver",
        "query_pipeline.planner.join_resolver",
        "query_pipeline.planner.phase7_bfs_join_resolver",
        "query_pipeline.planner.grain_analyzer",
        "kb_pipeline.semantic_providers.provider_chain",
        "sql_pipeline.deterministic_sql_generator",
    }
    assert not (imports & forbidden)
