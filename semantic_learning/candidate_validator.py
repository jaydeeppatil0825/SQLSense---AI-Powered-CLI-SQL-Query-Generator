"""Deterministic validation for learned-alias candidates."""

from __future__ import annotations

from typing import Any

from semantic_learning.learned_aliases import LearnedAliasCandidate

_NUMERIC_MARKERS = ("int", "decimal", "numeric", "float", "double", "real")
_DATE_MARKERS = ("date", "time", "timestamp")
_VALID_TARGETS = {"table", "column", "value", "metric", "dimension"}
_VALID_ROLES = {"table_entity", "metric", "dimension", "filter_value"}


def _column(knowledge_base: dict[str, Any], table: str, column: str) -> dict[str, Any] | None:
    for item in (knowledge_base.get(table, {}) or {}).get("columns", []) or []:
        if str(item.get("name") or "") == column:
            return item
    return None


def _is_numeric(column: dict[str, Any]) -> bool:
    text = f"{column.get('type', '')} {column.get('semantic_type', '')}".lower()
    return any(marker in text for marker in _NUMERIC_MARKERS) and "id" not in text


def _is_dimension(column: dict[str, Any]) -> bool:
    text = f"{column.get('type', '')} {column.get('semantic_type', '')}".lower()
    roles = column.get("planner_roles") if isinstance(column.get("planner_roles"), dict) else {}
    return bool(roles.get("dimension_candidate") or roles.get("filter_candidate")) or any(
        marker in text for marker in ("char", "text", "date", "status", "category", "type", "name", "city", "method")
    )


def validate_candidate(
    candidate: LearnedAliasCandidate,
    knowledge_base: dict[str, Any],
    *,
    schema_fingerprint: str = "",
    approved_aliases: list[LearnedAliasCandidate] | None = None,
) -> LearnedAliasCandidate:
    item = LearnedAliasCandidate.from_dict(candidate.to_dict())
    if item.contract_version != "learned-alias-v1":
        item.status = "rejected"
        item.rejection_reason = "unsupported_contract_version"
        return item
    if not item.normalized_phrase:
        item.status = "rejected"
        item.rejection_reason = "empty_or_sensitive_phrase"
        return item
    if item.target_type not in _VALID_TARGETS or item.semantic_role not in _VALID_ROLES:
        item.status = "rejected"
        item.rejection_reason = "invalid_target_or_role"
        return item
    if schema_fingerprint and item.schema_fingerprint != schema_fingerprint:
        item.status = "rejected"
        item.rejection_reason = "schema_fingerprint_mismatch"
        return item
    if item.table not in knowledge_base:
        item.status = "rejected"
        item.rejection_reason = "target_table_missing"
        return item
    if item.target_type == "table":
        return item

    column = _column(knowledge_base, item.table, item.column)
    if column is None:
        item.status = "rejected"
        item.rejection_reason = "target_column_missing"
        return item
    if item.semantic_role == "metric" and not _is_numeric(column):
        item.status = "rejected"
        item.rejection_reason = "metric_not_numeric_eligible"
        return item
    if item.semantic_role == "dimension" and not _is_dimension(column):
        item.status = "rejected"
        item.rejection_reason = "dimension_not_structurally_valid"
        return item
    if item.target_type == "value":
        samples = (((column.get("profile_facts") or {}).get("sample_values") or []) if isinstance(column.get("profile_facts"), dict) else [])
        if samples and str(item.value) not in {str(value) for value in samples}:
            item.status = "rejected"
            item.rejection_reason = "value_not_sample_backed"
            return item
    conflicts = [
        alias for alias in approved_aliases or []
        if alias.normalized_phrase == item.normalized_phrase
        and (alias.table, alias.column, alias.value) != (item.table, item.column, item.value)
        and alias.support_count >= item.support_count
    ]
    if conflicts:
        item.status = "pending"
        item.rejection_reason = "conflicts_with_approved_alias"
    return item
