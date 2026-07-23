"""Canonical SQL validation result contract."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

SQL_VALIDATOR_CONTRACT_VERSION = "sql-validator-v1"


def _sql_hash(sql: str) -> str:
    return hashlib.sha256(str(sql or "").encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class SQLValidationViolation:
    code: str
    message: str
    clause: str = ""
    severity: str = "error"
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "clause": self.clause,
            "severity": self.severity,
            "evidence": dict(self.evidence),
        }


@dataclass(frozen=True)
class SQLValidationResult:
    validator_contract_version: str = SQL_VALIDATOR_CONTRACT_VERSION
    valid: bool = False
    status: str = "rejected"
    reason_code: str = ""
    violations: list[SQLValidationViolation] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    query_shape: str = ""
    route: str = ""
    sql_hash: str = ""
    plan_contract_version: str = ""
    schema_fingerprint: str = ""
    graph_fingerprint: str = ""
    validated_tables: list[str] = field(default_factory=list)
    validated_columns: list[str] = field(default_factory=list)
    validated_joins: list[dict[str, Any]] = field(default_factory=list)
    validated_filters: list[dict[str, Any]] = field(default_factory=list)
    validated_grouping: list[dict[str, Any]] = field(default_factory=list)
    validated_having: list[dict[str, Any]] = field(default_factory=list)
    validated_ordering: dict[str, Any] = field(default_factory=dict)
    validated_limit: int | None = None
    selected_join_path_verified: bool = False
    grain_verified: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "validator_contract_version": self.validator_contract_version,
            "valid": self.valid,
            "status": self.status,
            "reason_code": self.reason_code,
            "violations": [violation.to_dict() for violation in self.violations],
            "warnings": list(self.warnings),
            "query_shape": self.query_shape,
            "route": self.route,
            "sql_hash": self.sql_hash,
            "plan_contract_version": self.plan_contract_version,
            "schema_fingerprint": self.schema_fingerprint,
            "graph_fingerprint": self.graph_fingerprint,
            "validated_tables": list(self.validated_tables),
            "validated_columns": list(self.validated_columns),
            "validated_joins": [dict(item) for item in self.validated_joins],
            "validated_filters": [dict(item) for item in self.validated_filters],
            "validated_grouping": [dict(item) for item in self.validated_grouping],
            "validated_having": [dict(item) for item in self.validated_having],
            "validated_ordering": dict(self.validated_ordering),
            "validated_limit": self.validated_limit,
            "selected_join_path_verified": self.selected_join_path_verified,
            "grain_verified": self.grain_verified,
        }


def passed_validation_result(sql: str, **fields: Any) -> SQLValidationResult:
    return SQLValidationResult(
        valid=True,
        status="passed",
        reason_code=fields.pop("reason_code", "validation_passed"),
        sql_hash=_sql_hash(sql),
        **fields,
    )


def rejected_validation_result(sql: str, code: str, message: str, *, clause: str = "", **fields: Any) -> SQLValidationResult:
    return SQLValidationResult(
        valid=False,
        status="rejected",
        reason_code=code,
        sql_hash=_sql_hash(sql),
        violations=[SQLValidationViolation(code=code, message=message, clause=clause)],
        **fields,
    )
