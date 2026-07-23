"""Safe SQL execution for the SQL Generation Pipeline."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text

from sql_pipeline.execution_artifact import (
    ValidatedSQLArtifact,
    build_validated_sql_artifact,
)
from sql_pipeline.query_plan import DeterministicQueryPlan
from sql_pipeline.sql_validator import validate_sql, validate_sql_contract


@dataclass(frozen=True)
class ExecutionResult:
    success: bool
    message: str
    rows: list[dict] = field(default_factory=list)
    row_count: int = 0
    returned_row_count: int = 0
    truncated: bool = False
    reason_code: str = ""


def _sql_hash(sql: str) -> str:
    return hashlib.sha256(str(sql or "").encode("utf-8")).hexdigest()


def _artifact_from_value(value) -> ValidatedSQLArtifact:
    if isinstance(value, ValidatedSQLArtifact):
        return value
    if isinstance(value, dict):
        return ValidatedSQLArtifact.from_dict(value)
    raise TypeError("execute_validated_artifact requires a ValidatedSQLArtifact.")


def _rows_from_result(result) -> list[dict]:
    if hasattr(result, "mappings"):
        return [dict(row) for row in result.mappings().all()]

    rows = result.fetchall() if hasattr(result, "fetchall") else []
    keys = list(result.keys()) if hasattr(result, "keys") else []
    output = []
    for row in rows:
        if hasattr(row, "_mapping"):
            output.append(dict(row._mapping))
        elif isinstance(row, dict):
            output.append(row)
        else:
            output.append(dict(zip(keys, row)))
    return output


def execute_validated_artifact(artifact, engine, options: dict[str, Any] | None = None) -> ExecutionResult:
    """Execute only SQL carried by a valid SQLSense execution artifact."""
    artifact_obj = _artifact_from_value(artifact)
    options = dict(options or {})

    if not artifact_obj.safe_for_execution or not artifact_obj.executable:
        return ExecutionResult(False, "Validated SQL artifact is not safe for execution.", reason_code=artifact_obj.reason_code or "artifact_not_executable")
    if artifact_obj.sql_hash != _sql_hash(artifact_obj.sql):
        return ExecutionResult(False, "Validated SQL artifact hash mismatch.", reason_code="artifact_hash_mismatch")

    plan = DeterministicQueryPlan(**artifact_obj.deterministic_query_plan)
    query_context = plan.to_legacy_context()
    query_context.update(
        {
            "query_shape": plan.query_shape,
            "selected_join_path": plan.selected_join_path,
            "phase8a_grain_analysis": plan.grain_decision,
            "aggregate_function": plan.aggregate_function,
            "selected_metric": {"table": plan.aggregate_table, "column": plan.aggregate_column},
            "selected_dimensions": list(plan.group_by_columns),
            "selected_output_columns": list(plan.selected_columns),
            "selected_filters": list(plan.where_filters),
            "selected_having": list(plan.having_filters),
            "selected_order_by": dict(plan.order_by),
            "limit": plan.limit,
        }
    )
    validation = validate_sql_contract(
        artifact_obj.sql,
        options.get("knowledge_base") or {},
        deterministic_query_plan=plan,
        selected_join_path=artifact_obj.selected_join_path or None,
        query_context=query_context,
    )
    if not validation.valid or validation.sql_hash != artifact_obj.sql_hash:
        reason = validation.violations[0].message if validation.violations else validation.reason_code
        return ExecutionResult(False, reason or "SQL artifact revalidation failed.", reason_code=validation.reason_code or "artifact_revalidation_failed")

    safety_ok, safety_reason = validate_sql(artifact_obj.sql)
    if not safety_ok:
        return ExecutionResult(False, safety_reason, reason_code="artifact_safety_rejected")

    max_rows = int(options.get("max_rows") or 1000)
    try:
        with engine.connect() as connection:
            result = connection.execute(text(artifact_obj.sql))
            rows = _rows_from_result(result)
    except Exception:
        return ExecutionResult(False, "Database query failed.", reason_code="database_execution_failed")

    truncated = len(rows) > max_rows
    returned = rows[:max_rows] if truncated else rows
    return ExecutionResult(
        True,
        "Query executed successfully",
        rows=returned,
        row_count=len(rows),
        returned_row_count=len(returned),
        truncated=truncated,
        reason_code="execution_completed",
    )


def execute_query(
    sql: str,
    engine,
    knowledge_base: dict | None = None,
    selected_join_path: dict | None = None,
    query_context: dict | None = None,
    deterministic_query_plan=None,
) -> list[dict]:
    """Validate and execute a read-only SELECT query using a SQLAlchemy engine."""
    if deterministic_query_plan is not None:
        validation_result = validate_sql_contract(
            sql,
            knowledge_base or {},
            deterministic_query_plan=deterministic_query_plan,
            selected_join_path=selected_join_path,
            query_context=query_context,
        )
        if not validation_result.valid:
            if validation_result.violations:
                raise ValueError(validation_result.violations[0].message)
            raise ValueError(validation_result.reason_code or "SQL validation failed")
        plan = deterministic_query_plan if isinstance(deterministic_query_plan, DeterministicQueryPlan) else DeterministicQueryPlan(**deterministic_query_plan)
        artifact = build_validated_sql_artifact(sql, plan, validation_result)
        artifact_result = execute_validated_artifact(
            artifact,
            engine,
            options={"knowledge_base": knowledge_base or {}},
        )
        if not artifact_result.success:
            raise ValueError(artifact_result.message)
        return artifact_result.rows

    is_valid, reason = validate_sql(sql)
    if not is_valid:
        raise ValueError(reason)

    if not str(sql).strip().upper().startswith("SELECT"):
        raise ValueError("Only SELECT queries are permitted.")

    # If knowledge_base provided, also run structure validation.
    if knowledge_base:
        from sql_pipeline.sql_validator import validate_sql_structure
        struct_ok, struct_reason = validate_sql_structure(
            sql,
            knowledge_base,
            selected_join_path=selected_join_path,
            query_context=query_context,
        )
        if not struct_ok:
            raise ValueError(f"SQL structure invalid: {struct_reason}")

    try:
        with engine.connect() as connection:
            result = connection.execute(text(sql))
            return _rows_from_result(result)
    except ValueError:
        raise
    except Exception as exc:
        raise RuntimeError(f"Database query failed: {exc}") from exc
