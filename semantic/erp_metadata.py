"""
ERP-oriented knowledge base enrichment helpers.

This module normalizes saved metadata so the knowledge base stays useful even
when AI enrichment is noisy or partially wrong.
"""

from __future__ import annotations

from copy import deepcopy
import re


VALID_MODULES = (
    "sales",
    "purchase",
    "inventory",
    "finance",
    "HR/payroll",
    "manufacturing",
    "CRM/support",
    "master data",
)

LOW_CONFIDENCE_RELATIONSHIP_THRESHOLD = 0.75

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
    ("item_product", ("product", "item", "sku", "material", "part", "fg_item", "rm_item")),
]

_MODULE_RULES: dict[str, tuple[str, ...]] = {
    "sales": ("sale", "sales", "order", "quotation", "invoice", "dispatch", "shipment", "receivable"),
    "purchase": ("purchase", "procurement", "vendor_invoice", "supplier_invoice", "po", "grn", "payable"),
    "inventory": ("inventory", "stock", "warehouse", "bin", "movement", "receipt", "issue"),
    "finance": ("ledger", "account", "journal", "payment", "receipt", "tax", "gst", "debit", "credit", "balance"),
    "HR/payroll": ("employee", "salary", "payroll", "department", "attendance", "leave"),
    "manufacturing": ("production", "bom", "material", "work_order", "routing", "machine"),
    "CRM/support": ("customer", "lead", "opportunity", "ticket", "support", "complaint", "service"),
    "master data": ("master", "category", "product", "item", "vendor", "supplier", "customer", "employee", "department", "warehouse"),
}

_MODULE_PURPOSES: dict[str, str] = {
    "sales": "Stores sales orders, receivables, and revenue transactions.",
    "purchase": "Stores vendor purchases, procurement, and payable transactions.",
    "inventory": "Tracks stock balances, warehouse inventory, and material movement.",
    "finance": "Captures accounting entries, payments, taxes, and balances.",
    "HR/payroll": "Tracks employees, departments, salaries, and payroll records.",
    "manufacturing": "Tracks BOMs, production activity, and material consumption.",
    "CRM/support": "Tracks customers, support tickets, and service activity.",
    "master data": "Stores reference and master entities used across ERP modules.",
}

_MASTER_DATA_TABLE_NAMES = {
    "vendors",
    "vendor",
    "suppliers",
    "supplier",
    "customers",
    "customer",
    "items",
    "item",
    "products",
    "product",
    "warehouses",
    "warehouse",
    "categories",
    "category",
    "departments",
    "department",
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

_QUESTION_TEXT_RE = re.compile(r"^(what|show|list|which|where|when|how|give|display)\b", re.IGNORECASE)
_NUMERIC_ONLY_RE = re.compile(r"^[\s.+\-0-9]+$")
_DATE_ONLY_RE = re.compile(
    r"^(?:\d{4}-\d{2}-\d{2}(?:[ t]\d{2}:\d{2}(?::\d{2})?(?:\.\d+)?)?(?:z)?|/date\(\d+\)/)$",
    re.IGNORECASE,
)


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


def _humanize_identifier(value: str) -> str:
    tokens = _tokenize_identifier(value)
    if not tokens:
        return "record"
    return " ".join(tokens)


def classify_column_metadata(column_name: str, table_name: str = "") -> tuple[str, float, str]:
    """
    Return semantic type, confidence, and reason for a column.
    """
    normalized = _normalize_identifier(column_name)
    full_name = "_".join(part for part in (_normalize_identifier(table_name), normalized) if part)

    for semantic_type, patterns in _SEMANTIC_PATTERNS:
        for pattern in patterns:
            if pattern == normalized:
                return semantic_type, 0.99, f"Exact ERP pattern match for '{pattern}'."
            if pattern in normalized:
                return semantic_type, 0.95, f"Matched ERP column pattern '{pattern}'."
            if pattern in full_name:
                return semantic_type, 0.9, f"Matched ERP table context pattern '{pattern}'."

    return "general", 0.6, "No ERP-specific semantic pattern matched."


def classify_semantic_type(column_name: str, table_name: str = "") -> str:
    semantic_type, _, _ = classify_column_metadata(column_name, table_name)
    return semantic_type


def detect_table_module(table_name: str, table_data: dict) -> tuple[str, str]:
    """
    Detect a stable ERP module and default business purpose for a table.
    """
    normalized_table_name = _normalize_identifier(table_name)
    if normalized_table_name in _MASTER_DATA_TABLE_NAMES:
        return "master data", _MODULE_PURPOSES["master data"]
    if any(keyword in normalized_table_name for keyword in ("purchase", "procurement", "grn")):
        return "purchase", _MODULE_PURPOSES["purchase"]
    if any(keyword in normalized_table_name for keyword in ("payment", "ledger", "account", "tax", "gst", "journal")):
        return "finance", _MODULE_PURPOSES["finance"]
    if any(keyword in normalized_table_name for keyword in ("inventory", "stock", "warehouse", "bin")):
        return "inventory", _MODULE_PURPOSES["inventory"]
    if any(keyword in normalized_table_name for keyword in ("salary", "payroll", "employee", "department")):
        return "HR/payroll", _MODULE_PURPOSES["HR/payroll"]
    if any(keyword in normalized_table_name for keyword in ("production", "bom", "material", "work_order")):
        return "manufacturing", _MODULE_PURPOSES["manufacturing"]
    if any(keyword in normalized_table_name for keyword in ("customer", "support", "ticket", "lead", "crm")):
        return "CRM/support", _MODULE_PURPOSES["CRM/support"]
    if any(keyword in normalized_table_name for keyword in ("sales", "invoice", "order", "quotation", "dispatch", "shipment")):
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

    return best_module, _MODULE_PURPOSES.get(best_module, _MODULE_PURPOSES["master data"])


def build_rule_based_business_purpose(table_name: str, module_name: str) -> str:
    entity = _humanize_identifier(_singularize(table_name))

    if module_name == "sales":
        return f"Stores {entity} records for sales operations."
    if module_name == "purchase":
        return f"Stores {entity} records for purchase operations."
    if module_name == "inventory":
        return f"Stores {entity} records for inventory tracking."
    if module_name == "finance":
        return f"Stores {entity} records for finance workflows."
    if module_name == "HR/payroll":
        return f"Stores {entity} records for HR and payroll."
    if module_name == "manufacturing":
        return f"Stores {entity} records for manufacturing workflows."
    if module_name == "CRM/support":
        return f"Stores {entity} records for customer and support workflows."
    return f"Stores {entity} reference records."


def is_meaningful_business_text(value: str) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    if len(text) < 8:
        return False
    if _NUMERIC_ONLY_RE.fullmatch(text):
        return False
    if _DATE_ONLY_RE.fullmatch(text):
        return False
    if text.startswith("{") or text.startswith("[") or '":' in text:
        return False
    if "?" in text:
        return False
    if _QUESTION_TEXT_RE.match(text):
        return False

    alpha_words = re.findall(r"[A-Za-z]+", text)
    if len(alpha_words) < 2:
        return False
    return True


def sanitize_business_purpose(value: str, table_name: str, module_name: str) -> str:
    text = str(value or "").strip()
    if is_meaningful_business_text(text):
        return text
    return build_rule_based_business_purpose(table_name, module_name)


def sanitize_short_text(value: str, fallback: str = "") -> str:
    text = str(value or "").strip()
    if not text:
        return fallback
    if text.startswith("{") or text.startswith("["):
        return fallback
    if _NUMERIC_ONLY_RE.fullmatch(text):
        return fallback
    return text


def _column_map(table_data: dict) -> dict[str, dict]:
    return {str(column.get("name", "")): column for column in table_data.get("columns", [])}


def _sample_overlap(local_column: dict, remote_column: dict) -> int:
    local_samples = {str(value) for value in (local_column.get("sample_values") or []) if value is not None}
    remote_samples = {str(value) for value in (remote_column.get("sample_values") or []) if value is not None}
    if not local_samples or not remote_samples:
        return 0
    return len(local_samples & remote_samples)


def _relationship_target_candidates(from_column_name: str, target_table: str, target_data: dict) -> list[str]:
    target_columns = {column.get("name", "") for column in target_data.get("columns", [])}
    singular_target = _singularize(target_table)

    candidates = []
    if from_column_name in target_columns:
        candidates.append(from_column_name)
    if f"{singular_target}_id" in target_columns:
        candidates.append(f"{singular_target}_id")
    for primary_key in target_data.get("primary_keys", []):
        if primary_key in target_columns and primary_key not in candidates:
            candidates.append(primary_key)

    return candidates


def _relationship_score(
    from_table: str,
    from_column_name: str,
    local_column: dict,
    to_table: str,
    target_column_name: str,
    target_data: dict,
) -> tuple[float, list[str]]:
    normalized_column = _normalize_identifier(from_column_name)
    base_name = normalized_column[:-3] if normalized_column.endswith("_id") else normalized_column
    singular_target = _singularize(to_table)
    target_primary_keys = set(target_data.get("primary_keys", []))
    target_columns = _column_map(target_data)
    target_column = target_columns.get(target_column_name, {})
    score = 0.0
    reasons: list[str] = []
    same_name = from_column_name == target_column_name
    table_name_match = base_name == singular_target or base_name == _normalize_identifier(to_table)

    if not same_name and not table_name_match:
        return 0.0, []
    if not table_name_match and target_column_name not in target_primary_keys:
        return 0.0, []

    if same_name:
        score += 0.35
        reasons.append("same column name exists in both tables")

    if target_column_name in target_primary_keys:
        score += 0.25
        reasons.append("target column is a primary key")

    if normalized_column.endswith("_id"):
        score += 0.15
        reasons.append("column uses an _id pattern")

    if table_name_match:
        score += 0.2
        reasons.append("column name matches the target table name")

    overlap_count = _sample_overlap(local_column, target_column)
    if overlap_count:
        score += 0.2
        reasons.append(f"sample data overlaps ({overlap_count} shared values)")

    local_semantic = str(local_column.get("semantic_type", "")).lower()
    target_module = str(target_data.get("module", "")).lower()
    if local_semantic == "vendor" and target_module in {"purchase", "master data"}:
        score += 0.05
    if local_semantic == "customer" and target_module in {"sales", "crm/support", "master data"}:
        score += 0.05
    if local_semantic == "warehouse" and target_module in {"inventory", "master data"}:
        score += 0.05

    return score, reasons


def _relationship_key(relationship: dict) -> tuple[str, str, str, str]:
    return (
        relationship["from_table"],
        relationship["from_column"],
        relationship["to_table"],
        relationship["to_column"],
    )


def detect_relationships(knowledge_base: dict) -> list[dict]:
    """
    Detect ERP relationships using real FKs plus rule-based inference.
    """
    relationships: list[dict] = []
    seen: set[tuple[str, str, str, str]] = set()

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

    for from_table, from_data in knowledge_base.items():
        local_columns = _column_map(from_data)
        for from_column_name, local_column in local_columns.items():
            normalized_column = _normalize_identifier(from_column_name)
            if normalized_column in _GENERIC_RELATIONSHIP_COLUMNS:
                continue

            for to_table, to_data in knowledge_base.items():
                if from_table == to_table:
                    continue

                candidates = _relationship_target_candidates(from_column_name, to_table, to_data)
                if not candidates:
                    continue

                for target_column_name in candidates:
                    key = (from_table, from_column_name, to_table, target_column_name)
                    if key in seen:
                        continue

                    score, reasons = _relationship_score(
                        from_table,
                        from_column_name,
                        local_column,
                        to_table,
                        target_column_name,
                        to_data,
                    )
                    if score < 0.65:
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


def summarize_knowledge_base(knowledge_base: dict) -> dict:
    """
    Build a CLI-friendly summary of detected ERP metadata.
    """
    module_counts: dict[str, int] = {}
    relationship_map: dict[tuple[str, str, str, str], dict] = {}

    for table_name, table_data in (knowledge_base or {}).items():
        module_name = table_data.get("module", "master data")
        module_counts[module_name] = module_counts.get(module_name, 0) + 1

        for relationship in table_data.get("relationships", []):
            relationship_map.setdefault(_relationship_key(relationship), relationship)

    low_confidence_relationships = [
        relationship
        for relationship in relationship_map.values()
        if relationship.get("confidence", 0) < LOW_CONFIDENCE_RELATIONSHIP_THRESHOLD
    ]
    missing_relationship_tables = sorted(
        table_name
        for table_name, table_data in (knowledge_base or {}).items()
        if not table_data.get("relationships")
    )

    return {
        "modules_detected": module_counts,
        "relationship_count": len(relationship_map),
        "low_confidence_relationships": low_confidence_relationships,
        "tables_with_missing_relationships": missing_relationship_tables,
    }


def enrich_knowledge_base_for_erp(knowledge_base: dict) -> dict:
    """
    Return a copy of the knowledge base with clean ERP metadata attached.
    """
    enriched = deepcopy(knowledge_base or {})

    for table_name, table_data in enriched.items():
        table_data["foreign_keys"] = [
            foreign_key
            for foreign_key in table_data.get("foreign_keys", [])
            if not foreign_key.get("inferred")
        ]
        module_name, default_purpose = detect_table_module(table_name, table_data)
        table_data["table_name"] = table_name
        table_data["module"] = module_name
        table_data["business_purpose"] = sanitize_business_purpose(
            table_data.get("business_purpose", "") or default_purpose,
            table_name,
            module_name,
        )
        table_data["table_tokens"] = _tokenize_identifier(table_name)
        table_data["business_description"] = sanitize_short_text(
            table_data.get("business_description", ""),
            fallback=f"{_humanize_identifier(_singularize(table_name)).title()} records",
        )

        clean_questions = []
        for question in table_data.get("possible_business_questions", []):
            text = str(question or "").strip()
            if not text or len(text) > 100 or not any(ch.isalpha() for ch in text):
                continue
            clean_questions.append(text)
        table_data["possible_business_questions"] = clean_questions[:2]

        for column in table_data.get("columns", []):
            semantic_type, confidence, reason = classify_column_metadata(
                column.get("name", ""),
                table_name=table_name,
            )
            column["semantic_type"] = semantic_type
            column["confidence"] = round(confidence, 2)
            column["reason"] = reason
            column["business_description"] = sanitize_short_text(column.get("business_description", ""))
            column["business_terms"] = [
                str(term).strip()
                for term in column.get("business_terms", [])
                if str(term).strip()
            ][:3]

    detected_relationships = detect_relationships(enriched)
    related_by_table: dict[str, list[dict]] = {table_name: [] for table_name in enriched}
    for relationship in detected_relationships:
        outgoing = dict(relationship)
        outgoing["direction"] = "outgoing"
        related_by_table.setdefault(relationship["from_table"], []).append(outgoing)

        incoming = dict(relationship)
        incoming["direction"] = "incoming"
        related_by_table.setdefault(relationship["to_table"], []).append(incoming)

    for table_name, table_data in enriched.items():
        table_data["relationships"] = sorted(
            related_by_table.get(table_name, []),
            key=lambda relationship: (
                relationship.get("direction") != "outgoing",
                -relationship.get("confidence", 0),
                relationship.get("from_table", ""),
                relationship.get("to_table", ""),
                relationship.get("from_column", ""),
            ),
        )

        existing_foreign_keys = {
            (
                foreign_key.get("column", ""),
                foreign_key.get("referenced_table", ""),
                foreign_key.get("referenced_column", ""),
            ): foreign_key
            for foreign_key in table_data.get("foreign_keys", [])
        }

        for relationship in table_data["relationships"]:
            if relationship.get("direction") != "outgoing":
                continue
            key = (
                relationship["from_column"],
                relationship["to_table"],
                relationship["to_column"],
            )
            if key in existing_foreign_keys:
                existing_foreign_keys[key]["confidence"] = relationship["confidence"]
                existing_foreign_keys[key]["reason"] = relationship["reason"]
                existing_foreign_keys[key]["source"] = relationship["source"]
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
