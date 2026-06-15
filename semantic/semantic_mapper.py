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
}


# Generic semantic type patterns based on column name patterns
# These are universal patterns that apply across any database, not ERP-specific
GENERIC_SEMANTIC_PATTERNS: dict[str, str] = {
    # Money/financial patterns (universal)
    "amount": "money",
    "price": "money",
    "cost": "money",
    "total": "money",
    "balance": "money",
    "value": "money",
    "rate": "money",
    "fee": "money",
    "charge": "money",
    "tax": "money",
    "discount": "money",
    "salary": "money",
    "wage": "money",
    "commission": "money",
    "revenue": "money",
    "income": "money",
    "expense": "money",
    "profit": "money",
    "loss": "money",
    "debit": "money",
    "credit": "money",
    "paid": "money",
    "due": "money",
    "outstanding": "money",
    "pending": "money",
    "outstanding_balance": "money",
    "amount_due": "money",
    "total_amount": "money",
    "final_amount": "money",
    "net_amount": "money",
    "line_total": "money",

    # Quantity/measurement patterns (universal)
    "quantity": "quantity",
    "qty": "quantity",
    "count": "quantity",
    "number": "quantity",
    "units": "quantity",
    "stock": "quantity",
    "level": "quantity",
    "on_hand": "quantity",
    "available": "quantity",
    "reserved": "quantity",
    "ordered": "quantity",
    "shipped": "quantity",
    "received": "quantity",
    "produced": "quantity",
    "consumed": "quantity",
    "weight": "quantity",
    "volume": "quantity",
    "length": "quantity",
    "width": "quantity",
    "height": "quantity",
    "size": "quantity",
    "capacity": "quantity",
    "quantity_on_hand": "quantity",
    "available_stock": "quantity",
    "stock_qty": "quantity",
    "reorder_level": "quantity",
    "minimum_stock": "quantity",
    "min_stock": "quantity",

    # Date/time patterns (universal)
    "date": "date",
    "time": "date",
    "datetime": "date",
    "timestamp": "date",
    "created_at": "date",
    "created_date": "date",
    "updated_at": "date",
    "updated_date": "date",
    "modified_at": "date",
    "modified_date": "date",
    "start_date": "date",
    "end_date": "date",
    "from_date": "date",
    "to_date": "date",
    "due_date": "date",
    "expiry_date": "date",
    "effective_date": "date",
    "birth_date": "date",
    "hire_date": "date",
    "join_date": "date",
    "posted_at": "date",
    "month": "date",
    "year": "date",
    "quarter": "date",
    "invoice_date": "date",
    "order_date": "date",
    "payment_date": "date",
    "last_updated": "date",
    "joining_date": "date",

    # Status/state patterns (universal)
    "status": "status",
    "state": "status",
    "flag": "status",
    "active": "status",
    "inactive": "status",
    "enabled": "status",
    "disabled": "status",
    "deleted": "status",
    "archived": "status",
    "approved": "status",
    "rejected": "status",
    "pending": "status",
    "completed": "status",
    "cancelled": "status",
    "canceled": "status",
    "failed": "status",
    "success": "status",
    "error": "status",
    "valid": "status",
    "invalid": "status",
    "verified": "status",
    "confirmed": "status",
    "payment_status": "status",
    "order_status": "status",
    "ticket_status": "status",
    "approval": "status",
    "stage": "status",

    # ID/identifier patterns (universal)
    "id": "id",
    "identifier": "id",
    "uuid": "id",
    "guid": "id",
    "key": "id",
    "code": "code",
    "ref": "code",
    "reference": "code",
    "number": "id",
    "no": "id",
    "seq": "id",
    "sequence": "id",

    # Name/text patterns (universal)
    "name": "name",
    "title": "name",
    "description": "text",
    "desc": "text",
    "note": "text",
    "notes": "text",
    "comment": "text",
    "comments": "text",
    "remark": "text",
    "remarks": "text",
    "text": "text",
    "content": "text",
    "body": "text",
    "message": "text",

    # Boolean patterns (universal) - prefix patterns are most specific
    "is_active": "boolean",
    "is_enabled": "boolean",
    "is_disabled": "boolean",
    "is_deleted": "boolean",
    "is_verified": "boolean",
    "is_approved": "boolean",
    "is_rejected": "boolean",
    "is_public": "boolean",
    "is_private": "boolean",
    "is_locked": "boolean",
    "is_visible": "boolean",
    "is_hidden": "boolean",
    "has_": "boolean",
    "can_": "boolean",
    "should_": "boolean",
    "must_": "boolean",
    "enabled": "boolean",
    "disabled": "boolean",
    "locked": "boolean",
    "unlocked": "boolean",
    "verified": "boolean",
    "unverified": "boolean",
    "visible": "boolean",
    "hidden": "boolean",
    "public": "boolean",
    "private": "boolean",

    # Percentage patterns (universal)
    "percent": "percentage",
    "percentage": "percentage",
    "pct": "percentage",
    "ratio": "percentage",
    "rate": "percentage",
}


# Backward compatibility alias for existing imports
# This points to the generic patterns, not ERP-specific mappings
SEMANTIC_MAP = GENERIC_SEMANTIC_PATTERNS


def _matches_pattern(column_name: str, pattern: str) -> bool:
    normalized_name = column_name.lower()
    normalized_pattern = pattern.lower()
    tokens = [token for token in normalized_name.replace("-", "_").split("_") if token]

    if normalized_pattern in {"id", "no", "ref", "key"}:
        return (
            normalized_pattern in tokens
            or normalized_name.endswith(f"_{normalized_pattern}")
            or normalized_name.startswith(f"{normalized_pattern}_")
        )

    if normalized_pattern.endswith("_"):
        return normalized_name.startswith(normalized_pattern)

    return normalized_pattern in normalized_name


def add_semantic_mapping(schema_data: dict) -> dict:
    """
    Assign a semantic_type to every reflected column using generic patterns.
    
    Classification priority:
    1. Existing semantic_type from AI enrichment (if present)
    2. Generic semantic patterns from column name (longer patterns first for specificity)
    3. Data type inference
    4. Sample value analysis
    5. Fallback to 'general'
    
    This function does not use database-specific or ERP-specific mappings.
    """
    for table_name, table_data in (schema_data or {}).items():
        for column in table_data.get("columns", []):
            column_name = str(column.get("name", "")).lower()
            column_type = str(column.get("type", "")).lower()
            semantic_type = "general"

            # Priority 1: Use existing semantic_type from AI enrichment if present
            existing_semantic_type = str(column.get("semantic_type", "")).lower()
            if existing_semantic_type in _ALLOWED_SEMANTIC_TYPES - {"general"}:
                continue

            # Priority 2: Match against generic semantic patterns (sort by length for specificity)
            patterns_sorted = sorted(GENERIC_SEMANTIC_PATTERNS.items(), key=lambda x: -len(x[0]))
            for pattern, mapped_type in patterns_sorted:
                if _matches_pattern(column_name, pattern):
                    semantic_type = mapped_type
                    break

            # Priority 3: Data type inference for common types
            if semantic_type == "general":
                semantic_type = _infer_from_data_type(column_type)

            # Priority 4: Sample value analysis
            if semantic_type == "general":
                sample_values = column.get("sample_values", [])
                if sample_values:
                    semantic_type = _infer_from_sample_values(sample_values)

            # Priority 5: Fallback to the shared generic classifier
            if semantic_type == "general":
                semantic_type = classify_semantic_type(
                    column.get("name", ""),
                    table_name=table_name,
                )

            column["semantic_type"] = semantic_type

    return schema_data


def _infer_from_data_type(column_type: str) -> str:
    """
    Infer semantic type from database column type.
    
    Uses generic type patterns that apply across all databases.
    """
    # Integer types - could be id, quantity, or boolean
    if column_type in ("int", "integer", "bigint", "smallint", "tinyint"):
        return "id"  # Default to id, can be refined by name patterns
    
    # Decimal/numeric types - likely money or quantity
    if column_type in ("decimal", "numeric", "float", "double", "real"):
        return "money"  # Default to money, can be refined by name patterns
    
    # String types - could be name, text, code, or id
    if column_type in ("varchar", "char", "text", "string", "nvarchar", "nchar"):
        return "text"  # Default to text, can be refined by name patterns
    
    # Date/time types
    if column_type in ("date", "datetime", "timestamp", "time"):
        return "date"
    
    # Boolean types
    if column_type in ("boolean", "bool", "bit"):
        return "boolean"
    
    # JSON/Binary types
    if column_type in ("json", "jsonb", "blob", "binary"):
        return "text"
    
    return "general"


def _infer_from_sample_values(sample_values: list) -> str:
    """
    Infer semantic type from sample values.
    
    Uses generic value patterns that apply across all databases.
    """
    if not sample_values:
        return "general"
    
    # Check for boolean-like values
    bool_values = {"true", "false", "yes", "no", "1", "0"}
    if all(str(v).lower() in bool_values for v in sample_values if v is not None):
        return "boolean"
    
    # Check for percentage-like values
    if all(isinstance(v, (int, float)) and 0 <= v <= 100 for v in sample_values if v is not None):
        return "percentage"
    
    # Check for date-like values
    import re
    date_pattern = re.compile(r"^\d{4}-\d{2}-\d{2}")
    if all(date_pattern.match(str(v)) for v in sample_values[:5] if v is not None):
        return "date"
    
    # Check for money-like values (typically have 2 decimal places)
    money_count = 0
    for v in sample_values[:10]:
        if v is not None and isinstance(v, (int, float)):
            if abs(v - round(v, 2)) < 0.01:  # Has 2 or fewer decimal places
                money_count += 1
    if money_count >= len(sample_values[:10]) * 0.7:
        return "money"
    
    return "general"
