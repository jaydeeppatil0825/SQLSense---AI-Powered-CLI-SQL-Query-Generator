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

from collections import defaultdict
from typing import Dict, List, Any
import re

from utils.file_utils import save_json
from utils.logger import get_logger

logger = get_logger()

_GENERIC_FALLBACK_GLOSSARY = {
    "money": {
        "description": "Monetary values such as totals, prices, balances, or costs.",
        "mapped_columns": [],
        "example_questions": ["Show total amount"],
        "business_terms": ["amount", "total", "price", "cost", "balance"],
    },
    "quantity": {
        "description": "Counts or measurable quantities.",
        "mapped_columns": [],
        "example_questions": ["Show total quantity"],
        "business_terms": ["qty", "count", "units", "stock"],
    },
    "date": {
        "description": "Date or time information.",
        "mapped_columns": [],
        "example_questions": ["Show latest records"],
        "business_terms": ["time", "month", "year", "recent"],
    },
    "status": {
        "description": "State or lifecycle information.",
        "mapped_columns": [],
        "example_questions": ["Show pending records"],
        "business_terms": ["state", "active", "inactive", "pending"],
    },
    "name": {
        "description": "Names or labels used to identify records.",
        "mapped_columns": [],
        "example_questions": ["Show names"],
        "business_terms": ["title", "label"],
    },
    "code": {
        "description": "Codes, references, or external identifiers.",
        "mapped_columns": [],
        "example_questions": ["Show reference codes"],
        "business_terms": ["reference", "ref", "identifier"],
    },
}


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def _normalize_identifier(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", _normalize(value)).strip("_")


def _tokenize(value: str) -> list[str]:
    return [token for token in re.split(r"[^a-z0-9]+", _normalize(value)) if token]


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


def _generic_description_for_column(table_name: str, column: dict[str, Any]) -> str:
    semantic_type = str(column.get("semantic_type", "general")).lower()
    column_term = _humanize(column.get("name", "column"))
    table_term = _humanize(table_name)

    if semantic_type == "money":
        return f"Monetary field from {table_term}: {column_term}."
    if semantic_type == "quantity":
        return f"Quantity field from {table_term}: {column_term}."
    if semantic_type == "date":
        return f"Date or time field from {table_term}: {column_term}."
    if semantic_type == "status":
        return f"Status field from {table_term}: {column_term}."
    if semantic_type == "name":
        return f"Name field from {table_term}: {column_term}."
    if semantic_type == "code":
        return f"Reference field from {table_term}: {column_term}."
    return f"Column from {table_term}: {column_term}."


def _example_questions_for_column(table_name: str, column: dict[str, Any]) -> list[str]:
    semantic_type = str(column.get("semantic_type", "general")).lower()
    table_term = _humanize(table_name)
    column_term = _humanize(column.get("name", "column"))

    if semantic_type == "money":
        return [f"Show total {column_term}", f"Show {column_term} from {table_term}"]
    if semantic_type == "quantity":
        return [f"Show total {column_term}", f"Show {column_term} by record"]
    if semantic_type == "date":
        return [f"Show latest {table_term}", f"Show {table_term} by {column_term}"]
    if semantic_type == "status":
        return [f"Show {table_term} by {column_term}", f"Show pending {table_term}"]
    return [f"Show {column_term}", f"Show {table_term}"]


def _example_questions_for_table(table_name: str) -> list[str]:
    table_term = _humanize(table_name)
    return [f"Show {table_term}", f"Count {table_term}"]


def _table_alias_terms(table_name: str) -> list[str]:
    human_table = _humanize(table_name)
    tokens = [token for token in human_table.split() if token]
    aliases = [human_table, _singularize(human_table), table_name]
    if len(tokens) > 1:
        aliases.append(tokens[0])
        aliases.append(tokens[-1])
    return _unique_preserve_order(aliases)


def _column_synonym_aliases(column_name: str) -> list[str]:
    human_column = _humanize(column_name)
    tokens = [token for token in human_column.split() if token]
    aliases = [human_column]

    if {"gst", "vat", "tax"} & set(tokens):
        aliases.extend(["tax", "gst", "vat"])
    if {"balance", "outstanding", "due"} & set(tokens):
        aliases.extend(["balance", "outstanding", "due"])

    return _unique_preserve_order(aliases)


def _add_entry(
    glossary: dict[str, dict[str, Any]],
    term: str,
    *,
    description: str,
    mappings: list[dict[str, Any]] | None = None,
    example_questions: list[str] | None = None,
    business_terms: list[str] | None = None,
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
        },
    )

    if description and (
        not entry.get("description")
        or entry["description"].startswith("Column from ")
        or entry["description"].startswith("Table from ")
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


def get_default_business_glossary() -> Dict[str, Any]:
    """Return a generic fallback glossary with no database-specific mappings."""
    return {
        term: {
            "description": data["description"],
            "mapped_columns": list(data.get("mapped_columns", [])),
            "example_questions": list(data.get("example_questions", [])),
            "business_terms": list(data.get("business_terms", [])),
        }
        for term, data in _GENERIC_FALLBACK_GLOSSARY.items()
    }


def _semantic_aliases(term: str, semantic_type: str) -> list[str]:
    aliases = [term]
    if semantic_type == "money":
        aliases.extend(["amount", "total", "value", "balance"])
    elif semantic_type == "quantity":
        aliases.extend(["qty", "count", "stock", "units"])
    elif semantic_type == "date":
        aliases.extend(["time", "month", "year"])
    elif semantic_type == "status":
        aliases.extend(["state"])
    elif semantic_type == "name":
        aliases.extend(["label", "title"])
    elif semantic_type == "code":
        aliases.extend(["reference", "ref"])
    return _unique_preserve_order(aliases)


def _build_semantic_rollups(knowledge_base: dict) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for table_name, table_data in knowledge_base.items():
        for column in table_data.get("columns", []):
            semantic_type = str(column.get("semantic_type", "general")).lower()
            if semantic_type in {"general", "", "text", "boolean", "id"}:
                continue
            grouped[semantic_type].append(
                {
                    "table": table_name,
                    "column": column.get("name", ""),
                    "type": column.get("type", ""),
                    "confidence": "medium",
                }
            )
    return grouped


def generate_business_glossary(knowledge_base: dict, use_ai_enrichment: bool = False) -> Dict[str, Any]:
    """
    Generate a business glossary from the current knowledge base.

    The glossary is built from:
    1. Table names and table descriptions
    2. Column names and semantic types
    3. AI-enriched business terms already attached to columns/tables
    4. Generic semantic rollups such as money/date/quantity
    """
    logger.info("Generating business glossary")

    if not knowledge_base:
        return {}

    glossary: dict[str, dict[str, Any]] = {}

    for table_name, table_data in knowledge_base.items():
        human_table = _humanize(table_name)
        singular_table = _singularize(human_table)
        table_description = (
            str(table_data.get("business_description", "")).strip()
            or str(table_data.get("business_purpose", "")).strip()
            or f"Table for {human_table}."
        )
        table_mappings = _representative_mappings(table_name, table_data)
        table_questions = list(table_data.get("possible_business_questions", [])) or _example_questions_for_table(table_name)

        _add_entry(
            glossary,
            human_table,
            description=table_description,
            mappings=table_mappings,
            example_questions=table_questions,
            business_terms=_table_alias_terms(table_name),
        )
        if singular_table != human_table:
            _add_entry(
                glossary,
                singular_table,
                description=table_description,
                mappings=table_mappings,
                example_questions=table_questions,
                business_terms=_table_alias_terms(table_name),
            )

        for column in table_data.get("columns", []):
            column_name = str(column.get("name", "")).strip()
            if not column_name:
                continue

            semantic_type = str(column.get("semantic_type", "general")).lower()
            human_column = _humanize(column_name)
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

            aliases = _semantic_aliases(human_column, semantic_type)
            aliases.extend(_column_synonym_aliases(column_name))
            if use_ai_enrichment:
                aliases.extend(str(term).strip() for term in column.get("business_terms", []) if str(term).strip())
            else:
                aliases.extend(str(term).strip() for term in column.get("business_terms", []) if str(term).strip())

            for alias in _unique_preserve_order(aliases):
                _add_entry(
                    glossary,
                    alias,
                    description=column_description,
                    mappings=[mapping],
                    example_questions=_example_questions_for_column(table_name, column),
                    business_terms=[human_column, table_name, human_table],
                )

    for semantic_type, mappings in _build_semantic_rollups(knowledge_base).items():
        base_entry = _GENERIC_FALLBACK_GLOSSARY.get(semantic_type)
        if not base_entry:
            continue
        _add_entry(
            glossary,
            semantic_type,
            description=base_entry["description"],
            mappings=mappings[:8],
            example_questions=base_entry.get("example_questions", []),
            business_terms=base_entry.get("business_terms", []),
        )
        for alias in base_entry.get("business_terms", []):
            _add_entry(
                glossary,
                alias,
                description=base_entry["description"],
                mappings=mappings[:8],
                example_questions=base_entry.get("example_questions", []),
                business_terms=[semantic_type],
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

    Returns a generic fallback glossary when the file is missing, invalid, or
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
    - Business-term aliases
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
