"""
semantic/semantic_mapper.py
===========================
Maps database column names to human-readable semantic types so the AI
backend understands the business meaning of each column.

How it works
------------
- Each key in SEMANTIC_MAP is a substring pattern (case-insensitive).
- If a column name contains the pattern, it gets that semantic type.
- The first matching pattern wins (order matters — more specific first).
- If nothing matches, the column gets the fallback type "general".
"""

from __future__ import annotations


# ── Semantic map ─────────────────────────────────────────────────────────────
# Keys are lowercase substrings to match against column names.
# Values are the human-readable semantic type assigned to matching columns.
# ORDER MATTERS: the first match wins, so put more specific patterns first.
SEMANTIC_MAP: dict[str, str] = {
    # Money / financial values
    "amount":   "value",
    "price":    "value",
    "cost":     "value",
    "revenue":  "value",
    "total":    "value",
    "salary":   "value",
    "balance":  "value",

    # Quantities / counts
    "qty":      "quantity",
    "quantity": "quantity",
    "units":    "quantity",
    "stock":    "quantity",

    # Customer / person
    "customer": "customer",
    "client":   "customer",
    "buyer":    "customer",
    "user":     "customer",

    # Order / transaction
    "order":    "transaction",
    "invoice":  "transaction",
    "bill":     "transaction",
    "payment":  "transaction",

    # Dates / timestamps
    "date":       "date",
    "created_at": "date",
    "updated_at": "date",
    "timestamp":  "date",

    # Status / flags
    "status": "status",
    "flag":   "status",
    "active": "status",

    # Names / labels
    "name":  "name",
    "title": "name",
    "label": "name",

    # Categories / types
    "category": "category",
    "type":     "category",
    "group":    "category",
    "class":    "category",

    # Email / contact
    "email": "email",
    "phone": "phone",
    "mobile": "phone",

    # Description / notes
    "description": "description",
    "note":        "description",
    "comment":     "description",
    "remarks":     "description",
}


def add_semantic_mapping(schema_data: dict) -> dict:
    """
    Assign a ``semantic_type`` field to every column in ``schema_data``.

    Iterates all tables and columns. For each column name, performs a
    case-insensitive substring match against SEMANTIC_MAP keys. The first
    matching key wins. Falls back to ``"general"`` when nothing matches.

    Overwrites any existing ``semantic_type`` value on the column.

    Args:
        schema_data: Dictionary produced by read_database_schema() and
                     optionally enriched by profile_database_data().

    Returns:
        The same dictionary with ``semantic_type`` added/updated on every
        column entry.
    """
    for table_data in (schema_data or {}).values():
        for column in table_data.get("columns", []):
            column_name = str(column.get("name", "")).lower()
            semantic_type = "general"  # default fallback

            for pattern, mapped_type in SEMANTIC_MAP.items():
                if pattern.lower() in column_name:
                    semantic_type = mapped_type
                    break  # first match wins

            column["semantic_type"] = semantic_type

    return schema_data
