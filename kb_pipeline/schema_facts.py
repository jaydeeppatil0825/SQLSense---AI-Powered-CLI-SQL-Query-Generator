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
FALLBACK_RELATIONSHIP_MIN_CONFIDENCE = 0.85


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

    ai_metadata = column_ai_metadata(column)
    metric_type = str(ai_metadata.get("ai_semantic_type", "")).strip().lower()
    if metric_type not in _MEASURE_SEMANTIC_TYPES:
        metric_type = str((column or {}).get("metric_type", "")).strip().lower()
    if metric_type in _MEASURE_SEMANTIC_TYPES:
        return metric_type

    structural_facts = (column or {}).get("structural_facts")
    if not isinstance(structural_facts, dict):
        structural_facts = {}
    if bool(structural_facts.get("is_date", bool((column or {}).get("is_date")))):
        return "date"

    planner_roles = (column or {}).get("planner_roles")
    if not isinstance(planner_roles, dict):
        planner_roles = {}
    is_dimension = bool(planner_roles.get("dimension_candidate", bool((column or {}).get("is_dimension"))))
    if is_dimension and raw_semantic_type in {"text_candidate", "category_candidate"}:
        return "text"

    is_measure = bool(planner_roles.get("measure_candidate", bool((column or {}).get("is_measure"))))
    if is_measure and raw_semantic_type == "numeric_candidate":
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
        "non_null_count": int(raw.get("non_null_count", (column or {}).get("non_null_count", 0)) or 0),
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
    measure_candidate = bool(
        raw.get(
            "measure_candidate",
            bool((column or {}).get("is_measure"))
            or semantic_type in _MEASURE_SEMANTIC_TYPES
            or core_semantic_type == "numeric_candidate",
        )
    )
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


def column_is_measure(column: dict[str, Any]) -> bool:
    return bool(column_planner_roles(column).get("measure_candidate"))


def column_is_dimension(column: dict[str, Any]) -> bool:
    return bool(column_planner_roles(column).get("dimension_candidate"))


def column_is_date(column: dict[str, Any]) -> bool:
    return bool(column_structural_facts(column).get("is_date"))


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
    for legacy_key in (
        "is_primary_key",
        "is_foreign_key",
        "is_id",
        "is_date",
        "is_boolean",
        "sample_values",
        "null_count",
        "non_null_count",
        "unique_count",
        "min",
        "max",
        "min_value",
        "max_value",
        "business_description",
        "business_terms",
        "ai_semantic_type",
        "metric_type",
        "is_measure",
        "is_dimension",
    ):
        column.pop(legacy_key, None)
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
        if any(token in normalized_name for token in ("status", "state", "type", "category", "kind", "mode", "code", "reference")):
            return "category_candidate"
        if profiled_values:
            normalized_profile = [_normalize(value) for value in profiled_values if _normalize(value)]
            distinct_profile = list(dict.fromkeys(normalized_profile))
            if 0 < len(distinct_profile) <= 20 and all(len(value) <= 40 for value in distinct_profile):
                return "category_candidate"
        return "text_candidate"

    # Reflected or legacy schemas can omit a usable SQL type. Generic name
    # shape is still candidate evidence, but it never supplies business meaning.
    name_tokens = set(normalized_name.split("_"))
    if name_tokens & {"status", "state", "type", "category", "kind", "mode", "code", "reference"}:
        return "category_candidate"
    if name_tokens & {"name", "label", "title", "description", "text"}:
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


def real_foreign_key_relationships(schema_data: dict[str, Any]) -> list[dict[str, Any]]:
    """Return canonical relationship evidence for real database FK constraints."""
    relationships: list[dict[str, Any]] = []
    for table_name, table_data in (schema_data or {}).items():
        for foreign_key in table_data.get("foreign_keys", []):
            if foreign_key.get("inferred"):
                continue
            referenced_table = foreign_key.get("referenced_table") or foreign_key.get("to_table")
            referenced_column = foreign_key.get("referenced_column") or foreign_key.get("to_column")
            from_column = foreign_key.get("column") or foreign_key.get("from_column")
            if not referenced_table or not referenced_column or not from_column:
                continue
            relationships.append(
                {
                    "from_table": table_name,
                    "from_column": from_column,
                    "to_table": referenced_table,
                    "to_column": referenced_column,
                    "direction": "many-to-one",
                    "relationship_type": "foreign_key",
                    "confidence": 1.0,
                    "reason": "Database metadata declares this foreign key constraint.",
                    "evidence": ["foreign_key_constraint"],
                    "evidence_reasons": ["The database schema contains an explicit foreign key constraint."],
                    "safe_for_planner": True,
                    "is_inferred": False,
                    "is_fallback": False,
                    "source": "database_metadata",
                }
            )
    return relationships


def detect_relationships(schema_data: dict[str, Any]) -> list[dict[str, Any]]:
    """Detect real and safe fallback edges during KB construction only."""
    relationships = real_foreign_key_relationships(schema_data)
    table_names = set((schema_data or {}).keys())

    def _column_map(table_data: dict[str, Any]) -> dict[str, dict[str, Any]]:
        return {
            str(column.get("name", "")): column
            for column in table_data.get("columns", [])
            if str(column.get("name", ""))
        }

    def _column_type_family(column_type: Any) -> str:
        normalized = str(column_type or "").strip().lower()
        if any(token in normalized for token in ("date", "datetime", "timestamp", "time")):
            return "date"
        if any(token in normalized for token in ("decimal", "numeric", "float", "double", "real", "int", "integer", "bigint", "smallint", "tinyint")):
            return "numeric"
        if any(token in normalized for token in ("char", "text", "string", "json", "enum", "set")):
            return "text"
        if any(token in normalized for token in ("bool", "bit")):
            return "boolean"
        return "unknown"

    def _has_key_evidence(column_name: str, table_data: dict[str, Any], column: dict[str, Any] | None = None) -> bool:
        if column_name in set(table_data.get("primary_keys", [])):
            return True
        if column:
            structural = column.get("structural_facts")
            if isinstance(structural, dict) and structural.get("is_primary_key"):
                return True
            if column.get("unique") is True:
                return True
            profile = column_profile_facts(column)
            non_null_count = int(profile.get("non_null_count") or 0)
            unique_count = int(profile.get("unique_count") or 0)
            if non_null_count > 0 and unique_count == non_null_count:
                return True
        return False

    def _sample_overlap(left_column: dict[str, Any], right_column: dict[str, Any]) -> float:
        left_samples = {_normalize(value) for value in column_sample_values(left_column) if _normalize(value)}
        right_samples = {_normalize(value) for value in column_sample_values(right_column) if _normalize(value)}
        if not left_samples or not right_samples:
            return 0.0
        overlap = left_samples & right_samples
        if not overlap:
            return 0.0
        return round(len(overlap) / max(min(len(left_samples), len(right_samples)), 1), 4)

    for table_name, table_data in (schema_data or {}).items():
        primary_keys = set(table_data.get("primary_keys", []))
        table_columns = _column_map(table_data)
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

            target_table_data = schema_data.get(matched_table) or {}
            target_columns = _column_map(target_table_data)
            target_primary_keys = list(target_table_data.get("primary_keys", []))
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

            source_column = table_columns.get(column_name, column)
            target_column_data = target_columns.get(target_column, {})
            source_type_family = _column_type_family(source_column.get("type"))
            target_type_family = _column_type_family(target_column_data.get("type"))
            compatible_type = source_type_family != "unknown" and source_type_family == target_type_family
            target_key_evidence = _has_key_evidence(target_column, target_table_data, target_column_data)
            overlap_score = _sample_overlap(source_column, target_column_data)

            evidence = ["naming_pattern"]
            confidence = 0.56
            if compatible_type:
                evidence.append("compatible_data_type")
                confidence += 0.15
            if target_key_evidence:
                evidence.append("target_key_or_unique")
                confidence += 0.15
            if overlap_score >= 0.5:
                evidence.append("strong_sample_overlap")
                confidence += 0.08
            elif overlap_score > 0.0:
                evidence.append("sample_overlap")
                confidence += 0.04

            if not (compatible_type and target_key_evidence):
                continue
            confidence = round(min(confidence, 0.95), 2)
            if confidence < FALLBACK_RELATIONSHIP_MIN_CONFIDENCE:
                continue

            evidence_reasons = {
                "naming_pattern": "neutral *_id naming pattern aligns with the target table",
                "compatible_data_type": "source and target column SQL types are compatible",
                "target_key_or_unique": "target column is a declared primary key or profile-proven unique",
                "strong_sample_overlap": "sample values overlap strongly between the columns",
                "sample_overlap": "sample values overlap between the columns",
            }
            reason_details = [evidence_reasons[item] for item in evidence]
            relationships.append(
                {
                    "from_table": table_name,
                    "from_column": column_name,
                    "to_table": matched_table,
                    "to_column": target_column,
                    "direction": "many-to-one",
                    "relationship_type": "inferred",
                    "confidence": confidence,
                    "reason": "Fallback relationship inferred during KB build from " + ", ".join(reason_details) + ".",
                    "evidence": evidence,
                    "evidence_reasons": reason_details,
                    "safe_for_planner": True,
                    "is_inferred": True,
                    "is_fallback": True,
                    "source": "kb_build_inference",
                }
            )

    return relationships


def enrich_knowledge_base_schema_facts(
    knowledge_base: dict[str, Any],
    *,
    infer_relationships: bool = True,
) -> dict[str, Any]:
    enriched = deepcopy(knowledge_base or {})
    detected_relationships = detect_relationships(enriched) if infer_relationships else []

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
    "column_is_date",
    "column_is_dimension",
    "column_is_measure",
    "column_planner_roles",
    "column_profile_facts",
    "column_sample_values",
    "column_structural_facts",
    "detect_relationships",
    "FALLBACK_RELATIONSHIP_MIN_CONFIDENCE",
    "enrich_knowledge_base_for_erp",
    "enrich_knowledge_base_schema_facts",
    "CORE_SEMANTIC_TYPES",
    "is_core_semantic_type",
    "resolved_semantic_type",
    "real_foreign_key_relationships",
    "sanitize_business_purpose",
    "sanitize_short_text",
    "summarize_knowledge_base",
]
