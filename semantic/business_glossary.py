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


def _preferred_column_order(column: dict[str, Any]) -> tuple[int, str]:
    semantic = str(column.get("semantic_type", "")).lower()
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


def _relationship_terms(table_name: str, table_data: dict[str, Any]) -> list[str]:
    related_terms: list[str] = []
    for foreign_key in table_data.get("foreign_keys", []):
        related_table = _humanize(foreign_key.get("referenced_table", ""))
        if related_table:
            related_terms.append(related_table)
            singular = _singularize(related_table)
            if singular and singular != related_table:
                related_terms.append(singular)

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

    return _unique_preserve_order(related_terms)


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


def _entry_sources(table_data: dict[str, Any], *, includes_ai: bool, includes_relationships: bool) -> list[str]:
    sources = ["schema_identifier"]
    if includes_ai:
        sources.append("ai_semantic_metadata")
    if includes_relationships:
        sources.append("relationship_context")
    return sources


def _add_entry(
    glossary: dict[str, dict[str, Any]],
    term: str,
    *,
    description: str,
    mappings: list[dict[str, Any]] | None = None,
    example_questions: list[str] | None = None,
    business_terms: list[str] | None = None,
    sources: list[str] | None = None,
) -> None:
    normalized_term = _normalize(term)
    if not normalized_term:
        return

    entry = glossary.setdefault(
        normalized_term,
        {
            "description": description,
            "mapped_columns": [],
            "example_questions": [],
            "business_terms": [],
            "sources": [],
        },
    )

    if description and (
        not entry.get("description")
        or entry["description"].startswith("Schema ")
    ):
        entry["description"] = description

    existing_keys = {_mapping_key(mapping) for mapping in entry.get("mapped_columns", [])}
    for mapping in mappings or []:
        key = _mapping_key(mapping)
        if key in existing_keys:
            continue
        existing_keys.add(key)
        entry["mapped_columns"].append(mapping)

    entry["example_questions"] = _unique_preserve_order(
        list(entry.get("example_questions", [])) + list(example_questions or [])
    )[:4]
    entry["business_terms"] = _unique_preserve_order(
        list(entry.get("business_terms", [])) + [normalized_term] + list(business_terms or [])
    )[:8]
    entry["sources"] = _unique_preserve_order(
        list(entry.get("sources", [])) + list(sources or [])
    )


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
        relationship_terms = _relationship_terms(table_name, table_data)
        table_ai_terms = _clean_ai_terms(table_data.get("business_terms", []))
        table_terms = _unique_preserve_order(
            _schema_terms_for_table(table_name) + table_ai_terms
        )
        table_sources = _entry_sources(
            table_data,
            includes_ai=bool(table_ai_terms),
            includes_relationships=bool(relationship_terms),
        )

        for term in table_terms:
            _add_entry(
                glossary,
                term,
                description=table_description,
                mappings=table_mappings,
                example_questions=list(table_data.get("possible_business_questions", [])),
                business_terms=_unique_preserve_order(table_terms + relationship_terms),
                sources=table_sources,
            )

        for column in table_data.get("columns", []):
            column_name = str(column.get("name", "")).strip()
            if not column_name:
                continue

            column_description = (
                str(column.get("business_description", "")).strip()
                or _generic_description_for_column(table_name, column)
            )
            mapping = {
                "table": table_name,
                "column": column_name,
                "type": column.get("type", ""),
                "confidence": "high" if column.get("business_terms") else "medium",
            }
            column_ai_terms = _clean_ai_terms(column.get("business_terms", []))
            column_terms = _unique_preserve_order(
                _schema_terms_for_column(column_name) + column_ai_terms
            )
            column_sources = _entry_sources(
                table_data,
                includes_ai=bool(column_ai_terms or table_ai_terms),
                includes_relationships=bool(relationship_terms),
            )
            related_terms = relationship_terms if str(column.get("name", "")).endswith("_id") or column.get("is_foreign_key") else []

            for term in column_terms:
                _add_entry(
                    glossary,
                    term,
                    description=column_description,
                    mappings=[mapping],
                    example_questions=[],
                    business_terms=_unique_preserve_order(column_terms + related_terms + [human_table]),
                    sources=column_sources,
                )

    logger.info(f"Generated glossary with {len(glossary)} terms")
    return glossary


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
        return glossary if isinstance(glossary, dict) and glossary else get_default_business_glossary()
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

            for business_term in term_data.get("business_terms", []):
                if isinstance(business_term, str) and search_lower in business_term.lower():
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

            for question in term_data.get("example_questions", []):
                if isinstance(question, str) and search_lower in question.lower():
                    matches[term] = term_data
                    break
        except Exception as exc:
            logger.warning(f"Error processing term '{term}': {exc}")
            continue

    logger.info(f"Glossary search for '{search_term}' found {len(matches)} matches")
    return matches
