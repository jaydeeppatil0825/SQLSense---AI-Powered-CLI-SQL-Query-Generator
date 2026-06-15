"""
ERP-oriented knowledge base enrichment helpers.

This module keeps ERP-specific logic out of the CLI layer. It enriches the
knowledge base with:
- stronger semantic column typing
- ERP module detection per table
- business purpose descriptions
- dynamic relationship detection with confidence and reasons
"""

from __future__ import annotations

from copy import deepcopy
import re


_SEMANTIC_PATTERNS: list[tuple[str, tuple[str, ...]]] = [
    ("document_number", ("invoice_number", "invoice_no", "bill_no", "voucher_no", "order_number", "order_no", "document_no", "document_number", "receipt_no", "challan_no")),
    ("reference_number", ("reference_number", "reference_no", "ref_number", "ref_no", "txn_ref", "transaction_ref", "external_ref")),
    ("warehouse", ("warehouse", "warehouse_id", "warehouse_code", "store", "location_bin", "bin_location")),
    ("customer", ("customer", "customer_id", "customer_name", "client", "buyer", "party_customer")),
    ("vendor", ("vendor", "supplier", "seller", "creditor", "vendor_id", "supplier_id")),
    ("employee", ("employee", "employee_id", "staff", "payroll", "department_head")),
    ("tax", ("gst", "vat", "tax", "cess", "duty", "withholding_tax")),
    ("account", ("ledger", "account", "coa", "gl_code", "gl_account", "cost_center")),
    ("status", ("status", "state", "stage", "approval", "payment_status", "order_status")),
    ("date", ("date", "month", "year", "created_at", "updated_at", "posted_at", "due_date", "invoice_date", "order_date", "payment_date")),
    ("quantity", ("quantity", "qty", "units", "stock", "available_stock", "reorder_level", "on_hand")),
    ("money", ("amount", "price", "cost", "value", "rate", "total", "balance", "salary", "wage", "revenue", "debit", "credit", "paid", "due")),
    ("item_product", ("product", "item", "sku", "material", "part", "bom_item", "fg_item", "rm_item")),
]

_MODULE_RULES: dict[str, tuple[str, ...]] = {
    "sales": ("sale", "sales", "order", "quotation", "customer_invoice", "dispatch", "shipment", "receivable"),
    "purchase": ("purchase", "procurement", "vendor_invoice", "supplier_invoice", "po", "grn", "payable"),
    "inventory": ("inventory", "stock", "warehouse", "bin", "movement", "receipt", "issue"),
    "finance": ("ledger", "account", "journal", "payment", "receipt", "tax", "gst", "debit", "credit", "balance"),
    "HR/payroll": ("employee", "salary", "payroll", "department", "attendance", "leave"),
    "manufacturing": ("production", "bom", "material", "work_order", "routing", "machine"),
    "CRM/support": ("customer", "lead", "opportunity", "ticket", "support", "complaint", "service"),
    "master data": ("master", "product", "item", "vendor", "supplier", "customer", "employee", "department", "warehouse"),
}

_MODULE_PURPOSES: dict[str, str] = {
    "sales": "Stores sales orders, receivables, and revenue-facing transactions.",
    "purchase": "Stores vendor purchases, procurement, and payable transactions.",
    "inventory": "Tracks stock positions, warehouse balances, and material movement.",
    "finance": "Captures accounting, payments, taxes, and ledger balances.",
    "HR/payroll": "Tracks employees, departments, salaries, and payroll facts.",
    "manufacturing": "Tracks BOMs, production activity, and material consumption.",
    "CRM/support": "Tracks customers, leads, support tickets, and service activity.",
    "master data": "Stores reference and master entities used by operational modules.",
}

_GENERIC_RELATIONSHIP_COLUMNS = {
    "id",
    "name",
    "code",
    "status",
    "date",
    "amount",
    "quantity",
}


def _normalize_identifier(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")


def _tokenize_identifier(value: str) -> list[str]:
    normalized = _normalize_identifier(value)
    return [token for token in normalized.split("_") if token]


def _singularize(name: str) -> str:
    value = _normalize_identifier(name)
    if value.endswith("ies") and len(value) > 3:
        return value[:-3] + "y"
    if value.endswith("ses") and len(value) > 3:
        return value[:-2]
    if value.endswith("s") and not value.endswith("ss") and len(value) > 1:
        return value[:-1]
    return value


def classify_semantic_type(column_name: str, table_name: str = "") -> str:
    """
    Classify a column into an ERP-friendly semantic type.

    The mapping intentionally prefers precise ERP classes over older generic
    labels so downstream table selection can reason about money, tax,
    warehouses, vendors, and document numbers directly.
    """
    normalized = _normalize_identifier(column_name)
    full_name = "_".join(part for part in (_normalize_identifier(table_name), normalized) if part)

    for semantic_type, patterns in _SEMANTIC_PATTERNS:
        if any(pattern in normalized or pattern in full_name for pattern in patterns):
            return semantic_type

    return "general"


def detect_table_module(table_name: str, table_data: dict) -> tuple[str, str]:
    """
    Detect the ERP module and a concise business purpose for a table.
    """
    normalized_table_name = _normalize_identifier(table_name)
    if normalized_table_name in {"vendors", "vendor", "suppliers", "supplier", "items", "item", "warehouses", "warehouse"}:
        return "master data", _MODULE_PURPOSES["master data"]
    if any(keyword in normalized_table_name for keyword in ("payment", "ledger", "account", "tax", "gst")):
        return "finance", _MODULE_PURPOSES["finance"]
    if any(keyword in normalized_table_name for keyword in ("purchase", "procurement", "grn")):
        return "purchase", _MODULE_PURPOSES["purchase"]
    if any(keyword in normalized_table_name for keyword in ("inventory", "stock", "warehouse")):
        return "inventory", _MODULE_PURPOSES["inventory"]
    if any(keyword in normalized_table_name for keyword in ("salary", "payroll", "employee", "department")):
        return "HR/payroll", _MODULE_PURPOSES["HR/payroll"]
    if any(keyword in normalized_table_name for keyword in ("production", "bom", "material")):
        return "manufacturing", _MODULE_PURPOSES["manufacturing"]
    if any(keyword in normalized_table_name for keyword in ("ledger", "account", "payment", "tax", "gst")):
        return "finance", _MODULE_PURPOSES["finance"]
    if any(keyword in normalized_table_name for keyword in ("customer", "support", "ticket", "lead")):
        return "CRM/support", _MODULE_PURPOSES["CRM/support"]
    if any(keyword in normalized_table_name for keyword in ("sales", "invoice", "order", "quotation")):
        return "sales", _MODULE_PURPOSES["sales"]

    search_space = [normalized_table_name]
    search_space.extend(_normalize_identifier(column.get("name", "")) for column in table_data.get("columns", []))
    search_text = " ".join(search_space)

    best_module = "master data"
    best_score = 0
    for module_name, keywords in _MODULE_RULES.items():
        score = sum(1 for keyword in keywords if keyword in search_text)
        if module_name == "master data":
            score = max(score - 1, 0)
        if score > best_score:
            best_module = module_name
            best_score = score

    purpose = _MODULE_PURPOSES.get(best_module, _MODULE_PURPOSES["master data"])
    return best_module, purpose


def _column_map(table_data: dict) -> dict[str, dict]:
    return {str(column.get("name", "")): column for column in table_data.get("columns", [])}


def _sample_overlap(local_column: dict, remote_column: dict) -> int:
    local_samples = {str(value) for value in (local_column.get("sample_values") or []) if value is not None}
    remote_samples = {str(value) for value in (remote_column.get("sample_values") or []) if value is not None}
    if not local_samples or not remote_samples:
        return 0
    return len(local_samples & remote_samples)


def _pick_reference_column(local_column_name: str, target_table: str, target_data: dict) -> str | None:
    target_columns = {column.get("name", "") for column in target_data.get("columns", [])}
    target_pks = list(target_data.get("primary_keys", []))
    singular_target = _singularize(target_table)
    candidates = [
        local_column_name,
        f"{singular_target}_id",
        "id",
    ]
    candidates.extend(target_pks)

    for candidate in candidates:
        if candidate in target_columns:
            return candidate

    return None


def _relationship_key(relationship: dict) -> tuple[str, str, str, str]:
    return (
        relationship["from_table"],
        relationship["from_column"],
        relationship["to_table"],
        relationship["to_column"],
    )


def detect_relationships(knowledge_base: dict) -> list[dict]:
    """
    Detect table relationships using schema, naming, and sample-value overlap.
    """
    relationships: list[dict] = []
    seen: set[tuple[str, str, str, str]] = set()

    # Preserve real foreign keys first with maximum confidence.
    for table_name, table_data in knowledge_base.items():
        for foreign_key in table_data.get("foreign_keys", []):
            relationship = {
                "from_table": table_name,
                "from_column": foreign_key.get("column", ""),
                "to_table": foreign_key.get("referenced_table", ""),
                "to_column": foreign_key.get("referenced_column", ""),
                "confidence": 0.99,
                "reason": "Detected from a real foreign key constraint.",
                "source": "foreign_key",
            }
            key = _relationship_key(relationship)
            if key not in seen:
                relationships.append(relationship)
                seen.add(key)

    # Infer the rest from table structure and sampled values.
    for from_table, from_data in knowledge_base.items():
        local_columns = _column_map(from_data)
        for from_column_name, local_column in local_columns.items():
            normalized_column = _normalize_identifier(from_column_name)
            if normalized_column in _GENERIC_RELATIONSHIP_COLUMNS:
                continue

            for to_table, to_data in knowledge_base.items():
                if from_table == to_table:
                    continue

                target_column_name = _pick_reference_column(from_column_name, to_table, to_data)
                if not target_column_name:
                    continue

                key = (from_table, from_column_name, to_table, target_column_name)
                if key in seen:
                    continue

                score = 0.0
                reasons: list[str] = []
                target_columns = _column_map(to_data)
                target_column = target_columns.get(target_column_name, {})
                target_primary_keys = set(to_data.get("primary_keys", []))
                base_name = normalized_column[:-3] if normalized_column.endswith("_id") else normalized_column
                singular_target = _singularize(to_table)

                if (
                    from_column_name not in target_columns
                    and base_name != singular_target
                    and base_name != _normalize_identifier(to_table)
                ):
                    continue

                if from_column_name in target_primary_keys or target_column_name in target_primary_keys:
                    score += 0.45
                    reasons.append("references a primary key")

                if from_column_name in target_columns:
                    score += 0.20
                    reasons.append("same column name exists in both tables")

                if normalized_column.endswith("_id"):
                    score += 0.15
                    reasons.append("column uses an _id pattern")

                if base_name == singular_target or base_name == _normalize_identifier(to_table):
                    score += 0.20
                    reasons.append("column name matches the target table name")

                overlap_count = _sample_overlap(local_column, target_column)
                if overlap_count:
                    score += 0.15
                    reasons.append(f"sample data overlaps ({overlap_count} shared values)")

                if score < 0.60:
                    continue

                relationships.append(
                    {
                        "from_table": from_table,
                        "from_column": from_column_name,
                        "to_table": to_table,
                        "to_column": target_column_name,
                        "confidence": round(min(score, 0.98), 2),
                        "reason": ". ".join(reasons).capitalize() + ".",
                        "source": "inference",
                    }
                )
                seen.add(key)

    relationships.sort(
        key=lambda relationship: (
            relationship["from_table"],
            relationship["to_table"],
            -relationship["confidence"],
            relationship["from_column"],
        )
    )
    return relationships


def enrich_knowledge_base_for_erp(knowledge_base: dict) -> dict:
    """
    Return a copy of the knowledge base enriched with ERP metadata.
    """
    enriched = deepcopy(knowledge_base or {})

    for table_name, table_data in enriched.items():
        module_name, business_purpose = detect_table_module(table_name, table_data)
        table_data["module"] = module_name
        table_data["business_purpose"] = business_purpose
        table_data["table_tokens"] = _tokenize_identifier(table_name)

        for column in table_data.get("columns", []):
            column["semantic_type"] = classify_semantic_type(
                column.get("name", ""),
                table_name=table_name,
            )

    relationships = detect_relationships(enriched)
    by_source_table: dict[str, list[dict]] = {table_name: [] for table_name in enriched}
    for relationship in relationships:
        by_source_table.setdefault(relationship["from_table"], []).append(relationship)

    for table_name, table_data in enriched.items():
        table_data["relationships"] = by_source_table.get(table_name, [])

        existing_foreign_keys = {
            (
                foreign_key.get("column", ""),
                foreign_key.get("referenced_table", ""),
                foreign_key.get("referenced_column", ""),
            ): foreign_key
            for foreign_key in table_data.get("foreign_keys", [])
        }

        for relationship in table_data["relationships"]:
            key = (
                relationship["from_column"],
                relationship["to_table"],
                relationship["to_column"],
            )
            if key in existing_foreign_keys:
                existing_foreign_keys[key].setdefault("confidence", relationship["confidence"])
                existing_foreign_keys[key].setdefault("reason", relationship["reason"])
                existing_foreign_keys[key].setdefault("source", relationship["source"])
                continue

            table_data.setdefault("foreign_keys", []).append(
                {
                    "column": relationship["from_column"],
                    "referenced_table": relationship["to_table"],
                    "referenced_column": relationship["to_column"],
                    "inferred": relationship["source"] != "foreign_key",
                    "confidence": relationship["confidence"],
                    "reason": relationship["reason"],
                    "source": relationship["source"],
                }
            )

    return enriched
