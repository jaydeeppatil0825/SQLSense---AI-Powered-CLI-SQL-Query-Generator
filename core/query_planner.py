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


_INTENT_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("low_stock", ("low stock", "reorder", "below stock", "minimum stock")),
    ("pending_outstanding", ("outstanding", "unpaid", "pending", "due", "overdue", "payable", "receivable")),
    ("trend", ("trend", "monthly", "by month", "per month", "by date", "over time")),
    ("comparison", ("compare", "comparison", "versus", "vs")),
    ("top_n", ("top ", "highest", "largest", "most")),
    ("average", ("average", "avg", "mean")),
    ("count", ("count", "how many", "number of")),
    ("total", ("total", "sum")),
    ("list", ("list", "show", "display", "fetch", "get")),
]

_STATUS_FILTERS = {
    "pending": "Pending",
    "unpaid": "Unpaid",
    "paid": "Paid",
    "open": "Open",
    "closed": "Closed",
    "cancelled": "Cancelled",
    "canceled": "Cancelled",
    "approved": "Approved",
    "active": "Active",
    "inactive": "Inactive",
}

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

_MONEY_HINTS = {"sales", "revenue", "income", "amount", "total", "price", "cost", "balance", "due", "outstanding", "payable", "payables", "receivable", "receivables", "tax", "gst", "vat"}
_QUANTITY_HINTS = {"quantity", "qty", "stock", "units", "count", "inventory", "warehouse"}
_PERCENTAGE_HINTS = {"percent", "percentage", "ratio", "rate"}


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip().lower())


def _normalize_identifier(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", _normalize(text)).strip("_")


def _humanize(text: str) -> str:
    return _normalize_identifier(text).replace("_", " ").strip()


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


def _detect_intent(question: str) -> str:
    normalized = _normalize(question)
    for intent, phrases in _INTENT_RULES:
        if any(phrase in normalized for phrase in phrases):
            return intent
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


def _detect_filters(question: str) -> list[dict[str, Any]]:
    normalized = _normalize(question)
    filters = []
    for trigger, value in _STATUS_FILTERS.items():
        if re.search(r"\b" + re.escape(trigger) + r"\b", normalized):
            filters.append({"type": "status", "value": value, "term": trigger})
    return filters


def _default_limit_for_intent(intent: str) -> int | None:
    if intent in {"list", "top_n", "low_stock"}:
        return 50
    return None


def _semantic_hints(question: str, intent: str) -> set[str]:
    terms = set(_content_terms(question))
    hints = set()

    if terms & _MONEY_HINTS or intent in {"total", "average", "pending_outstanding"}:
        hints.add("money")
    if terms & _QUANTITY_HINTS or intent == "low_stock":
        hints.add("quantity")
    if terms & _PERCENTAGE_HINTS:
        hints.add("percentage")
    if any(token in terms for token in {"date", "month", "year", "recent", "latest", "today"}):
        hints.add("date")
    if any(token in terms for token in {"status", "state", "pending", "active", "inactive", "paid", "unpaid", "closed", "open"}):
        hints.add("status")

    return hints


def _primary_metric_hint(semantic_hints: set[str]) -> str | None:
    for candidate in ("money", "quantity", "percentage", "status", "date"):
        if candidate in semantic_hints:
            return candidate
    return next(iter(sorted(semantic_hints))) if semantic_hints else None


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
            "columns": active_retriever.get_relevant_columns(question, top_k=10),
            "glossary_terms": active_retriever.get_relevant_glossary_terms(question, top_k=5),
            "used_vector": True,
        }
    except Exception as exc:
        logger.warning(f"Vector retrieval error: {exc}")
        return {
            "table_names": [],
            "columns": [],
            "glossary_terms": [],
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
        for mapping in term_data.get("mapped_columns", []):
            if mapping.get("table") == table_name:
                score += 1.1
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


def _expand_selected_tables(knowledge_base: dict, selected_names: list[str], dimension: str | None) -> list[str]:
    selected = list(selected_names)
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


def _build_selected_table_entries(
    knowledge_base: dict,
    scored_tables: list[tuple[str, float, list[str]]],
    plan: dict[str, Any],
    glossary_matches: list[tuple[str, dict[str, Any]]],
    vector_results: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    if not scored_tables:
        return []

    top_tables = [entry for entry in scored_tables if entry[1] > 0.6][:3]
    if not top_tables:
        top_tables = scored_tables[:3]

    selected_names = _expand_selected_tables(
        knowledge_base,
        [entry[0] for entry in top_tables],
        plan.get("dimension"),
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
    filters = _detect_filters(normalized_question)
    date_range = _detect_date_range(normalized_question)
    limit = _extract_limit(normalized_question) or _default_limit_for_intent(intent)
    semantic_hints = _semantic_hints(normalized_question, intent)
    glossary_matches = _glossary_matches(question, business_glossary)

    vector_results = None
    if use_vector_retrieval:
        vector_results = _retrieve_with_vector(
            question,
            enriched_kb,
            business_glossary,
            retriever=vector_retriever,
        )

    sorting = None
    if intent == "top_n":
        sorting = {"direction": "desc", "by": "metric"}
    elif intent == "low_stock":
        sorting = {"direction": "asc", "by": "quantity"}
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
        "filters": filters,
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
    if intent in {"list", "top_n", "low_stock"}:
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
    }
