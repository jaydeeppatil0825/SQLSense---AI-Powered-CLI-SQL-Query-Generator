"""
ai/simple_query_generator.py
=============================
Deterministic SQL generator for simple, single-table questions.

Classifier order (most specific → least specific):
  1. count
  2. latest / recent
  3. status filter
  4. total aggregation
  5. average aggregation
  6. show all  ← last so it never overrides a more specific intent
"""

from __future__ import annotations

import re

from semantic.business_glossary import load_business_glossary


# ── Complex-intent keywords ───────────────────────────────────────────────────
# If ANY of these appear in the question → return None, let AI handle it.

_COMPLEX_KEYWORDS = {
    "by", "group", "top", "highest", "lowest", "monthly",
    "month", "year", "yearly", "trend", "compare", "comparison",
    "category", "city", "customer by", "product by", "join",
    "revenue by", "sales by", "per", "breakdown", "distribution",
    "ranking", "rank", "versus", "vs", "between", "range",
    "report", "summary", "detail", "details",
}

_COMPLEX_RE = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in sorted(_COMPLEX_KEYWORDS, key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)

# Words that indicate a more specific intent than "show all".
# If any of these are present, _try_show_all returns None.
_NOT_SHOW_ALL = re.compile(
    r"\b(total|sum|average|avg|count|how\s+many|latest|recent|newest|"
    r"active|inactive|paid|unpaid|pending|cancelled|canceled|"
    r"delivered|shipped|processing|open|closed|resolved|high|low)\b",
    re.IGNORECASE,
)


# ── Table alias map ───────────────────────────────────────────────────────────

_TABLE_ALIASES: dict[str, str] = {
    "customer": "customers", "customers": "customers",
    "client": "customers", "clients": "customers",
    "buyer": "customers", "buyers": "customers",
    "user": "customers", "users": "customers",
    "product": "products", "products": "products",
    "item": "products", "items": "products", "sku": "products",
    "order": "orders", "orders": "orders",
    "booking": "orders", "bookings": "orders",
    "invoice": "orders", "invoices": "orders",
    "payment": "payments", "payments": "payments",
    "transaction": "payments", "transactions": "payments",
    "employee": "employees", "employees": "employees",
    "staff": "employees", "worker": "employees", "workers": "employees",
    "ticket": "support_tickets", "tickets": "support_tickets",
    "support ticket": "support_tickets", "support tickets": "support_tickets",
    "issue": "support_tickets", "issues": "support_tickets",
    "order item": "order_items", "order items": "order_items",
    "line item": "order_items", "line items": "order_items",
}

# ── Business-term → (default table, candidate columns) ──────────────────────
# Used when no table name appears in the question.
# Order matters: first match with an existing table+column wins.

_BUSINESS_TERM_TABLE: list[tuple[str, str, list[str]]] = [
    # (trigger_word_in_question, default_table, candidate_columns)
    ("salary",       "employees",   ["salary"]),
    ("wage",         "employees",   ["salary"]),
    ("paid amount",  "payments",    ["paid_amount"]),
    ("paid",         "payments",    ["paid_amount"]),
    ("discount",     "orders",      ["discount_amount"]),
    ("tax",          "orders",      ["tax_amount"]),
    ("quantity",     "order_items", ["quantity"]),
    ("unit price",   "products",    ["unit_price"]),
    ("product price","products",    ["unit_price", "cost_price"]),
    ("price",        "products",    ["unit_price", "cost_price"]),
    ("revenue",      "orders",      ["final_amount", "total_amount"]),
    ("sales",        "orders",      ["final_amount", "total_amount"]),
    ("order value",  "orders",      ["final_amount", "total_amount"]),
    ("amount",       "orders",      ["final_amount", "total_amount", "paid_amount"]),
    ("total",        "orders",      ["final_amount", "total_amount"]),
]

# ── Candidate columns ─────────────────────────────────────────────────────────

_DATE_COLUMNS = [
    "order_date", "payment_date", "created_at", "created_date",
    "signup_date", "joining_date", "resolved_date", "updated_at",
]

_AMOUNT_COLUMNS: dict[str, list[str]] = {
    "sales":         ["final_amount", "total_amount", "paid_amount", "amount", "line_total"],
    "revenue":       ["final_amount", "total_amount", "paid_amount", "amount"],
    "paid":          ["paid_amount", "final_amount", "total_amount"],
    "discount":      ["discount_amount"],
    "tax":           ["tax_amount"],
    "salary":        ["salary"],
    "wage":          ["salary"],
    "product price": ["unit_price", "cost_price"],
    "unit price":    ["unit_price"],
    "price":         ["unit_price", "final_amount", "total_amount"],
    "amount":        ["final_amount", "total_amount", "paid_amount", "amount", "line_total"],
    "value":         ["final_amount", "total_amount", "unit_price"],
    "cost":          ["cost_price", "unit_price"],
    "total":         ["final_amount", "total_amount", "paid_amount", "amount"],
    "quantity":      ["quantity"],
}

# Status filters: (trigger_word, column_name, SQL_value)
_STATUS_FILTERS: list[tuple[str, str, str]] = [
    ("paid",       "payment_status", "Paid"),
    ("unpaid",     "payment_status", "Pending"),
    ("pending",    "payment_status", "Pending"),
    ("refunded",   "payment_status", "Refunded"),
    ("delivered",  "order_status",   "Delivered"),
    ("cancelled",  "order_status",   "Cancelled"),
    ("canceled",   "order_status",   "Cancelled"),
    ("processing", "order_status",   "Processing"),
    ("shipped",    "order_status",   "Shipped"),
    ("active",     "status",         "Active"),
    ("inactive",   "status",         "Inactive"),
    ("open",       "ticket_status",  "Open"),
    ("closed",     "ticket_status",  "Closed"),
    ("resolved",   "ticket_status",  "Resolved"),
    ("high",       "priority",       "High"),
    ("low",        "priority",       "Low"),
]


# ── Helper functions ──────────────────────────────────────────────────────────

_MONTHS: dict[str, int] = {
    "january": 1, "jan": 1,
    "february": 2, "feb": 2,
    "march": 3, "mar": 3,
    "april": 4, "apr": 4,
    "may": 5,
    "june": 6, "jun": 6,
    "july": 7, "jul": 7,
    "august": 8, "aug": 8,
    "september": 9, "sep": 9, "sept": 9,
    "october": 10, "oct": 10,
    "november": 11, "nov": 11,
    "december": 12, "dec": 12,
}


def _has_tables(knowledge_base: dict, *tables: str) -> bool:
    return all(table in knowledge_base for table in tables)


def _has_cols(knowledge_base: dict, table: str, *columns: str) -> bool:
    table_cols = set(_get_columns(table, knowledge_base))
    return all(column in table_cols for column in columns)


def _amount_col(knowledge_base: dict) -> str | None:
    return _first_existing_col("orders", ["final_amount", "total_amount", "amount"], knowledge_base)


def _customer_name_col(knowledge_base: dict) -> str | None:
    return _first_existing_col("customers", ["customer_name", "name"], knowledge_base)


def _product_name_col(knowledge_base: dict) -> str | None:
    return _first_existing_col("products", ["product_name", "name"], knowledge_base)


def _order_month_range(q: str) -> tuple[str, str] | None:
    year_match = re.search(r"\b(20\d{2}|19\d{2})\b", q)
    if not year_match:
        return None
    year = int(year_match.group(1))

    month_number = None
    for month_name, value in _MONTHS.items():
        if re.search(r"\b" + re.escape(month_name) + r"\b", q):
            month_number = value
            break

    if month_number is None:
        return None

    next_year = year + 1 if month_number == 12 else year
    next_month = 1 if month_number == 12 else month_number + 1
    return (
        f"{year:04d}-{month_number:02d}-01",
        f"{next_year:04d}-{next_month:02d}-01",
    )


def _amount_threshold(q: str) -> str | None:
    match = re.search(
        r"\b(?:above|over|greater\s+than|more\s+than)\s+([0-9][0-9,]*(?:\.\d+)?)\b",
        q,
        re.IGNORECASE,
    )
    if not match:
        return None
    return match.group(1).replace(",", "")


def _item_sales_expr(knowledge_base: dict) -> str | None:
    if _has_cols(knowledge_base, "order_items", "line_total"):
        return "oi.line_total"
    if _has_cols(knowledge_base, "order_items", "quantity", "unit_price"):
        return "oi.quantity * oi.unit_price"
    if _has_cols(knowledge_base, "order_items", "quantity") and _has_cols(knowledge_base, "products", "unit_price"):
        return "oi.quantity * p.unit_price"
    return None


def _try_pcsoft_business_sql(user_question: str, knowledge_base: dict) -> str | None:
    """Deterministic SQL for common PCSoft business questions."""
    q = user_question.lower().strip()
    limit = _extract_limit_from_question(q)
    amount_col = _amount_col(knowledge_base)
    customer_name_col = _customer_name_col(knowledge_base)
    product_name_col = _product_name_col(knowledge_base)

    if _has_tables(knowledge_base, "orders") and amount_col:
        if re.search(r"\b(total\s+sales|total\s+revenue|sum\s+sales)\b", q) and not re.search(r"\bby\b", q):
            return f"SELECT SUM({amount_col}) AS total_sales FROM orders;"

        if re.search(r"\b(average\s+order\s+value|avg\s+order\s+value)\b", q):
            return f"SELECT AVG({amount_col}) AS average_order_value FROM orders;"

        if re.search(r"\b(highest|maximum|max)\b", q) and "order" in q and "amount" in q:
            return f"SELECT MAX({amount_col}) AS highest_order_amount FROM orders;"

        if re.search(r"\b(lowest|minimum|min)\b", q) and "order" in q and "amount" in q:
            return f"SELECT MIN({amount_col}) AS lowest_order_amount FROM orders;"

        threshold = _amount_threshold(q)
        if threshold and ("high value" in q or "order" in q):
            return (
                f"SELECT * FROM orders WHERE {amount_col} > {threshold} "
                f"ORDER BY {amount_col} DESC LIMIT {limit};"
            )

        month_range = _order_month_range(q)
        if month_range and "sales" in q and _has_cols(knowledge_base, "orders", "order_date"):
            start_date, end_date = month_range
            return (
                f"SELECT SUM({amount_col}) AS total_sales FROM orders "
                f"WHERE order_date >= '{start_date}' AND order_date < '{end_date}';"
            )

        if month_range and "order" in q and _has_cols(knowledge_base, "orders", "order_date"):
            start_date, end_date = month_range
            return (
                f"SELECT * FROM orders WHERE order_date >= '{start_date}' "
                f"AND order_date < '{end_date}' LIMIT {limit};"
            )

        if re.search(r"\b(monthly|by\s+month|per\s+month)\b", q) and re.search(r"\b(sales|revenue)\b", q):
            if _has_cols(knowledge_base, "orders", "order_date"):
                return (
                    "SELECT DATE_FORMAT(order_date, '%Y-%m') AS month, "
                    f"SUM({amount_col}) AS total_sales FROM orders "
                    "GROUP BY DATE_FORMAT(order_date, '%Y-%m') "
                    "ORDER BY month LIMIT 50;"
                )

        if re.search(r"\border(s)?\s+by\s+status\b", q) and _has_cols(knowledge_base, "orders", "order_status"):
            return (
                "SELECT order_status, COUNT(*) AS total_orders "
                "FROM orders GROUP BY order_status "
                "ORDER BY total_orders DESC LIMIT 50;"
            )

        if re.search(r"\btotal\s+sales\s+by\s+payment\s+status\b", q) and _has_cols(knowledge_base, "orders", "payment_status"):
            return (
                f"SELECT payment_status, SUM({amount_col}) AS total_sales "
                "FROM orders GROUP BY payment_status "
                "ORDER BY total_sales DESC LIMIT 50;"
            )

    if _has_tables(knowledge_base, "customers", "orders") and amount_col:
        can_join_customer_orders = (
            _has_cols(knowledge_base, "orders", "customer_id")
            and _has_cols(knowledge_base, "customers", "customer_id")
        )

        if can_join_customer_orders and customer_name_col and re.search(r"\border(s)?\s+with\s+customer\s+names?\b", q):
            select_cols = ["o.order_id", f"c.{customer_name_col} AS customer_name"]
            for optional_col in ["order_date", "order_status", "payment_status", amount_col]:
                if _col_exists("orders", optional_col, knowledge_base):
                    select_cols.append(f"o.{optional_col}")
            return (
                f"SELECT {', '.join(select_cols)} FROM orders o "
                "JOIN customers c ON o.customer_id = c.customer_id "
                f"LIMIT {limit};"
            )

        if can_join_customer_orders and customer_name_col and re.search(r"\btop\s+\d+\s+customers?\b", q) and re.search(r"\b(sales|revenue)\b", q):
            return (
                f"SELECT c.customer_id, c.{customer_name_col} AS customer_name, "
                f"SUM(o.{amount_col}) AS total_sales FROM customers c "
                "JOIN orders o ON c.customer_id = o.customer_id "
                f"GROUP BY c.customer_id, c.{customer_name_col} "
                f"ORDER BY total_sales DESC LIMIT {limit};"
            )

        if can_join_customer_orders and _has_cols(knowledge_base, "customers", "city") and re.search(r"\btotal\s+sales\s+by\s+city\b", q):
            return (
                f"SELECT c.city, SUM(o.{amount_col}) AS total_sales "
                "FROM customers c JOIN orders o ON c.customer_id = o.customer_id "
                "GROUP BY c.city ORDER BY total_sales DESC LIMIT 50;"
            )

        if can_join_customer_orders and _has_cols(knowledge_base, "customers", "customer_type") and re.search(r"\bsales\s+by\s+customer\s+type\b", q):
            return (
                f"SELECT c.customer_type, SUM(o.{amount_col}) AS total_sales "
                "FROM customers c JOIN orders o ON c.customer_id = o.customer_id "
                "GROUP BY c.customer_type ORDER BY total_sales DESC LIMIT 50;"
            )

        if _has_cols(knowledge_base, "customers", "city") and re.search(r"\bcustomers?\s+by\s+city\b", q):
            return (
                "SELECT city, COUNT(*) AS total_customers "
                "FROM customers GROUP BY city "
                "ORDER BY total_customers DESC LIMIT 50;"
            )

    if _has_tables(knowledge_base, "products", "order_items"):
        can_join_products_items = (
            _has_cols(knowledge_base, "products", "product_id")
            and _has_cols(knowledge_base, "order_items", "product_id")
        )
        item_sales_expr = _item_sales_expr(knowledge_base)

        if _has_cols(knowledge_base, "products", "category") and re.search(r"\bproducts?\s+by\s+category\b", q):
            return (
                "SELECT category, COUNT(*) AS total_products "
                "FROM products GROUP BY category "
                "ORDER BY total_products DESC LIMIT 50;"
            )

        if can_join_products_items and item_sales_expr and _has_cols(knowledge_base, "products", "category") and re.search(r"\bsales\s+by\s+product\s+category\b|\bsales\s+by\s+category\b", q):
            return (
                f"SELECT p.category, SUM({item_sales_expr}) AS total_sales "
                "FROM products p JOIN order_items oi ON p.product_id = oi.product_id "
                "GROUP BY p.category ORDER BY total_sales DESC LIMIT 50;"
            )

        if can_join_products_items and product_name_col and _has_cols(knowledge_base, "order_items", "quantity") and re.search(r"\btop(?:\s+\d+)?\s+selling\s+products?\b", q):
            return (
                f"SELECT p.product_id, p.{product_name_col} AS product_name, "
                "SUM(oi.quantity) AS total_quantity "
                "FROM products p JOIN order_items oi ON p.product_id = oi.product_id "
                f"GROUP BY p.product_id, p.{product_name_col} "
                f"ORDER BY total_quantity DESC LIMIT {limit};"
            )

    if _has_tables(knowledge_base, "payments", "orders", "customers"):
        can_join_payment_customer = (
            _has_cols(knowledge_base, "payments", "order_id")
            and _has_cols(knowledge_base, "orders", "order_id", "customer_id")
            and _has_cols(knowledge_base, "customers", "customer_id")
        )
        if can_join_payment_customer and customer_name_col and re.search(r"\bpayment\s+details?\s+with\s+customer\s+names?\b", q):
            select_cols = ["p.payment_id", "p.order_id", f"c.{customer_name_col} AS customer_name"]
            for optional_col in ["payment_date", "payment_method", "paid_amount", "payment_status"]:
                if _col_exists("payments", optional_col, knowledge_base):
                    select_cols.append(f"p.{optional_col}")
            return (
                f"SELECT {', '.join(select_cols)} FROM payments p "
                "JOIN orders o ON p.order_id = o.order_id "
                "JOIN customers c ON o.customer_id = c.customer_id "
                f"LIMIT {limit};"
            )

        if can_join_payment_customer and customer_name_col and re.search(r"\bcustomers?\s+with\s+pending\s+payments?\b", q):
            status_alias = "p" if _col_exists("payments", "payment_status", knowledge_base) else "o"
            return (
                f"SELECT DISTINCT c.customer_id, c.{customer_name_col} AS customer_name "
                "FROM customers c "
                "JOIN orders o ON c.customer_id = o.customer_id "
                "JOIN payments p ON o.order_id = p.order_id "
                f"WHERE {status_alias}.payment_status = 'Pending' LIMIT 50;"
            )

    if _has_tables(knowledge_base, "support_tickets"):
        if re.search(r"\bopen\s+support\s+tickets?\b", q) and _has_cols(knowledge_base, "support_tickets", "ticket_status"):
            return "SELECT * FROM support_tickets WHERE ticket_status = 'Open' LIMIT 50;"

        if re.search(r"\bsupport\s+tickets?\s+by\s+priority\b", q) and _has_cols(knowledge_base, "support_tickets", "priority"):
            return (
                "SELECT priority, COUNT(*) AS total_tickets "
                "FROM support_tickets GROUP BY priority "
                "ORDER BY total_tickets DESC LIMIT 50;"
            )

        if re.search(r"\bsupport\s+tickets?\s+by\s+status\b", q) and _has_cols(knowledge_base, "support_tickets", "ticket_status"):
            return (
                "SELECT ticket_status, COUNT(*) AS total_tickets "
                "FROM support_tickets GROUP BY ticket_status "
                "ORDER BY total_tickets DESC LIMIT 50;"
            )

        if (
            _has_tables(knowledge_base, "customers")
            and _has_cols(knowledge_base, "support_tickets", "customer_id")
            and _has_cols(knowledge_base, "customers", "customer_id")
            and customer_name_col
            and re.search(r"\bcustomers?\s+who\s+raised\s+support\s+tickets?\b", q)
        ):
            return (
                f"SELECT DISTINCT c.customer_id, c.{customer_name_col} AS customer_name "
                "FROM customers c "
                "JOIN support_tickets st ON c.customer_id = st.customer_id "
                "LIMIT 50;"
            )

    return None


def _get_columns(table_name: str, knowledge_base: dict) -> list[str]:
    table = knowledge_base.get(table_name, {})
    return [col["name"] for col in table.get("columns", [])]


def _col_exists(table_name: str, col_name: str, knowledge_base: dict) -> bool:
    return col_name in _get_columns(table_name, knowledge_base)


def _get_glossary_column_mapping(business_term: str) -> tuple[str, str] | None:
    """
    Look up a business term in the glossary and return (table, column) mapping.
    
    Args:
        business_term: The business term to look up (e.g., "sales", "revenue")
    
    Returns:
        Tuple of (table_name, column_name) if found, None otherwise
    """
    try:
        glossary = load_business_glossary("semantic/business_glossary.json")
        
        if not glossary:
            return None
        
        # Search for the term in the glossary
        term_lower = business_term.lower()
        
        # Direct match
        if term_lower in glossary:
            term_data = glossary[term_lower]
            mapped_columns = term_data.get("mapped_columns", [])
            if mapped_columns:
                # Return the highest confidence mapping
                for mapping in mapped_columns:
                    if mapping.get("confidence") == "high":
                        return (mapping.get("table", ""), mapping.get("column", ""))
                # Fallback to first mapping
                return (mapped_columns[0].get("table", ""), mapped_columns[0].get("column", ""))
        
        # Search in business_terms
        for term, term_data in glossary.items():
            for business_term_entry in term_data.get("business_terms", []):
                if business_term_entry.lower() == term_lower:
                    mapped_columns = term_data.get("mapped_columns", [])
                    if mapped_columns:
                        return (mapped_columns[0].get("table", ""), mapped_columns[0].get("column", ""))
        
        return None
    except Exception:
        return None


def _first_existing_col(table_name: str, candidates: list[str], knowledge_base: dict) -> str | None:
    for col in candidates:
        if _col_exists(table_name, col, knowledge_base):
            return col
    return None


def find_table_from_question(user_question: str, knowledge_base: dict) -> str | None:
    """
    Find which table the question is about.
    Tries multi-word aliases first, then single-word, then direct name match.
    Returns None if no table found in the knowledge base.
    """
    q = user_question.lower().strip()
    kb_tables = set(knowledge_base.keys())

    # Multi-word aliases first (longest first to avoid partial matches).
    for alias, table in sorted(_TABLE_ALIASES.items(), key=lambda x: -len(x[0])):
        if " " in alias and alias in q and table in kb_tables:
            return table

    # Single-word aliases.
    for word in re.split(r"\W+", q):
        if word and word in _TABLE_ALIASES:
            table = _TABLE_ALIASES[word]
            if table in kb_tables:
                return table

    # Direct table name in question.
    for table in kb_tables:
        if table.lower() in q or table.lower().rstrip("s") in q:
            return table

    return None


def _find_table_by_business_term(user_question: str, knowledge_base: dict) -> tuple[str, str, list[str]] | None:
    """
    When no table name is in the question, try to infer table + columns
    from business terms like 'sales', 'salary', 'paid amount'.
    Returns (table_name, trigger_term, candidate_columns) or None.
    
    First tries the business glossary if available, then falls back to
    hardcoded mappings.
    """
    q = user_question.lower()
    kb_tables = set(knowledge_base.keys())

    # Exact multi-word phrases are more specific than single glossary tokens.
    # Example: "paid amount" must map to payments.paid_amount, not the word
    # "paid" mapping to payment_status.
    for trigger, default_table, cols in _BUSINESS_TERM_TABLE:
        if " " in trigger and trigger in q and default_table in kb_tables:
            return (default_table, trigger, cols)

    # Fall back to hardcoded single-word mappings.
    for trigger, default_table, cols in _BUSINESS_TERM_TABLE:
        if " " not in trigger and re.search(r"\b" + re.escape(trigger) + r"\b", q) and default_table in kb_tables:
            return (default_table, trigger, cols)

    # Try business glossary next for schema-generated terms.
    for word in re.split(r"\W+", q):
        if word:
            glossary_mapping = _get_glossary_column_mapping(word)
            if glossary_mapping:
                table, column = glossary_mapping
                if table in kb_tables:
                    return (table, word, [column])

    return None


def _is_complex_question(user_question: str) -> bool:
    return bool(_COMPLEX_RE.search(user_question))


def _extract_limit_from_question(user_question: str) -> int:
    match = re.search(
        r"\b(?:latest|last|recent|first|top|show|get|fetch)\s+(\d+)\b"
        r"|\b(\d+)\s+(?:rows?|records?|results?)\b",
        user_question,
        re.IGNORECASE,
    )
    if match:
        return int(match.group(1) or match.group(2))
    return 50


# ── Query classifiers ─────────────────────────────────────────────────────────

def _try_count(q: str, table: str, knowledge_base: dict) -> str | None:
    if re.search(r"\b(count|how\s+many|total\s+number|number\s+of)\b", q, re.IGNORECASE):
        alias = f"total_{table}"
        return f"SELECT COUNT(*) AS {alias} FROM {table};"
    return None


def _try_latest(q: str, table: str, knowledge_base: dict) -> str | None:
    if not re.search(r"\b(latest|recent|newest|last|most\s+recent)\b", q, re.IGNORECASE):
        return None
    date_col = _first_existing_col(table, _DATE_COLUMNS, knowledge_base)
    if not date_col:
        return None
    limit = _extract_limit_from_question(q)
    return f"SELECT * FROM {table} ORDER BY {date_col} DESC LIMIT {limit};"


def _try_status_filter(q: str, table: str, knowledge_base: dict) -> str | None:
    table_cols = set(_get_columns(table, knowledge_base))
    for trigger, col, value in _STATUS_FILTERS:
        # Use whole-word matching so "paid" doesn't match "prepaid"
        if re.search(r"\b" + re.escape(trigger) + r"\b", q, re.IGNORECASE):
            if col in table_cols:
                return f"SELECT * FROM {table} WHERE {col} = '{value}' LIMIT 50;"
    return None


def _try_total_aggregation(q: str, table: str, knowledge_base: dict, candidate_cols: list[str] | None = None, business_term: str | None = None) -> str | None:
    if not re.search(r"\b(total|sum)\b", q, re.IGNORECASE):
        return None

    # Use provided candidate columns (from business-term lookup) or scan by keyword.
    col = None
    col_alias = None

    if candidate_cols:
        col = _first_existing_col(table, candidate_cols, knowledge_base)
        if col:
            # Use the business term for the alias (e.g. "sales" → total_sales),
            # falling back to the column name if no term was provided.
            term_for_alias = (business_term or col).replace(" ", "_")
            col_alias = f"total_{term_for_alias}"
    else:
        for term, candidates in _AMOUNT_COLUMNS.items():
            if re.search(r"\b" + re.escape(term) + r"\b", q, re.IGNORECASE):
                col = _first_existing_col(table, candidates, knowledge_base)
                if col:
                    col_alias = f"total_{term.replace(' ', '_')}"
                    break

    if not col:
        return None
    return f"SELECT SUM({col}) AS {col_alias} FROM {table};"


def _try_average_aggregation(q: str, table: str, knowledge_base: dict, candidate_cols: list[str] | None = None, business_term: str | None = None) -> str | None:
    if not re.search(r"\b(average|avg|mean)\b", q, re.IGNORECASE):
        return None

    col = None
    col_alias = None

    if candidate_cols:
        col = _first_existing_col(table, candidate_cols, knowledge_base)
        if col:
            term_for_alias = (business_term or col).replace(" ", "_")
            col_alias = f"average_{term_for_alias}"
    else:
        for term, candidates in _AMOUNT_COLUMNS.items():
            if re.search(r"\b" + re.escape(term) + r"\b", q, re.IGNORECASE):
                col = _first_existing_col(table, candidates, knowledge_base)
                if col:
                    col_alias = f"average_{term.replace(' ', '_')}"
                    break

    if not col:
        return None
    return f"SELECT AVG({col}) AS {col_alias} FROM {table};"


def _try_show_all(q: str, table: str, knowledge_base: dict) -> str | None:
    # Do NOT fire if a more specific intent is present.
    if _NOT_SHOW_ALL.search(q):
        return None
    # Fire on explicit "show/list/get all" OR just mentioning the table with no other intent.
    if re.search(r"\b(show|list|display|get|fetch|view|see|give)\b", q, re.IGNORECASE):
        return f"SELECT * FROM {table} LIMIT 50;"
    return None


# ── Public entry point ────────────────────────────────────────────────────────

def generate_simple_sql(user_question: str, knowledge_base: dict) -> str | None:
    """
    Try to generate SQL for a simple single-table question using Python logic.

    Returns ready-to-execute SQL or None (let AI handle complex queries).

    Classifier order (most specific first):
      1. count
      2. latest/recent
      3. status filter
      4. total aggregation
      5. average aggregation
      6. show all   ← only if no more specific intent detected
    """
    if not user_question or not knowledge_base:
        return None

    business_sql = _try_pcsoft_business_sql(user_question, knowledge_base)
    if business_sql:
        return business_sql

    # Complex questions go to AI.
    if _is_complex_question(user_question):
        return None

    q = user_question.lower().strip()

    # Try to find the table from the question text.
    table = find_table_from_question(user_question, knowledge_base)
    business_candidates: list[str] | None = None
    business_term: str | None = None

    # If no table found, try business-term mapping.
    if not table:
        result = _find_table_by_business_term(user_question, knowledge_base)
        if result:
            table, business_term, business_candidates = result
        else:
            return None   # cannot identify table → AI handles it

    # Run classifiers in order (most specific first).
    # When the table was found via a business term that implies aggregation
    # (e.g. "paid amount", "total sales"), run aggregation classifiers BEFORE
    # the status filter so "Show total paid amount" → SUM, not WHERE status='Paid'.
    if business_candidates:
        classifiers = [
            lambda q, t, kb: _try_count(q, t, kb),
            lambda q, t, kb: _try_latest(q, t, kb),
            lambda q, t, kb: _try_total_aggregation(q, t, kb, business_candidates, business_term),
            lambda q, t, kb: _try_average_aggregation(q, t, kb, business_candidates, business_term),
            lambda q, t, kb: _try_status_filter(q, t, kb),
            lambda q, t, kb: _try_show_all(q, t, kb),
        ]
    else:
        classifiers = [
            lambda q, t, kb: _try_count(q, t, kb),
            lambda q, t, kb: _try_latest(q, t, kb),
            lambda q, t, kb: _try_status_filter(q, t, kb),
            lambda q, t, kb: _try_total_aggregation(q, t, kb, None, None),
            lambda q, t, kb: _try_average_aggregation(q, t, kb, None, None),
            lambda q, t, kb: _try_show_all(q, t, kb),
        ]

    for classifier in classifiers:
        result = classifier(q, table, knowledge_base)
        if result:
            return result

    return None
