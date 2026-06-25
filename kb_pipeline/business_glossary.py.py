"""
semantic/business_glossary.py
==============================
Generate and search a business glossary from the active knowledge base.

The glossary is dynamic and database-aware:
- The knowledge base remains the source of truth.
- The glossary is derived from current tables, columns, relationships, and
  optional AI business terms already attached to the knowledge base.
- Fallback behavior stays generic and never assumes demo or ERP table names.
"""

from __future__ import annotations

from typing import Dict, Any
import re

from kb_pipeline.schema_facts import column_ai_metadata, column_business_description, column_business_terms, column_structural_facts
from utils.file_utils import save_json
from utils.logger import get_logger

logger = get_logger()


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def _normalize_identifier(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", _normalize(value)).strip("_")


def _humanize(value: str) -> str:
    return _normalize_identifier(value).replace("_", " ").strip()


def _singularize(term: str) -> str:
    normalized = _humanize(term)
    if normalized.endswith("ies") and len(normalized) > 3:
        return normalized[:-3] + "y"
    if normalized.endswith("ses") and len(normalized) > 3:
        return normalized[:-2]
    if normalized.endswith("s") and not normalized.endswith("ss") and len(normalized) > 1:
        return normalized[:-1]
    return normalized


def _unique_preserve_order(items: list[str]) -> list[str]:
    seen = set()
    result = []
    for item in items:
        clean = str(item or "").strip()
        if not clean:
            continue
        key = clean.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(clean)
    return result


def _mapping_key(mapping: dict[str, Any]) -> tuple[str, str]:
    return (
        str(mapping.get("table", "")),
        str(mapping.get("column", "")),
    )


def _table_key(table_name: str) -> str:
    return str(table_name or "").strip()


def _preferred_column_order(column: dict[str, Any]) -> tuple[int, str]:
    semantic = str(column_ai_metadata(column).get("ai_semantic_type", "") or column.get("semantic_type", "")).lower()
    name = str(column.get("name", ""))
    if semantic == "name":
        return (0, name)
    if semantic in {"money", "quantity", "date", "status", "code"}:
        return (1, name)
    if name.endswith("_id") or semantic == "id":
        return (2, name)
    return (3, name)


def _representative_mappings(table_name: str, table_data: dict) -> list[dict[str, Any]]:
    mappings: list[dict[str, Any]] = []
    for column in sorted(table_data.get("columns", []), key=_preferred_column_order)[:4]:
        mappings.append(
            {
                "table": table_name,
                "column": column.get("name", ""),
                "type": column.get("type", ""),
                "confidence": "high",
            }
        )
    return mappings


def _generic_description_for_table(table_name: str) -> str:
    return f"Schema table: {_humanize(table_name)}."


def _generic_description_for_column(table_name: str, column: dict[str, Any]) -> str:
    return f"Schema column from {_humanize(table_name)}: {_humanize(column.get('name', 'column'))}."


def _relationship_context(table_name: str, table_data: dict[str, Any]) -> tuple[list[str], list[str], list[str]]:
    related_terms: list[str] = []
    related_tables: list[str] = []
    relationship_sources: list[str] = []
    for foreign_key in table_data.get("foreign_keys", []):
        referenced_table = str(foreign_key.get("referenced_table", "")).strip()
        related_table = _humanize(referenced_table)
        if related_table:
            related_terms.append(related_table)
            singular = _singularize(related_table)
            if singular and singular != related_table:
                related_terms.append(singular)
        if referenced_table:
            related_tables.append(referenced_table)
            relationship_sources.append("foreign_key")

    for relationship in table_data.get("relationships", []):
        from_table = str(relationship.get("from_table", "")).strip()
        to_table = str(relationship.get("to_table", "")).strip()
        direction = str(relationship.get("direction", "")).strip().lower()
        if direction == "incoming":
            related_table = _humanize(from_table)
        elif from_table == table_name:
            related_table = _humanize(to_table)
        else:
            related_table = _humanize(from_table)
        if related_table:
            related_terms.append(related_table)
            singular = _singularize(related_table)
            if singular and singular != related_table:
                related_terms.append(singular)
        related_identifier = to_table if from_table == table_name else from_table
        if related_identifier:
            related_tables.append(related_identifier)
            relationship_sources.append(str(relationship.get("source", "")).strip() or "relationship")

    return (
        _unique_preserve_order(related_terms),
        _unique_preserve_order(related_tables),
        _unique_preserve_order(relationship_sources),
    )


def _schema_terms_for_table(table_name: str) -> list[str]:
    human_table = _humanize(table_name)
    singular_table = _singularize(human_table)
    return _unique_preserve_order([human_table, singular_table])


def _schema_terms_for_column(column_name: str) -> list[str]:
    human_column = _humanize(column_name)
    singular_column = _singularize(human_column)
    return _unique_preserve_order([human_column, singular_column])


def _clean_ai_terms(values: list[str]) -> list[str]:
    cleaned: list[str] = []
    for value in values:
        term = str(value or "").strip()
        if not term:
            continue
        humanized = _humanize(term)
        if not humanized:
            continue
        cleaned.append(humanized)
    return _unique_preserve_order(cleaned)


def _term_source_confidence(
    term: str,
    *,
    schema_terms: list[str],
    ai_terms: list[str],
    ai_confidence: float,
    target_type: str,
) -> float:
    normalized_term = _normalize(term)
    schema_normalized = {_normalize(item) for item in schema_terms}
    ai_normalized = {_normalize(item) for item in ai_terms}
    if normalized_term in schema_normalized:
        return 0.99 if target_type == "table" else 0.95
    if normalized_term in ai_normalized:
        return round(min(max(ai_confidence or 0.8, 0.6), 0.94), 2)
    return 0.75


def _validated_primary_terms(
    terms: list[str],
    *,
    related_terms: list[str],
    related_tables: list[str],
    fallback_terms: list[str],
) -> list[str]:
    related_blocklist = {
        _normalize(item)
        for item in (
            list(related_terms or [])
            + [_humanize(table) for table in (related_tables or [])]
            + [_singularize(table) for table in [_humanize(value) for value in (related_tables or [])]]
        )
        if _normalize(item)
    }
    cleaned: list[str] = []
    for term in terms:
        readable = _humanize(term)
        normalized = _normalize(readable)
        if not readable or not normalized:
            continue
        if normalized in related_blocklist:
            continue
        cleaned.append(readable)
    cleaned = _unique_preserve_order(cleaned)
    if cleaned:
        return cleaned
    return _unique_preserve_order([_humanize(term) for term in fallback_terms if _humanize(term)])


def _normalized_mapped_tables(
    mapped_tables: list[str] | None,
    mapped_columns: list[dict[str, Any]] | None,
) -> list[str]:
    tables = list(mapped_tables or [])
    for mapping in mapped_columns or []:
        table_name = str(mapping.get("table", "")).strip()
        if table_name:
            tables.append(table_name)
    return _unique_preserve_order(tables)


def _normalized_related_tables(related_tables: list[str] | None) -> list[str]:
    return _unique_preserve_order([str(item).strip() for item in (related_tables or []) if str(item).strip()])


def _entry_sources(table_data: dict[str, Any], *, includes_ai: bool, includes_relationships: bool) -> list[str]:
    sources = ["schema_identifier"]
    if includes_ai:
        sources.append("ai_semantic_metadata")
    if includes_relationships:
        sources.append("relationship_context")
    return sources


def _primary_terms_from_entry(term: str, primary_terms: list[str] | None = None) -> list[str]:
    return _unique_preserve_order([_humanize(term), *(primary_terms or [])])


def _mapping_priority(*, mapping_kind: str, structural_facts: dict[str, Any] | None = None) -> int:
    if mapping_kind == "table":
        return 0
    facts = structural_facts or {}
    if facts.get("is_primary_key") and not facts.get("is_foreign_key"):
        return 1
    if not facts.get("is_foreign_key") and not facts.get("is_id"):
        return 2
    if facts.get("is_foreign_key"):
        return 4
    if facts.get("is_id"):
        return 5
    return 3


def _strip_internal_fields(glossary: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    cleaned: dict[str, dict[str, Any]] = {}
    for term, entry in glossary.items():
        target_type = str(entry.get("target_type", "") or "column").strip().lower()
        if target_type not in {"table", "column"}:
            target_type = "column"
        usage_scope = str(entry.get("usage_scope", "") or "primary_match").strip().lower()
        if usage_scope not in {"primary_match", "table_lookup", "column_lookup"}:
            usage_scope = "table_lookup" if target_type == "table" else "column_lookup"
        mapped_columns = list(entry.get("mapped_columns", []) or [])
        mapped_tables = _normalized_mapped_tables(list(entry.get("mapped_tables", []) or []), mapped_columns)
        related_tables = _normalized_related_tables(list(entry.get("related_tables", []) or []))
        related_terms = _unique_preserve_order(list(entry.get("related_terms", []) or []))
        primary_terms = _validated_primary_terms(
            list(entry.get("primary_terms", []) or entry.get("business_terms", []) or []),
            related_terms=related_terms,
            related_tables=related_tables,
            fallback_terms=[term],
        )
        confidence = float(entry.get("confidence", 0.0) or 0.0)
        confidence = round(min(max(confidence, 0.0), 1.0), 2) if confidence else 0.75
        cleaned[term] = {
            "description": entry.get("description", ""),
            "target_type": target_type,
            "mapped_tables": mapped_tables,
            "mapped_columns": mapped_columns,
            "related_tables": related_tables,
            "example_questions": _unique_preserve_order(list(entry.get("example_questions", []) or []))[:4],
            "primary_terms": primary_terms,
            "related_terms": related_terms,
            "business_terms": list(primary_terms),
            "usage_scope": usage_scope,
            "confidence": confidence,
            "sources": _unique_preserve_order(list(entry.get("sources", []) or [])),
            "relationship_sources": _unique_preserve_order(list(entry.get("relationship_sources", []) or [])),
        }
    return cleaned


def _add_entry(
    glossary: dict[str, dict[str, Any]],
    term: str,
    *,
    description: str,
    mapped_tables: list[str] | None = None,
    mappings: list[dict[str, Any]] | None = None,
    example_questions: list[str] | None = None,
    primary_terms: list[str] | None = None,
    related_terms: list[str] | None = None,
    related_tables: list[str] | None = None,
    sources: list[str] | None = None,
    relationship_sources: list[str] | None = None,
    mapping_kind: str = "column",
    structural_facts: dict[str, Any] | None = None,
    allow_mapping_merge: bool = True,
    usage_scope: str | None = None,
    confidence: float | None = None,
) -> None:
    normalized_term = _normalize(term)
    if not normalized_term:
        return

    entry = glossary.setdefault(
        normalized_term,
        {
            "description": description,
            "target_type": "table" if mapping_kind == "table" else "column",
            "mapped_tables": [],
            "mapped_columns": [],
            "related_tables": [],
            "example_questions": [],
            "primary_terms": [],
            "related_terms": [],
            "business_terms": [],
            "usage_scope": usage_scope or ("table_lookup" if mapping_kind == "table" else "column_lookup"),
            "confidence": round(min(max(float(confidence or 0.0), 0.0), 1.0), 2) if confidence is not None else 0.75,
            "sources": [],
            "relationship_sources": [],
            "__mapping_priority": 999,
        },
    )

    new_priority = _mapping_priority(
        mapping_kind=mapping_kind,
        structural_facts=structural_facts,
    )
    current_priority = int(entry.get("__mapping_priority", 999))
    should_refresh_single_mapping = (
        allow_mapping_merge
        or not mappings
        or not entry.get("mapped_columns")
        or new_priority < current_priority
    )

    if description and should_refresh_single_mapping and (
        not entry.get("description")
        or entry["description"].startswith("Schema ")
    ):
        entry["description"] = description

    if allow_mapping_merge:
        entry["mapped_tables"] = _unique_preserve_order(
            list(entry.get("mapped_tables", [])) + list(mapped_tables or [])
        )
        entry["related_tables"] = _unique_preserve_order(
            list(entry.get("related_tables", [])) + list(related_tables or [])
        )
        existing_keys = {_mapping_key(mapping) for mapping in entry.get("mapped_columns", [])}
        for mapping in mappings or []:
            key = _mapping_key(mapping)
            if key in existing_keys:
                continue
            existing_keys.add(key)
            entry["mapped_columns"].append(mapping)
    elif mappings:
        if should_refresh_single_mapping:
            entry["mapped_columns"] = list(mappings)
            entry["mapped_tables"] = _unique_preserve_order(list(mapped_tables or []))
            entry["related_tables"] = _unique_preserve_order(list(related_tables or []))
            entry["relationship_sources"] = _unique_preserve_order(list(relationship_sources or []))
            entry["__mapping_priority"] = new_priority

    entry["example_questions"] = _unique_preserve_order(
        list(entry.get("example_questions", [])) + list(example_questions or [])
    )[:4]
    entry["primary_terms"] = _unique_preserve_order(
        list(entry.get("primary_terms", []))
        + _primary_terms_from_entry(term, list(primary_terms or []))
    )[:8]
    if allow_mapping_merge:
        entry["related_terms"] = _unique_preserve_order(
            list(entry.get("related_terms", [])) + list(related_terms or [])
        )[:8]
    elif should_refresh_single_mapping:
        entry["related_terms"] = _unique_preserve_order(list(related_terms or []))[:8]
    entry["business_terms"] = list(entry["primary_terms"])
    if confidence is not None:
        entry["confidence"] = max(float(entry.get("confidence", 0.0) or 0.0), round(min(max(float(confidence), 0.0), 1.0), 2))
    entry["sources"] = _unique_preserve_order(
        list(entry.get("sources", [])) + list(sources or [])
    )
    if allow_mapping_merge:
        entry["relationship_sources"] = _unique_preserve_order(
            list(entry.get("relationship_sources", [])) + list(relationship_sources or [])
        )
    elif should_refresh_single_mapping:
        entry["relationship_sources"] = _unique_preserve_order(list(relationship_sources or []))


def get_default_business_glossary() -> Dict[str, Any]:
    """Return an empty glossary - no hardcoded fallback terms."""
    return {}


def generate_business_glossary(knowledge_base: dict, use_ai_enrichment: bool = False) -> Dict[str, Any]:
    """
    Generate a business glossary from the current knowledge base.

    The glossary is built from:
    1. Humanized table names and descriptions
    2. Humanized column names and metadata
    3. AI-enriched business terms already attached to columns/tables
    4. Real relationship context from foreign keys and relationship metadata
    """
    logger.info("Generating business glossary")

    if not knowledge_base:
        return {}

    glossary: dict[str, dict[str, Any]] = {}

    for table_name, table_data in knowledge_base.items():
        table_data = dict(table_data or {})
        human_table = _humanize(table_name)
        table_description = (
            str(table_data.get("business_description", "")).strip()
            or str(table_data.get("business_purpose", "")).strip()
            or _generic_description_for_table(table_name)
        )
        table_mappings = _representative_mappings(table_name, table_data)
        relationship_terms, related_tables, relationship_sources = _relationship_context(table_name, table_data)
        table_ai_terms = _clean_ai_terms(table_data.get("business_terms", []))
        table_ai_metadata = table_data.get("ai_metadata", {}) if isinstance(table_data.get("ai_metadata"), dict) else {}
        table_ai_confidence = float(table_ai_metadata.get("confidence", 0.0) or 0.0)
        table_terms = _unique_preserve_order(
            _schema_terms_for_table(table_name) + table_ai_terms
        )
        validated_table_terms = _validated_primary_terms(
            table_terms,
            related_terms=relationship_terms,
            related_tables=related_tables,
            fallback_terms=_schema_terms_for_table(table_name),
        )
        table_sources = _entry_sources(
            table_data,
            includes_ai=bool(table_ai_terms),
            includes_relationships=bool(relationship_terms),
        )

        for term in validated_table_terms:
            _add_entry(
                glossary,
                term,
                description=table_description,
                mapped_tables=[table_name],
                mappings=table_mappings,
                example_questions=list(table_data.get("possible_business_questions", [])),
                primary_terms=validated_table_terms,
                related_terms=relationship_terms,
                related_tables=related_tables,
                sources=table_sources,
                relationship_sources=relationship_sources,
                mapping_kind="table",
                allow_mapping_merge=True,
                usage_scope="table_lookup",
                confidence=_term_source_confidence(
                    term,
                    schema_terms=_schema_terms_for_table(table_name),
                    ai_terms=table_ai_terms,
                    ai_confidence=table_ai_confidence,
                    target_type="table",
                ),
            )

        for column in table_data.get("columns", []):
            column_name = str(column.get("name", "")).strip()
            if not column_name:
                continue

            column_description = (
                column_business_description(column)
                or _generic_description_for_column(table_name, column)
            )
            mapping = {
                "table": table_name,
                "column": column_name,
                "type": column.get("type", ""),
                "confidence": "high" if column_business_terms(column) else "medium",
            }
            column_ai_terms = _clean_ai_terms(column_business_terms(column))
            column_ai_meta = column_ai_metadata(column)
            column_ai_confidence = float(column_ai_meta.get("confidence", 0.0) or 0.0)
            column_terms = _unique_preserve_order(
                _schema_terms_for_column(column_name) + column_ai_terms
            )
            column_sources = _entry_sources(
                table_data,
                includes_ai=bool(column_ai_terms or table_ai_terms),
                includes_relationships=bool(relationship_terms),
            )
            structural_facts = column_structural_facts(column)
            column_related_terms = relationship_terms if structural_facts.get("is_foreign_key") else []
            column_related_tables = related_tables if structural_facts.get("is_foreign_key") else []
            column_relationship_sources = relationship_sources if structural_facts.get("is_foreign_key") else []
            validated_column_terms = _validated_primary_terms(
                column_terms,
                related_terms=column_related_terms,
                related_tables=column_related_tables,
                fallback_terms=_schema_terms_for_column(column_name),
            )

            for term in validated_column_terms:
                _add_entry(
                    glossary,
                    term,
                    description=column_description,
                    mapped_tables=[table_name],
                    mappings=[mapping],
                    example_questions=[],
                    primary_terms=validated_column_terms,
                    related_terms=column_related_terms,
                    related_tables=column_related_tables,
                    sources=column_sources,
                    relationship_sources=column_relationship_sources,
                    mapping_kind="column",
                    structural_facts=structural_facts,
                    allow_mapping_merge=False,
                    usage_scope="column_lookup",
                    confidence=_term_source_confidence(
                        term,
                        schema_terms=_schema_terms_for_column(column_name),
                        ai_terms=column_ai_terms,
                        ai_confidence=column_ai_confidence,
                        target_type="column",
                    ),
                )

    logger.info(f"Generated glossary with {len(glossary)} terms")
    return _strip_internal_fields(glossary)


def save_business_glossary(glossary: Dict[str, Any], output_path: str = "semantic/business_glossary.json") -> None:
    """Save the business glossary to a JSON file."""
    try:
        save_json(glossary, output_path)
        logger.info(f"Business glossary saved to {output_path}")
    except Exception as exc:
        logger.error(f"Failed to save business glossary: {exc}")
        raise


def load_business_glossary(glossary_path: str = "semantic/business_glossary.json") -> Dict[str, Any]:
    """
    Load the business glossary from a JSON file.

    Returns an empty fallback glossary when the file is missing, invalid, or
    unreadable. The fallback intentionally contains no fixed table mappings.
    """
    from utils.file_utils import load_json

    try:
        glossary = load_json(glossary_path)
        logger.info(f"Business glossary loaded from {glossary_path}")
        if isinstance(glossary, dict) and glossary:
            return _strip_internal_fields(glossary)
        return get_default_business_glossary()
    except FileNotFoundError:
        logger.warning(f"Business glossary not found at {glossary_path}")
        return get_default_business_glossary()
    except ValueError as exc:
        logger.error(f"Business glossary at {glossary_path} is invalid: {exc}")
        return get_default_business_glossary()
    except OSError as exc:
        logger.error(f"Business glossary at {glossary_path} is unreadable: {exc}")
        return get_default_business_glossary()
    except Exception as exc:
        logger.error(f"Failed to load business glossary: {exc}")
        return get_default_business_glossary()


def search_business_glossary(search_term: str, glossary: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """
    Search the business glossary for a term.

    Searches across:
    - Glossary term names
    - Descriptions
    - Business terms
    - Mapped table names
    - Mapped column names
    - Example questions
    """
    if glossary is None:
        glossary = load_business_glossary()

    if not glossary:
        logger.warning("Business glossary is empty or not loaded")
        return {}

    search_lower = _normalize(search_term)
    matches = {}

    for term, term_data in glossary.items():
        try:
            if search_lower in str(term).lower():
                matches[term] = term_data
                continue

            description = term_data.get("description", "")
            if isinstance(description, str) and search_lower in description.lower():
                matches[term] = term_data
                continue

            for business_term in (
                term_data.get("primary_terms", [])
                or term_data.get("business_terms", [])
                or []
            ):
                if isinstance(business_term, str) and search_lower in business_term.lower():
                    matches[term] = term_data
                    break
            if term in matches:
                continue

            for related_term in term_data.get("related_terms", []):
                if isinstance(related_term, str) and search_lower in related_term.lower():
                    matches[term] = term_data
                    break
            if term in matches:
                continue

            for table_name in term_data.get("mapped_tables", []):
                if isinstance(table_name, str) and search_lower in table_name.lower():
                    matches[term] = term_data
                    break
            if term in matches:
                continue

            for mapping in term_data.get("mapped_columns", []):
                table = mapping.get("table", "")
                if isinstance(table, str) and search_lower in table.lower():
                    matches[term] = term_data
                    break
                column = mapping.get("column", "")
                if isinstance(column, str) and search_lower in column.lower():
                    matches[term] = term_data
                    break
            if term in matches:
                continue

            for related_table in term_data.get("related_tables", []):
                if isinstance(related_table, str) and search_lower in related_table.lower():
                    matches[term] = term_data
                    break
            if term in matches:
                continue

            for question in term_data.get("example_questions", []):
                if isinstance(question, str) and search_lower in question.lower():
                    matches[term] = term_data
                    break
        except Exception as exc:
            logger.warning(f"Error processing term '{term}': {exc}")
            continue

    logger.info(f"Glossary search for '{search_term}' found {len(matches)} matches")
    return matches
