"""
semantic/semantic_mapper.py
===========================
Maps database columns to core structural semantic types.

This module preserves only structural or candidate semantic categories at the
knowledge-base layer. Richer business meaning belongs in AI metadata, not the
core `semantic_type`.
"""

from __future__ import annotations

from kb_pipeline.schema_facts import CORE_SEMANTIC_TYPES, apply_column_contract, classify_semantic_type

_ALLOWED_SEMANTIC_TYPES = set(CORE_SEMANTIC_TYPES)


# Backward compatibility alias for existing imports
# Now returns empty dict since pattern matching is removed
SEMANTIC_MAP: dict[str, str] = {}
GENERIC_SEMANTIC_PATTERNS: dict[str, str] = {
    "id": "id",
    "date": "date",
    "boolean": "boolean",
    "numeric_candidate": "numeric_candidate",
    "text_candidate": "text_candidate",
    "category_candidate": "category_candidate",
}


def add_semantic_mapping(schema_data: dict) -> dict:
    """
    Assign a semantic_type to every reflected column using structural rules.

    Classification priority:
    1. Structural facts (PK/FK, data type, sample values)
    2. Candidate types for unresolved numeric/text/category columns
    3. Fallback to 'unknown'

    This function does not use database-specific or ERP-specific mappings.
    """
    for table_name, table_data in (schema_data or {}).items():
        primary_keys = set(table_data.get("primary_keys", []))
        foreign_keys = {fk.get("column") for fk in table_data.get("foreign_keys", [])}
        
        for column in table_data.get("columns", []):
            column_name = str(column.get("name", "")).lower()
            column_type = str(column.get("type", "")).lower()

            is_primary_key = column_name in primary_keys
            is_foreign_key = column_name in foreign_keys
            sample_values = column.get("sample_values", [])
            structural_semantic_type = classify_semantic_type(
                column.get("name", ""),
                table_name=table_name,
                column_type=column_type,
                is_primary_key=is_primary_key,
                is_foreign_key=is_foreign_key,
                sample_values=sample_values,
            )

            # Strong structural facts must win over any previous guess.
            if structural_semantic_type in {"id", "date", "boolean"}:
                semantic_type = structural_semantic_type
            else:
                existing_semantic_type = str(column.get("semantic_type", "")).lower()
                semantic_type = existing_semantic_type if existing_semantic_type in _ALLOWED_SEMANTIC_TYPES else structural_semantic_type

            column["semantic_type"] = semantic_type
            apply_column_contract(
                column,
                primary_keys={str(value) for value in primary_keys if str(value)},
                foreign_keys={str(value) for value in foreign_keys if str(value)},
            )

    return schema_data
