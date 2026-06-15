"""
Structured query planning and relevant-table selection for ERP questions.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import date
import re

from semantic.erp_metadata import enrich_knowledge_base_for_erp


_INTENT_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("low_stock", ("low stock", "reorder", "below stock", "minimum stock")),
    ("pending_outstanding", ("outstanding", "unpaid", "pending", "due", "overdue")),
    ("trend", ("trend", "monthly", "by month", "per month", "by date")),
    ("comparison", ("compare", "comparison", "versus", "vs")),
    ("top_n", ("top ", "highest", "largest", "most")),
    ("average", ("average", "avg", "mean")),
    ("count", ("count", "how many", "number of")),
    ("total", ("total", "sum")),
    ("list", ("list", "show", "display", "get", "fetch")),
]

_METRIC_TERMS = {
    "sales": ("sales", "revenue", "order amount", "billing"),
    "purchase": ("purchase", "procurement", "po", "buy"),
    "payment": ("payment", "collection", "receipt"),
    "stock": ("stock", "inventory", "on hand", "current stock"),
    "salary": ("salary", "payroll", "wage"),
    "tax": ("tax", "gst", "vat"),
    "production": ("production", "manufacturing", "bom"),
    "balance": ("balance", "outstanding", "due"),
}

_DIMENSION_TERMS = {
    "customer": ("customer", "client", "buyer"),
    "vendor": ("vendor", "supplier"),
    "warehouse": ("warehouse", "store", "location"),
    "department": ("department",),
    "employee": ("employee", "staff"),
    "item_product": ("item", "product", "sku", "material"),
    "month": ("month", "monthly"),
    "date": ("date", "day"),
    "status": ("status", "state"),
    "bom": ("bom", "bill of materials"),
}

_STATUS_FILTERS = {
    "pending": "Pending",
    "unpaid": "Pending",
    "paid": "Paid",
    "open": "Open",
    "closed": "Closed",
    "cancelled": "Cancelled",
    "canceled": "Cancelled",
    "approved": "Approved",
}


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip().lower())


def _tokenize(text: str) -> list[str]:
    return [token for token in re.split(r"[^a-z0-9]+", _normalize(text)) if token]


def _extract_limit(question: str) -> int | None:
    match = re.search(
        r"\b(?:top|first|last|latest|recent|limit|show|get|fetch)\s+(\d+)\b"
        r"|\b(\d+)\s+(?:rows?|records?|results?|items?|invoices?|orders?)\b",
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


def _detect_metric(question: str) -> str | None:
    normalized = _normalize(question)
    for metric, phrases in _METRIC_TERMS.items():
        if any(phrase in normalized for phrase in phrases):
            return metric
    return None


def _detect_dimension(question: str) -> str | None:
    normalized = _normalize(question)
    by_match = re.search(r"\bby\s+([a-z_ ]+)", normalized)
    if by_match:
        by_value = by_match.group(1).strip()
        for dimension, phrases in _DIMENSION_TERMS.items():
            if any(phrase in by_value for phrase in phrases):
                return dimension

    for dimension, phrases in _DIMENSION_TERMS.items():
        if dimension == "month":
            if "monthly" in normalized or "per month" in normalized:
                return dimension
            continue
        if dimension == "date":
            if "by date" in normalized or "per date" in normalized:
                return dimension
            continue
        if any(phrase in normalized for phrase in phrases):
            return dimension
    return None


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


def _detect_filters(question: str) -> list[dict]:
    normalized = _normalize(question)
    filters = []
    for trigger, value in _STATUS_FILTERS.items():
        if re.search(r"\b" + re.escape(trigger) + r"\b", normalized):
            filters.append({"type": "status", "value": value})
    return filters


def _default_limit_for_intent(intent: str) -> int | None:
    if intent in {"list", "top_n", "low_stock"}:
        return 50
    return None


def _semantic_terms_from_plan(plan: dict) -> set[str]:
    terms = set(plan.get("question_terms", []))
    for key in ("metric", "dimension", "intent"):
        value = plan.get(key)
        if value:
            terms.add(str(value))
    for filter_data in plan.get("filters", []):
        terms.add(str(filter_data.get("value", "")).lower())
    return {term for term in terms if term}


def _enriched_kb(knowledge_base: dict) -> dict:
    if not knowledge_base:
        return {}

    if all("module" in table_data for table_data in knowledge_base.values()):
        return deepcopy(knowledge_base)
    return enrich_knowledge_base_for_erp(knowledge_base)


def _table_score(plan: dict, table_name: str, table_data: dict, glossary: dict | None) -> tuple[float, list[str]]:
    score = 0.0
    reasons: list[str] = []
    terms = _semantic_terms_from_plan(plan)
    table_text = " ".join([table_name, table_data.get("module", ""), table_data.get("business_purpose", "")]).lower()

    for term in terms:
        if term and term in table_text:
            score += 1.5
            reasons.append(f"matched '{term}' in table metadata")

    metric = plan.get("metric")
    table_name_lower = table_name.lower()
    if metric and table_data.get("module", "").lower().startswith(metric):
        score += 2.0
        reasons.append(f"module matched metric '{metric}'")
    if metric == "sales" and any(token in table_name_lower for token in ("invoice", "sales_order", "sales")):
        score += 1.5
        reasons.append("table name matched a sales fact pattern")
    if metric == "balance" and any(token in table_name_lower for token in ("invoice", "receivable", "payment")):
        score += 1.8
        reasons.append("table name matched a balance or receivable pattern")

    dimension = plan.get("dimension")
    for column in table_data.get("columns", []):
        column_name = str(column.get("name", "")).lower()
        semantic_type = str(column.get("semantic_type", "")).lower()

        for term in terms:
            if term and term in column_name:
                score += 1.0
                reasons.append(f"matched column '{column_name}'")
                break

        if metric in {"sales", "purchase", "payment", "salary", "tax", "balance"} and semantic_type in {"money", "tax", "account"}:
            score += 0.8
            if metric == "balance" and any(token in column_name for token in ("outstanding", "due", "balance", "invoice_amount", "net_amount", "payment_amount")):
                score += 1.0
                reasons.append(f"matched balance column '{column_name}'")
        if metric in {"stock", "production"} and semantic_type in {"quantity", "item_product", "warehouse"}:
            score += 0.8
        if dimension and semantic_type == dimension:
            score += 1.2
            reasons.append(f"has {dimension} semantic column")

    if glossary:
        for term, term_data in glossary.items():
            if term not in terms:
                continue
            for mapping in term_data.get("mapped_columns", []):
                if mapping.get("table") == table_name:
                    score += 1.4
                    reasons.append(f"glossary term '{term}' mapped to this table")
                    break

    return score, reasons


def _metric_semantic_targets(metric: str | None) -> set[str]:
    if metric in {"sales", "purchase", "payment", "salary", "balance"}:
        return {"money", "tax", "account", "status", "date"}
    if metric == "tax":
        return {"tax", "money", "date"}
    if metric in {"stock", "production"}:
        return {"quantity", "item_product", "warehouse", "date"}
    return set()


def _column_score(plan: dict, column: dict) -> tuple[float, list[str]]:
    score = 0.0
    reasons: list[str] = []
    terms = _semantic_terms_from_plan(plan)
    column_name = str(column.get("name", "")).lower()
    semantic_type = str(column.get("semantic_type", "")).lower()
    metric = plan.get("metric")
    dimension = plan.get("dimension")

    for term in terms:
        if term and term in column_name:
            score += 1.3
            reasons.append(f"matched question term '{term}'")

    if semantic_type in _metric_semantic_targets(metric):
        score += 1.2
        reasons.append(f"semantic type matched metric '{metric}'")

    if dimension and semantic_type == dimension:
        score += 1.5
        reasons.append(f"semantic type matched dimension '{dimension}'")

    if semantic_type == "date" and plan.get("date_range"):
        score += 1.1
        reasons.append("date filter needs a date column")

    if semantic_type == "status" and any(filter_data.get("type") == "status" for filter_data in plan.get("filters", [])):
        score += 1.1
        reasons.append("status filter needs a status column")

    if metric == "sales" and any(token in column_name for token in ("invoice_amount", "final_amount", "total_amount", "net_amount", "line_total")):
        score += 1.6
        reasons.append("sales metric prefers a sales amount column")
    if metric == "purchase" and any(token in column_name for token in ("total_amount", "line_total", "unit_cost", "amount")):
        score += 1.6
        reasons.append("purchase metric prefers a purchase amount column")
    if metric == "tax" and "tax" in column_name:
        score += 1.8
        reasons.append("tax metric prefers a tax column")
    if metric == "balance" and any(token in column_name for token in ("outstanding", "due", "balance", "net_amount", "invoice_amount", "payment_amount")):
        score += 1.4
        reasons.append("balance metric prefers due or payment columns")
    if metric == "stock" and any(token in column_name for token in ("quantity_on_hand", "stock_qty", "quantity", "reorder_level", "warehouse")):
        score += 1.4
        reasons.append("stock metric prefers stock quantity columns")

    return score, reasons


def _select_columns_for_table(plan: dict, table_data: dict) -> list[dict]:
    scored_columns: list[tuple[str, float, list[str], str]] = []
    for column in table_data.get("columns", []):
        score, reasons = _column_score(plan, column)
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


def _expand_selected_tables(knowledge_base: dict, selected_names: list[str], plan: dict) -> list[str]:
    selected = list(selected_names)
    dimension = plan.get("dimension")

    for table_name in list(selected_names):
        table_data = knowledge_base.get(table_name, {})
        for relationship in table_data.get("relationships", []):
            if relationship.get("direction") == "incoming":
                target_table = relationship.get("from_table")
            else:
                target_table = relationship.get("to_table")
            if target_table not in knowledge_base or target_table in selected:
                continue

            target_module = knowledge_base[target_table].get("module", "")
            target_semantics = {
                str(column.get("semantic_type", "")).lower()
                for column in knowledge_base[target_table].get("columns", [])
            }
            if dimension and (dimension in target_semantics or dimension in target_module.lower()):
                selected.append(target_table)
            elif relationship.get("confidence", 0) >= 0.90 and len(selected) < 4:
                selected.append(target_table)

    return selected


def _build_selected_table_entries(knowledge_base: dict, scored_tables: list[tuple[str, float, list[str]]], plan: dict) -> list[dict]:
    if not scored_tables:
        return []

    top_tables = [entry for entry in scored_tables if entry[1] > 0.9][:3]
    selected_names = _expand_selected_tables(knowledge_base, [entry[0] for entry in top_tables], plan)
    entries = []
    score_lookup = {table_name: (score, reasons) for table_name, score, reasons in scored_tables}

    for table_name in selected_names:
        score, reasons = score_lookup.get(table_name, (0.8, ["selected as a relationship bridge"]))
        selected_columns = _select_columns_for_table(plan, knowledge_base.get(table_name, {}))
        entries.append(
            {
                "table": table_name,
                "confidence": round(min(max(score / 4.0, 0.55), 0.99), 2),
                "reason": "; ".join(dict.fromkeys(reasons)) or "selected from semantic relationship graph",
                "module": knowledge_base.get(table_name, {}).get("module", "master data"),
                "selected_columns": selected_columns,
            }
        )

    return entries


def build_query_context(
    question: str,
    knowledge_base: dict,
    business_glossary: dict | None = None,
) -> dict:
    """
    Build a structured plan and a reduced ERP-aware schema slice.
    """
    enriched_kb = _enriched_kb(knowledge_base)
    normalized_question = _normalize(question)
    intent = _detect_intent(normalized_question)
    metric = _detect_metric(normalized_question)
    dimension = _detect_dimension(normalized_question)
    filters = _detect_filters(normalized_question)
    date_range = _detect_date_range(normalized_question)
    limit = _extract_limit(normalized_question) or _default_limit_for_intent(intent)

    sorting = None
    if intent == "top_n":
        sorting = {"direction": "desc", "by": metric or "metric"}
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
        "intent": intent,
        "metric": metric,
        "dimension": dimension,
        "filters": filters,
        "date_range": date_range,
        "grouping": grouping,
        "sorting": sorting,
        "limit": limit,
        "question_terms": _tokenize(normalized_question),
    }

    scored_tables = []
    for table_name, table_data in enriched_kb.items():
        score, reasons = _table_score(plan, table_name, table_data, business_glossary)
        if score > 0:
            scored_tables.append((table_name, score, reasons))

    scored_tables.sort(key=lambda item: (-item[1], item[0]))
    selected_tables = _build_selected_table_entries(enriched_kb, scored_tables, plan)
    selected_names = [entry["table"] for entry in selected_tables]

    if not selected_names:
        selected_names = list(enriched_kb.keys())
        selected_tables = [
            {
                "table": table_name,
                "confidence": 0.55,
                "reason": "fell back to full schema because no table scored above the selection threshold",
                "module": table_data.get("module", "master data"),
            }
            for table_name, table_data in enriched_kb.items()
        ]

    reduced_kb = {table_name: deepcopy(enriched_kb[table_name]) for table_name in selected_names}
    warnings = []
    if len(reduced_kb) < len(enriched_kb):
        warnings.append(
            f"Using {len(reduced_kb)} relevant table(s) instead of the full schema."
        )
    if intent in {"list", "top_n", "low_stock"}:
        warnings.append("Read-only row limits stay enabled for list-style ERP questions.")

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
    }
