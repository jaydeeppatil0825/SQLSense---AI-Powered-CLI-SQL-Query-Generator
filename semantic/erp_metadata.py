"""
Generic knowledge-base enrichment helpers.

This module keeps the saved knowledge base consistent and useful without
injecting database-specific business assumptions. The knowledge base remains
the source of truth; this layer only adds generic semantic typing, table
metadata, and relationship inference.
"""

from __future__ import annotations

from copy import deepcopy
import re


VALID_MODULES = (
    "reference",
    "transaction",
    "event",
    "snapshot",
    "general",
)

LOW_CONFIDENCE_RELATIONSHIP_THRESHOLD = 0.75
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

_SEMANTIC_PATTERNS: list[tuple[str, tuple[str, ...]]] = [
    ("id", ("id", "identifier", "uuid", "guid", "pk")),
    ("code", ("code", "ref", "reference", "number", "no", "sequence", "seq")),
    ("name", ("name", "title", "label")),
    ("text", ("description", "desc", "note", "notes", "comment", "comments", "remark", "remarks", "message", "content", "body", "address", "email")),
    ("boolean", ("is_", "has_", "can_", "should_", "must_", "enabled", "disabled", "locked", "visible", "hidden", "verified")),
    ("status", ("status", "state", "stage", "approval", "active", "inactive", "pending", "open", "closed", "completed", "cancelled", "canceled", "failed")),
    ("money", ("amount", "price", "cost", "total", "balance", "value", "fee", "charge", "discount", "salary", "wage", "revenue", "income", "expense", "profit", "loss", "debit", "credit", "paid", "due", "outstanding", "tax")),
    ("date", ("date", "time", "timestamp", "created", "updated", "modified", "posted", "start", "end", "effective", "expiry", "month", "year")),
    ("quantity", ("quantity", "qty", "count", "units", "stock", "level", "available", "reserved", "ordered", "shipped", "received", "produced", "consumed", "weight", "volume", "capacity", "size", "reorder")),
    ("percentage", ("percent", "percentage", "pct", "ratio")),
]

_MODULE_RULES: dict[str, tuple[str, ...]] = {
    "reference": ("master", "lookup", "catalog", "directory", "reference", "type", "category"),
    "transaction": ("order", "invoice", "payment", "sale", "purchase", "entry", "transaction", "ledger", "bill"),
    "event": ("log", "event", "audit", "history", "activity", "message", "ticket"),
    "snapshot": ("inventory", "stock", "balance", "summary", "snapshot", "status"),
}

_MODULE_PURPOSES: dict[str, str] = {
    "reference": "Stores reusable reference records.",
    "transaction": "Stores transactional records.",
    "event": "Stores time-based activity records.",
    "snapshot": "Stores current-state or balance records.",
    "general": "Stores database records.",
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
    """Return generic semantic type, confidence, and reason for a column."""
    normalized = _normalize_identifier(column_name)
    full_name = "_".join(part for part in (_normalize_identifier(table_name), normalized) if part)

    for semantic_type, patterns in _SEMANTIC_PATTERNS:
        for pattern in patterns:
            if pattern == normalized:
                return semantic_type, 0.99, f"Exact generic pattern match for '{pattern}'."
            if semantic_type == "boolean" and normalized.startswith(pattern):
                return semantic_type, 0.97, f"Matched generic boolean prefix '{pattern}'."
            if pattern in normalized:
                return semantic_type, 0.94, f"Matched generic column pattern '{pattern}'."
            if pattern in full_name:
                return semantic_type, 0.9, f"Matched generic table context pattern '{pattern}'."

    if normalized.endswith("_id"):
        return "id", 0.9, "Column ends with _id."

    return "general", 0.6, "No generic semantic pattern matched."


def classify_semantic_type(column_name: str, table_name: str = "") -> str:
    semantic_type, _, _ = classify_column_metadata(column_name, table_name)
    return semantic_type


def detect_table_module(table_name: str, table_data: dict) -> tuple[str, str]:
    """Detect a generic table category and default business purpose."""
    normalized_table_name = _normalize_identifier(table_name)
    search_space = [normalized_table_name]
    search_space.extend(_normalize_identifier(column.get("name", "")) for column in table_data.get("columns", []))
    search_text = " ".join(search_space)

    primary_keys = table_data.get("primary_keys", [])
    foreign_keys = table_data.get("foreign_keys", [])
    has_dates = any(
        str(column.get("semantic_type", "")).lower() == "date"
        or "date" in _normalize_identifier(column.get("name", ""))
        for column in table_data.get("columns", [])
    )

    if normalized_table_name.endswith(("_log", "_history", "_events")):
        return "event", _MODULE_PURPOSES["event"]
    if any(keyword in search_text for keyword in _MODULE_RULES["transaction"]):
        return "transaction", _MODULE_PURPOSES["transaction"]
    if any(keyword in search_text for keyword in _MODULE_RULES["snapshot"]):
        return "snapshot", _MODULE_PURPOSES["snapshot"]
    if any(keyword in search_text for keyword in _MODULE_RULES["reference"]):
        return "reference", _MODULE_PURPOSES["reference"]
    if foreign_keys and has_dates:
        return "transaction", _MODULE_PURPOSES["transaction"]
    if foreign_keys:
        return "event", _MODULE_PURPOSES["event"]
    if primary_keys and len(primary_keys) == 1 and len(table_data.get("columns", [])) <= 8:
        return "reference", _MODULE_PURPOSES["reference"]
    return "general", _MODULE_PURPOSES["general"]


def build_rule_based_business_purpose(table_name: str, module_name: str) -> str:
    entity = _humanize_identifier(_singularize(table_name))

    if module_name == "reference":
        return f"Stores reference records for {entity}."
    if module_name == "transaction":
        return f"Stores transaction records for {entity}."
    if module_name == "event":
        return f"Stores activity records for {entity}."
    if module_name == "snapshot":
        return f"Stores current-state records for {entity}."
    return f"Stores records for {entity}."


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
    target_semantics = {
        str(column.get("semantic_type", "")).lower()
        for column in target_data.get("columns", [])
    }
    if local_semantic in {"id", "code"} and {"id", "code"} & target_semantics:
        score += 0.05
        reasons.append("identifier semantics align")

    return score, reasons


def _relationship_key(relationship: dict) -> tuple[str, str, str, str]:
    return (
        relationship["from_table"],
        relationship["from_column"],
        relationship["to_table"],
        relationship["to_column"],
    )


def detect_relationships(knowledge_base: dict) -> list[dict]:
    """Detect relationships using real FKs plus generic inference."""
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
    """Build a CLI-friendly summary of detected metadata."""
    module_counts: dict[str, int] = {}
    relationship_map: dict[tuple[str, str, str, str], dict] = {}

    for table_name, table_data in (knowledge_base or {}).items():
        module_name = table_data.get("module", "general")
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
    Return a copy of the knowledge base with clean generic metadata attached.

    The historical function name is kept for compatibility with the rest of the
    project, but the enrichment is now database-agnostic.
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
            existing_semantic_type = str(column.get("semantic_type", "")).lower()
            if existing_semantic_type in _ALLOWED_SEMANTIC_TYPES - {"general"}:
                semantic_type = str(column.get("semantic_type", "")).lower()
                confidence = float(column.get("confidence", 0.75) or 0.75)
                reason = str(column.get("reason", "Preserved existing semantic type."))
            else:
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
