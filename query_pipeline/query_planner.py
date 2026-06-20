"""
Structured query planning and relevant-table selection.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import date
from typing import Any
import re

from kb_pipeline.schema_facts import enrich_knowledge_base_schema_facts
from kb_pipeline.vector import VectorRetriever
from utils.logger import get_logger

logger = get_logger()


_QUESTION_STOP_WORDS = {
    "show",
    "list",
    "display",
    "get",
    "fetch",
    "what",
    "which",
    "where",
    "when",
    "how",
    "many",
    "current",
    "latest",
    "recent",
    "all",
    "records", 
    "record",
    "data",
    "table",
    
}


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip().lower())


def _normalize_identifier(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", _normalize(text)).strip("_")


def _humanize(text: str) -> str:
    return _normalize_identifier(text).replace("_", " ").strip()


def _singularize_token(token: str) -> str:
    if token.endswith("ies") and len(token) > 3:
        return token[:-3] + "y"
    if token.endswith("ses") and len(token) > 3:
        return token[:-2]
    if token.endswith("s") and not token.endswith("ss") and len(token) > 1:
        return token[:-1]
    return token


def _tokenize(text: str) -> list[str]:
    return [token for token in re.split(r"[^a-z0-9]+", _normalize(text)) if token]


def _content_terms(question: str) -> list[str]:
    return [token for token in _tokenize(question) if token not in _QUESTION_STOP_WORDS]


def _extract_limit(question: str) -> int | None:
    match = re.search(
        r"\b(?:top|first|last|latest|recent|limit|show|get|fetch)\s+(\d+)\b"
        r"|\b(\d+)\s+(?:rows?|records?|results?|items?)\b",
        question,
        re.IGNORECASE,
    )
    if match:
        return int(match.group(1) or match.group(2))
    return None


def _detect_sorting(question: str) -> dict[str, str] | None:
    normalized = _normalize(question)

    explicit_match = re.search(r"\b(?:sorted|sort|ordered|order)\s+by\s+([a-z0-9_ ]+)", normalized)
    if explicit_match:
        sort_value = explicit_match.group(1)
        sort_value = re.split(r"\b(?:for|with|where|in|on|from|and|or|limit|top|first|last)\b", sort_value)[0].strip()
        if sort_value:
            return {"direction": "asc", "by": sort_value}

    if re.search(r"\b(?:latest|recent|newest|most recent|last)\b", normalized):
        return {"direction": "desc", "by": "date"}

    if re.search(r"\b(?:oldest|earliest|first)\b", normalized):
        return {"direction": "asc", "by": "date"}
    
    top_n_match = re.search(r"\btop\s+\d+\s+[a-z0-9_ ]+?\s+by\s+([a-z0-9_ ]+)", normalized)
    if top_n_match:
        sort_value = top_n_match.group(1).strip()
        if sort_value:
            return {"direction": "desc", "by": sort_value}

    return None

def _detect_intent(question: str) -> str:
    normalized = _normalize(question)

    if re.search(r"\b(count|how many|number of)\b", normalized):
        return "count"
    
    if re.search(r"\b(average|avg|mean)\b", normalized):
        return "average"
    
    if re.search(r"\b(total|sum)\b", normalized):
        return "total"
    
    if re.search(r"\b(compare|comparison|versus|vs)\b", normalized):
        return "comparison"
    if re.search(r"\b(trend|monthly|by month|per month|by date|over time)\b", normalized):
        return "trend"
    if re.search(r"\b(highest|largest|lowest|smallest|most|least)\b", normalized):
        return "top_n"
    if re.search(r"\btop\s+\d+\b", normalized):
        if " by " in normalized:
            return "top_n"
        return "list"
    if re.search(r"\b(list|show|display|fetch|get)\b", normalized):
        return "list"
    return "list"



def _detect_dimension(question: str, intent: str | None = None) -> str | None:
    normalized = _normalize(question)
    if str(intent or "") == "top_n":
        top_n_match = re.search(r"\btop\s+\d+\s+([a-z0-9_ ]+?)\s+by\s+([a-z0-9_ ]+)", normalized)
        if top_n_match:
            return top_n_match.group(1).strip() or None
    wise_match = re.search(r"\b([a-z0-9_ ]+?)\s+wise\b", normalized)
    if wise_match:
        tokens = wise_match.group(1).strip().split()
        if tokens:
            return tokens[-1]
    if re.search(r"\b(?:sorted|sort|ordered|order)\s+by\s+", normalized):
        return None
    match = re.search(r"\b(?:by|per)\s+([a-z0-9_ ]+)", normalized)
    if not match:
        return None
    value = match.group(1)
    value = re.split(r"\b(?:for|with|where|in|on|from|and)\b", value)[0].strip()
    return value or None


def _detect_date_range(question: str) -> dict | None:
    normalized = _normalize(question)
    today = date.today()

    if "this month" in normalized or "current month" in normalized:
        start_date = today.replace(day=1)
        if start_date.month == 12:
            end_date = start_date.replace(year=start_date.year + 1, month=1)
        else:
            end_date = start_date.replace(month=start_date.month + 1)
        return {
            "label": "this_month",
            "start": start_date.isoformat(),
            "end_exclusive": end_date.isoformat(),
        }

    if "this year" in normalized or "current year" in normalized:
        return {
            "label": "this_year",
            "start": f"{today.year}-01-01",
            "end_exclusive": f"{today.year + 1}-01-01",
        }

    year_match = re.search(r"\b(?:in|for|during)\s+(20\d{2})\b|\b(20\d{2})\b", normalized)
    if year_match:
        year = int(year_match.group(1) or year_match.group(2))
        return {
            "label": f"year_{year}",
            "start": f"{year}-01-01",
            "end_exclusive": f"{year + 1}-01-01",
        }

    return None





def _normalized_sample_values(column: dict[str, Any]) -> list[tuple[str, str]]:
    values = []
    for raw_value in column.get("sample_values", []) or []:
        if raw_value is None:
            continue
        normalized = _normalize(str(raw_value))
        if normalized:
            values.append((normalized, str(raw_value)))
    return values


def _column_can_hold_status(column: dict[str, Any]) -> bool:
    return str(column.get("semantic_type", "")).lower() == "status"


def _column_supports_sample_filter(column: dict[str, Any]) -> bool:
    semantic_type = str(column.get("semantic_type", "")).lower()
    if semantic_type in {"status", "name", "text", "code", "reference"}:
        return True

    column_type = _normalize(str(column.get("type", "")))
    return any(token in column_type for token in ("char", "text", "enum"))




def _detect_runtime_filters(question: str, candidate_tables: dict[str, Any]) -> list[dict[str, Any]]:
    normalized_question = _normalize(question)
    question_terms = set(_tokenize(question))
    requested_limit = _extract_limit(question)
    filters: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()

    for table_name, table_data in candidate_tables.items():
        for column in table_data.get("columns", []):
            if not _column_supports_sample_filter(column):
                continue
            column_name = str(column.get("name", ""))
            for normalized_value, raw_value in _normalized_sample_values(column):
                value_terms = set(_tokenize(normalized_value))
                if not value_terms:
                    continue
                if requested_limit is not None and normalized_value == str(requested_limit):
                    continue
                if normalized_value not in normalized_question and not value_terms <= question_terms:
                    continue

                signature = (table_name, column_name, raw_value)
                if signature in seen:
                    continue
                seen.add(signature)
                filters.append(
                    {
                        "type": "status" if _column_can_hold_status(column) else "value",
                        "table": table_name,
                        "column": column_name,
                        "value": raw_value,
                        "term": normalized_value,
                    }
                )

    return filters


def _extract_preposition_filter_value(question: str) -> str | None:
    match = re.search(r"\b(?:from|in)\s+([A-Za-z][A-Za-z0-9 ]*)$", str(question or "").strip(), re.IGNORECASE)
    if not match:
        return None
    value = match.group(1).strip()
    value = re.split(r"\b(?:by|with|where|and|or|order|sorted|latest|top)\b", value)[0].strip()
    if not value or re.fullmatch(r"20\d{2}", value, re.IGNORECASE):
        return None
    return value


def _column_supports_generic_text_filter(column: dict[str, Any]) -> bool:
    semantic_type = str(column.get("semantic_type", "")).lower()
    if semantic_type in {"status", "date", "id", "money", "quantity", "percentage"}:
        return False
    column_type = _normalize(str(column.get("type", "")))
    return any(token in column_type for token in ("char", "text", "enum"))


def _column_metadata_tokens(column: dict[str, Any]) -> set[str]:
    tokens = set(_tokenize(str(column.get("name", ""))))
    tokens.update(_tokenize(str(column.get("business_description", ""))))
    for term in column.get("business_terms", []) or []:
        tokens.update(_tokenize(str(term)))
    return tokens


def _generic_filter_column_score(column: dict[str, Any], filter_value: str) -> float:
    score = 0.0
    semantic_type = str(column.get("semantic_type", "")).lower()
    if semantic_type in {"name", "text", "code", "reference"}:
        score += 1.0

    normalized_filter = _normalize(filter_value)
    filter_terms = set(_tokenize(filter_value))
    metadata_overlap = len(filter_terms & _column_metadata_tokens(column))
    if metadata_overlap:
        score += metadata_overlap * 0.45

    for normalized_value, _ in _normalized_sample_values(column):
        if normalized_filter == normalized_value:
            score += 4.0
            break
        if normalized_filter in normalized_value or normalized_value in normalized_filter:
            score += 1.5
            break

    return score


def _detect_generic_value_filters(question: str, candidate_tables: dict[str, Any]) -> list[dict[str, Any]]:
    filter_value = _extract_preposition_filter_value(question)
    if not filter_value:
        return []

    ranked: list[tuple[float, dict[str, Any]]] = []
    for table_name, table_data in candidate_tables.items():
        for column in table_data.get("columns", []):
            if not _column_supports_generic_text_filter(column):
                continue
            score = _generic_filter_column_score(column, filter_value)
            if score <= 0:
                continue
            ranked.append(
                (
                    score,
                    {
                        "type": "value",
                        "table": table_name,
                        "column": str(column.get("name", "")),
                        "value": filter_value,
                        "term": filter_value,
                    },
                )
            )

    ranked.sort(key=lambda item: (-item[0], item[1]["table"], item[1]["column"]))
    if not ranked:
        return []
    top_score = ranked[0][0]
    return [filter_data for score, filter_data in ranked if score == top_score][:1]


def _default_limit_for_intent(intent: str) -> int | None:
    if intent in {"list", "top_n"}:
        return 50
    return None


def _semantic_hints(intent: str, date_range: dict[str, Any] | None, sorting: dict[str, str] | None) -> set[str]:
    hints = set()

    if intent == "trend" or date_range or str((sorting or {}).get("by", "")).lower() == "date":
        hints.add("date")

    return hints


def _primary_metric_hint(semantic_hints: set[str]) -> str | None:
    return "date" if "date" in semantic_hints else None


def _should_preserve_simple_list_metric(plan: dict[str, Any]) -> bool:
    if str(plan.get("intent") or "") != "list":
        return True
    if plan.get("dimension") or plan.get("grouping"):
        return True
    return False


def _is_simple_primary_table_question(plan: dict[str, Any]) -> bool:
    if (
        str(plan.get("intent") or "") in {"count", "total", "average"}
        and not plan.get("dimension")
        and not plan.get("grouping")
    ):
        return True

    return (
        str(plan.get("intent") or "") in {"list", "count"}
        and not plan.get("dimension")
        and not plan.get("grouping")
        and not plan.get("filters")
        and not plan.get("date_range")
        and not (set(plan.get("semantic_hints") or set()) & {"date", "status"})
    )


def _table_name_matches_question(question: str, table_name: str) -> bool:
    normalized_question = _normalize(question)
    human_table = _humanize(table_name)
    if human_table and human_table in normalized_question:
        return True

    question_terms = set(_tokenize(question))
    table_terms: set[str] = set()
    for token in _tokenize(table_name):
        table_terms.add(token)
        table_terms.add(_singularize_token(token))

    return bool(table_terms & question_terms)


def _retrieve_with_vector(
    question: str,
    knowledge_base: dict,
    business_glossary: dict | None = None,
    retriever: VectorRetriever | None = None,
) -> dict:
    """Use vector retrieval to find relevant tables, columns, and glossary terms."""
    try:
        active_retriever = retriever
        if active_retriever is None:
            return {
                "table_names": [],
                "tables": [],
                "columns": [],
                "glossary_terms": [],
                "relationships": [],
                "semantic_descriptions": [],
                "profiling_hints": [],
                "retriever_status": {},
                "used_vector": False,
                "error": "vector retriever unavailable",
            }

        return {
            "table_names": active_retriever.get_relevant_tables(question, top_k=5),
            "tables": active_retriever.get_relevant_table_details(question, top_k=5),
            "columns": active_retriever.get_relevant_columns(question, top_k=10),
            "glossary_terms": active_retriever.get_relevant_glossary_terms(question, top_k=5),
            "relationships": active_retriever.get_relevant_relationships(question, top_k=5),
            "semantic_descriptions": active_retriever.get_relevant_semantic_descriptions(question, top_k=8),
            "profiling_hints": active_retriever.get_relevant_profiling_hints(question, top_k=8),
            "retriever_status": active_retriever.get_status(),
            "used_vector": True,
        }
    except Exception as exc:
        logger.warning(f"Vector retrieval error: {exc}")
        return {
            "table_names": [],
            "tables": [],
            "columns": [],
            "glossary_terms": [],
            "relationships": [],
            "semantic_descriptions": [],
            "profiling_hints": [],
            "retriever_status": {},
            "used_vector": False,
            "error": str(exc),
        }


def _glossary_matches(question: str, glossary: dict | None) -> list[tuple[str, dict[str, Any]]]:
    if not glossary:
        return []

    normalized_question = _normalize(question)
    question_terms = set(_content_terms(question))
    matches = []
    for term, term_data in glossary.items():
        normalized_term = _normalize(term)
        term_tokens = set(_tokenize(term))
        alias_tokens = {
            token
            for alias in term_data.get("business_terms", [])
            for token in _tokenize(alias)
        }
        if normalized_term and normalized_term in normalized_question:
            matches.append((term, term_data))
            continue
        if term_tokens and term_tokens <= question_terms:
            matches.append((term, term_data))
            continue
        if alias_tokens and alias_tokens & question_terms:
            matches.append((term, term_data))
    return matches


def _glossary_alias_hits_question(question: str, term: str, term_data: dict[str, Any]) -> bool:
    normalized_question = _normalize(question)
    question_terms = set(_content_terms(question))
    normalized_term = _normalize(term)
    term_tokens = set(_tokenize(term))

    if normalized_term and normalized_term in normalized_question:
        return True
    if term_tokens and term_tokens <= question_terms:
        return True

    for alias in term_data.get("business_terms", []) or []:
        normalized_alias = _normalize(alias)
        alias_tokens = set(_tokenize(alias))
        if normalized_alias and normalized_alias in normalized_question:
            return True
        if alias_tokens and alias_tokens <= question_terms:
            return True

    return False


def _glossary_mapped_tables(term_data: dict[str, Any]) -> set[str]:
    return {
        str(mapping.get("table", "")).strip()
        for mapping in term_data.get("mapped_columns", []) or []
        if str(mapping.get("table", "")).strip()
    }


def _has_strong_glossary_table_match(
    question: str,
    table_name: str,
    glossary_matches: list[tuple[str, dict[str, Any]]],
) -> bool:
    for term, term_data in glossary_matches:
        mapped_tables = _glossary_mapped_tables(term_data)
        if table_name not in mapped_tables:
            continue
        if len(mapped_tables) != 1:
            continue
        if _glossary_alias_hits_question(question, term, term_data):
            return True
    return False


def _enriched_kb(knowledge_base: dict) -> dict:
    if not knowledge_base:
        return {}
    return enrich_knowledge_base_schema_facts(deepcopy(knowledge_base))


def _question_text_for_table(table_name: str, table_data: dict) -> str:
    pieces = [
        table_name,
        _humanize(table_name),
        str(table_data.get("business_purpose", "")),
        str(table_data.get("business_description", "")),
    ]
    for column in table_data.get("columns", []):
        pieces.append(str(column.get("name", "")))
        pieces.extend(str(term) for term in column.get("business_terms", []))
    return " ".join(piece for piece in pieces if piece)


def _table_score(
    plan: dict[str, Any],
    table_name: str,
    table_data: dict[str, Any],
    glossary_matches: list[tuple[str, dict[str, Any]]],
    vector_results: dict[str, Any] | None,
) -> tuple[float, list[str]]:
    score = 0.0
    reasons: list[str] = []
    normalized_question = _normalize(plan.get("question", ""))
    question_terms = set(plan.get("question_terms", []))
    table_text = _question_text_for_table(table_name, table_data).lower()
    table_tokens = set(_tokenize(table_text))

    overlap = len(question_terms & table_tokens)
    if overlap:
        score += overlap * 0.45
        reasons.append(f"matched {overlap} question term(s) in table metadata")

    human_table = _humanize(table_name)
    if human_table and human_table in _normalize(plan.get("question", "")):
        score += 1.6
        reasons.append("table name appears directly in the question")

    for semantic_hint in plan.get("semantic_hints", set()):
        if any(str(column.get("semantic_type", "")).lower() == semantic_hint for column in table_data.get("columns", [])):
            score += 0.8
            reasons.append(f"contains {semantic_hint} column(s)")

    dimension = str(plan.get("dimension") or "").strip()
    if dimension:
        dimension_tokens = set(_tokenize(dimension))
        if dimension_tokens & table_tokens:
            score += 0.9
            reasons.append(f"dimension '{dimension}' matched table metadata")

    if plan.get("date_range") and any(str(column.get("semantic_type", "")).lower() == "date" for column in table_data.get("columns", [])):
        score += 0.6
        reasons.append("date filter needs a date column")

    if any(filter_data.get("type") == "status" for filter_data in plan.get("filters", [])):
        if any(str(column.get("semantic_type", "")).lower() == "status" for column in table_data.get("columns", [])):
            score += 0.6
            reasons.append("status filter needs a status column")

    for term, term_data in glossary_matches:
        direct_term_match = _glossary_alias_hits_question(plan.get("question", ""), term, term_data)
        mapped_tables = _glossary_mapped_tables(term_data)
        mapped_table_count = len(mapped_tables)
        glossary_boost = 1.1 if direct_term_match else 0.35
        if _is_simple_primary_table_question(plan):
            if direct_term_match and mapped_table_count == 1:
                glossary_boost = 1.35
            elif direct_term_match and mapped_table_count == 2:
                glossary_boost = 0.55
            elif direct_term_match:
                glossary_boost = 0.2
            else:
                glossary_boost = 0.1
        for mapping in term_data.get("mapped_columns", []):
            if mapping.get("table") == table_name:
                score += glossary_boost
                reasons.append(f"glossary term '{term}' mapped to this table")
                break

    if vector_results:
        vector_table_names = set(vector_results.get("table_names") or [])
        if table_name in vector_table_names:
            score += 1.8
            reasons.append("vector retrieval nominated this table")
        vector_columns = vector_results.get("columns") or []
        column_matches = [col for col in vector_columns if col.get("table_name") == table_name]
        if column_matches:
            score += min(1.2, 0.4 * len(column_matches))
            reasons.append("vector retrieval nominated columns in this table")
        for term_meta in vector_results.get("glossary_terms") or []:
            if table_name in (term_meta.get("table_names") or []):
                score += 0.8
                reasons.append("vector glossary retrieval matched this table")
                break

    return score, reasons


def _column_score(
    plan: dict[str, Any],
    table_name: str,
    column: dict[str, Any],
    glossary_matches: list[tuple[str, dict[str, Any]]],
    vector_results: dict[str, Any] | None,
) -> tuple[float, list[str]]:
    score = 0.0
    reasons: list[str] = []
    column_name = str(column.get("name", "")).lower()
    semantic_type = str(column.get("semantic_type", "")).lower()
    question_terms = set(plan.get("question_terms", []))
    column_tokens = set(_tokenize(column_name))
    column_tokens.update(_tokenize(str(column.get("business_description", ""))))
    for term in column.get("business_terms", []) or []:
        column_tokens.update(_tokenize(term))

    overlap = len(question_terms & column_tokens)
    if overlap:
        score += overlap * 0.55
        reasons.append(f"matched {overlap} question term(s)")

    if semantic_type in plan.get("semantic_hints", set()):
        score += 1.1
        reasons.append(f"semantic type matched '{semantic_type}'")

    dimension = str(plan.get("dimension") or "").strip()
    if dimension:
        dimension_tokens = set(_tokenize(dimension))
        table_tokens = set(_tokenize(table_name))
        if dimension_tokens & column_tokens:
            score += 1.0
            reasons.append(f"dimension '{dimension}' matched this column")
        if dimension_tokens & table_tokens and semantic_type in {"name", "text", "code", "reference"}:
            score += 1.4
            reasons.append("display-style column matched the grouping table")
        if dimension_tokens & table_tokens and column_tokens & {"name", "label", "title", "display", "segment", "code"}:
            score += 0.6
            reasons.append("column looks suitable as a grouping label")

    if semantic_type == "date" and plan.get("date_range"):
        score += 1.0
        reasons.append("date filter needs a date column")

    if semantic_type == "status" and any(filter_data.get("type") == "status" for filter_data in plan.get("filters", [])):
        score += 1.0
        reasons.append("status filter needs a status column")

    for term, term_data in glossary_matches:
        for mapping in term_data.get("mapped_columns", []):
            if mapping.get("table") == table_name and mapping.get("column") == column.get("name"):
                score += 1.2
                reasons.append(f"glossary term '{term}' mapped to this column")
                break

    if vector_results:
        for vector_column in vector_results.get("columns") or []:
            if vector_column.get("table_name") == table_name and vector_column.get("column_name") == column.get("name"):
                score += 1.4
                reasons.append("vector retrieval nominated this column")
                break

    return score, reasons


def _select_columns_for_table(
    plan: dict[str, Any],
    table_name: str,
    table_data: dict[str, Any],
    glossary_matches: list[tuple[str, dict[str, Any]]],
    vector_results: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    scored_columns: list[tuple[str, float, list[str], str]] = []
    for column in table_data.get("columns", []):
        score, reasons = _column_score(plan, table_name, column, glossary_matches, vector_results)
        if score <= 0:
            continue
        scored_columns.append(
            (
                str(column.get("name", "")),
                score,
                reasons,
                str(column.get("semantic_type", "general")),
            )
        )

    scored_columns.sort(key=lambda item: (-item[1], item[0]))
    selected_columns = []
    for column_name, score, reasons, semantic_type in scored_columns[:6]:
        selected_columns.append(
            {
                "column": column_name,
                "semantic_type": semantic_type,
                "confidence": round(min(max(score / 3.0, 0.45), 0.99), 2),
                "reason": "; ".join(dict.fromkeys(reasons)),
            }
        )
    return selected_columns


def _preferred_primary_tables_for_simple_question(
    plan: dict[str, Any],
    scored_tables: list[tuple[str, float, list[str]]],
    glossary_matches: list[tuple[str, dict[str, Any]]],
    vector_results: dict[str, Any] | None,
) -> list[str] | None:
    if not _is_simple_primary_table_question(plan) or not scored_tables:
        return None

    top_table, top_score, _ = scored_tables[0]
    second_table = scored_tables[1][0] if len(scored_tables) > 1 else None
    second_score = scored_tables[1][1] if len(scored_tables) > 1 else 0.0
    question = str(plan.get("question") or "")
    has_direct_match = _table_name_matches_question(question, top_table)
    vector_table_names = list((vector_results or {}).get("table_names") or [])
    has_top_vector_match = bool(vector_table_names and vector_table_names[0] == top_table)
    has_strong_glossary_match = _has_strong_glossary_table_match(question, top_table, glossary_matches)
    second_has_strong_glossary_match = bool(
        second_table
        and _has_strong_glossary_table_match(question, second_table, glossary_matches)
    )

    if len(scored_tables) == 1 and (has_direct_match or has_top_vector_match or has_strong_glossary_match):
        return [top_table]

    if second_score <= 0:
        return [top_table] if (has_direct_match or has_top_vector_match or has_strong_glossary_match) else None

    if str(plan.get("intent") or "") in {"total", "average"} and top_score > second_score:
        return [top_table]

    if (has_direct_match or has_top_vector_match) and top_score >= (second_score * 1.5):
        return [top_table]

    if has_strong_glossary_match and not second_has_strong_glossary_match and top_score > second_score:
        return [top_table]

    if (has_top_vector_match or has_strong_glossary_match) and top_score >= second_score + 0.35:
        return [top_table]

    return None


def _expand_selected_tables(
    knowledge_base: dict,
    selected_names: list[str],
    dimension: str | None,
    plan: dict[str, Any] | None = None,
) -> list[str]:
    selected = list(selected_names)
    if plan and _is_simple_primary_table_question(plan):
        return selected

    dimension_tokens = set(_tokenize(dimension or ""))

    for table_name in list(selected_names):
        table_data = knowledge_base.get(table_name, {})
        for relationship in table_data.get("relationships", []):
            target_table = relationship.get("from_table") if relationship.get("direction") == "incoming" else relationship.get("to_table")
            if target_table not in knowledge_base or target_table in selected:
                continue

            target_tokens = set(_tokenize(target_table))
            target_tokens.update(knowledge_base[target_table].get("table_tokens", []))
            if dimension_tokens and dimension_tokens & target_tokens:
                selected.append(target_table)
            elif relationship.get("confidence", 0) >= 0.92 and len(selected) < 4:
                selected.append(target_table)

    return selected


def _candidate_tables_for_filters(
    knowledge_base: dict[str, Any],
    scored_tables: list[tuple[str, float, list[str]]],
    vector_results: dict[str, Any] | None,
) -> dict[str, Any]:
    candidate_names: list[str] = []
    for table_name, _, _ in scored_tables[:3]:
        if table_name in knowledge_base and table_name not in candidate_names:
            candidate_names.append(table_name)
    for table_name in list((vector_results or {}).get("table_names") or [])[:3]:
        if table_name in knowledge_base and table_name not in candidate_names:
            candidate_names.append(table_name)
    if not candidate_names:
        candidate_names = list(knowledge_base.keys())[:3]
    return {table_name: knowledge_base[table_name] for table_name in candidate_names}


def _build_selected_table_entries(
    knowledge_base: dict,
    scored_tables: list[tuple[str, float, list[str]]],
    plan: dict[str, Any],
    glossary_matches: list[tuple[str, dict[str, Any]]],
    vector_results: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    if not scored_tables:
        return []

    top_tables = []
    preferred_primary_tables = _preferred_primary_tables_for_simple_question(
        plan,
        scored_tables,
        glossary_matches,
        vector_results,
    )
    if preferred_primary_tables:
        top_tables = [
            entry for entry in scored_tables
            if entry[0] in preferred_primary_tables
        ]

    if not top_tables:
        top_tables = [entry for entry in scored_tables if entry[1] > 0.6][:3]
    if not top_tables:
        top_tables = scored_tables[:3]

    selected_names = _expand_selected_tables(
        knowledge_base,
        [entry[0] for entry in top_tables],
        plan.get("dimension"),
        plan=plan,
    )
    entries = []
    score_lookup = {table_name: (score, reasons) for table_name, score, reasons in scored_tables}

    for table_name in selected_names:
        score, reasons = score_lookup.get(table_name, (0.75, ["selected as a relationship bridge"]))
        selected_columns = _select_columns_for_table(
            plan,
            table_name,
            knowledge_base.get(table_name, {}),
            glossary_matches,
            vector_results,
        )
        entries.append(
            {
                "table": table_name,
                "confidence": round(min(max(score / 4.0, 0.55), 0.99), 2),
                "reason": "; ".join(dict.fromkeys(reasons)) or "selected from semantic and vector context",
                "selected_columns": selected_columns,
            }
        )

    return entries


def _infer_metric_from_selected_columns(
    plan: dict[str, Any],
    selected_tables: list[dict[str, Any]],
) -> str | None:
    current_metric = plan.get("metric")
    if plan.get("intent") not in {"total", "average", "top_n", "trend", "comparison"}:
        return current_metric

    ranked_semantics: list[tuple[float, str]] = []
    for table_entry in selected_tables:
        for column_entry in table_entry.get("selected_columns", []):
            semantic_type = str(column_entry.get("semantic_type", "")).lower()
            if semantic_type not in {"money", "quantity", "percentage", "date", "status"}:
                continue
            ranked_semantics.append((float(column_entry.get("confidence") or 0.0), semantic_type))

    ranked_semantics.sort(key=lambda item: (-item[0], item[1]))
    for _, semantic_type in ranked_semantics:
        if semantic_type in {"money", "quantity", "percentage"}:
            return semantic_type

    return current_metric


def _infer_metric_from_glossary_matches(
    glossary_matches: list[tuple[str, dict[str, Any]]],
    knowledge_base: dict[str, Any],
) -> str | None:
    ranked_semantics: list[tuple[float, str]] = []
    confidence_rank = {"high": 1.0, "medium": 0.75, "low": 0.5}

    for _, term_data in glossary_matches:
        for mapping in term_data.get("mapped_columns", []):
            table_name = str(mapping.get("table", "") or "")
            column_name = str(mapping.get("column", "") or "")
            if not table_name or not column_name:
                continue

            semantic_type = ""
            for column in knowledge_base.get(table_name, {}).get("columns", []):
                if str(column.get("name", "")) == column_name:
                    semantic_type = str(column.get("semantic_type", "")).lower()
                    break

            if semantic_type not in {"money", "quantity", "percentage"}:
                continue

            confidence = confidence_rank.get(str(mapping.get("confidence", "")).lower(), 0.6)
            ranked_semantics.append((confidence, semantic_type))

    ranked_semantics.sort(key=lambda item: (-item[0], item[1]))
    return ranked_semantics[0][1] if ranked_semantics else None


def _build_fk_relationship_graph(knowledge_base: dict) -> dict:
    """Build a graph of FK relationships between tables for join path computation."""
    graph: dict[str, dict[str, list[str]]] = {}
    
    for table_name, table_data in knowledge_base.items():
        if table_name not in graph:
            graph[table_name] = {"outgoing": [], "incoming": []}
        
        for fk in table_data.get("foreign_keys", []):
            from_table = fk.get("from_table") or table_name
            to_table = fk.get("to_table") or fk.get("referenced_table")
            from_column = fk.get("column")
            to_column = fk.get("referenced_column")
            
            if from_table and to_table and from_table in knowledge_base and to_table in knowledge_base:
                # Outgoing relationship: from_table -> to_table
                if from_table not in graph:
                    graph[from_table] = {"outgoing": [], "incoming": []}
                if to_table not in graph:
                    graph[to_table] = {"outgoing": [], "incoming": []}
                
                graph[from_table]["outgoing"].append({
                    "from_table": from_table,
                    "to_table": to_table,
                    "from_column": from_column,
                    "to_column": to_column,
                })
                graph[to_table]["incoming"].append({
                    "from_table": from_table,
                    "from_column": from_column,
                    "to_column": to_column,
                })
    
    return graph


def _find_shortest_path(graph: dict, start: str, end: str, max_depth: int = 5) -> list[dict] | None:
    """Find shortest path between two tables using BFS."""
    from collections import deque
    
    if start not in graph or end not in graph:
        return None
    
    if start == end:
        return []
    
    queue = deque([(start, [])])
    visited = {start}
    
    while queue and len(queue[0][1]) < max_depth:
        current, path = queue.popleft()
        
        # Check outgoing edges
        for edge in graph[current].get("outgoing", []):
            next_table = edge["to_table"]
            path_edge = {
                "from_table": current,
                "from_column": edge["from_column"],
                "to_table": next_table,
                "to_column": edge["to_column"],
                "join_condition": f"{current}.{edge['from_column']} = {next_table}.{edge['to_column']}",
            }
            if next_table == end:
                return path + [path_edge]
            if next_table not in visited:
                visited.add(next_table)
                queue.append((next_table, path + [path_edge]))
        
        # Check incoming edges
        for edge in graph[current].get("incoming", []):
            next_table = edge["from_table"]
            path_edge = {
                "from_table": current,
                "from_column": edge["to_column"],
                "to_table": next_table,
                "to_column": edge["from_column"],
                "join_condition": f"{current}.{edge['to_column']} = {next_table}.{edge['from_column']}",
            }
            if next_table == end:
                return path + [path_edge]
            if next_table not in visited:
                visited.add(next_table)
                queue.append((next_table, path + [path_edge]))
    
    return None


def _compute_join_paths(selected_tables: list[str], knowledge_base: dict) -> list[dict]:
    """Compute join paths between all selected tables using FK relationships."""
    graph = _build_fk_relationship_graph(knowledge_base)
    join_paths = []
    
    # Find paths between all pairs of selected tables
    for i, table_a in enumerate(selected_tables):
        for table_b in selected_tables[i+1:]:
            path = _find_shortest_path(graph, table_a, table_b)
            if path:
                join_paths.append({
                    "from_table": table_a,
                    "to_table": table_b,
                    "path": path,
                    "length": len(path),
                })
    
    return join_paths


def _tables_from_join_paths(join_paths: list[dict]) -> list[str]:
    """Return all tables required by computed FK paths in encounter order."""
    table_names: list[str] = []
    for join_path in join_paths:
        for candidate in (join_path.get("from_table"), join_path.get("to_table")):
            if candidate and candidate not in table_names:
                table_names.append(candidate)
        for edge in join_path.get("path", []):
            for candidate in (edge.get("from_table"), edge.get("to_table")):
                if candidate and candidate not in table_names:
                    table_names.append(candidate)
    return table_names


def _join_columns_for_table(table_name: str, join_paths: list[dict]) -> set[str]:
    join_columns: set[str] = set()
    for join_path in join_paths:
        for edge in join_path.get("path", []):
            if edge.get("from_table") == table_name and edge.get("from_column"):
                join_columns.add(str(edge["from_column"]))
            if edge.get("to_table") == table_name and edge.get("to_column"):
                join_columns.add(str(edge["to_column"]))
    return join_columns


def _selected_join_columns_for_table(table_name: str, table_data: dict[str, Any], join_paths: list[dict]) -> list[dict[str, Any]]:
    selected_columns = []
    needed_columns = _join_columns_for_table(table_name, join_paths)
    if not needed_columns:
        return selected_columns

    columns_by_name = {
        str(column.get("name", "")): column
        for column in table_data.get("columns", [])
        if column.get("name")
    }
    for column_name in sorted(needed_columns):
        column = columns_by_name.get(column_name, {})
        selected_columns.append(
            {
                "column": column_name,
                "semantic_type": str(column.get("semantic_type", "id")),
                "confidence": 0.82,
                "reason": "required by computed FK join path",
            }
        )
    return selected_columns


def _promote_join_path_tables(
    selected_names: list[str],
    selected_tables: list[dict[str, Any]],
    knowledge_base: dict[str, Any],
    plan: dict[str, Any],
    join_paths: list[dict],
) -> tuple[list[str], list[dict[str, Any]]]:
    """Promote bridge tables that are required by FK join paths into AI context."""
    if plan and _is_simple_primary_table_question(plan):
        return selected_names, selected_tables

    promoted_names = list(selected_names)
    promoted_entries = list(selected_tables)
    existing = set(promoted_names)
    score_by_table = {
        entry.get("table"): entry
        for entry in promoted_entries
        if entry.get("table")
    }

    for table_name in _tables_from_join_paths(join_paths):
        if table_name not in knowledge_base:
            continue
        if table_name not in existing:
            existing.add(table_name)
            promoted_names.append(table_name)
        if table_name not in score_by_table:
            table_data = knowledge_base.get(table_name, {})
            entry = {
                "table": table_name,
                "confidence": 0.76,
                "reason": "promoted because it is required by a computed FK join path",
                "selected_columns": _selected_join_columns_for_table(table_name, table_data, join_paths),
            }
            promoted_entries.append(entry)
            score_by_table[table_name] = entry
            continue

        existing_columns = {
            column_entry.get("column")
            for column_entry in score_by_table[table_name].setdefault("selected_columns", [])
        }
        for column_entry in _selected_join_columns_for_table(table_name, knowledge_base.get(table_name, {}), join_paths):
            if column_entry.get("column") not in existing_columns:
                score_by_table[table_name]["selected_columns"].append(column_entry)
                existing_columns.add(column_entry.get("column"))

    return promoted_names, promoted_entries


def _add_missing_tables_for_columns(
    selected_tables: list[str],
    selected_columns: list[dict],
    knowledge_base: dict,
) -> tuple[list[str], list[dict]]:
    """Add tables to selected_tables if selected columns belong to tables not in selected_tables."""
    column_tables = {col["table"] for col in selected_columns}
    missing_tables = column_tables - set(selected_tables)
    
    if missing_tables:
        for table in missing_tables:
            if table in knowledge_base:
                selected_tables.append(table)
    
    return selected_tables, selected_columns


def _find_bridge_tables(
    selected_tables: list[str],
    knowledge_base: dict,
    max_bridges: int = 3,
) -> list[str]:
    """Find bridge tables that connect disconnected selected tables."""
    graph = _build_fk_relationship_graph(knowledge_base)
    bridge_tables = []
    
    # Check if selected tables are connected
    if len(selected_tables) < 2:
        return bridge_tables
    
    # Build set of all reachable tables from first selected table using BFS
    start_table = selected_tables[0]
    reachable = {start_table}
    queue = [start_table]
    
    while queue:
        current = queue.pop(0)
        for edge in graph.get(current, {}).get("outgoing", []):
            if edge["to_table"] not in reachable:
                reachable.add(edge["to_table"])
                queue.append(edge["to_table"])
        for edge in graph.get(current, {}).get("incoming", []):
            if edge["from_table"] not in reachable:
                reachable.add(edge["from_table"])
                queue.append(edge["from_table"])
    
    # Find selected tables not reachable from start
    disconnected = [t for t in selected_tables if t not in reachable]
    
    if not disconnected:
        return bridge_tables
    
    # Find bridge tables to connect disconnected tables
    for disconnected_table in disconnected[:max_bridges]:
        path = _find_shortest_path(graph, start_table, disconnected_table, max_depth=5)
        if path and len(path) > 0:
            # Add intermediate tables as bridges (exclude the final target table)
            for edge in path[:-1]:  # Exclude final edge
                bridge = edge["to_table"]
                if bridge not in selected_tables and bridge not in bridge_tables and bridge in knowledge_base:
                    bridge_tables.append(bridge)
    
    return bridge_tables


def _planner_intent_from_structured_intent(intent: dict[str, Any] | None) -> str:
    intent_type = str((intent or {}).get("intent_type") or "").strip().lower()
    if intent_type == "count":
        return "count"
    if intent_type == "ranking":
        return "top_n"
    if intent_type == "comparison":
        return "comparison"
    return "list"


def _structured_dimension(intent: dict[str, Any] | None) -> str | None:
    requested_dimensions = list((intent or {}).get("requested_dimensions") or [])
    if not requested_dimensions:
        return None
    return str(requested_dimensions[0]).strip() or None


def _structured_sorting(intent: dict[str, Any] | None, planner_intent: str) -> dict[str, str] | None:
    requested_sort = dict((intent or {}).get("requested_sort") or {})
    sort_terms = str(requested_sort.get("terms") or "").strip()
    direction = str(requested_sort.get("direction") or "").strip().lower() or "asc"
    if sort_terms:
        return {"direction": direction, "by": sort_terms}
    if planner_intent == "top_n":
        return {"direction": "desc", "by": "metric"}
    return None


def _structured_filter_entries(
    filter_candidates: list[dict[str, Any]],
    requested_filters: list[str],
) -> list[dict[str, Any]]:
    filters: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    fallback_term = str(requested_filters[0]).strip() if requested_filters else ""

    for entry in filter_candidates:
        table_name = str(entry.get("table", "")).strip()
        column_name = str(entry.get("column", "")).strip()
        if not table_name or not column_name:
            continue
        matched_terms = list(entry.get("matched_terms") or [])
        term = str(matched_terms[0] if matched_terms else fallback_term).strip()
        signature = (table_name, column_name, term)
        if signature in seen:
            continue
        seen.add(signature)
        filters.append(
            {
                "type": "value",
                "table": table_name,
                "column": column_name,
                "value": term,
                "term": term,
            }
        )
    return filters[:4]


def _merge_candidate_columns(*groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for group in groups:
        for entry in group:
            table_name = str(entry.get("table", "")).strip()
            column_name = str(entry.get("column", "")).strip()
            if not table_name or not column_name:
                continue
            key = (table_name, column_name)
            existing = merged.get(key)
            if not existing:
                merged[key] = dict(entry)
                continue
            existing["score"] = max(float(existing.get("score") or 0.0), float(entry.get("score") or 0.0))
            existing["matched_terms"] = list(dict.fromkeys(list(existing.get("matched_terms") or []) + list(entry.get("matched_terms") or [])))
            existing["is_measure"] = bool(existing.get("is_measure")) or bool(entry.get("is_measure"))
            existing["is_dimension"] = bool(existing.get("is_dimension")) or bool(entry.get("is_dimension"))
            existing["source"] = existing.get("source") if existing.get("source") == "vector" else entry.get("source", existing.get("source"))
    results = list(merged.values())
    results.sort(key=lambda item: (-float(item.get("score") or 0.0), str(item.get("table") or ""), str(item.get("column") or "")))
    return results


def _column_selection_from_retrieved_context(
    table_name: str,
    merged_columns: list[dict[str, Any]],
    join_paths: list[dict],
) -> list[dict[str, Any]]:
    selected = []
    for entry in merged_columns:
        if str(entry.get("table") or "") != table_name:
            continue
        selected.append(
            {
                "column": str(entry.get("column") or ""),
                "semantic_type": str(entry.get("semantic_type") or "general"),
                "confidence": round(min(max(float(entry.get("score") or 0.0), 0.45), 0.99), 2),
                "reason": "; ".join(
                    filter(
                        None,
                        [
                            f"retrieved from {entry.get('source')}" if entry.get("source") else "",
                            ", ".join(entry.get("matched_terms") or []),
                        ],
                    )
                ).strip("; "),
            }
        )
    existing = {entry.get("column") for entry in selected}
    for join_column in _selected_join_columns_for_table(table_name, {}, join_paths):
        if join_column.get("column") in existing:
            continue
        selected.append(join_column)
        existing.add(join_column.get("column"))
    selected.sort(key=lambda item: (-float(item.get("confidence") or 0.0), str(item.get("column") or "")))
    return selected[:8]


def _table_entries_from_retrieved_context(
    matched_tables: list[dict[str, Any]],
    merged_columns: list[dict[str, Any]],
    join_paths: list[dict],
    selected_table_names: list[str] | None = None,
) -> list[dict[str, Any]]:
    selected_table_names = list(selected_table_names or [])
    table_lookup = {
        str(entry.get("table", "")).strip(): dict(entry)
        for entry in matched_tables
        if str(entry.get("table", "")).strip()
    }
    entries = []
    ordered_table_names = selected_table_names or list(table_lookup.keys())
    for table_name in ordered_table_names:
        entry = table_lookup.get(table_name, {"table": table_name, "score": 0.55, "matched_terms": [], "source": "retrieved_context"})
        if not table_name:
            continue
        matched_terms = ", ".join(entry.get("matched_terms") or [])
        reason_parts = [f"retrieved from {entry.get('source')}"] if entry.get("source") else []
        if matched_terms:
            reason_parts.append(matched_terms)
        entries.append(
            {
                "table": table_name,
                "confidence": round(min(max(float(entry.get("score") or 0.0), 0.4), 0.99), 2),
                "reason": "; ".join(reason_parts) or "selected from retrieved context",
                "selected_columns": _column_selection_from_retrieved_context(table_name, merged_columns, join_paths),
            }
        )
    return entries


def _tables_from_column_candidates(candidates: list[dict[str, Any]]) -> list[str]:
    tables: list[str] = []
    for entry in candidates:
        table_name = str(entry.get("table", "")).strip()
        if table_name and table_name not in tables:
            tables.append(table_name)
    return tables


def _build_query_context_from_retrieved_context(
    question: str,
    enriched_kb: dict,
    intent: dict[str, Any],
    retrieved_context: dict[str, Any],
) -> dict:
    planner_intent = _planner_intent_from_structured_intent(intent)
    dimension = _structured_dimension(intent)
    sorting = _structured_sorting(intent, planner_intent)
    limit = (intent or {}).get("limit")
    requested_metrics = list((intent or {}).get("requested_metrics") or [])
    requested_dimensions = list((intent or {}).get("requested_dimensions") or [])
    requested_filters = list((intent or {}).get("requested_filters") or [])

    matched_tables = [
        dict(entry)
        for entry in (retrieved_context.get("matched_tables") or [])
        if str(entry.get("table", "")).strip() in enriched_kb
    ]
    matched_columns = [
        dict(entry)
        for entry in (retrieved_context.get("matched_columns") or [])
        if str(entry.get("table", "")).strip() in enriched_kb
    ]
    measure_candidates = [
        dict(entry)
        for entry in (retrieved_context.get("measure_candidates") or [])
        if str(entry.get("table", "")).strip() in enriched_kb
    ]
    dimension_candidates = [
        dict(entry)
        for entry in (retrieved_context.get("dimension_candidates") or [])
        if str(entry.get("table", "")).strip() in enriched_kb
    ]
    filter_candidates = [
        dict(entry)
        for entry in (retrieved_context.get("filter_candidates") or [])
        if str(entry.get("table", "")).strip() in enriched_kb
    ]
    join_paths = [
        dict(path)
        for path in (retrieved_context.get("possible_join_paths") or [])
        if str(path.get("from_table", "")).strip() in enriched_kb and str(path.get("to_table", "")).strip() in enriched_kb
    ]
    matched_relationships = [
        dict(entry)
        for entry in (retrieved_context.get("matched_relationships") or [])
        if str(entry.get("from_table", "")).strip() in enriched_kb and str(entry.get("to_table", "")).strip() in enriched_kb
    ]

    merged_columns = _merge_candidate_columns(matched_columns, measure_candidates, dimension_candidates, filter_candidates)

    selected_table_names: list[str] = []
    for table_name in _tables_from_column_candidates(dimension_candidates + measure_candidates + filter_candidates):
        if table_name not in selected_table_names:
            selected_table_names.append(table_name)
    for entry in matched_tables:
        table_name = str(entry.get("table", "")).strip()
        if table_name and table_name not in selected_table_names:
            selected_table_names.append(table_name)
    for table_name in _tables_from_join_paths(join_paths):
        if table_name in enriched_kb and table_name not in selected_table_names:
            selected_table_names.append(table_name)

    selected_tables = _table_entries_from_retrieved_context(
        [entry for entry in matched_tables if entry.get("table") in selected_table_names],
        merged_columns,
        join_paths,
        selected_table_names=selected_table_names,
    )
    selected_table_names, selected_tables = _promote_join_path_tables(
        selected_table_names,
        selected_tables,
        enriched_kb,
        {"intent": planner_intent, "dimension": dimension, "grouping": [dimension] if dimension else [], "filters": requested_filters},
        join_paths,
    )

    selected_columns = [
        {"table": entry["table"], **column_entry}
        for entry in selected_tables
        for column_entry in entry.get("selected_columns", [])
    ]
    filters = _structured_filter_entries(filter_candidates, requested_filters)
    confidence = float(retrieved_context.get("confidence") or 0.0)
    if not selected_table_names:
        confidence = min(confidence, 0.35)
    elif len(selected_table_names) == 1 and len(selected_columns) >= 1:
        confidence = max(confidence, 0.72)

    plan = {
        "question": question,
        "intent": planner_intent,
        "metric": None,
        "dimension": dimension,
        "filters": filters,
        "date_range": None,
        "grouping": [value for value in requested_dimensions if str(value or "").strip()],
        "sorting": sorting,
        "limit": limit,
        "question_terms": list(retrieved_context.get("query_terms") or _content_terms(question)),
        "semantic_hints": set(),
        "matched_glossary_terms": [
            str(entry.get("term", "")).strip()
            for entry in (retrieved_context.get("matched_glossary_terms") or [])
            if str(entry.get("term", "")).strip()
        ],
        "requested_metrics": requested_metrics,
        "requested_dimensions": requested_dimensions,
        "requested_filters": requested_filters,
        "unresolved_metrics": requested_metrics if requested_metrics and not measure_candidates else [],
        "evidence_sources": list(retrieved_context.get("retrieval_sources") or []),
    }

    reduced_kb = {
        table_name: deepcopy(enriched_kb[table_name])
        for table_name in selected_table_names
        if table_name in enriched_kb
    }
    warnings = []
    if confidence < 0.5:
        warnings.append("Retrieved context is weak; planner confidence is low.")
    if requested_metrics and not measure_candidates:
        warnings.append("Requested metric remains unresolved in dynamic context.")

    return {
        "plan": plan,
        "selected_tables": selected_tables,
        "selected_columns": selected_columns,
        "selected_table_names": selected_table_names,
        "selected_knowledge_base": reduced_kb,
        "warnings": warnings,
        "confidence": round(confidence, 2),
        "knowledge_base": enriched_kb,
        "vector_results": None,
        "vector_used": False,
        "join_paths": join_paths,
        "fk_relationships": _build_fk_relationship_graph(enriched_kb),
        "matched_relationships": matched_relationships,
        "measure_candidates": measure_candidates,
        "dimension_candidates": dimension_candidates,
        "filters": filters,
        "evidence_sources": list(retrieved_context.get("retrieval_sources") or []),
    }


def build_query_context(
    question: str,
    knowledge_base: dict,
    business_glossary: dict | None = None,
    use_vector_retrieval: bool = True,
    vector_retriever: VectorRetriever | None = None,
    intent: dict[str, Any] | None = None,
    retrieved_context: dict[str, Any] | None = None,
) -> dict:
    """Build a structured plan and reduced schema slice for SQL generation."""
    enriched_kb = _enriched_kb(knowledge_base)
    if intent and retrieved_context is not None:
        return _build_query_context_from_retrieved_context(
            question,
            enriched_kb,
            intent,
            retrieved_context,
        )
    normalized_question = _normalize(question)
    intent = _detect_intent(normalized_question)
    dimension = _detect_dimension(normalized_question, intent)
    date_range = _detect_date_range(normalized_question)
    limit = _extract_limit(normalized_question) or _default_limit_for_intent(intent)
    sorting = _detect_sorting(normalized_question)
    semantic_hints = _semantic_hints(intent, date_range, sorting)
    glossary_matches = _glossary_matches(question, business_glossary)
    
    logger.debug(f"[DEBUG] Question: {question}")
    logger.debug(f"[DEBUG] Intent: {intent}, Dimension: {dimension}, Semantic hints: {semantic_hints}")

    vector_results = None
    if use_vector_retrieval:
        vector_results = _retrieve_with_vector(
            question,
            enriched_kb,
            business_glossary,
            retriever=vector_retriever,
        )
    
    logger.debug(f"[DEBUG] Vector results: {vector_results.get('used_vector') if vector_results else False}")
    if vector_results:
        logger.debug(f"[DEBUG] Vector table candidates: {vector_results.get('table_names', [])}")
        logger.debug(f"[DEBUG] Vector column candidates: {[col.get('column_name') for col in vector_results.get('columns', [])[:5]]}")

    if intent == "top_n":
        sorting = {"direction": "desc", "by": "metric"}
    elif intent == "trend":
        sorting = {"direction": "asc", "by": "date"}

    grouping = []
    if dimension:
        grouping.append(dimension)
    if intent == "trend" and "month" not in grouping:
        grouping.append("month")

    plan = {
        "question": question,
        "intent": intent,
        "metric": _primary_metric_hint(semantic_hints),
        "dimension": dimension,
        "filters": [],
        "date_range": date_range,
        "grouping": grouping,
        "sorting": sorting,
        "limit": limit,
        "question_terms": _content_terms(normalized_question),
        "semantic_hints": semantic_hints,
        "matched_glossary_terms": [term for term, _ in glossary_matches],
    }

    scored_by_name: dict[str, tuple[float, list[str]]] = {}
    for table_name, table_data in enriched_kb.items():
        score, reasons = _table_score(plan, table_name, table_data, glossary_matches, vector_results)
        if score > 0:
            scored_by_name[table_name] = (score, reasons)

    if vector_results:
        for table_name in vector_results.get("table_names") or []:
            if table_name not in enriched_kb:
                continue
            if table_name in scored_by_name:
                continue
            scored_by_name[table_name] = (1.0, ["vector retrieval match"])

        for column_meta in vector_results.get("columns") or []:
            table_name = column_meta.get("table_name")
            if not table_name or table_name not in enriched_kb:
                continue
            score, reasons = scored_by_name.get(table_name, (0.0, []))
            reasons = list(reasons)
            score += 0.7
            reasons.append(f"vector column match: {column_meta.get('column_name')}")
            scored_by_name[table_name] = (score, reasons)

    scored_tables = [
        (table_name, score, reasons)
        for table_name, (score, reasons) in scored_by_name.items()
    ]
    scored_tables.sort(key=lambda item: (-item[1], item[0]))

    filters = _detect_runtime_filters(
        question,
        _candidate_tables_for_filters(enriched_kb, scored_tables, vector_results),
    )
    if not filters:
        filters = _detect_generic_value_filters(
            question,
            _candidate_tables_for_filters(enriched_kb, scored_tables, vector_results),
        )
    if filters:
        plan["filters"] = filters
        if any(filter_data.get("type") == "status" for filter_data in filters):
            plan["semantic_hints"].add("status")
        plan["metric"] = _primary_metric_hint(plan["semantic_hints"])
        rescored_by_name: dict[str, tuple[float, list[str]]] = {}
        for table_name, table_data in enriched_kb.items():
            score, reasons = _table_score(plan, table_name, table_data, glossary_matches, vector_results)
            if score > 0:
                rescored_by_name[table_name] = (score, reasons)
        if vector_results:
            for table_name in vector_results.get("table_names") or []:
                if table_name not in enriched_kb or table_name in rescored_by_name:
                    continue
                rescored_by_name[table_name] = (1.0, ["vector retrieval match"])

            for column_meta in vector_results.get("columns") or []:
                table_name = column_meta.get("table_name")
                if not table_name or table_name not in enriched_kb:
                    continue
                score, reasons = rescored_by_name.get(table_name, (0.0, []))
                reasons = list(reasons)
                score += 0.7
                reasons.append(f"vector column match: {column_meta.get('column_name')}")
                rescored_by_name[table_name] = (score, reasons)

        scored_tables = [
            (table_name, score, reasons)
            for table_name, (score, reasons) in rescored_by_name.items()
        ]
        scored_tables.sort(key=lambda item: (-item[1], item[0]))

    selected_tables = _build_selected_table_entries(
        enriched_kb,
        scored_tables,
        plan,
        glossary_matches,
        vector_results,
    )
    selected_names = [entry["table"] for entry in selected_tables if entry.get("table") in enriched_kb]
    if len(selected_names) != len(selected_tables):
        selected_tables = [entry for entry in selected_tables if entry.get("table") in enriched_kb]
    
    logger.debug(f"[DEBUG] Selected tables before join path computation: {selected_names}")
    
    # Build FK relationship graph from knowledge base
    fk_graph = _build_fk_relationship_graph(enriched_kb)
    logger.debug(f"[DEBUG] FK relationships loaded: {len(fk_graph)} tables with relationships")
    for table_name, edges in list(fk_graph.items())[:3]:
        logger.debug(f"[DEBUG]   {table_name}: {len(edges['outgoing'])} outgoing, {len(edges['incoming'])} incoming")
    
    # Compute join paths between selected tables
    join_paths = _compute_join_paths(selected_names, enriched_kb)
    logger.debug(f"[DEBUG] Computed {len(join_paths)} join paths between selected tables")
    for jp in join_paths[:3]:
        logger.debug(f"[DEBUG]   {jp['from_table']} -> {jp['to_table']} (length: {jp['length']})")

    selected_names, selected_tables = _promote_join_path_tables(
        selected_names,
        selected_tables,
        enriched_kb,
        plan,
        join_paths,
    )
    join_paths = _compute_join_paths(selected_names, enriched_kb)
    logger.debug(f"[DEBUG] Selected tables after join-path promotion: {selected_names}")
    logger.debug(f"[DEBUG] Recomputed {len(join_paths)} join paths after promotion")

    if not selected_names:
        selected_names = list(enriched_kb.keys())
        selected_tables = [
            {
                "table": table_name,
                "confidence": 0.55,
                "reason": "fell back to full schema because no table scored above the selection threshold",
                "selected_columns": [],
            }
            for table_name, table_data in enriched_kb.items()
        ]

    reduced_kb = {table_name: deepcopy(enriched_kb[table_name]) for table_name in selected_names}
    warnings = []
    if len(reduced_kb) < len(enriched_kb):
        warnings.append(f"Using {len(reduced_kb)} relevant table(s) instead of the full schema.")
    if intent in {"list", "top_n"}:
        warnings.append("Read-only row limits stay enabled for list-style questions.")
    if vector_results and not vector_results.get("used_vector"):
        warnings.append("Vector retrieval was unavailable; using KB and glossary rules only.")

    overall_confidence = round(
        sum(entry["confidence"] for entry in selected_tables) / max(len(selected_tables), 1),
        2,
    )
    selected_columns = [
        {
            "table": entry["table"],
            **column_entry,
        }
        for entry in selected_tables
        for column_entry in entry.get("selected_columns", [])
    ]
    
    logger.debug(f"[DEBUG] Selected columns before missing table addition: {[(col['table'], col['column']) for col in selected_columns[:10]]}")
    
    # Add missing tables if selected columns belong to tables not in selected_tables
    # DISABLED: This feature changes table selection behavior and may affect rule-based generator
    # Re-enable after investigating impact on rule-based generator and test expectations
    # selected_names, selected_columns = _add_missing_tables_for_columns(
    #     selected_names,
    #     selected_columns,
    #     enriched_kb,
    # )
    
    logger.debug(f"[DEBUG] Selected columns: {[(col['table'], col['column']) for col in selected_columns[:10]]}")
    
    metric_from_selected_columns = _infer_metric_from_selected_columns(plan, selected_tables)
    metric_from_glossary = _infer_metric_from_glossary_matches(glossary_matches, enriched_kb)
    if _should_preserve_simple_list_metric(plan):
        if metric_from_selected_columns in {"money", "quantity", "percentage"}:
            plan["metric"] = metric_from_selected_columns
        elif metric_from_glossary in {"money", "quantity", "percentage"}:
            plan["metric"] = metric_from_glossary
        else:
            plan["metric"] = metric_from_selected_columns
    else:
        plan["metric"] = None

    return {
        "plan": plan,
        "selected_tables": selected_tables,
        "selected_columns": selected_columns,
        "selected_table_names": selected_names,
        "selected_knowledge_base": reduced_kb,
        "warnings": warnings,
        "confidence": overall_confidence,
        "knowledge_base": enriched_kb,
        "vector_results": vector_results,
        "vector_used": bool(vector_results and vector_results.get("used_vector")),
        "join_paths": join_paths,
        "fk_relationships": fk_graph,
    }
