"""
Generic semantic metadata helpers and relationship enrichment.

This module keeps the historical import surface used across the project while
staying schema-driven and database-agnostic.
"""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
import re
from typing import Any


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip().lower())


def _normalize_identifier(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", _normalize(text)).strip("_")


def _humanize(text: str) -> str:
    return _normalize_identifier(text).replace("_", " ").strip()


def _singularize_phrase(text: str) -> str:
    words = [word for word in _humanize(text).split() if word]
    if not words:
        return "records"

    last_word = words[-1]
    if last_word.endswith("ies") and len(last_word) > 3:
        words[-1] = last_word[:-3] + "y"
    elif last_word.endswith("ses") and len(last_word) > 3:
        words[-1] = last_word[:-2]
    elif last_word.endswith("s") and not last_word.endswith("ss") and len(last_word) > 1:
        words[-1] = last_word[:-1]
    return " ".join(words)


def sanitize_short_text(text: str, fallback: str = "") -> str:
    cleaned = re.sub(r"\s+", " ", str(text or "").strip())
    cleaned = cleaned.strip(" .,:;|")
    if not cleaned or not any(char.isalpha() for char in cleaned):
        return str(fallback or "").strip()
    return cleaned[:120]


def build_rule_based_business_purpose(table_name: str, module_name: str) -> str:
    label = _singularize_phrase(table_name)
    module_label = _humanize(module_name) or "general"
    return f"Stores {module_label} records for {label}."


def sanitize_business_purpose(text: str, table_name: str, module_name: str) -> str:
    cleaned = sanitize_short_text(text)
    if not cleaned:
        return build_rule_based_business_purpose(table_name, module_name)
    if "?" in cleaned or len(cleaned) < 4:
        return build_rule_based_business_purpose(table_name, module_name)
    return cleaned


def classify_semantic_type(
    column_name: str,
    table_name: str = "",
    column_type: str = "",
    is_primary_key: bool = False,
    is_foreign_key: bool = False,
    sample_values: list | None = None,
) -> str:
    """
    Classify semantic type from strong structural facts plus weak candidate evidence.

    Direct structural facts:
    - primary key / foreign key / *_id -> id
    - date/datetime/timestamp -> date
    - boolean type or boolean-only profile values -> boolean

    Everything else remains a candidate for later AI/KB enrichment:
    - numeric_candidate
    - text_candidate
    - category_candidate
    """
    normalized_name = _normalize_identifier(column_name)
    column_type_lower = str(column_type or "").lower()
    profiled_values = [value for value in (sample_values or []) if value is not None]
    name_tokens = {token for token in normalized_name.split("_") if token}

    def _type_contains(*tokens: str) -> bool:
        return any(token in column_type_lower for token in tokens)

    if is_primary_key or is_foreign_key:
        return "id"
    if normalized_name == "id" or normalized_name.endswith("_id"):
        return "id"
    if _type_contains("date", "datetime", "timestamp"):
        return "date"
    if _type_contains("boolean", "bool", "bit"):
        return "boolean"

    if profiled_values:
        normalized_profile = {str(value).strip().lower() for value in profiled_values}
        boolean_profile_values = {"true", "false", "yes", "no", "1", "0"}
        if normalized_profile and normalized_profile <= boolean_profile_values:
            return "boolean"

    if _type_contains("decimal", "numeric", "float", "double", "real", "int", "integer", "bigint", "smallint", "tinyint"):
        return "numeric_candidate"
    if _type_contains("enum", "set"):
        return "category_candidate"
    if _type_contains("varchar", "char", "text", "string", "nvarchar", "nchar", "json"):
        if name_tokens & {"status", "state", "type", "category", "segment", "code", "flag"}:
            return "category_candidate"
        return "text_candidate"

    if name_tokens & {"status", "state", "type", "category", "segment", "code", "flag"}:
        return "category_candidate"
    if name_tokens & {"name", "label", "title", "text", "description", "comment", "note"}:
        return "text_candidate"
    if name_tokens & {
        "amount",
        "total",
        "price",
        "cost",
        "balance",
        "value",
        "qty",
        "quantity",
        "units",
        "rate",
        "percent",
        "percentage",
    }:
        return "numeric_candidate"

    return "general"


def _infer_module_name(table_name: str) -> str:
    normalized_name = _normalize_identifier(table_name)
    if any(keyword in normalized_name for keyword in ["customer", "client", "account"]):
        return "reference"
    if any(keyword in normalized_name for keyword in ["vendor", "supplier", "provider"]):
        return "reference"
    if any(keyword in normalized_name for keyword in ["item", "product", "material"]):
        return "reference"
    if any(keyword in normalized_name for keyword in ["invoice", "order", "transaction"]):
        return "transaction"
    if any(keyword in normalized_name for keyword in ["payment", "receipt", "settlement"]):
        return "transaction"
    if any(keyword in normalized_name for keyword in ["inventory", "stock", "warehouse"]):
        return "inventory"
    if any(keyword in normalized_name for keyword in ["employee", "staff", "personnel"]):
        return "reference"
    return "reference"


def _column_confidence(column_name: str, semantic_type: str) -> float:
    normalized_name = _normalize_identifier(column_name)
    if semantic_type == "general":
        return 0.55
    if semantic_type == "id" and (normalized_name == "id" or normalized_name.endswith("_id")):
        return 0.98
    if semantic_type in {"date", "boolean"}:
        return 0.95
    if semantic_type in {"numeric_candidate", "text_candidate", "category_candidate"}:
        return 0.68
    return 0.8


def _column_reason(column_name: str, semantic_type: str) -> str:
    if semantic_type == "general":
        return "No strong structural fact was found; semantic meaning remains unresolved."
    if semantic_type == "id":
        return "Structural fact: primary/foreign-key or _id identifier column."
    if semantic_type == "date":
        return "Structural fact: date/datetime/timestamp column."
    if semantic_type == "boolean":
        return "Structural fact: boolean type or boolean-like profile values."
    if semantic_type == "numeric_candidate":
        return "Candidate evidence: numeric-like type or numeric-style column meaning."
    if semantic_type == "text_candidate":
        return "Candidate evidence: text-like type or label-style column meaning."
    if semantic_type == "category_candidate":
        return "Candidate evidence: categorical/code-like type or column meaning."
    return f"Structural semantic classification: {semantic_type}."


def _append_relationship(target: list[dict[str, Any]], relationship: dict[str, Any]) -> None:
    signature = (
        relationship.get("from_table"),
        relationship.get("from_column"),
        relationship.get("to_table"),
        relationship.get("to_column"),
        relationship.get("direction"),
    )
    for existing in target:
        existing_signature = (
            existing.get("from_table"),
            existing.get("from_column"),
            existing.get("to_table"),
            existing.get("to_column"),
            existing.get("direction"),
        )
        if existing_signature == signature:
            return
    target.append(dict(relationship))


def detect_relationships(schema_data: dict[str, Any]) -> list[dict[str, Any]]:
    relationships: list[dict[str, Any]] = []
    table_names = set((schema_data or {}).keys())

    for table_name, table_data in (schema_data or {}).items():
        for foreign_key in table_data.get("foreign_keys", []):
            referenced_table = foreign_key.get("referenced_table")
            referenced_column = foreign_key.get("referenced_column")
            from_column = foreign_key.get("column")
            if not referenced_table or not referenced_column or not from_column:
                continue
            relationships.append(
                {
                    "from_table": table_name,
                    "from_column": from_column,
                    "to_table": referenced_table,
                    "to_column": referenced_column,
                    "direction": "many-to-one",
                    "confidence": 0.99,
                    "reason": "Detected from a real foreign key constraint.",
                    "source": "foreign_key",
                }
            )

    for table_name, table_data in (schema_data or {}).items():
        primary_keys = set(table_data.get("primary_keys", []))
        for column in table_data.get("columns", []):
            column_name = str(column.get("name", ""))
            normalized_name = _normalize_identifier(column_name)
            if normalized_name == "id" or not normalized_name.endswith("_id"):
                continue
            if column_name in primary_keys:
                continue

            stem = normalized_name[:-3]
            candidate_names = {
                stem,
                f"{stem}s",
                f"{stem}_directory",
                f"{stem}_master",
            }
            matched_table = next((candidate for candidate in candidate_names if candidate in table_names), None)
            if not matched_table or matched_table == table_name:
                continue

            target_primary_keys = list((schema_data.get(matched_table) or {}).get("primary_keys", []))
            target_column = target_primary_keys[0] if target_primary_keys else column_name
            signature = (table_name, column_name, matched_table, target_column)
            if any(
                (
                    rel.get("from_table"),
                    rel.get("from_column"),
                    rel.get("to_table"),
                    rel.get("to_column"),
                ) == signature
                for rel in relationships
            ):
                continue

            relationships.append(
                {
                    "from_table": table_name,
                    "from_column": column_name,
                    "to_table": matched_table,
                    "to_column": target_column,
                    "direction": "many-to-one",
                    "confidence": 0.82,
                    "reason": "Inferred from identifier naming patterns.",
                    "source": "inference",
                }
            )

    return relationships


def enrich_knowledge_base_for_erp(knowledge_base: dict[str, Any]) -> dict[str, Any]:
    enriched = deepcopy(knowledge_base or {})
    detected_relationships = detect_relationships(enriched)

    for table_name, table_data in enriched.items():
        module_name = str(table_data.get("module") or _infer_module_name(table_name))
        table_data["table_name"] = table_name
        table_data["module"] = module_name
        table_data["business_purpose"] = sanitize_business_purpose(
            table_data.get("business_purpose", ""),
            table_name,
            module_name,
        )
        table_data["business_description"] = sanitize_short_text(
            table_data.get("business_description", ""),
            fallback=_humanize(table_name).title(),
        )
        table_data.setdefault("possible_business_questions", [])
        table_data.setdefault("relationships", [])

        for column in table_data.get("columns", []):
            semantic_type = str(column.get("semantic_type") or "")
            if not semantic_type or semantic_type == "general":
                semantic_type = classify_semantic_type(
                    column.get("name", ""),
                    table_name=table_name,
                    column_type=column.get("type", ""),
                    is_primary_key=str(column.get("name", "")) in set(table_data.get("primary_keys", [])),
                    is_foreign_key=str(column.get("name", "")) in {
                        str(fk.get("column", ""))
                        for fk in table_data.get("foreign_keys", [])
                    },
                    sample_values=column.get("sample_values", []),
                )
            column["semantic_type"] = semantic_type
            column["confidence"] = max(
                float(column.get("confidence", 0.0) or 0.0),
                _column_confidence(column.get("name", ""), semantic_type),
            )
            column["reason"] = sanitize_short_text(
                column.get("reason", ""),
                fallback=_column_reason(column.get("name", ""), semantic_type),
            )
            column["is_date"] = bool(semantic_type == "date")

    for relationship in detected_relationships:
        from_table = relationship.get("from_table")
        to_table = relationship.get("to_table")
        if from_table not in enriched or to_table not in enriched:
            continue

        outgoing = dict(relationship)
        outgoing["direction"] = relationship.get("direction", "many-to-one")
        incoming = dict(relationship)
        incoming["direction"] = "incoming"

        _append_relationship(enriched[from_table].setdefault("relationships", []), outgoing)
        _append_relationship(enriched[to_table].setdefault("relationships", []), incoming)

    return enriched


def summarize_knowledge_base(knowledge_base: dict[str, Any]) -> dict[str, Any]:
    enriched = enrich_knowledge_base_for_erp(knowledge_base)
    module_counts = Counter(
        str(table_data.get("module", "reference"))
        for table_data in enriched.values()
    )
    relationship_signatures = {
        (
            relationship.get("from_table"),
            relationship.get("from_column"),
            relationship.get("to_table"),
            relationship.get("to_column"),
        )
        for table_data in enriched.values()
        for relationship in table_data.get("relationships", [])
        if relationship.get("direction") != "incoming"
    }
    tables_with_missing_relationships = sorted(
        [
            table_name
            for table_name, table_data in enriched.items()
            if not table_data.get("relationships")
        ]
    )
    low_confidence_relationships = sorted(
        [
            {
                "from_table": relationship.get("from_table"),
                "from_column": relationship.get("from_column"),
                "to_table": relationship.get("to_table"),
                "to_column": relationship.get("to_column"),
                "confidence": relationship.get("confidence"),
                "source": relationship.get("source"),
            }
            for table_data in enriched.values()
            for relationship in table_data.get("relationships", [])
            if relationship.get("direction") != "incoming"
            and float(relationship.get("confidence") or 0.0) < 0.9
        ],
        key=lambda item: (
            str(item.get("from_table", "")),
            str(item.get("from_column", "")),
            str(item.get("to_table", "")),
            str(item.get("to_column", "")),
        ),
    )
    return {
        "table_count": len(enriched),
        "modules_detected": dict(sorted(module_counts.items())),
        "relationship_count": len(relationship_signatures),
        "low_confidence_relationships": low_confidence_relationships,
        "tables_with_missing_relationships": tables_with_missing_relationships,
    }
