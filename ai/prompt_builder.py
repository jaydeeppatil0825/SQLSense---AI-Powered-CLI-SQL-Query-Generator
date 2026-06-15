"""
ai/prompt_builder.py
====================
Builds the structured prompt that is sent to the AI backend (Ollama or
OpenAI) for SQL generation.

This module is the single place that controls how much context the model
receives.  A richer prompt = more accurate SQL.

What the prompt contains
------------------------
1. Hard rules   — what the model MUST and MUST NOT do
2. Query rules  — how to handle aggregates, joins, ORDER BY, LIMIT, etc.
3. Business glossary — maps plain English terms to SQL patterns
4. Schema context — every table, column, type, nullable, semantic_type,
                    sample values, min/max, row count
5. Relationship map — every foreign key explained as a JOIN instruction
6. LIMIT hint   — dynamically set based on the user's question

Debug mode
----------
Set DEBUG_PROMPT=true in .env to print the full system prompt to the
terminal before sending it to the model.  Useful for diagnosing why a
query came out wrong.
"""

from __future__ import annotations

import os
import re

from semantic.business_glossary import load_business_glossary, search_business_glossary


# ── Business glossary ─────────────────────────────────────────────────────────
# Maps plain-English terms a user might write to the SQL concept they imply.
# The model sees this glossary and uses it to choose the right column/function.
# Entries are schema-aware: they reference the actual table.column names so
# the model picks the correct column instead of guessing.
_BUSINESS_GLOSSARY = """
Business term glossary — map the user's words to the correct table/column:

  SALES / REVENUE / INCOME / EARNINGS
    → Use orders.final_amount or orders.total_amount with SUM().
    → The primary sales table is "orders", NOT "customers".
    → Do NOT use customers.signup_date for sales questions.
    → For monthly sales: GROUP BY DATE_FORMAT(orders.order_date, '%Y-%m').

  MONTHLY / BY MONTH / PER MONTH
    → Date column to use: orders.order_date (type DATE, in table "orders").
    → Expression: DATE_FORMAT(order_date, '%Y-%m') AS month
    → Do NOT use customers.signup_date for monthly sales.

  TOTAL AMOUNT / PAID AMOUNT
    → orders.final_amount  — the after-tax, after-discount order total.
    → orders.total_amount  — the pre-tax subtotal.
    → payments.paid_amount — the amount actually collected.

  CUSTOMER / CLIENT / BUYER / USER
    -> Trusted join: orders.customer_id = customers.customer_id.
    → customers.customer_name, customers.customer_id
    → Join orders to customers on orders.customer_id = customers.customer_id.

  TRUSTED PCSOFT RELATIONSHIPS
    -> customers.customer_id = orders.customer_id
    -> orders.order_id = order_items.order_id
    -> products.product_id = order_items.product_id
    -> orders.order_id = payments.order_id
    -> customers.customer_id = support_tickets.customer_id
    -> orders.order_id = support_tickets.order_id.

  ORDER / INVOICE / BOOKING / TRANSACTION
    -> High value orders use orders.final_amount or orders.total_amount with a numeric threshold.
    → Table "orders": order_id, order_date, order_status, final_amount.

  PAYMENT DETAILS / PENDING PAYMENTS
    -> Join payments to orders on payments.order_id = orders.order_id.
    -> Join orders to customers on orders.customer_id = customers.customer_id.
    -> Pending payments usually means payment_status = 'Pending'.

  SUPPORT TICKETS
    -> Use support_tickets.ticket_status, support_tickets.priority, and support_tickets.customer_id.
    -> Join to customers for customer names.

  QUANTITY / QTY / UNITS / COUNT (of items)
    → order_items.quantity  (per line item)
    → SUM(order_items.quantity) for totals.

  STATUS / STATE
    → orders.order_status   values: Delivered, Cancelled, Processing, Shipped
    → orders.payment_status values: Paid, Refunded, Pending

  PRODUCT / ITEM / SKU
    → products.product_name, products.category, products.unit_price

  CATEGORY / TYPE / GROUP (product)
    → products.category  values: Electronics, Furniture, Stationery

  CITY / LOCATION
    → customers.city  or  orders.shipping_city

  EMPLOYEE / STAFF / SALESPERSON
    → employees.employee_name, employees.department, employees.salary
""".strip()


# ── Query rules ───────────────────────────────────────────────────────────────
# Explicit instructions for common query patterns so the model does not guess.
_QUERY_RULES = """
SQL query construction rules:
  - Use COUNT(*) or COUNT(column) when the user asks "how many".
  - Use SUM(column) when the user asks for total/sales/revenue/amount.
  - Use AVG(column) when the user asks for average.
  - Use MAX/MIN when the user asks for highest/lowest/most/least.
  - Always add GROUP BY when mixing aggregate functions with non-aggregate columns.
  - Add ORDER BY <alias or aggregate> DESC when the user asks "top", "highest", "most".
  - Add ORDER BY <alias or aggregate> ASC  when the user asks "lowest", "least", "fewest".
  - ORDER BY MUST reference a column name or alias that exists in the SELECT clause.
  - NEVER write "ORDER BY LIMIT" — ORDER BY requires a column/alias before LIMIT.
  - NEVER write "ORDER BY ;" or "ORDER BY" with nothing after it.
  - If you are unsure what to ORDER BY, omit ORDER BY entirely rather than writing it incorrectly.
  - Use WHERE to filter by city, status, date range, category, or any specific value.
  - Use JOIN only when a foreign key relationship exists between the two tables.
  - Use the exact join condition from the foreign key definition (do not guess).
  - Never invent table names or column names — use only what is listed below.
  - Prefer fully-qualified column references (table.column) to avoid ambiguity.
  - Do NOT add extra text, explanations, or comments inside the SQL output.

TABLE ALIAS RULES (very important — alias errors cause runtime failures):
  - When you write "FROM orders o", the alias for orders is "o". Use "o." everywhere.
  - When you write "JOIN customers c", the alias for customers is "c". Use "c." everywhere.
  - When you write "JOIN payments p", the alias for payments is "p". Use "p." everywhere.
  - NEVER use an alias that was not declared in the FROM or JOIN clause.
  - NEVER invent an alias like "pt" for payments if you declared "p" in the JOIN.

Alias correctness examples:
  CORRECT:  FROM payments p  →  SELECT p.payment_method, p.paid_amount
  WRONG:    FROM payments p  →  SELECT pt.payment_method   ← "pt" was never declared
  CORRECT:  FROM orders o JOIN customers c  →  SELECT o.order_id, c.customer_name
  WRONG:    FROM orders o JOIN customers c  →  SELECT ord.order_id  ← "ord" was never declared

ORDER BY correctness examples:
  CORRECT:   SELECT month, SUM(final_amount) AS total_sales ... ORDER BY month
  CORRECT:   SELECT customer_name, SUM(final_amount) AS revenue ... ORDER BY revenue DESC
  WRONG:     ... ORDER BY LIMIT 50          ← missing column before LIMIT
  WRONG:     ... ORDER BY;                  ← missing column before semicolon
  WRONG:     ... ORDER BY                   ← missing column entirely
""".strip()


# ── Safety rules ──────────────────────────────────────────────────────────────
_PCSOFT_RELATIONSHIP_GUIDANCE = """
Trusted PCSoft relationship guidance:
  - customers.customer_id = orders.customer_id
  - orders.order_id = order_items.order_id
  - products.product_id = order_items.product_id
  - orders.order_id = payments.order_id
  - customers.customer_id = support_tickets.customer_id
  - orders.order_id = support_tickets.order_id
  - Use these joins only when the listed tables and columns exist in the schema context.
""".strip()


_SAFETY_RULES = """
Safety rules (these are enforced by a validator after you respond):
  - Return ONLY a single SELECT statement — nothing else.
  - Do NOT use: INSERT, UPDATE, DELETE, DROP, ALTER, TRUNCATE, CREATE, REPLACE.
  - Do NOT use markdown fences (```sql), backticks around the answer, or explanations.
  - Do NOT include SQL comments (#, --, /* */, or /*! */).
  - Do NOT include multiple statements separated by semicolons.
  - A trailing semicolon on the final statement is allowed.
""".strip()


# ── LIMIT extraction ──────────────────────────────────────────────────────────

def _extract_limit(user_question: str) -> int | None:
    """
    Parse an explicit row count from the user's question.

    Returns the integer if found (e.g. "top 5" → 5), or None if the
    question does not specify a count (caller will default to 50).

    Examples
    --------
    "Show top 5 customers"  → 5
    "List latest 10 orders" → 10
    "Show all customers"    → None
    """
    match = re.search(
        r"\b(?:top|first|last|latest|recent|limit|show|get|return|fetch)\s+(\d+)\b"
        r"|\b(\d+)\s+(?:rows?|records?|results?|customers?|orders?|products?)\b",
        user_question,
        re.IGNORECASE,
    )
    if match:
        # One of the two capture groups will have the number.
        raw = match.group(1) or match.group(2)
        return int(raw)
    return None


# ── Dynamic glossary from business_glossary.json ─────────────────────────────

def _get_relevant_glossary_terms(user_question: str, knowledge_base: dict | None = None, glossary_path: str | None = None) -> str:
    """
    Load the business glossary and extract terms relevant to the user's question.
    
    Args:
        user_question: The user's natural language question
        knowledge_base: Optional knowledge base for additional context
        glossary_path: Optional path to glossary file (defaults to semantic/business_glossary.json)
    
    Returns:
        Formatted glossary section for the prompt
    """
    from utils.logger import get_logger
    
    logger = get_logger()
    
    if glossary_path is None:
        glossary_path = "semantic/business_glossary.json"
    
    try:
        glossary = load_business_glossary(glossary_path)
        
        if not glossary:
            logger.debug("Business glossary not found, using hardcoded glossary")
            # Fall back to hardcoded glossary if business glossary not available
            return _BUSINESS_GLOSSARY
        
        # Extract words from the user's question
        question_words = set(user_question.lower().split())
        
        # Find matching glossary terms
        relevant_terms = {}
        for term, term_data in glossary.items():
            # Check if term or any of its business_terms match the question
            term_lower = term.lower()
            if term_lower in question_words:
                relevant_terms[term] = term_data
                continue
            
            # Check business_terms
            for business_term in term_data.get("business_terms", []):
                if business_term.lower() in question_words:
                    relevant_terms[term] = term_data
                    break
        
        # If no matches found, use a few common terms as fallback
        if not relevant_terms:
            # Include a few common terms as a safety net
            common_terms = ["sales", "customer", "revenue"]
            for term in common_terms:
                if term in glossary:
                    relevant_terms[term] = glossary[term]
        
        logger.debug(f"Found {len(relevant_terms)} relevant glossary terms for question")
        
        # Format the relevant glossary terms
        if not relevant_terms:
            return _BUSINESS_GLOSSARY  # Fallback to hardcoded
        
        lines = ["Business term glossary — map the user's words to the correct table/column:"]
        lines.append("")
        
        for term, term_data in relevant_terms.items():
            description = term_data.get("description", "")
            lines.append(f"  {term.upper()}")
            lines.append(f"    → {description}")
            
            mapped_columns = term_data.get("mapped_columns", [])
            if mapped_columns:
                col_mappings = []
                for mapping in mapped_columns[:3]:  # Limit to top 3 mappings
                    table = mapping.get("table", "")
                    column = mapping.get("column", "")
                    col_mappings.append(f"{table}.{column}")
                if col_mappings:
                    lines.append(f"    → Maps to: {', '.join(col_mappings)}")
            
            example_questions = term_data.get("example_questions", [])
            if example_questions:
                lines.append(f"    → Example questions: {', '.join(example_questions[:2])}")
            
            lines.append("")
        
        return "\n".join(lines)
    
    except Exception as exc:
        logger.warning(f"Failed to load business glossary: {exc}, using hardcoded glossary")
        # If anything goes wrong, fall back to hardcoded glossary
        return _BUSINESS_GLOSSARY


# ── Schema context builder ────────────────────────────────────────────────────

def _build_schema_section(knowledge_base: dict) -> list[str]:
    """
    Convert the knowledge base into a readable schema section for the prompt.

    Each table gets:
    - table name and row count
    - primary keys
    - every column: name, type, nullable, semantic_type, sample values, min/max
    - foreign key relationships explained as JOIN instructions
    """
    lines: list[str] = []
    lines.append("Database schema (use ONLY these tables and columns):")
    lines.append("")

    for table_name, table_data in knowledge_base.items():
        row_count = table_data.get("row_count", "unknown")
        lines.append(f"TABLE: {table_name}  (approx. {row_count} rows)")

        # Primary keys
        primary_keys = table_data.get("primary_keys", [])
        if primary_keys:
            lines.append(f"  Primary key(s): {', '.join(str(k) for k in primary_keys)}")

        # Columns
        for col in table_data.get("columns", []):
            name     = col.get("name", "")
            col_type = col.get("type", "")
            nullable = "nullable" if col.get("nullable") else "not null"
            sem_type = col.get("semantic_type", "general")

            col_line = (
                f"  COLUMN: {name}"
                f"  type={col_type}"
                f"  {nullable}"
                f"  semantic_type={sem_type}"
            )
            lines.append(col_line)

            # Sample values give the model concrete examples (read-only context).
            samples = [str(v) for v in (col.get("sample_values") or [])[:5] if v is not None]
            if samples:
                lines.append(f"    sample_values: {', '.join(samples)}")

            # Min/max help with range queries and ORDER BY choices.
            if "min_value" in col and col["min_value"] is not None:
                lines.append(
                    f"    range: {col['min_value']} … {col.get('max_value')}"
                )

        # Foreign keys — explained as explicit JOIN instructions.
        foreign_keys = table_data.get("foreign_keys", [])
        if foreign_keys:
            lines.append(f"  Relationships (JOIN hints for {table_name}):")
            for fk in foreign_keys:
                local_col  = fk.get("column", "")
                ref_table  = fk.get("referenced_table", "")
                ref_col    = fk.get("referenced_column", "")
                lines.append(
                    f"    {table_name}.{local_col} references {ref_table}.{ref_col}"
                    f"  →  JOIN {ref_table} ON {table_name}.{local_col} = {ref_table}.{ref_col}"
                )

        lines.append("")  # blank line between tables

    return lines


def _build_plan_section(query_plan: dict | None, selected_tables: list[dict] | None) -> list[str]:
    if not query_plan and not selected_tables:
        return []

    lines: list[str] = []
    lines.append("Structured query plan:")
    if query_plan:
        lines.append(f"  intent: {query_plan.get('intent')}")
        lines.append(f"  metric: {query_plan.get('metric')}")
        lines.append(f"  dimension: {query_plan.get('dimension')}")
        lines.append(f"  filters: {query_plan.get('filters')}")
        lines.append(f"  date_range: {query_plan.get('date_range')}")
        lines.append(f"  grouping: {query_plan.get('grouping')}")
        lines.append(f"  sorting: {query_plan.get('sorting')}")
        lines.append(f"  limit: {query_plan.get('limit')}")

    if selected_tables:
        lines.append("Relevant tables selected before SQL generation:")
        for table_entry in selected_tables:
            table_name = table_entry.get("table", "")
            confidence = table_entry.get("confidence", "unknown")
            reason = table_entry.get("reason", "")
            lines.append(f"  - {table_name} (confidence={confidence}): {reason}")

    lines.append("")
    return lines


# ── Worked examples (injected dynamically based on question topic) ────────────

def _monthly_sales_example(user_question: str, knowledge_base: dict | None = None) -> str:
    """Return monthly sales example only if schema supports it and question matches."""
    q = user_question.lower()
    is_monthly = any(w in q for w in ("month", "monthly", "per month", "by month"))
    is_sales = any(w in q for w in ("sale", "sales", "revenue", "income", "amount", "total"))

    if not (is_monthly and is_sales):
        return ""

    # Only include if the schema has orders.order_date + a sales amount column
    if knowledge_base:
        orders = knowledge_base.get("orders", {})
        order_cols = [c["name"] for c in orders.get("columns", [])]
        has_date = "order_date" in order_cols
        has_amount = any(c in order_cols for c in ["final_amount", "total_amount"])
        if not (has_date and has_amount):
            return ""   # schema doesn't match — don't add this example

    amount_col = "final_amount"
    if knowledge_base:
        orders = knowledge_base.get("orders", {})
        order_cols = [c["name"] for c in orders.get("columns", [])]
        if "final_amount" not in order_cols and "total_amount" in order_cols:
            amount_col = "total_amount"

    return (
        "Worked example — monthly sales query:\n"
        "  Question: Show monthly sales\n"
        "  Correct SQL:\n"
        f"    SELECT DATE_FORMAT(order_date, '%Y-%m') AS month,\n"
        f"           SUM({amount_col}) AS total_sales\n"
        "    FROM orders\n"
        f"    GROUP BY DATE_FORMAT(order_date, '%Y-%m')\n"
        "    ORDER BY month\n"
        "    LIMIT 50;\n"
        "  Key points:\n"
        "    - Use orders.order_date for monthly grouping.\n"
        f"    - Use SUM({amount_col}) for the money total.\n"
        "    - GROUP BY the DATE_FORMAT expression, not just the alias.\n"
        "    - ORDER BY month (the alias defined in SELECT)."
    )


def _paid_orders_example(user_question: str, knowledge_base: dict | None = None) -> str:
    """Return paid orders example only if schema supports it and question matches."""
    q = user_question.lower()
    is_paid = "paid" in q
    is_orders = any(w in q for w in ("order", "orders", "invoice"))

    if not (is_paid and is_orders):
        return ""

    # Only include if orders, customers, and payments tables exist with right columns
    if knowledge_base:
        kb_tables = set(knowledge_base.keys())
        required = {"orders", "customers", "payments"}
        if not required.issubset(kb_tables):
            return ""
        # Check payment_status exists
        payments_cols = [c["name"] for c in knowledge_base.get("payments", {}).get("columns", [])]
        orders_cols = [c["name"] for c in knowledge_base.get("orders", {}).get("columns", [])]
        has_payment_status = "payment_status" in payments_cols or "payment_status" in orders_cols
        if not has_payment_status:
            return ""

    return (
        "Worked example — paid orders query:\n"
        "  Question: Show paid orders\n"
        "  Correct SQL:\n"
        "    SELECT o.order_id, c.customer_name, o.order_date,\n"
        "           p.paid_amount, p.payment_method\n"
        "    FROM orders o\n"
        "    JOIN customers c ON o.customer_id = c.customer_id\n"
        "    JOIN payments p ON o.order_id = p.order_id\n"
        "    WHERE p.payment_status = 'Paid'\n"
        "    LIMIT 50;\n"
        "  Key alias rules:\n"
        "    - orders alias is 'o', customers alias is 'c', payments alias is 'p'.\n"
        "    - NEVER use 'pt.' — it was not declared anywhere."
    )


# ── Main entry point ──────────────────────────────────────────────────────────

def build_sql_prompt(
    user_question: str,
    knowledge_base: dict,
    query_plan: dict | None = None,
    selected_tables: list[dict] | None = None,
) -> list[dict]:
    """
    Build an OpenAI-compatible message list for SQL generation.

    The returned list has exactly two items:
      - {"role": "system", "content": <full context + rules>}
      - {"role": "user",   "content": <user question>}

    This format is accepted by both Ollama (/api/chat) and the OpenAI SDK.

    Args:
        user_question:  The plain-English question from the user.
        knowledge_base: The dict loaded from semantic/knowledge_base.json.

    Returns:
        A two-element messages list ready to send to the AI backend.

    Raises:
        ValueError: If knowledge_base is None or empty.
    """
    if not knowledge_base:
        raise ValueError(
            "Knowledge base is missing or empty. "
            "Please run option 2 (Build Knowledge Base) first."
        )

    # ── Determine LIMIT for this question ────────────────────────────────────
    explicit_limit = _extract_limit(user_question)
    if explicit_limit:
        # The user asked for a specific number — tell the model exactly.
        limit_instruction = (
            f"The user asked for {explicit_limit} rows. "
            f"Use LIMIT {explicit_limit} in your query."
        )
    else:
        # No count specified — apply the default safety cap.
        limit_instruction = (
            "The user did not specify a row count. "
            "Add LIMIT 50 at the end of the query."
        )

    # ── Assemble the system message ───────────────────────────────────────────
    system_parts: list[str] = []

    # 1. Role and output format
    system_parts.append(
        "You are a MySQL SQL expert. "
        "Your only job is to write a single SELECT SQL statement. "
        "Return ONLY the SQL — no explanation, no markdown, no extra text."
    )
    system_parts.append("")

    # 2. Safety rules
    system_parts.append(_SAFETY_RULES)
    system_parts.append("")

    # 3. Query construction rules
    system_parts.append(_QUERY_RULES)
    system_parts.append("")

    system_parts.append(_PCSOFT_RELATIONSHIP_GUIDANCE)
    system_parts.append("")

    # 4. LIMIT instruction (dynamic)
    system_parts.append(f"LIMIT rule: {limit_instruction}")
    system_parts.append("")

    # 5. Business glossary (dynamic from business_glossary.json if available)
    dynamic_glossary = _get_relevant_glossary_terms(user_question, knowledge_base)
    system_parts.append(dynamic_glossary)
    system_parts.append("")

    system_parts.extend(_build_plan_section(query_plan, selected_tables))

    # 5b. Generic semantic type guidance (always included, works for any database)
    system_parts.append(
        "Semantic type guidance:\n"
        "  - For sales/revenue/amount questions: prefer columns with semantic_type=money. Use SUM().\n"
        "  - For date/month/time questions: prefer columns with semantic_type=date. Use DATE_FORMAT() for monthly grouping.\n"
        "  - For customer questions: prefer columns with semantic_type=customer.\n"
        "  - For vendor questions: prefer columns with semantic_type=vendor.\n"
        "  - For warehouse or stock questions: prefer semantic_type=warehouse and semantic_type=quantity.\n"
        "  - For invoice or ERP document filters: prefer semantic_type=document_number or semantic_type=reference_number.\n"
        "  - For status/state filters: prefer columns with semantic_type=status.\n"
        "  - For quantity/count questions: prefer columns with semantic_type=quantity. Use SUM() or COUNT()."
    )
    system_parts.append("")

    # 6. Worked examples — injected dynamically based on the question topic.
    #    Each example gives the model a concrete reference so it copies the
    #    correct tables, columns, and aliases instead of hallucinating them.
    for example_fn in (_monthly_sales_example, _paid_orders_example):
        example = example_fn(user_question, knowledge_base)
        if example:
            system_parts.append(example)
            system_parts.append("")

    # 7. Full schema context
    system_parts.extend(_build_schema_section(knowledge_base))

    system_content = "\n".join(system_parts).strip()

    # ── Debug mode ────────────────────────────────────────────────────────────
    # Set DEBUG_PROMPT=true in .env to print the prompt before sending it.
    # This helps diagnose why a query came out wrong without changing any logic.
    if os.getenv("DEBUG_PROMPT", "").strip().lower() == "true":
        print("\n" + "=" * 60)
        print("  DEBUG: Full system prompt being sent to AI")
        print("=" * 60)
        print(system_content)
        print("=" * 60 + "\n")

    return [
        {"role": "system", "content": system_content},
        {"role": "user",   "content": user_question},
    ]
