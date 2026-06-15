"""
Deterministic ERP SQL generation using structured query plans.
"""

from __future__ import annotations

import re


def _normalize(text: str) -> str:
    return str(text or "").strip().lower()


def _columns(table_data: dict) -> list[dict]:
    return list(table_data.get("columns", []))


def _find_tables_by_module(knowledge_base: dict, module_name: str) -> list[str]:
    return [
        table_name
        for table_name, table_data in knowledge_base.items()
        if str(table_data.get("module", "")).lower() == module_name.lower()
    ]


def _find_column(
    table_data: dict,
    *,
    patterns: tuple[str, ...] = (),
    semantic_types: tuple[str, ...] = (),
) -> str | None:
    for pattern in patterns:
        for column in _columns(table_data):
            name = str(column.get("name", "")).lower()
            if pattern in name:
                return str(column.get("name", ""))

    for semantic_type in semantic_types:
        for column in _columns(table_data):
            if str(column.get("semantic_type", "")).lower() == semantic_type:
                return str(column.get("name", ""))

    return None


def _find_status_column(table_data: dict) -> str | None:
    return _find_column(table_data, patterns=("status", "state"), semantic_types=("status",))


def _find_date_column(table_data: dict) -> str | None:
    return _find_column(
        table_data,
        patterns=("date", "created_at", "posted_at", "invoice_date", "order_date", "payment_date"),
        semantic_types=("date",),
    )


def _find_money_column(table_data: dict, preferred_patterns: tuple[str, ...] = ()) -> str | None:
    patterns = preferred_patterns or (
        "outstanding_amount",
        "amount_due",
        "pending_amount",
        "tax_amount",
        "gst_amount",
        "paid_amount",
        "final_amount",
        "total_amount",
        "amount",
        "balance",
        "salary",
    )
    return _find_column(table_data, patterns=patterns, semantic_types=("money", "tax"))


def _find_quantity_column(table_data: dict, preferred_patterns: tuple[str, ...] = ()) -> str | None:
    patterns = preferred_patterns or (
        "available_stock",
        "on_hand",
        "stock_qty",
        "quantity",
        "qty",
    )
    return _find_column(table_data, patterns=patterns, semantic_types=("quantity",))


def _find_name_column(table_data: dict, semantic_type: str, fallback_patterns: tuple[str, ...]) -> str | None:
    explicit = _find_column(table_data, patterns=fallback_patterns)
    if explicit:
        return explicit

    for column in _columns(table_data):
        column_name = str(column.get("name", "")).lower()
        if str(column.get("semantic_type", "")).lower() != semantic_type:
            continue
        if any(token in column_name for token in ("name", "code", "number", "no")):
            return str(column.get("name", ""))

    return None


def _find_join(knowledge_base: dict, left_table: str, right_table: str) -> tuple[str, str] | None:
    left_relationships = knowledge_base.get(left_table, {}).get("relationships", [])
    for relationship in left_relationships:
        if relationship.get("direction") == "outgoing" and relationship.get("to_table") == right_table:
            return relationship["from_column"], relationship["to_column"]
        if relationship.get("direction") == "incoming" and relationship.get("from_table") == right_table:
            return relationship["to_column"], relationship["from_column"]

    right_relationships = knowledge_base.get(right_table, {}).get("relationships", [])
    for relationship in right_relationships:
        if relationship.get("direction") == "outgoing" and relationship.get("to_table") == left_table:
            return relationship["to_column"], relationship["from_column"]
        if relationship.get("direction") == "incoming" and relationship.get("from_table") == left_table:
            return relationship["from_column"], relationship["to_column"]

    return None


def _current_month_filter(date_column: str, plan: dict) -> str:
    date_range = plan.get("date_range") or {}
    if date_range.get("label") == "this_month" and date_range.get("start"):
        return f"WHERE {date_column} >= '{date_range['start']}'"
    if date_range.get("label") == "this_year" and date_range.get("start"):
        return f"WHERE {date_column} >= '{date_range['start']}'"
    return ""


def _limit_clause(plan: dict, default_limit: int = 50) -> str:
    limit = plan.get("limit") or default_limit
    return f"LIMIT {int(limit)}"


def _sales_total_sql(question: str, knowledge_base: dict, plan: dict) -> str | None:
    sales_tables = _find_tables_by_module(knowledge_base, "sales")
    for table_name in sales_tables + list(knowledge_base.keys()):
        table_data = knowledge_base.get(table_name, {})
        amount_column = _find_money_column(table_data, ("final_amount", "total_amount", "amount"))
        date_column = _find_date_column(table_data)
        if not amount_column:
            continue
        where_clause = _current_month_filter(date_column, plan) if date_column else ""
        sql_parts = [f"SELECT SUM({amount_column}) AS total_sales", f"FROM {table_name}"]
        if where_clause:
            sql_parts.append(where_clause)
        return " ".join(sql_parts) + ";"
    return None


def _purchase_by_vendor_sql(knowledge_base: dict, plan: dict) -> str | None:
    purchase_table = None
    vendor_table = None

    for table_name, table_data in knowledge_base.items():
        table_name_lower = table_name.lower()
        if (
            purchase_table is None
            and (
                "purchase" in table_name_lower
                or table_data.get("module") == "purchase"
            )
            and _find_money_column(table_data, ("purchase_amount", "final_amount", "total_amount", "amount"))
        ):
            purchase_table = table_name
        if vendor_table is None and _find_name_column(table_data, "vendor", ("vendor_name", "supplier_name", "name")):
            vendor_table = table_name

    if not purchase_table:
        return None

    purchase_data = knowledge_base[purchase_table]
    amount_column = _find_money_column(purchase_data, ("purchase_amount", "final_amount", "total_amount", "amount"))
    if not amount_column:
        return None

    if vendor_table and vendor_table != purchase_table:
        join_columns = _find_join(knowledge_base, purchase_table, vendor_table)
        vendor_name = _find_name_column(knowledge_base[vendor_table], "vendor", ("vendor_name", "supplier_name", "name"))
        if join_columns and vendor_name:
            left_column, right_column = join_columns
            return (
                f"SELECT v.{vendor_name} AS vendor_name, SUM(p.{amount_column}) AS total_purchase "
                f"FROM {purchase_table} p "
                f"JOIN {vendor_table} v ON p.{left_column} = v.{right_column} "
                f"GROUP BY v.{vendor_name} ORDER BY total_purchase DESC {_limit_clause(plan)};"
            )

    vendor_name = _find_name_column(purchase_data, "vendor", ("vendor_name", "supplier_name"))
    vendor_id = _find_column(purchase_data, patterns=("vendor_id", "supplier_id"), semantic_types=("vendor",))
    if vendor_name:
        group_column = vendor_name
    elif vendor_id:
        group_column = vendor_id
    else:
        return None

    return (
        f"SELECT {group_column}, SUM({amount_column}) AS total_purchase "
        f"FROM {purchase_table} GROUP BY {group_column} ORDER BY total_purchase DESC {_limit_clause(plan)};"
    )


def _stock_by_warehouse_sql(knowledge_base: dict, plan: dict) -> str | None:
    stock_table = None
    warehouse_table = None

    for table_name, table_data in knowledge_base.items():
        if (
            stock_table is None
            and (
                "inventory" in table_name.lower()
                or "stock" in table_name.lower()
                or table_data.get("module") == "inventory"
            )
            and _find_quantity_column(table_data)
        ):
            stock_table = table_name
        if warehouse_table is None and _find_name_column(table_data, "warehouse", ("warehouse_name", "name")):
            warehouse_table = table_name

    if not stock_table:
        return None

    stock_data = knowledge_base[stock_table]
    quantity_column = _find_quantity_column(stock_data)
    if not quantity_column:
        return None

    if warehouse_table and warehouse_table != stock_table:
        join_columns = _find_join(knowledge_base, stock_table, warehouse_table)
        warehouse_name = _find_name_column(knowledge_base[warehouse_table], "warehouse", ("warehouse_name", "name"))
        if join_columns and warehouse_name:
            left_column, right_column = join_columns
            return (
                f"SELECT w.{warehouse_name} AS warehouse_name, SUM(s.{quantity_column}) AS current_stock "
                f"FROM {stock_table} s "
                f"JOIN {warehouse_table} w ON s.{left_column} = w.{right_column} "
                f"GROUP BY w.{warehouse_name} ORDER BY warehouse_name {_limit_clause(plan)};"
            )

    warehouse_column = _find_column(stock_data, patterns=("warehouse",), semantic_types=("warehouse",))
    if not warehouse_column:
        return None

    return (
        f"SELECT {warehouse_column}, SUM({quantity_column}) AS current_stock "
        f"FROM {stock_table} GROUP BY {warehouse_column} ORDER BY {warehouse_column} {_limit_clause(plan)};"
    )


def _low_stock_sql(knowledge_base: dict, plan: dict) -> str | None:
    stock_table = next(iter(_find_tables_by_module(knowledge_base, "inventory")), None)
    if not stock_table:
        return None

    stock_data = knowledge_base[stock_table]
    quantity_column = _find_quantity_column(stock_data)
    reorder_column = _find_column(stock_data, patterns=("reorder_level", "minimum_stock", "min_stock"), semantic_types=("quantity",))
    if not quantity_column:
        return None

    item_table = None
    for table_name, table_data in knowledge_base.items():
        if _find_name_column(table_data, "item_product", ("item_name", "product_name", "material_name", "name")):
            item_table = table_name
            break

    if item_table and item_table != stock_table:
        join_columns = _find_join(knowledge_base, stock_table, item_table)
        item_name = _find_name_column(knowledge_base[item_table], "item_product", ("item_name", "product_name", "material_name", "name"))
        if join_columns and item_name:
            left_column, right_column = join_columns
            where_clause = f"WHERE s.{quantity_column} <= s.{reorder_column}" if reorder_column else f"WHERE s.{quantity_column} <= 10"
            return (
                f"SELECT i.{item_name} AS item_name, s.{quantity_column} AS current_stock"
                f"{', s.' + reorder_column + ' AS reorder_level' if reorder_column else ''} "
                f"FROM {stock_table} s "
                f"JOIN {item_table} i ON s.{left_column} = i.{right_column} "
                f"{where_clause} ORDER BY s.{quantity_column} ASC {_limit_clause(plan)};"
            )

    item_column = _find_column(stock_data, patterns=("item", "product", "material"), semantic_types=("item_product",))
    where_clause = f"WHERE {quantity_column} <= {reorder_column}" if reorder_column else f"WHERE {quantity_column} <= 10"
    select_column = item_column or quantity_column
    return (
        f"SELECT {select_column}, {quantity_column} AS current_stock "
        f"FROM {stock_table} {where_clause} ORDER BY {quantity_column} ASC {_limit_clause(plan)};"
    )


def _unpaid_invoices_sql(knowledge_base: dict, plan: dict) -> str | None:
    invoice_table = None
    for table_name, table_data in knowledge_base.items():
        if "invoice" in table_name.lower() or table_data.get("module") in {"sales", "purchase", "finance"}:
            if _find_money_column(table_data, ("amount_due", "outstanding_amount", "total_amount", "final_amount")):
                invoice_table = table_name
                break
    if not invoice_table:
        return None

    invoice_data = knowledge_base[invoice_table]
    status_column = _find_status_column(invoice_data)
    due_column = _find_money_column(invoice_data, ("amount_due", "outstanding_amount", "balance", "pending_amount"))
    if status_column:
        return (
            f"SELECT * FROM {invoice_table} WHERE {status_column} IN ('Pending', 'Unpaid', 'Outstanding') "
            f"{_limit_clause(plan)};"
        )
    if due_column:
        return f"SELECT * FROM {invoice_table} WHERE {due_column} > 0 {_limit_clause(plan)};"
    return None


def _outstanding_by_customer_sql(knowledge_base: dict, plan: dict) -> str | None:
    fact_table = None
    customer_table = None
    for table_name, table_data in knowledge_base.items():
        if fact_table is None and _find_money_column(table_data, ("outstanding_amount", "amount_due", "balance")):
            fact_table = table_name
        if customer_table is None and _find_name_column(table_data, "customer", ("customer_name", "name")):
            customer_table = table_name

    if not fact_table:
        return None

    fact_data = knowledge_base[fact_table]
    amount_column = _find_money_column(fact_data, ("outstanding_amount", "amount_due", "balance", "pending_amount"))
    if not amount_column:
        return None

    if customer_table and customer_table != fact_table:
        join_columns = _find_join(knowledge_base, fact_table, customer_table)
        customer_name = _find_name_column(knowledge_base[customer_table], "customer", ("customer_name", "name"))
        if join_columns and customer_name:
            left_column, right_column = join_columns
            return (
                f"SELECT c.{customer_name} AS customer_name, SUM(f.{amount_column}) AS outstanding_balance "
                f"FROM {fact_table} f "
                f"JOIN {customer_table} c ON f.{left_column} = c.{right_column} "
                f"GROUP BY c.{customer_name} ORDER BY outstanding_balance DESC {_limit_clause(plan)};"
            )

    customer_column = _find_column(fact_data, patterns=("customer",), semantic_types=("customer",))
    if not customer_column:
        return None

    return (
        f"SELECT {customer_column}, SUM({amount_column}) AS outstanding_balance "
        f"FROM {fact_table} GROUP BY {customer_column} ORDER BY outstanding_balance DESC {_limit_clause(plan)};"
    )


def _pending_vendor_payments_sql(knowledge_base: dict, plan: dict) -> str | None:
    payment_table = None
    vendor_table = None
    for table_name, table_data in knowledge_base.items():
        if payment_table is None and (_find_money_column(table_data, ("paid_amount", "payment_amount", "amount_due")) or "payment" in table_name.lower()):
            payment_table = table_name
        if vendor_table is None and _find_name_column(table_data, "vendor", ("vendor_name", "supplier_name", "name")):
            vendor_table = table_name

    if not payment_table:
        return None

    payment_data = knowledge_base[payment_table]
    amount_column = _find_money_column(payment_data, ("amount_due", "paid_amount", "payment_amount", "amount"))
    status_column = _find_status_column(payment_data)
    if not amount_column:
        return None

    where_clause = ""
    if status_column:
        where_clause = f"WHERE p.{status_column} IN ('Pending', 'Unpaid', 'Outstanding')"

    if vendor_table and vendor_table != payment_table:
        join_columns = _find_join(knowledge_base, payment_table, vendor_table)
        vendor_name = _find_name_column(knowledge_base[vendor_table], "vendor", ("vendor_name", "supplier_name", "name"))
        if join_columns and vendor_name:
            left_column, right_column = join_columns
            return (
                f"SELECT v.{vendor_name} AS vendor_name, SUM(p.{amount_column}) AS pending_amount "
                f"FROM {payment_table} p "
                f"JOIN {vendor_table} v ON p.{left_column} = v.{right_column} "
                f"{where_clause} GROUP BY v.{vendor_name} ORDER BY pending_amount DESC {_limit_clause(plan)};"
            )

    vendor_column = _find_column(payment_data, patterns=("vendor", "supplier"), semantic_types=("vendor",))
    if not vendor_column:
        return None
    prefix = f"{where_clause[6:]} AND " if where_clause.startswith("WHERE ") else ""
    condition = f"WHERE {prefix}" if prefix else ""
    return (
        f"SELECT {vendor_column}, SUM({amount_column}) AS pending_amount "
        f"FROM {payment_table} {condition}{vendor_column} IS NOT NULL "
        f"GROUP BY {vendor_column} ORDER BY pending_amount DESC {_limit_clause(plan)};"
    )


def _salary_by_department_sql(knowledge_base: dict, plan: dict) -> str | None:
    employee_table = None
    for table_name, table_data in knowledge_base.items():
        if employee_table is None and (
            table_data.get("module") == "HR/payroll" or _find_money_column(table_data, ("salary", "gross_salary", "net_salary"))
        ):
            employee_table = table_name
    if not employee_table:
        return None

    employee_data = knowledge_base[employee_table]
    salary_column = _find_money_column(employee_data, ("salary", "gross_salary", "net_salary", "pay_amount"))
    department_column = _find_column(employee_data, patterns=("department",), semantic_types=("employee",))
    if not salary_column or not department_column:
        return None

    return (
        f"SELECT {department_column}, SUM({salary_column}) AS total_salary "
        f"FROM {employee_table} GROUP BY {department_column} "
        f"ORDER BY total_salary DESC {_limit_clause(plan)};"
    )


def _tax_by_month_sql(knowledge_base: dict, plan: dict) -> str | None:
    fact_table = None
    for table_name, table_data in knowledge_base.items():
        if _find_money_column(table_data, ("tax_amount", "gst_amount", "vat_amount")) and _find_date_column(table_data):
            fact_table = table_name
            break
    if not fact_table:
        return None

    fact_data = knowledge_base[fact_table]
    tax_column = _find_money_column(fact_data, ("tax_amount", "gst_amount", "vat_amount"))
    date_column = _find_date_column(fact_data)
    if not tax_column or not date_column:
        return None

    return (
        f"SELECT DATE_FORMAT({date_column}, '%Y-%m') AS month, "
        f"SUM({tax_column}) AS total_tax "
        f"FROM {fact_table} "
        f"GROUP BY DATE_FORMAT({date_column}, '%Y-%m') "
        f"ORDER BY month {_limit_clause(plan)};"
    )


def _production_bom_sql(question: str, knowledge_base: dict, plan: dict) -> str | None:
    normalized = _normalize(question)
    bom_table = None
    production_table = None
    material_table = None

    for table_name, table_data in knowledge_base.items():
        if (
            bom_table is None
            and (
                table_name.lower() in {"boms", "bom", "bill_of_materials"}
                or _find_name_column(table_data, "item_product", ("bom_name", "bom_no", "name"))
            )
        ):
            bom_table = table_name
        if (
            production_table is None
            and (
                "production" in table_name.lower()
                or _find_quantity_column(table_data, ("production_qty", "produced_qty"))
            )
        ):
            production_table = table_name
        if material_table is None and _find_name_column(table_data, "item_product", ("material_name", "item_name", "product_name", "name")):
            material_table = table_name

    if "material" in normalized and bom_table:
        bom_data = knowledge_base[bom_table]
        material_column = _find_column(bom_data, patterns=("material", "item", "product"), semantic_types=("item_product",))
        quantity_column = _find_quantity_column(bom_data, ("required_qty", "quantity", "qty"))
        if material_table and material_table != bom_table:
            join_columns = _find_join(knowledge_base, bom_table, material_table)
            material_name = _find_name_column(knowledge_base[material_table], "item_product", ("material_name", "item_name", "product_name", "name"))
            if join_columns and material_name:
                left_column, right_column = join_columns
                return (
                    f"SELECT m.{material_name} AS material_name, b.{quantity_column} AS required_quantity "
                    f"FROM {bom_table} b "
                    f"JOIN {material_table} m ON b.{left_column} = m.{right_column} "
                    f"{_limit_clause(plan)};"
                )
        if material_column and quantity_column:
            return f"SELECT {material_column}, {quantity_column} AS required_quantity FROM {bom_table} {_limit_clause(plan)};"

    if production_table and bom_table:
        production_data = knowledge_base[production_table]
        quantity_column = _find_quantity_column(production_data, ("production_qty", "produced_qty", "quantity", "qty"))
        if not quantity_column:
            return None
        join_columns = _find_join(knowledge_base, production_table, bom_table)
        bom_name = _find_name_column(knowledge_base[bom_table], "item_product", ("bom_name", "bom_no", "name"))
        if join_columns and bom_name:
            left_column, right_column = join_columns
            return (
                f"SELECT b.{bom_name} AS bom_name, SUM(p.{quantity_column}) AS produced_quantity "
                f"FROM {production_table} p "
                f"JOIN {bom_table} b ON p.{left_column} = b.{right_column} "
                f"GROUP BY b.{bom_name} ORDER BY produced_quantity DESC {_limit_clause(plan)};"
            )

    return None


def generate_erp_sql(question: str, knowledge_base: dict, query_plan: dict | None = None) -> str | None:
    """
    Generate deterministic SQL for common ERP-style questions.
    """
    if not knowledge_base or not query_plan:
        return None

    normalized = _normalize(question)
    intent = query_plan.get("intent")
    metric = query_plan.get("metric")
    dimension = query_plan.get("dimension")

    if metric == "sales" and intent == "total":
        return _sales_total_sql(question, knowledge_base, query_plan)
    if metric == "purchase" and dimension == "vendor":
        return _purchase_by_vendor_sql(knowledge_base, query_plan)
    if metric == "stock" and dimension == "warehouse":
        return _stock_by_warehouse_sql(knowledge_base, query_plan)
    if intent == "low_stock":
        return _low_stock_sql(knowledge_base, query_plan)
    if "invoice" in normalized and intent == "pending_outstanding":
        return _unpaid_invoices_sql(knowledge_base, query_plan)
    if dimension == "customer" and metric == "balance":
        return _outstanding_by_customer_sql(knowledge_base, query_plan)
    if dimension == "vendor" and metric == "payment":
        return _pending_vendor_payments_sql(knowledge_base, query_plan)
    if metric == "salary" and dimension == "department":
        return _salary_by_department_sql(knowledge_base, query_plan)
    if metric == "tax" and intent == "trend":
        return _tax_by_month_sql(knowledge_base, query_plan)
    if metric == "production" or "bom" in normalized:
        return _production_bom_sql(question, knowledge_base, query_plan)

    return None
