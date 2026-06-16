"""
Structured query planning and relevant-table selection.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import date
from typing import Any
import re

from semantic.erp_metadata import enrich_knowledge_base_for_erp
from vector_store import VectorIndexBuilder, VectorRetriever, EmbeddingService
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

_MONEY_HINTS = {"amount", "total", "price", "cost", "balance", "value"}
_QUANTITY_HINTS = {"quantity", "qty", "unit", "units", "count", "volume", "number"}
_PERCENTAGE_HINTS = {"percent", "percentage", "ratio", "rate"}


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
# if match return int (match)

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
    if re.search(r"\b(top\s+\d+|highest|largest|lowest|smallest|most|least)\b", normalized):
        return "top_n"
    if re.search(r"\b(list|show|display|fetch|get)\b", normalized):
        return "list"
    return "list"



def _detect_dimension(question: str) -> str | None:
    normalized = _normalize(question)
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
    semantic_type = str(column.get("semantic_type", "")).lower()
    if semantic_type == "status":
        return True
    column_name = _normalize_identifier(column.get("name", ""))
    return any(token in column_name for token in ("status", "state", "stage"))


def _detect_runtime_filters(question: str, candidate_tables: dict[str, Any]) -> list[dict[str, Any]]:
    normalized_question = _normalize(question)
    question_terms = set(_tokenize(question))
    filters: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()

    for table_name, table_data in candidate_tables.items():
        for column in table_data.get("columns", []):
            if not _column_can_hold_status(column):
                continue
            column_name = str(column.get("name", ""))
            for normalized_value, raw_value in _normalized_sample_values(column):
                value_terms = set(_tokenize(normalized_value))
                if not value_terms:
                    continue
                if normalized_value not in normalized_question and not value_terms <= question_terms:
                    continue

                signature = (table_name, column_name, raw_value)
                if signature in seen:
                    continue
                seen.add(signature)
                filters.append(
                    {
                        "type": "status",
                        "table": table_name,
                        "column": column_name,
                        "value": raw_value,
                        "term": normalized_value,
                    }
                )

    return filters


def _default_limit_for_intent(intent: str) -> int | None:
    if intent in {"list", "top_n"}:
        return 50
    return None


def _semantic_hints(question: str, intent: str) -> set[str]:
    terms = set(_content_terms(question))
    hints = set()

    if terms & _MONEY_HINTS or intent in {"total", "average"}:
        hints.add("money")
    if terms & _QUANTITY_HINTS:
        hints.add("quantity")
    if terms & _PERCENTAGE_HINTS:
        hints.add("percentage")
    if any(token in terms for token in {"date", "month", "year", "recent", "latest", "today"}):
        hints.add("date")
    if any(token in terms for token in {"status", "state", "stage"}):
        hints.add("status")

    return hints


def _primary_metric_hint(semantic_hints: set[str]) -> str | None:
    for candidate in ("money", "quantity", "percentage", "status", "date"):
        if candidate in semantic_hints:
            return candidate
    return next(iter(sorted(semantic_hints))) if semantic_hints else None


def _is_simple_primary_table_question(plan: dict[str, Any]) -> bool:
    if (
        str(plan.get("intent") or "") == "count"
        and not plan.get("dimension")
        and not plan.get("grouping")
        and not plan.get("filters")
        and not plan.get("date_range")
    ):
        return True

    return (
        str(plan.get("intent") or "") in {"list", "count"}
        and not plan.get("dimension")
        and not plan.get("grouping")
        and not plan.get("filters")
        and not plan.get("date_range")
        and not (set(plan.get("semantic_hints") or set()) & {"money", "quantity", "percentage", "date", "status"})
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


def _infer_generic_semantic_type(column_name: str) -> str | None:
    tokens = set(_tokenize(column_name))
    if not tokens:
        return None
    if tokens & _MONEY_HINTS:
        return "money"
    if tokens & _QUANTITY_HINTS:
        return "quantity"
    if tokens & _PERCENTAGE_HINTS:
        return "percentage"
    if {"date", "time", "month", "year"} & tokens:
        return "date"
    if {"status", "state", "stage"} & tokens:
        return "status"
    return None


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
            embedding_service = EmbeddingService()
            index_builder = VectorIndexBuilder(embedding_service)
            active_retriever = VectorRetriever(embedding_service)
            active_retriever.add_documents(index_builder.build_from_knowledge_base(knowledge_base))
            if business_glossary:
                active_retriever.add_documents(index_builder.build_from_glossary(business_glossary))

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


def _enriched_kb(knowledge_base: dict) -> dict:
    if not knowledge_base:
        return {}

    if all("module" in table_data for table_data in knowledge_base.values()):
        return deepcopy(knowledge_base)
    return enrich_knowledge_base_for_erp(knowledge_base)


def _question_text_for_table(table_name: str, table_data: dict) -> str:
    pieces = [
        table_name,
        _humanize(table_name),
        str(table_data.get("module", "")),
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
        normalized_term = _normalize(term)
        term_tokens = set(_tokenize(term))
        direct_term_match = (
            (normalized_term and normalized_term in normalized_question)
            or (term_tokens and term_tokens <= question_terms)
        )
        glossary_boost = 1.1 if direct_term_match else 0.35
        if _is_simple_primary_table_question(plan) and not direct_term_match:
            glossary_boost = 0.15
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
        if dimension_tokens & column_tokens:
            score += 1.0
            reasons.append(f"dimension '{dimension}' matched this column")

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
    vector_results: dict[str, Any] | None,
) -> list[str] | None:
    if not _is_simple_primary_table_question(plan) or not scored_tables:
        return None

    top_table, top_score, _ = scored_tables[0]
    second_score = scored_tables[1][1] if len(scored_tables) > 1 else 0.0
    question = str(plan.get("question") or "")
    has_direct_match = _table_name_matches_question(question, top_table)
    vector_table_names = list((vector_results or {}).get("table_names") or [])
    has_top_vector_match = bool(vector_table_names and vector_table_names[0] == top_table)

    if len(scored_tables) == 1 and (has_direct_match or has_top_vector_match):
        return [top_table]

    if second_score <= 0:
        return [top_table] if (has_direct_match or has_top_vector_match) else None

    if (has_direct_match or has_top_vector_match) and top_score >= (second_score * 1.5):
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
                "module": knowledge_base.get(table_name, {}).get("module", "general"),
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
            if semantic_type in {"", "general", "unknown"}:
                semantic_type = str(_infer_generic_semantic_type(str(column_entry.get("column", ""))) or "")
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

            if semantic_type in {"", "general", "unknown"}:
                semantic_type = str(_infer_generic_semantic_type(column_name) or "")
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
            from_table = fk.get("from_table")
            to_table = fk.get("to_table")
            from_column = fk.get("column")
            to_column = fk.get("referenced_column")
            
            if from_table and to_table and from_table in knowledge_base and to_table in knowledge_base:
                # Outgoing relationship: from_table -> to_table
                if from_table not in graph:
                    graph[from_table] = {"outgoing": [], "incoming": []}
                if to_table not in graph:
                    graph[to_table] = {"outgoing": [], "incoming": []}
                
                graph[from_table]["outgoing"].append({
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
            if next_table == end:
                return path + [edge]
            if next_table not in visited:
                visited.add(next_table)
                queue.append((next_table, path + [edge]))
        
        # Check incoming edges
        for edge in graph[current].get("incoming", []):
            next_table = edge["from_table"]
            if next_table == end:
                return path + [{"to_table": next_table, "from_column": edge["to_column"], "to_column": edge["from_column"]}]
            if next_table not in visited:
                visited.add(next_table)
                queue.append((next_table, path + [{"to_table": next_table, "from_column": edge["to_column"], "to_column": edge["from_column"]}]))
    
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


def build_query_context(
    question: str,
    knowledge_base: dict,
    business_glossary: dict | None = None,
    use_vector_retrieval: bool = True,
    vector_retriever: VectorRetriever | None = None,
) -> dict:
    """Build a structured plan and reduced schema slice for SQL generation."""
    enriched_kb = _enriched_kb(knowledge_base)
    normalized_question = _normalize(question)
    intent = _detect_intent(normalized_question)
    dimension = _detect_dimension(normalized_question)
    date_range = _detect_date_range(normalized_question)
    limit = _extract_limit(normalized_question) or _default_limit_for_intent(intent)
    semantic_hints = _semantic_hints(normalized_question, intent)
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

    sorting = None
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
    if filters:
        plan["filters"] = filters
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
    
    # Find bridge tables if selected tables are disconnected
    # DISABLED: This feature changes table selection behavior and may affect rule-based generator
    # Re-enable after investigating impact on rule-based generator and test expectations
    # bridge_tables = _find_bridge_tables(selected_names, enriched_kb)
    # if bridge_tables:
    #     logger.debug(f"[DEBUG] Found bridge tables: {bridge_tables}")
    #     selected_names.extend(bridge_tables)
    #     # Rebuild selected_tables entries for bridge tables
    #     for bridge_table in bridge_tables:
    #         if bridge_table in enriched_kb:
    #             selected_columns = _select_columns_for_table(
    #                 plan,
    #                 bridge_table,
    #                 enriched_kb[bridge_table],
    #                 glossary_matches,
    #                 vector_results,
    #             )
    #             selected_tables.append({
    #                 "table": bridge_table,
    #                 "confidence": 0.65,
    #                 "reason": "added as bridge table to connect disconnected selected tables",
    #                 "module": enriched_kb[bridge_table].get("module", "general"),
    #                 "selected_columns": selected_columns,
    #             })
    
    # logger.debug(f"[DEBUG] Selected tables after bridge table addition: {[entry['table'] for entry in selected_tables]}")

    if not selected_names:
        selected_names = list(enriched_kb.keys())
        selected_tables = [
            {
                "table": table_name,
                "confidence": 0.55,
                "reason": "fell back to full schema because no table scored above the selection threshold",
                "module": table_data.get("module", "general"),
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
    if metric_from_selected_columns in {"money", "quantity", "percentage"}:
        plan["metric"] = metric_from_selected_columns
    elif metric_from_glossary in {"money", "quantity", "percentage"}:
        plan["metric"] = metric_from_glossary
    else:
        plan["metric"] = metric_from_selected_columns

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
