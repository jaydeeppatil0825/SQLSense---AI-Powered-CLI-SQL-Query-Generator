"""Validation for enriched KB semantic metadata."""

from __future__ import annotations

from typing import Any

from .schema_label_contract import ALLOWED_SEMANTIC_ROLES, SQL_TEXT_RE, semantic_role_from_type


class SemanticMappingValidationError(ValueError):
    """Raised when provider output is not safe semantic metadata."""


def _validate_text(value: Any, label: str, *, max_length: int = 300) -> None:
    text = str(value or "")
    if len(text) > max_length:
        raise SemanticMappingValidationError(f"{label} is too long")
    if SQL_TEXT_RE.search(text):
        raise SemanticMappingValidationError(f"{label} contains SQL text")


def _validate_terms(values: Any, label: str) -> None:
    if values is None:
        return
    if not isinstance(values, list):
        raise SemanticMappingValidationError(f"{label} must be a list")
    if len(values) > 12:
        raise SemanticMappingValidationError(f"{label} has too many values")
    for item in values:
        if not isinstance(item, str):
            raise SemanticMappingValidationError(f"{label} must contain strings")
        _validate_text(item, label, max_length=80)


def validate_enriched_knowledge_base(
    enriched: dict[str, Any],
    schema: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(enriched, dict):
        raise SemanticMappingValidationError("enrichment must be an object")
    if set(enriched) - set(schema):
        raise SemanticMappingValidationError("provider invented table")

    for table_name, table_data in enriched.items():
        schema_table = schema.get(table_name)
        if not isinstance(table_data, dict) or not isinstance(schema_table, dict):
            raise SemanticMappingValidationError("invalid table metadata")
        schema_columns = {str(column.get("name", "")) for column in schema_table.get("columns", [])}

        ai_metadata = table_data.get("ai_metadata", {})
        if isinstance(ai_metadata, dict):
            for key in ("table_description", "business_purpose", "table_role", "reason"):
                _validate_text(ai_metadata.get(key, ""), f"{table_name}.{key}")
            _validate_terms(ai_metadata.get("business_terms", []), f"{table_name}.business_terms")
            confidence = float(ai_metadata.get("confidence", 0.0) or 0.0)
            if confidence < 0 or confidence > 1:
                raise SemanticMappingValidationError("table confidence out of range")

        for key in ("business_description", "business_purpose", "table_role"):
            _validate_text(table_data.get(key, ""), f"{table_name}.{key}")
        _validate_terms(table_data.get("business_terms", []), f"{table_name}.business_terms")

        for column in table_data.get("columns", []):
            column_name = str(column.get("name", ""))
            if column_name not in schema_columns:
                raise SemanticMappingValidationError("provider invented column")
            column_ai = column.get("ai_metadata", {})
            if not isinstance(column_ai, dict):
                continue
            for key in ("business_description", "reason"):
                _validate_text(column_ai.get(key, ""), f"{table_name}.{column_name}.{key}")
            _validate_terms(column_ai.get("business_terms", []), f"{table_name}.{column_name}.business_terms")
            confidence = float(column_ai.get("confidence", 0.0) or 0.0)
            if confidence < 0 or confidence > 1:
                raise SemanticMappingValidationError("column confidence out of range")
            role = semantic_role_from_type(column_ai.get("ai_semantic_type", ""))
            if role not in ALLOWED_SEMANTIC_ROLES:
                raise SemanticMappingValidationError("invalid semantic role")

    return enriched
