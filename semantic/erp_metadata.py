"""
Generic semantic metadata helpers and relationship enrichment.

This module keeps the historical import surface used across the project while
staying schema-driven and database-agnostic.
"""

from __future__ import annotations

from copy import deepcopy
import re
from typing import Any


_MODULE_RULES: tuple[tuple[str, str], ...] = (
    ("transaction", "transaction"),
    ("invoice", "transaction"),
    ("payment", "transaction"),
    ("order", "transaction"),
    ("shipment", "transaction"),
    ("return", "transaction"),
    ("event", "transaction"),
    ("entry", "transaction"),
    ("record", "transaction"),
    ("balance", "snapshot"),
    ("position", "snapshot"),
    ("stock", "snapshot"),
    ("summary", "snapshot"),
    ("directory", "reference"),
    ("master", "reference"),
    ("catalog", "reference"),
    ("category", "reference"),
    ("branch", "reference"),
    ("warehouse", "reference"),
    ("customer", "reference"),
    ("supplier", "reference"),
    ("vendor", "reference"),
)

_SEMANTIC_PATTERNS: tuple[tuple[str, str], ...] = (
    ("amount", "money"),
    ("price", "money"),
    ("cost", "money"),
    ("total", "money"),
    ("balance", "money"),
    ("value", "money"),
    ("tax", "money"),
    ("discount", "money"),
    ("salary", "money"),
    ("wage", "money"),
    ("credit", "money"),
    ("debit", "money"),
    ("due", "money"),
    ("outstanding", "money"),
    ("quantity", "quantity"),
    ("qty", "quantity"),
    ("count", "quantity"),
    ("stock", "quantity"),
    ("volume", "quantity"),
    ("weight", "quantity"),
    ("status", "status"),
    ("state", "status"),
    ("stage", "status"),
    ("active", "status"),
    ("inactive", "status"),
    ("date", "date"),
    ("time", "date"),
    ("month", "date"),
    ("year", "date"),
    ("created", "date"),
    ("updated", "date"),
    ("name", "name"),
    ("title", "name"),
    ("description", "text"),
    ("note", "text"),
    ("comment", "text"),
    ("code", "code"),
    ("reference", "code"),
    ("identifier", "id"),
)


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


def classify_semantic_type(column_name: str, table_name: str = "") -> str:
    normalized_name = _normalize_identifier(column_name)
    tokens = [token for token in normalized_name.split("_") if token]

    if normalized_name == "id" or normalized_name.endswith("_id"):
        return "id"
    if normalized_name.startswith("is_") or normalized_name.startswith("has_"):
        return "boolean"

    for pattern, semantic_type in _SEMANTIC_PATTERNS:
        if pattern in normalized_name or pattern in tokens:
            return semantic_type

    if tokens and tokens[-1] in {"id", "key"}:
        return "id"
    return "general"


def _infer_module_name(table_name: str) -> str:
    normalized_name = _normalize_identifier(table_name)
    for pattern, module_name in _MODULE_RULES:
        if pattern in normalized_name:
            return module_name
    return "reference"


def _column_confidence(column_name: str, semantic_type: str) -> float:
    normalized_name = _normalize_identifier(column_name)
    if semantic_type == "general":
        return 0.55
    if semantic_type == "id" and (normalized_name == "id" or normalized_name.endswith("_id")):
        return 0.98
    if semantic_type in {"money", "quantity", "date", "status", "name", "code"}:
        return 0.95
    return 0.85


def _column_reason(column_name: str, semantic_type: str) -> str:
    if semantic_type == "general":
        return "Used generic fallback classification."
    return f"Matched generic semantic pattern for {semantic_type}."


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
                semantic_type = classify_semantic_type(column.get("name", ""), table_name=table_name)
            column["semantic_type"] = semantic_type
            column["confidence"] = max(float(column.get("confidence", 0.0) or 0.0), _column_confidence(column.get("name", ""), semantic_type))
            column["reason"] = sanitize_short_text(
                column.get("reason", ""),
                fallback=_column_reason(column.get("name", ""), semantic_type),
            )

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
    modules_detected = sorted(
        {
            str(table_data.get("module", "reference"))
            for table_data in enriched.values()
        }
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
    return {
        "table_count": len(enriched),
        "modules_detected": modules_detected,
        "relationship_count": len(relationship_signatures),
        "tables_with_missing_relationships": tables_with_missing_relationships,
    }
