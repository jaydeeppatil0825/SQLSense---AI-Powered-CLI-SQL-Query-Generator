"""
Neutral schema-fact helpers for KB enrichment.

This module contains runtime schema-driven metadata helpers used by the
knowledge-base pipeline. It intentionally avoids ERP-specific module logic and
acts as the primary implementation behind the legacy `semantic.erp_metadata`
compatibility wrapper.
"""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
import re
from typing import Any


CORE_SEMANTIC_TYPES = {
    "id",
    "date",
    "boolean",
    "numeric_candidate",
    "text_candidate",
    "category_candidate",
    "unknown",
}

_MEASURE_SEMANTIC_TYPES = {"money", "quantity", "percentage"}
_DIMENSION_SEMANTIC_TYPES = {"status", "name", "text", "code", "reference"}


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


def build_rule_based_business_purpose(table_name: str, module_name: str = "") -> str:
    label = _singularize_phrase(table_name)
    return f"Stores records for {label}."


def sanitize_business_purpose(text: str, table_name: str, module_name: str = "") -> str:
    cleaned = sanitize_short_text(text)
    if not cleaned:
        return build_rule_based_business_purpose(table_name, module_name)
    if "?" in cleaned or len(cleaned) < 4:
        return build_rule_based_business_purpose(table_name, module_name)
    return cleaned


def is_core_semantic_type(value: str) -> bool:
    return str(value or "").strip().lower() in CORE_SEMANTIC_TYPES


def column_core_semantic_type(column: dict[str, Any], fallback: str = "unknown") -> str:
    semantic_type = str((column or {}).get("semantic_type", "")).strip().lower()
    if semantic_type in CORE_SEMANTIC_TYPES:
        return semantic_type
    return fallback


def resolved_semantic_type(column: dict[str, Any], fallback: str = "unknown") -> str:
    raw_semantic_type = str((column or {}).get("semantic_type", "")).strip().lower()
    if raw_semantic_type and raw_semantic_type not in CORE_SEMANTIC_TYPES:
        return raw_semantic_type

    ai_metadata = (column or {}).get("ai_metadata")
    if isinstance(ai_metadata, dict):
        ai_semantic_type = str(ai_metadata.get("ai_semantic_type", ai_metadata.get("semantic_type", ""))).strip().lower()
        if ai_semantic_type:
            return ai_semantic_type

    metric_type = str((column or {}).get("metric_type", "")).strip().lower()
    if metric_type in _MEASURE_SEMANTIC_TYPES:
        return metric_type

    if bool((column or {}).get("is_date")):
        return "date"

    if bool((column or {}).get("is_dimension")) and raw_semantic_type in {"text_candidate", "category_candidate"}:
        return "text"

    if bool((column or {}).get("is_measure")) and raw_semantic_type == "numeric_candidate":
        return "numeric_candidate"

    if raw_semantic_type in CORE_SEMANTIC_TYPES:
        return raw_semantic_type
    return fallback


def column_ai_metadata(column: dict[str, Any]) -> dict[str, Any]:
    raw = (column or {}).get("ai_metadata")
    if not isinstance(raw, dict):
        raw = {}
    raw_semantic_type = str((column or {}).get("semantic_type", "") or "").strip().lower()
    fallback_ai_semantic = raw_semantic_type if raw_semantic_type and raw_semantic_type not in CORE_SEMANTIC_TYPES else ""
    ai_semantic_type = str(
        raw.get(
            "ai_semantic_type",
            raw.get("semantic_type", fallback_ai_semantic),
        )
        or ""
    ).strip().lower()
    business_description = str(
        raw.get(
            "business_description",
            (column or {}).get("business_description", ""),
        )
        or ""
    ).strip()
    business_terms = raw.get("business_terms", (column or {}).get("business_terms", [])) or []
    if not isinstance(business_terms, list):
        business_terms = [business_terms]
    confidence = raw.get("confidence", (column or {}).get("confidence", 0.0))
    reason = str(raw.get("reason", (column or {}).get("reason", "")) or "").strip()
    accepted = bool(raw.get("accepted", bool(ai_semantic_type)))
    return {
        "ai_semantic_type": ai_semantic_type,
        "business_description": business_description,
        "business_terms": [str(term).strip() for term in business_terms if str(term).strip()],
        "confidence": float(confidence or 0.0),
        "reason": reason,
        "accepted": accepted,
    }


def column_business_description(column: dict[str, Any]) -> str:
    return str(column_ai_metadata(column).get("business_description", "") or "").strip()


def column_business_terms(column: dict[str, Any]) -> list[str]:
    return list(column_ai_metadata(column).get("business_terms", []) or [])


def column_profile_facts(column: dict[str, Any]) -> dict[str, Any]:
    raw = (column or {}).get("profile_facts")
    if not isinstance(raw, dict):
        raw = {}
    sample_values = raw.get("sample_values", (column or {}).get("sample_values", [])) or []
    if not isinstance(sample_values, list):
        sample_values = [sample_values]
    return {
        "null_count": int(raw.get("null_count", (column or {}).get("null_count", 0)) or 0),
        "unique_count": int(raw.get("unique_count", (column or {}).get("unique_count", 0)) or 0),
        "sample_values": list(sample_values),
        "min": raw.get("min", (column or {}).get("min", (column or {}).get("min_value"))),
        "max": raw.get("max", (column or {}).get("max", (column or {}).get("max_value"))),
    }


def column_sample_values(column: dict[str, Any]) -> list[Any]:
    return list(column_profile_facts(column).get("sample_values", []) or [])


def column_structural_facts(
    column: dict[str, Any],
    *,
    primary_keys: set[str] | None = None,
    foreign_keys: set[str] | None = None,
) -> dict[str, bool]:
    raw = (column or {}).get("structural_facts")
    if not isinstance(raw, dict):
        raw = {}
    column_name = str((column or {}).get("name", "") or "")
    core_semantic_type = column_core_semantic_type(column)
    pk_names = {str(value) for value in (primary_keys or set()) if str(value)}
    fk_names = {str(value) for value in (foreign_keys or set()) if str(value)}
    is_primary_key = bool(raw.get("is_primary_key", column_name in pk_names or (column or {}).get("is_primary_key", False)))
    is_foreign_key = bool(raw.get("is_foreign_key", column_name in fk_names or (column or {}).get("is_foreign_key", False)))
    is_id = bool(raw.get("is_id", core_semantic_type == "id" or column_name.lower() == "id" or column_name.lower().endswith("_id")))
    is_date = bool(raw.get("is_date", core_semantic_type == "date" or (column or {}).get("is_date", False)))
    is_boolean = bool(raw.get("is_boolean", core_semantic_type == "boolean"))
    return {
        "is_primary_key": is_primary_key,
        "is_foreign_key": is_foreign_key,
        "is_id": is_id,
        "is_date": is_date,
        "is_boolean": is_boolean,
    }


def column_planner_roles(column: dict[str, Any]) -> dict[str, bool]:
    raw = (column or {}).get("planner_roles")
    if not isinstance(raw, dict):
        raw = {}
    core_semantic_type = column_core_semantic_type(column)
    semantic_type = resolved_semantic_type(column)
    structural = column_structural_facts(column)
    measure_candidate = bool(raw.get("measure_candidate", bool((column or {}).get("is_measure"))))
    dimension_candidate = bool(
        raw.get(
            "dimension_candidate",
            bool((column or {}).get("is_dimension")) or semantic_type in _DIMENSION_SEMANTIC_TYPES or core_semantic_type in {"text_candidate", "category_candidate", "date", "boolean"},
        )
    )
    filter_candidate = bool(
        raw.get(
            "filter_candidate",
            dimension_candidate or core_semantic_type in {"date", "boolean", "category_candidate", "text_candidate"},
        )
    )
    join_candidate = bool(raw.get("join_candidate", structural["is_primary_key"] or structural["is_foreign_key"] or structural["is_id"]))
    date_candidate = bool(raw.get("date_candidate", structural["is_date"]))
    sort_candidate = bool(raw.get("sort_candidate", measure_candidate or dimension_candidate or date_candidate))
    return {
        "measure_candidate": measure_candidate,
        "dimension_candidate": dimension_candidate,
        "filter_candidate": filter_candidate,
        "join_candidate": join_candidate,
        "date_candidate": date_candidate,
        "sort_candidate": sort_candidate,
    }


def column_formula_evidence(column: dict[str, Any]) -> list[dict[str, Any]]:
    raw = (column or {}).get("formula_evidence")
    if isinstance(raw, list):
        return [entry for entry in raw if isinstance(entry, dict)]
    return []


def apply_column_contract(
    column: dict[str, Any],
    *,
    primary_keys: set[str] | None = None,
    foreign_keys: set[str] | None = None,
) -> dict[str, Any]:
    semantic_type = column_core_semantic_type(column)
    structural_facts = column_structural_facts(column, primary_keys=primary_keys, foreign_keys=foreign_keys)
    profile_facts = column_profile_facts(column)
    ai_metadata = column_ai_metadata(column)
    planner_roles = column_planner_roles(column)
    formula_evidence = column_formula_evidence(column)
    column["semantic_type"] = semantic_type
    column["structural_facts"] = structural_facts
    column["profile_facts"] = profile_facts
    column["ai_metadata"] = ai_metadata
    column["planner_roles"] = planner_roles
    column["formula_evidence"] = formula_evidence
    column["is_primary_key"] = structural_facts["is_primary_key"]
    column["is_foreign_key"] = structural_facts["is_foreign_key"]
    column["is_date"] = structural_facts["is_date"]
    column["sample_values"] = list(profile_facts["sample_values"])
    column["null_count"] = profile_facts["null_count"]
    column["unique_count"] = profile_facts["unique_count"]
    column["min"] = profile_facts["min"]
    column["max"] = profile_facts["max"]
    column["min_value"] = profile_facts["min"]
    column["max_value"] = profile_facts["max"]
    column["business_description"] = ai_metadata["business_description"]
    column["business_terms"] = list(ai_metadata["business_terms"])
    column["confidence"] = max(float(column.get("confidence", 0.0) or 0.0), float(ai_metadata["confidence"] or 0.0))
    return column


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
        if profiled_values:
            normalized_profile = [_normalize(value) for value in profiled_values if _normalize(value)]
            distinct_profile = list(dict.fromkeys(normalized_profile))
            if 0 < len(distinct_profile) <= 12 and all(len(value) <= 40 for value in distinct_profile):
                return "category_candidate"
        return "text_candidate"

    return "unknown"


def _column_confidence(column_name: str, semantic_type: str) -> float:
    normalized_name = _normalize_identifier(column_name)
    if semantic_type == "unknown":
        return 0.55
    if semantic_type == "id" and (normalized_name == "id" or normalized_name.endswith("_id")):
        return 0.98
    if semantic_type in {"date", "boolean"}:
        return 0.95
    if semantic_type in {"numeric_candidate", "text_candidate", "category_candidate"}:
        return 0.68
    return 0.8


def _column_reason(column_name: str, semantic_type: str) -> str:
    if semantic_type == "unknown":
        return "No strong structural fact was found; semantic meaning remains unresolved."
    if semantic_type == "id":
        return "Structural fact: primary/foreign-key or _id identifier column."
    if semantic_type == "date":
        return "Structural fact: date/datetime/timestamp column."
    if semantic_type == "boolean":
        return "Structural fact: boolean type or boolean-like profile values."
    if semantic_type == "numeric_candidate":
        return "Candidate evidence: numeric-like SQL type."
    if semantic_type == "text_candidate":
        return "Candidate evidence: text-like SQL type."
    if semantic_type == "category_candidate":
        return "Candidate evidence: low-cardinality text/category profile."
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
                    "reason": "Inferred from a neutral *_id naming pattern.",
                    "source": "inferred_by_naming",
                }
            )

    return relationships


def enrich_knowledge_base_schema_facts(knowledge_base: dict[str, Any]) -> dict[str, Any]:
    enriched = deepcopy(knowledge_base or {})
    detected_relationships = detect_relationships(enriched)

    for table_name, table_data in enriched.items():
        table_data["table_name"] = table_name
        table_data.pop("module", None)
        table_data["business_purpose"] = sanitize_business_purpose(
            table_data.get("business_purpose", ""),
            table_name,
        )
        table_data["business_description"] = sanitize_short_text(
            table_data.get("business_description", ""),
            fallback=_humanize(table_name).title(),
        )
        table_data.setdefault("possible_business_questions", [])
        table_data.setdefault("relationships", [])
        primary_keys = {str(value) for value in table_data.get("primary_keys", []) if str(value)}
        foreign_keys = {
            str(fk.get("column", ""))
            for fk in table_data.get("foreign_keys", [])
            if str(fk.get("column", ""))
        }

        for column in table_data.get("columns", []):
            original_semantic_type = str(column.get("semantic_type") or "").strip().lower()
            if original_semantic_type and not is_core_semantic_type(original_semantic_type):
                ai_metadata = column.get("ai_metadata")
                if not isinstance(ai_metadata, dict):
                    ai_metadata = {}
                ai_metadata.setdefault("ai_semantic_type", original_semantic_type)
                ai_metadata.setdefault("accepted", True)
                column["ai_metadata"] = ai_metadata

            semantic_type = original_semantic_type
            if not is_core_semantic_type(semantic_type):
                semantic_type = classify_semantic_type(
                    column.get("name", ""),
                    table_name=table_name,
                    column_type=column.get("type", ""),
                    is_primary_key=str(column.get("name", "")) in primary_keys,
                    is_foreign_key=str(column.get("name", "")) in foreign_keys,
                    sample_values=column_sample_values(column),
                )
            column["semantic_type"] = semantic_type if semantic_type in CORE_SEMANTIC_TYPES else "unknown"
            column["confidence"] = max(
                float(column.get("confidence", 0.0) or 0.0),
                _column_confidence(column.get("name", ""), semantic_type),
            )
            column["reason"] = sanitize_short_text(
                column.get("reason", ""),
                fallback=_column_reason(column.get("name", ""), semantic_type),
            )
            column["is_date"] = bool(semantic_type == "date")
            apply_column_contract(
                column,
                primary_keys=primary_keys,
                foreign_keys=foreign_keys,
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


def enrich_knowledge_base_for_erp(knowledge_base: dict[str, Any]) -> dict[str, Any]:
    """
    Backward-compatible alias for older imports.

    Runtime behavior is schema-fact-only and no longer injects ERP modules.
    """
    return enrich_knowledge_base_schema_facts(knowledge_base)


def summarize_knowledge_base(knowledge_base: dict[str, Any]) -> dict[str, Any]:
    enriched = enrich_knowledge_base_schema_facts(knowledge_base)
    context_counts = Counter(
        str(table_data.get("schema_context", "schema_only"))
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
        "schema_contexts": dict(sorted(context_counts.items())),
        "modules_detected": dict(sorted(context_counts.items())),
        "relationship_count": len(relationship_signatures),
        "low_confidence_relationships": low_confidence_relationships,
        "tables_with_missing_relationships": tables_with_missing_relationships,
    }


__all__ = [
    "apply_column_contract",
    "build_rule_based_business_purpose",
    "column_ai_metadata",
    "column_business_description",
    "column_business_terms",
    "classify_semantic_type",
    "column_core_semantic_type",
    "column_formula_evidence",
    "column_planner_roles",
    "column_profile_facts",
    "column_sample_values",
    "column_structural_facts",
    "detect_relationships",
    "enrich_knowledge_base_for_erp",
    "enrich_knowledge_base_schema_facts",
    "CORE_SEMANTIC_TYPES",
    "is_core_semantic_type",
    "resolved_semantic_type",
    "sanitize_business_purpose",
    "sanitize_short_text",
    "summarize_knowledge_base",
]
