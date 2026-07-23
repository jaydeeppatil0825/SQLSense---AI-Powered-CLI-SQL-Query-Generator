"""Shared SQL clause rendering."""

from __future__ import annotations

from typing import Any


def render_plan_in_canonical_order(plan: Any) -> str:
    if uses_single_table_star_projection(plan):
        select_parts = ["*"]
    else:
        select_parts = []
        for item in plan.select_items:
            expression = str(item.get("expression") or "").strip()
            alias = str(item.get("alias") or "").strip()
            select_parts.append(f"{expression} AS {alias}" if alias else expression)
    sql = f"SELECT {', '.join(select_parts)} FROM {plan.base_table}"
    for join in plan.joins:
        joined_table = str(join.get("table") or "")
        edge = join.get("edge") if isinstance(join.get("edge"), dict) else {}
        sql += (
            f" INNER JOIN {joined_table} ON "
            f"{edge.get('from_table')}.{edge.get('from_column')} = "
            f"{edge.get('to_table')}.{edge.get('to_column')}"
        )
    if plan.where_clauses:
        sql += f" WHERE {render_predicates(plan.where_clauses, plan.where_conjunctions)}"
    if plan.group_by:
        sql += f" GROUP BY {', '.join(plan.group_by)}"
    if plan.having_clauses:
        sql += f" HAVING {render_predicates(plan.having_clauses, plan.having_conjunctions)}"
    if plan.order_by:
        sql += f" ORDER BY {', '.join(plan.order_by)}"
    if plan.limit:
        sql += f" LIMIT {plan.limit}"
    return sql + ";"


def uses_single_table_star_projection(plan: Any) -> bool:
    return (
        plan.sql_skeleton_type == "filtered_single_table_list"
        and plan.projection_mode == "full_row"
        and not plan.joins
        and not plan.group_by
        and not plan.having_clauses
        and not plan.order_by
        and not plan.aggregation_type
    )


def render_predicates(clauses: list[str], conjunctions: list[str]) -> str:
    predicate = clauses[0]
    for index, clause in enumerate(clauses[1:], start=1):
        conjunction = conjunctions[index] if index < len(conjunctions) else "and"
        predicate += f" {(conjunction or 'and').upper()} {clause}"
    return predicate
