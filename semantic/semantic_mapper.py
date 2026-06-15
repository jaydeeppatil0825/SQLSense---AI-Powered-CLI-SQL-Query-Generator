"""
semantic/semantic_mapper.py
===========================
Maps database columns to ERP-friendly semantic types.
"""

from __future__ import annotations

from semantic.erp_metadata import classify_semantic_type


SEMANTIC_MAP: dict[str, str] = {
    # ERP document / reference identifiers
    "invoice_number": "document_number",
    "invoice_no": "document_number",
    "order_number": "document_number",
    "order_no": "document_number",
    "document_number": "document_number",
    "document_no": "document_number",
    "reference_number": "reference_number",
    "reference_no": "reference_number",
    "ref_number": "reference_number",
    "ref_no": "reference_number",

    # Finance / money
    "amount": "money",
    "price": "money",
    "cost": "money",
    "revenue": "money",
    "total": "money",
    "salary": "money",
    "balance": "money",
    "debit": "money",
    "credit": "money",
    "paid": "money",
    "due": "money",

    # Quantities / stock
    "qty": "quantity",
    "quantity": "quantity",
    "units": "quantity",
    "stock": "quantity",
    "reorder": "quantity",

    # Core ERP parties
    "customer": "customer",
    "client": "customer",
    "buyer": "customer",
    "vendor": "vendor",
    "supplier": "vendor",
    "employee": "employee",
    "staff": "employee",

    # Inventory / master entities
    "product": "item_product",
    "item": "item_product",
    "material": "item_product",
    "sku": "item_product",
    "warehouse": "warehouse",
    "ledger": "account",
    "account": "account",
    "gst": "tax",
    "tax": "tax",

    # Date / status
    "date": "date",
    "created_at": "date",
    "updated_at": "date",
    "timestamp": "date",
    "status": "status",
    "flag": "status",
    "active": "status",
}


def add_semantic_mapping(schema_data: dict) -> dict:
    """
    Assign a semantic_type to every reflected column.
    """
    for table_name, table_data in (schema_data or {}).items():
        for column in table_data.get("columns", []):
            column_name = str(column.get("name", "")).lower()
            semantic_type = "general"

            for pattern, mapped_type in SEMANTIC_MAP.items():
                if pattern in column_name:
                    semantic_type = mapped_type
                    break

            if semantic_type == "general":
                semantic_type = classify_semantic_type(
                    column.get("name", ""),
                    table_name=table_name,
                )

            column["semantic_type"] = semantic_type

    return schema_data
