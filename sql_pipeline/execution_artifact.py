"""Validated SQL artifact boundary for executor input."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

from sql_pipeline.query_plan import DeterministicQueryPlan
from sql_pipeline.validation_result import SQLValidationResult

EXECUTION_ARTIFACT_CONTRACT_VERSION = "validated-sql-artifact-v1"


def _sql_hash(sql: str) -> str:
    return hashlib.sha256(str(sql or "").encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ValidatedSQLArtifact:
    execution_artifact_contract_version: str = EXECUTION_ARTIFACT_CONTRACT_VERSION
    sql: str = ""
    sql_hash: str = ""
    deterministic_query_plan: dict[str, Any] = field(default_factory=dict)
    validation_result: dict[str, Any] = field(default_factory=dict)
    query_shape: str = ""
    route: str = ""
    executable: bool = False
    schema_fingerprint: str = ""
    graph_fingerprint: str = ""
    selected_join_path: dict[str, Any] = field(default_factory=dict)
    grain_decision: dict[str, Any] = field(default_factory=dict)
    selected_join_path_verified: bool = False
    grain_verified: bool = False
    created_by: str = "sqlsense_generator"
    safe_for_execution: bool = False
    reason_code: str = ""
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "execution_artifact_contract_version": self.execution_artifact_contract_version,
            "sql": self.sql,
            "sql_hash": self.sql_hash,
            "deterministic_query_plan": dict(self.deterministic_query_plan),
            "validation_result": dict(self.validation_result),
            "query_shape": self.query_shape,
            "route": self.route,
            "executable": self.executable,
            "schema_fingerprint": self.schema_fingerprint,
            "graph_fingerprint": self.graph_fingerprint,
            "selected_join_path": dict(self.selected_join_path),
            "grain_decision": dict(self.grain_decision),
            "selected_join_path_verified": self.selected_join_path_verified,
            "grain_verified": self.grain_verified,
            "created_by": self.created_by,
            "safe_for_execution": self.safe_for_execution,
            "reason_code": self.reason_code,
            "warnings": list(self.warnings),
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ValidatedSQLArtifact":
        allowed = set(cls.__dataclass_fields__)
        return cls(**{key: item for key, item in dict(value or {}).items() if key in allowed})


def _unsafe_artifact(
    sql: str,
    plan: DeterministicQueryPlan,
    validation: SQLValidationResult,
    reason_code: str,
) -> ValidatedSQLArtifact:
    return ValidatedSQLArtifact(
        sql=sql,
        sql_hash=validation.sql_hash,
        deterministic_query_plan=plan.to_dict(),
        validation_result=validation.to_dict(),
        query_shape=plan.query_shape,
        route=plan.route,
        executable=False,
        schema_fingerprint=plan.schema_fingerprint,
        graph_fingerprint=plan.graph_fingerprint,
        selected_join_path=dict(plan.selected_join_path),
        grain_decision=dict(plan.grain_decision),
        selected_join_path_verified=validation.selected_join_path_verified,
        grain_verified=validation.grain_verified,
        safe_for_execution=False,
        reason_code=reason_code,
        warnings=list(validation.warnings),
    )


def build_validated_sql_artifact(
    sql: str,
    deterministic_query_plan: DeterministicQueryPlan,
    validation_result: SQLValidationResult,
) -> ValidatedSQLArtifact:
    """Create the only artifact the hardened executor should prefer."""
    plan = deterministic_query_plan
    validation = validation_result

    if not validation.valid:
        return _unsafe_artifact(sql, plan, validation, "validation_failed")
    if validation.sql_hash != _sql_hash(sql):
        return _unsafe_artifact(sql, plan, validation, "validation_hash_invalid")
    if not plan.executable:
        return _unsafe_artifact(sql, plan, validation, "plan_not_executable")
    if validation.query_shape and validation.query_shape != plan.query_shape:
        return _unsafe_artifact(sql, plan, validation, "query_shape_mismatch")
    if validation.route and validation.route != plan.route:
        return _unsafe_artifact(sql, plan, validation, "route_mismatch")
    if validation.schema_fingerprint and plan.schema_fingerprint and validation.schema_fingerprint != plan.schema_fingerprint:
        return _unsafe_artifact(sql, plan, validation, "schema_fingerprint_mismatch")
    if validation.graph_fingerprint and plan.graph_fingerprint and validation.graph_fingerprint != plan.graph_fingerprint:
        return _unsafe_artifact(sql, plan, validation, "graph_fingerprint_mismatch")
    if plan.selected_join_path and not validation.selected_join_path_verified:
        return _unsafe_artifact(sql, plan, validation, "join_path_not_verified")
    if plan.query_shape == "joined_aggregate" and len(plan.join_edges) == 2 and not validation.grain_verified:
        return _unsafe_artifact(sql, plan, validation, "grain_not_verified")

    return ValidatedSQLArtifact(
        sql=sql,
        sql_hash=validation.sql_hash,
        deterministic_query_plan=plan.to_dict(),
        validation_result=validation.to_dict(),
        query_shape=plan.query_shape,
        route=plan.route,
        executable=True,
        schema_fingerprint=plan.schema_fingerprint,
        graph_fingerprint=plan.graph_fingerprint,
        selected_join_path=dict(plan.selected_join_path),
        grain_decision=dict(plan.grain_decision),
        selected_join_path_verified=validation.selected_join_path_verified,
        grain_verified=validation.grain_verified,
        safe_for_execution=True,
        reason_code="artifact_validated",
        warnings=list(validation.warnings),
    )
