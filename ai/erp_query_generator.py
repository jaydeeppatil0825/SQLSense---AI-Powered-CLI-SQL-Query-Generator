"""
Deterministic ERP SQL generation using structured query plans.
"""

from __future__ import annotations

from collections import deque
import re


def _normalize(text: str) -> str:
    return str(text or "").strip().lower()


def _columns(table_data: dict) -> list[dict]:
    return list(table_data.get("columns", []))


def _column_names(table_data: dict) -> list[str]:
    return [str(column.get("name", "")) for column in _columns(table_data)]


def _find_tables_by_module(knowledge_base: dict, module_name: str) -> list[str]:
    return [
        table_name
        for table_name, table_data in knowledge_base.items()
        if str(table_data.get("module", "")).lower() == module_name.lower()
    ]


def _find_column(
    table_data: dict,
    * ,
    patterns: tuple[str, ...] = (),
    semantic_types: tuple[str, ...] = (),
    exclude_patterns: tuple[str, ...] = (),
) -> str | None:
    for pattern in patterns:
        for column in _columns(table_data):
            name = str(column.get("name", "")).lower()
            if exclude_patterns and any(exclude in name for exclude in exclude_patterns):
                continue
            if pattern in name:
                return str(column.get("name", ""))

    for semantic_type in semantic_types:
        for column in _columns(table_data):
            name = str(column.get("name", "")).lower()
            if exclude_patterns and any(exclude in name for exclude in exclude_patterns):
                continue
            if str(column.get("semantic_type", "")).lower() == semantic_type:
                return str(column.get("name", ""))

    return None


def _find_matching_columns(
    table_data: dict,
    *,
    patterns: tuple[str, ...] = (),
    semantic_types: tuple[str, ...] = (),
    exclude_patterns: tuple[str, ...] = (),
) -> list[str]:
    matches: list[str] = []
    for column in _columns(table_data):
        name = str(column.get("name", ""))
        lower_name = name.lower()
        semantic_type = str(column.get("semantic_type", "")).lower()
        if exclude_patterns and any(exclude in lower_name for exclude in exclude_patterns):
            continue
        if any(pattern in lower_name for pattern in patterns) or semantic_type in semantic_types:
            if name not in matches:
                matches.append(name)
    return matches


def _find_status_column(table_data: dict) -> str | None:
    return _find_column(table_data, patterns=("status", "state"), semantic_types=("status",))


def _find_date_column(table_data: dict, preferred_patterns: tuple[str, ...] = ()) -> str | None:
    patterns = preferred_patterns or (
        "invoice_date",
        "order_date",
        "purchase_order_date",
        "purchase_date",
        "payment_date",
        "last_updated",
        "joining_date",
        "date",
        "created_at",
        "posted_at",
    )
    return _find_column(table_data, patterns=patterns, semantic_types=("date",))


def _find_quantity_column(table_data: dict, preferred_patterns: tuple[str, ...] = ()) -> str | None:
    patterns = preferred_patterns or (
        "quantity_on_hand",
        "available_stock",
        "stock_qty",
        "quantity",
        "qty",
        "produced_qty",
        "production_qty",
    )
    return _find_column(table_data, patterns=patterns, semantic_types=("quantity",))


def _find_sales_amount_column(table_data: dict) -> str | None:
    return _find_column(
        table_data,
        patterns=("invoice_amount", "final_amount", "total_amount", "net_amount", "line_total", "amount"),
        semantic_types=("money",),
        exclude_patterns=("discount", "tax", "cost", "paid", "salary", "balance", "due"),
    )


def _find_purchase_amount_column(table_data: dict) -> str | None:
    return _find_column(
        table_data,
        patterns=("amount_due", "total_amount", "line_total", "unit_cost", "amount"),
        semantic_types=("money",),
        exclude_patterns=("tax", "discount", "salary"),
    )


def _find_tax_column(table_data: dict) -> str | None:
    return _find_column(table_data, patterns=("tax_amount", "gst_amount", "vat_amount"), semantic_types=("tax",))


def _find_payment_amount_column(table_data: dict) -> str | None:
    return _find_column(
        table_data,
        patterns=("amount_due", "payment_amount", "paid_amount", "amount"),
        semantic_types=("money",),
        exclude_patterns=("tax", "discount", "salary", "cost"),
    )


def _find_outstanding_amount_column(table_data: dict) -> str | None:
    return _find_column(
        table_data,
        patterns=("outstanding_amount", "amount_due", "pending_amount", "balance"),
        semantic_types=("account",),
        exclude_patterns=("tax", "discount", "salary", "cost"),
    )


def _find_reorder_level_column(table_data: dict) -> str | None:
    return _find_column(table_data, patterns=("reorder_level", "minimum_stock", "min_stock"))


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


def _find_primary_key(table_data: dict) -> str | None:
    primary_keys = table_data.get("primary_keys", [])
    if primary_keys:
        return str(primary_keys[0])
    return _find_column(table_data, patterns=("id",))


def _table_score(table_name: str, table_data: dict, *, module_names: tuple[str, ...] = (), name_tokens: tuple[str, ...] = ()) -> int:
    score = 0
    table_name_lower = table_name.lower()
    module_name = str(table_data.get("module", "")).lower()
    if module_names and module_name in {name.lower() for name in module_names}:
        score += 4
    for token in name_tokens:
        if token in table_name_lower:
            score += 3
    return score


def _find_best_table(
    knowledge_base: dict,
    *,
    module_names: tuple[str, ...] = (),
    name_tokens: tuple[str, ...] = (),
    required,
) -> str | None:
    best_table = None
    best_score = -1
    for table_name, table_data in knowledge_base.items():
        if not required(table_data):
            continue
        score = _table_score(table_name, table_data, module_names=module_names, name_tokens=name_tokens)
        if score > best_score:
            best_table = table_name
            best_score = score
    return best_table


def _alias_for_table(table_name: str, used_aliases: set[str], preferred: str | None = None) -> str:
    if preferred and preferred not in used_aliases:
        used_aliases.add(preferred)
        return preferred

    tokens = [token[0] for token in table_name.split("_") if token]
    base = "".join(tokens[:3]) or table_name[0].lower()
    alias = base
    suffix = 2
    while alias in used_aliases:
        alias = f"{base}{suffix}"
        suffix += 1
    used_aliases.add(alias)
    return alias


def _relationship_edges(knowledge_base: dict, table_name: str) -> list[tuple[str, str, str]]:
    edges: list[tuple[str, str, str]] = []
    seen = set()
    for relationship in knowledge_base.get(table_name, {}).get("relationships", []):
        if relationship.get("direction") == "incoming":
            neighbor_table = relationship.get("from_table")
            left_column = relationship.get("to_column")
            right_column = relationship.get("from_column")
        else:
            neighbor_table = relationship.get("to_table")
            left_column = relationship.get("from_column")
            right_column = relationship.get("to_column")
        if not neighbor_table or not left_column or not right_column:
            continue
        signature = (neighbor_table, left_column, right_column)
        if signature in seen:
            continue
        seen.add(signature)
        edges.append(signature)
    return edges


def _find_path(knowledge_base: dict, start_table: str, target_table: str, max_depth: int = 4) -> list[tuple[str, str, str, str]] | None:
    if start_table == target_table:
        return []

    queue = deque([(start_table, [])])
    visited = {start_table}
    while queue:
        current_table, path = queue.popleft()
        if len(path) >= max_depth:
            continue
        for neighbor_table, left_column, right_column in _relationship_edges(knowledge_base, current_table):
            edge = (current_table, neighbor_table, left_column, right_column)
            if neighbor_table == target_table:
                return path + [edge]
            if neighbor_table in visited:
                continue
            visited.add(neighbor_table)
            queue.append((neighbor_table, path + [edge]))
    return None


def _build_path_joins(
    knowledge_base: dict,
    start_table: str,
    target_table: str,
    alias_map: dict[str, str],
    used_aliases: set[str],
) -> tuple[str, dict[str, str]] | None:
    path = _find_path(knowledge_base, start_table, target_table)
    if path is None:
        return None

    join_clauses = []
    for left_table, right_table, left_column, right_column in path:
        left_alias = alias_map[left_table]
        right_alias = alias_map.get(right_table)
        if right_alias is None:
            right_alias = _alias_for_table(right_table, used_aliases)
            alias_map[right_table] = right_alias
        join_clauses.append(
            f"JOIN {right_table} {right_alias} ON {left_alias}.{left_column} = {right_alias}.{right_column}"
        )
    return " ".join(join_clauses), alias_map


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


def _date_condition(date_column: str, plan: dict, alias: str | None = None) -> str:
    date_range = plan.get("date_range") or {}
    if not date_range or not date_range.get("start"):
        return ""

    qualified_date = f"{alias}.{date_column}" if alias else date_column
    conditions = [f"{qualified_date} >= '{date_range['start']}'"]
    if date_range.get("end_exclusive"):
        conditions.append(f"{qualified_date} < '{date_range['end_exclusive']}'")
    return " AND ".join(conditions)


def _limit_clause(plan: dict, default_limit: int = 50) -> str:
    limit = plan.get("limit") or default_limit
    return f"LIMIT {int(limit)}"


def _status_values_for_question(question: str) -> list[str]:
    normalized = _normalize(question)
    if "overdue" in normalized:
        return ["Overdue"]
    if "unpaid" in normalized:
        return ["Unpaid", "Overdue", "Outstanding", "Pending", "Partially Paid"]
    if "outstanding" in normalized or "pending" in normalized or "due" in normalized:
        return ["Pending", "Unpaid", "Outstanding", "Overdue", "Partially Paid"]
    return ["Pending"]


def _select_invoice_columns(table_data: dict, alias: str | None = None) -> str:
    selected_columns = []
    primary_key = _find_primary_key(table_data)
    if primary_key:
        selected_columns.append(primary_key)

    for column_name in _find_matching_columns(
        table_data,
        patterns=(
            "invoice_no",
            "invoice_number",
            "sales_order_id",
            "invoice_date",
            "due_date",
            "status",
            "invoice_amount",
            "net_amount",
            "outstanding_amount",
            "amount_due",
            "tax_amount",
        ),
        semantic_types=("date", "status", "money", "tax"),
    ):
        if column_name not in selected_columns:
            selected_columns.append(column_name)

    if not selected_columns:
        selected_columns = _column_names(table_data)[:6]

    if alias:
        return ", ".join(f"{alias}.{column_name}" for column_name in selected_columns[:8])
    return ", ".join(selected_columns[:8])


def _sales_total_sql(question: str, knowledge_base: dict, plan: dict) -> str | None:
    sales_candidates = []
    for table_name, table_data in knowledge_base.items():
        amount_column = _find_sales_amount_column(table_data)
        if not amount_column:
            continue
        score = _table_score(table_name, table_data, module_names=("sales",), name_tokens=("invoice", "sales_order", "sales"))
        date_column = _find_date_column(table_data)
        if date_column:
            score += 3
        if "invoice" in table_name.lower():
            score += 3
        if "item" in table_name.lower():
            score -= 4
        if amount_column in {"invoice_amount", "final_amount", "total_amount", "net_amount"}:
            score += 2
        if amount_column == "line_total":
            score -= 2
        sales_candidates.append((score, table_name))

    if not sales_candidates:
        return None
    sales_candidates.sort(reverse=True)
    sales_table = sales_candidates[0][1]
    if not sales_table:
        return None

    sales_data = knowledge_base[sales_table]
    amount_column = _find_sales_amount_column(sales_data)
    date_column = _find_date_column(sales_data)
    if not amount_column:
        return None

    where_conditions = []
    date_condition = _date_condition(date_column, plan) if date_column else ""
    if date_condition:
        where_conditions.append(date_condition)

    sql_parts = [f"SELECT SUM({amount_column}) AS total_sales", f"FROM {sales_table}"]
    if where_conditions:
        sql_parts.append(f"WHERE {' AND '.join(where_conditions)}")
    return " ".join(sql_parts) + ";"


def _purchase_by_vendor_sql(knowledge_base: dict, plan: dict) -> str | None:
    purchase_table = _find_best_table(
        knowledge_base,
        module_names=("purchase",),
        name_tokens=("purchase", "vendor", "supplier"),
        required=lambda table_data: _find_purchase_amount_column(table_data) is not None,
    )
    vendor_table = _find_best_table(
        knowledge_base,
        name_tokens=("vendor", "supplier"),
        required=lambda table_data: _find_name_column(table_data, "vendor", ("vendor_name", "supplier_name", "name")) is not None,
    )
    if not purchase_table:
        return None

    purchase_data = knowledge_base[purchase_table]
    amount_column = _find_purchase_amount_column(purchase_data)
    if not amount_column:
        return None

    used_aliases: set[str] = set()
    purchase_alias = _alias_for_table(purchase_table, used_aliases, preferred="p")
    where_conditions = []
    purchase_date = _find_date_column(purchase_data)
    date_condition = _date_condition(purchase_date, plan, purchase_alias) if purchase_date else ""
    if date_condition:
        where_conditions.append(date_condition)

    if vendor_table and vendor_table != purchase_table:
        join_columns = _find_join(knowledge_base, purchase_table, vendor_table)
        vendor_name = _find_name_column(knowledge_base[vendor_table], "vendor", ("vendor_name", "supplier_name", "name"))
        if join_columns and vendor_name:
            vendor_alias = _alias_for_table(vendor_table, used_aliases, preferred="v")
            left_column, right_column = join_columns
            where_clause = f" WHERE {' AND '.join(where_conditions)}" if where_conditions else ""
            return (
                f"SELECT {vendor_alias}.{vendor_name} AS vendor_name, SUM({purchase_alias}.{amount_column}) AS total_purchase "
                f"FROM {purchase_table} {purchase_alias} "
                f"JOIN {vendor_table} {vendor_alias} ON {purchase_alias}.{left_column} = {vendor_alias}.{right_column}"
                f"{where_clause} "
                f"GROUP BY {vendor_alias}.{vendor_name} ORDER BY total_purchase DESC {_limit_clause(plan)};"
            )

    vendor_column = _find_column(purchase_data, patterns=("vendor_id", "supplier_id", "vendor_name", "supplier_name"), semantic_types=("vendor",))
    if not vendor_column:
        return None

    where_clause = f" WHERE {' AND '.join(where_conditions)}" if where_conditions else ""
    return (
        f"SELECT {purchase_alias}.{vendor_column}, SUM({purchase_alias}.{amount_column}) AS total_purchase "
        f"FROM {purchase_table} {purchase_alias}{where_clause} "
        f"GROUP BY {purchase_alias}.{vendor_column} ORDER BY total_purchase DESC {_limit_clause(plan)};"
    )


def _stock_by_warehouse_sql(knowledge_base: dict, plan: dict) -> str | None:
    stock_table = _find_best_table(
        knowledge_base,
        module_names=("inventory",),
        name_tokens=("inventory", "stock"),
        required=lambda table_data: _find_quantity_column(table_data) is not None,
    )
    warehouse_table = _find_best_table(
        knowledge_base,
        name_tokens=("warehouse",),
        required=lambda table_data: _find_name_column(table_data, "warehouse", ("warehouse_name", "name")) is not None,
    )
    if not stock_table:
        return None

    stock_data = knowledge_base[stock_table]
    quantity_column = _find_quantity_column(stock_data)
    if not quantity_column:
        return None

    used_aliases: set[str] = set()
    stock_alias = _alias_for_table(stock_table, used_aliases, preferred="s")
    if warehouse_table and warehouse_table != stock_table:
        join_columns = _find_join(knowledge_base, stock_table, warehouse_table)
        warehouse_name = _find_name_column(knowledge_base[warehouse_table], "warehouse", ("warehouse_name", "name"))
        if join_columns and warehouse_name:
            warehouse_alias = _alias_for_table(warehouse_table, used_aliases, preferred="w")
            left_column, right_column = join_columns
            return (
                f"SELECT {warehouse_alias}.{warehouse_name} AS warehouse_name, SUM({stock_alias}.{quantity_column}) AS current_stock "
                f"FROM {stock_table} {stock_alias} "
                f"JOIN {warehouse_table} {warehouse_alias} ON {stock_alias}.{left_column} = {warehouse_alias}.{right_column} "
                f"GROUP BY {warehouse_alias}.{warehouse_name} ORDER BY {warehouse_alias}.{warehouse_name} {_limit_clause(plan)};"
            )

    warehouse_column = _find_column(stock_data, patterns=("warehouse",), semantic_types=("warehouse",))
    if not warehouse_column:
        return None
    return (
        f"SELECT {stock_alias}.{warehouse_column}, SUM({stock_alias}.{quantity_column}) AS current_stock "
        f"FROM {stock_table} {stock_alias} "
        f"GROUP BY {stock_alias}.{warehouse_column} ORDER BY {stock_alias}.{warehouse_column} {_limit_clause(plan)};"
    )


def _low_stock_sql(knowledge_base: dict, plan: dict) -> str | None:
    stock_table = _find_best_table(
        knowledge_base,
        module_names=("inventory",),
        name_tokens=("inventory", "stock", "balance"),
        required=lambda table_data: _find_quantity_column(table_data) is not None,
    )
    item_table = _find_best_table(
        knowledge_base,
        name_tokens=("product", "item", "material"),
        required=lambda table_data: _find_name_column(table_data, "item_product", ("item_name", "product_name", "material_name", "name")) is not None,
    )
    if not stock_table:
        return None

    stock_data = knowledge_base[stock_table]
    quantity_column = _find_quantity_column(stock_data)
    if not quantity_column:
        return None

    stock_reorder_column = _find_reorder_level_column(stock_data)
    used_aliases: set[str] = set()
    stock_alias = _alias_for_table(stock_table, used_aliases, preferred="s")

    if item_table and item_table != stock_table:
        join_columns = _find_join(knowledge_base, stock_table, item_table)
        item_name = _find_name_column(knowledge_base[item_table], "item_product", ("item_name", "product_name", "material_name", "name"))
        item_reorder_column = _find_reorder_level_column(knowledge_base[item_table])
        if join_columns and item_name:
            item_alias = _alias_for_table(item_table, used_aliases, preferred="i")
            left_column, right_column = join_columns
            reorder_expression = None
            if stock_reorder_column:
                reorder_expression = f"{stock_alias}.{stock_reorder_column}"
            elif item_reorder_column:
                reorder_expression = f"{item_alias}.{item_reorder_column}"
            if reorder_expression is None:
                reorder_expression = "10"
            reorder_select = ""
            if stock_reorder_column:
                reorder_select = f", {stock_alias}.{stock_reorder_column} AS reorder_level"
            elif item_reorder_column:
                reorder_select = f", {item_alias}.{item_reorder_column} AS reorder_level"

            return (
                f"SELECT {item_alias}.{item_name} AS item_name, {stock_alias}.{quantity_column} AS current_stock{reorder_select} "
                f"FROM {stock_table} {stock_alias} "
                f"JOIN {item_table} {item_alias} ON {stock_alias}.{left_column} = {item_alias}.{right_column} "
                f"WHERE {stock_alias}.{quantity_column} <= {reorder_expression} "
                f"ORDER BY {stock_alias}.{quantity_column} ASC {_limit_clause(plan)};"
            )

    item_column = _find_column(stock_data, patterns=("item", "product", "material"), semantic_types=("item_product",))
    reorder_expression = f"{stock_alias}.{stock_reorder_column}" if stock_reorder_column else "10"
    select_column = f"{stock_alias}.{item_column}" if item_column else f"{stock_alias}.{quantity_column}"
    return (
        f"SELECT {select_column}, {stock_alias}.{quantity_column} AS current_stock "
        f"FROM {stock_table} {stock_alias} "
        f"WHERE {stock_alias}.{quantity_column} <= {reorder_expression} "
        f"ORDER BY {stock_alias}.{quantity_column} ASC {_limit_clause(plan)};"
    )


def _unpaid_invoices_sql(question: str, knowledge_base: dict, plan: dict) -> str | None:
    invoice_table = _find_best_table(
        knowledge_base,
        module_names=("sales", "finance", "purchase"),
        name_tokens=("invoice",),
        required=lambda table_data: _find_status_column(table_data) is not None,
    )
    if not invoice_table:
        return None

    invoice_data = knowledge_base[invoice_table]
    status_column = _find_status_column(invoice_data)
    if not status_column:
        return None

    used_aliases: set[str] = set()
    invoice_alias = _alias_for_table(invoice_table, used_aliases, preferred="i")
    select_list = _select_invoice_columns(invoice_data, invoice_alias)
    status_values = ", ".join(f"'{value}'" for value in _status_values_for_question(question))
    where_conditions = [f"{invoice_alias}.{status_column} IN ({status_values})"]
    date_column = _find_date_column(invoice_data)
    date_condition = _date_condition(date_column, plan, invoice_alias) if date_column else ""
    if date_condition:
        where_conditions.append(date_condition)

    order_column = _find_column(invoice_data, patterns=("due_date", "invoice_date")) or _find_primary_key(invoice_data)
    order_clause = f" ORDER BY {invoice_alias}.{order_column}" if order_column else ""
    return (
        f"SELECT {select_list} FROM {invoice_table} {invoice_alias} "
        f"WHERE {' AND '.join(where_conditions)}"
        f"{order_clause} {_limit_clause(plan)};"
    )


def _outstanding_by_customer_sql(knowledge_base: dict, plan: dict) -> str | None:
    customer_table = _find_best_table(
        knowledge_base,
        name_tokens=("customer",),
        required=lambda table_data: _find_name_column(table_data, "customer", ("customer_name", "name")) is not None,
    )
    if not customer_table:
        return None

    fact_candidates = []
    for table_name, table_data in knowledge_base.items():
        outstanding_column = _find_outstanding_amount_column(table_data)
        sales_amount_column = _find_sales_amount_column(table_data)
        if not outstanding_column and not sales_amount_column:
            continue
        score = _table_score(table_name, table_data, module_names=("sales", "finance"), name_tokens=("invoice", "receivable", "sales"))
        if outstanding_column:
            score += 6
        if "invoice" in table_name.lower():
            score += 5
        if "order_item" in table_name.lower() or "sales_order" in table_name.lower():
            score -= 3
        if _find_date_column(table_data, ("invoice_date", "due_date")):
            score += 1
        fact_candidates.append((score, table_name))

    if not fact_candidates:
        return None
    fact_candidates.sort(reverse=True)
    fact_table = fact_candidates[0][1]
    if not fact_table:
        return None

    customer_name = _find_name_column(knowledge_base[customer_table], "customer", ("customer_name", "name"))
    if not customer_name:
        return None

    used_aliases: set[str] = set()
    alias_map = {
        fact_table: _alias_for_table(fact_table, used_aliases, preferred="f"),
        customer_table: _alias_for_table(customer_table, used_aliases, preferred="c"),
    }

    path_result = _build_path_joins(knowledge_base, fact_table, customer_table, alias_map, used_aliases)
    if path_result is None:
        return None
    join_clause, alias_map = path_result
    fact_alias = alias_map[fact_table]
    customer_alias = alias_map[customer_table]

    outstanding_column = _find_outstanding_amount_column(knowledge_base[fact_table])
    if outstanding_column:
        return (
            f"SELECT {customer_alias}.{customer_name} AS customer_name, SUM({fact_alias}.{outstanding_column}) AS outstanding_balance "
            f"FROM {fact_table} {fact_alias} "
            f"{join_clause} "
            f"GROUP BY {customer_alias}.{customer_name} "
            f"HAVING outstanding_balance > 0 "
            f"ORDER BY outstanding_balance DESC {_limit_clause(plan)};"
        )

    invoice_amount_column = _find_sales_amount_column(knowledge_base[fact_table])
    fact_primary_key = _find_primary_key(knowledge_base[fact_table])
    payment_table = _find_best_table(
        knowledge_base,
        module_names=("finance",),
        name_tokens=("payment",),
        required=lambda table_data: _find_payment_amount_column(table_data) is not None,
    )
    if not payment_table or not invoice_amount_column or not fact_primary_key:
        return None

    payment_amount_column = _find_payment_amount_column(knowledge_base[payment_table])
    payment_join = _find_join(knowledge_base, payment_table, fact_table)
    if not payment_amount_column or not payment_join:
        return None

    payment_fact_column, fact_join_column = payment_join
    return (
        f"SELECT {customer_alias}.{customer_name} AS customer_name, "
        f"SUM({fact_alias}.{invoice_amount_column} - COALESCE(paid.total_paid, 0)) AS outstanding_balance "
        f"FROM {fact_table} {fact_alias} "
        f"{join_clause} "
        f"LEFT JOIN ("
        f"SELECT {payment_fact_column} AS invoice_ref, SUM({payment_amount_column}) AS total_paid "
        f"FROM {payment_table} GROUP BY {payment_fact_column}"
        f") paid ON {fact_alias}.{fact_join_column} = paid.invoice_ref "
        f"GROUP BY {customer_alias}.{customer_name} "
        f"HAVING outstanding_balance > 0 "
        f"ORDER BY outstanding_balance DESC {_limit_clause(plan)};"
    )


def _pending_vendor_payments_sql(question: str, knowledge_base: dict, plan: dict) -> str | None:
    vendor_table = _find_best_table(
        knowledge_base,
        name_tokens=("vendor", "supplier"),
        required=lambda table_data: _find_name_column(table_data, "vendor", ("vendor_name", "supplier_name", "name")) is not None,
    )
    if not vendor_table:
        return None

    fact_candidates = []
    for table_name, table_data in knowledge_base.items():
        vendor_join = _find_join(knowledge_base, table_name, vendor_table)
        vendor_column = _find_column(table_data, patterns=("vendor_id", "supplier_id", "vendor_name", "supplier_name"), semantic_types=("vendor",))
        has_vendor_link = vendor_join is not None or vendor_column is not None
        status_column = _find_status_column(table_data)
        amount_column = _find_outstanding_amount_column(table_data) or _find_payment_amount_column(table_data) or _find_purchase_amount_column(table_data)
        if not has_vendor_link or not status_column or not amount_column:
            continue

        score = _table_score(table_name, table_data, module_names=("purchase", "finance"), name_tokens=("payment", "purchase", "payable", "vendor", "supplier"))
        fact_candidates.append((score, table_name))

    if not fact_candidates:
        return None

    fact_candidates.sort(reverse=True)
    fact_table = fact_candidates[0][1]
    fact_data = knowledge_base[fact_table]
    amount_column = _find_outstanding_amount_column(fact_data) or _find_payment_amount_column(fact_data) or _find_purchase_amount_column(fact_data)
    status_column = _find_status_column(fact_data)
    vendor_name = _find_name_column(knowledge_base[vendor_table], "vendor", ("vendor_name", "supplier_name", "name"))
    if not amount_column or not status_column or not vendor_name:
        return None

    used_aliases: set[str] = set()
    fact_alias = _alias_for_table(fact_table, used_aliases, preferred="p")
    vendor_alias = _alias_for_table(vendor_table, used_aliases, preferred="v")
    status_values = ", ".join(f"'{value}'" for value in _status_values_for_question(question))
    date_column = _find_date_column(fact_data)
    where_conditions = [f"{fact_alias}.{status_column} IN ({status_values})"]
    date_condition = _date_condition(date_column, plan, fact_alias) if date_column else ""
    if date_condition:
        where_conditions.append(date_condition)

    join_columns = _find_join(knowledge_base, fact_table, vendor_table)
    if join_columns:
        left_column, right_column = join_columns
        return (
            f"SELECT {vendor_alias}.{vendor_name} AS vendor_name, SUM({fact_alias}.{amount_column}) AS pending_amount "
            f"FROM {fact_table} {fact_alias} "
            f"JOIN {vendor_table} {vendor_alias} ON {fact_alias}.{left_column} = {vendor_alias}.{right_column} "
            f"WHERE {' AND '.join(where_conditions)} "
            f"GROUP BY {vendor_alias}.{vendor_name} "
            f"HAVING pending_amount > 0 "
            f"ORDER BY pending_amount DESC {_limit_clause(plan)};"
        )

    vendor_column = _find_column(fact_data, patterns=("vendor", "supplier"), semantic_types=("vendor",))
    if not vendor_column:
        return None
    return (
        f"SELECT {fact_alias}.{vendor_column} AS vendor_name, SUM({fact_alias}.{amount_column}) AS pending_amount "
        f"FROM {fact_table} {fact_alias} "
        f"WHERE {' AND '.join(where_conditions)} "
        f"GROUP BY {fact_alias}.{vendor_column} "
        f"HAVING pending_amount > 0 "
        f"ORDER BY pending_amount DESC {_limit_clause(plan)};"
    )


def _salary_by_department_sql(knowledge_base: dict, plan: dict) -> str | None:
    employee_table = _find_best_table(
        knowledge_base,
        module_names=("hr/payroll",),
        name_tokens=("employee",),
        required=lambda table_data: _find_column(table_data, patterns=("salary", "gross_salary", "net_salary", "pay_amount"), semantic_types=("money",)) is not None,
    )
    if not employee_table:
        return None

    employee_data = knowledge_base[employee_table]
    salary_column = _find_column(employee_data, patterns=("salary", "gross_salary", "net_salary", "pay_amount"), semantic_types=("money",))
    if not salary_column:
        return None

    department_table = _find_best_table(
        knowledge_base,
        name_tokens=("department",),
        required=lambda table_data: _find_name_column(table_data, "employee", ("department_name", "name")) is not None
        or _find_column(table_data, patterns=("department_name",)),
    )

    used_aliases: set[str] = set()
    employee_alias = _alias_for_table(employee_table, used_aliases, preferred="e")

    if department_table and department_table != employee_table:
        join_columns = _find_join(knowledge_base, employee_table, department_table)
        department_name = _find_column(knowledge_base[department_table], patterns=("department_name", "name"))
        if join_columns and department_name:
            department_alias = _alias_for_table(department_table, used_aliases, preferred="d")
            left_column, right_column = join_columns
            return (
                f"SELECT {department_alias}.{department_name} AS department_name, SUM({employee_alias}.{salary_column}) AS total_salary "
                f"FROM {employee_table} {employee_alias} "
                f"JOIN {department_table} {department_alias} ON {employee_alias}.{left_column} = {department_alias}.{right_column} "
                f"GROUP BY {department_alias}.{department_name} "
                f"ORDER BY total_salary DESC {_limit_clause(plan)};"
            )

    department_column = _find_column(employee_data, patterns=("department",), semantic_types=("employee",))
    if not department_column:
        return None
    return (
        f"SELECT {department_column}, SUM({salary_column}) AS total_salary "
        f"FROM {employee_table} GROUP BY {department_column} "
        f"ORDER BY total_salary DESC {_limit_clause(plan)};"
    )


def _tax_by_month_sql(knowledge_base: dict, plan: dict) -> str | None:
    fact_table = _find_best_table(
        knowledge_base,
        module_names=("sales", "finance"),
        name_tokens=("invoice", "tax"),
        required=lambda table_data: _find_tax_column(table_data) is not None and _find_date_column(table_data) is not None,
    )
    if not fact_table:
        return None

    fact_data = knowledge_base[fact_table]
    tax_column = _find_tax_column(fact_data)
    date_column = _find_date_column(fact_data)
    if not tax_column or not date_column:
        return None

    where_clause = ""
    date_condition = _date_condition(date_column, plan)
    if date_condition:
        where_clause = f" WHERE {date_condition}"

    return (
        f"SELECT DATE_FORMAT({date_column}, '%Y-%m') AS month, "
        f"SUM({tax_column}) AS total_tax "
        f"FROM {fact_table}"
        f"{where_clause} "
        f"GROUP BY DATE_FORMAT({date_column}, '%Y-%m') "
        f"ORDER BY month {_limit_clause(plan)};"
    )


def _production_bom_sql(question: str, knowledge_base: dict, plan: dict) -> str | None:
    normalized = _normalize(question)
    bom_table = _find_best_table(
        knowledge_base,
        name_tokens=("bom", "bill_of_material"),
        required=lambda table_data: True,
    )
    production_table = _find_best_table(
        knowledge_base,
        module_names=("manufacturing",),
        name_tokens=("production",),
        required=lambda table_data: _find_quantity_column(table_data, ("produced_qty", "production_qty", "quantity", "qty")) is not None,
    )
    material_table = _find_best_table(
        knowledge_base,
        name_tokens=("item", "product", "material"),
        required=lambda table_data: _find_name_column(table_data, "item_product", ("material_name", "item_name", "product_name", "name")) is not None,
    )

    if "material" in normalized and bom_table:
        bom_data = knowledge_base[bom_table]
        material_column = _find_column(bom_data, patterns=("material", "item", "product"), semantic_types=("item_product",))
        quantity_column = _find_quantity_column(bom_data, ("required_qty", "quantity", "qty"))
        if material_table and material_table != bom_table:
            join_columns = _find_join(knowledge_base, bom_table, material_table)
            material_name = _find_name_column(knowledge_base[material_table], "item_product", ("material_name", "item_name", "product_name", "name"))
            if join_columns and material_name and quantity_column:
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
        quantity_column = _find_quantity_column(production_data, ("produced_qty", "production_qty", "quantity", "qty"))
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
        return _unpaid_invoices_sql(question, knowledge_base, query_plan)
    if dimension == "customer" and metric == "balance":
        return _outstanding_by_customer_sql(knowledge_base, query_plan)
    if dimension == "vendor" and intent == "pending_outstanding":
        return _pending_vendor_payments_sql(question, knowledge_base, query_plan)
    if metric == "salary" and dimension == "department":
        return _salary_by_department_sql(knowledge_base, query_plan)
    if metric == "tax" and (intent == "trend" or dimension in {"month", "date"}):
        return _tax_by_month_sql(knowledge_base, query_plan)
    if metric == "production" or "bom" in normalized:
        return _production_bom_sql(question, knowledge_base, query_plan)

    return None
