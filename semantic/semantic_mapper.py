"""
semantic/semantic_mapper.py
===========================
Maps database columns to generic semantic types.

This module provides generic semantic classification for database columns
based on column name patterns, data types, and sample values. It does not
contain database-specific or ERP-specific table mappings.

Generic semantic categories:
- money: Financial amounts, prices, costs
- quantity: Counts, measurements, stock levels
- date: Temporal information
- status: State flags, active/inactive indicators
- id: Primary keys, identifiers
- name: Text labels, descriptions
- text: General text fields
- boolean: True/false flags
- percentage: Ratios, percentages
- code: Reference codes, external identifiers
- general: Default fallback type
"""

from __future__ import annotations

from semantic.erp_metadata import classify_semantic_type

_ALLOWED_SEMANTIC_TYPES = {
    "money",
    "quantity",
    "date",
    "status",
    "id",
    "name",
    "text",
    "boolean",
    "percentage",
    "code",
    "general",
    "numeric_candidate",
    "text_candidate",
    "category_candidate",
}


# Backward compatibility alias for existing imports
# Now returns empty dict since pattern matching is removed
SEMANTIC_MAP: dict[str, str] = {}


def add_semantic_mapping(schema_data: dict) -> dict:
    """
    Assign a semantic_type to every reflected column using structural rules.

    Classification priority:
    1. Existing semantic_type from AI enrichment (if present)
    2. Structural rules (PK/FK, data type, sample values)
    3. Fallback to 'general'

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
                if existing_semantic_type in _ALLOWED_SEMANTIC_TYPES - {"general", "numeric_candidate", "text_candidate", "category_candidate"}:
                    semantic_type = existing_semantic_type
                else:
                    semantic_type = structural_semantic_type

            column["semantic_type"] = semantic_type
            column["is_date"] = bool(semantic_type == "date")

    return schema_data
