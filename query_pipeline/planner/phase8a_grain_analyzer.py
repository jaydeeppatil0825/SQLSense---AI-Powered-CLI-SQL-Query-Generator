"""Phase 8A grain safety analysis for planner-selected relationship paths."""

from __future__ import annotations

from typing import Any


GRAIN_STATUSES = {
    "grain_preserved",
    "row_multiplication_risk",
    "unknown_cardinality",
    "unsupported_bridge_path",
}


def analyze_selected_path_grain(
    *,
    metric_base_table: str,
    selected_join_path: dict[str, Any],
    relationship_graph: dict[str, dict[str, Any]],
    schema: dict[str, Any],
    aggregate_function: str | None = None,
    count_base_table: str | None = None,
) -> dict[str, Any]:
    """Check whether a selected one/two-edge path preserves base metric row grain."""
    base_table = str((selected_join_path or {}).get("base_table") or "")
    joined_tables = [str(value) for value in (selected_join_path or {}).get("joined_tables") or []]
    edges = [dict(edge) for edge in (selected_join_path or {}).get("edges") or [] if isinstance(edge, dict)]
    tables = [base_table, *joined_tables]

    if aggregate_function == "count" and str(count_base_table or "") != str(metric_base_table or ""):
        return _result("unknown_cardinality", "COUNT base table is not explicit or does not match metric grain")
    if base_table != metric_base_table or not base_table:
        return _result("unknown_cardinality", "metric/base table is ambiguous")
    if len(edges) not in {1, 2} or len(joined_tables) != len(edges):
        return _result("unsupported_bridge_path", "only one or two ordered edges are supported")
    if len(set(tables)) != len(tables):
        return _result("unsupported_bridge_path", "path repeats a table or forms a cycle")
    if any(table not in schema for table in tables):
        return _result("unknown_cardinality", "path table is missing from schema")

    diagnostics: list[dict[str, Any]] = []
    for index, edge in enumerate(edges):
        if str(edge.get("from_table") or "") != tables[index] or str(edge.get("to_table") or "") != tables[index + 1]:
            return _result("unsupported_bridge_path", "path edges are disconnected or reordered", diagnostics)
        graph_edge = _matching_graph_edge(edge, relationship_graph)
        if graph_edge is None:
            return _result("unknown_cardinality", "selected edge is not present in Relationship Graph", diagnostics)
        if _is_bridge_edge(graph_edge):
            return _result("unsupported_bridge_path", "bridge or many-to-many relationship is unsupported", diagnostics)
        if not _is_real_safe_fk(graph_edge):
            return _result("unknown_cardinality", "edge cardinality is not proven by safe database FK metadata", diagnostics)

        diagnostic = _edge_diagnostic(edge, graph_edge, schema)
        diagnostics.append(diagnostic)
        if diagnostic["status"] == "missing_pk_fk_evidence":
            return _result("unknown_cardinality", "edge is missing FK/PK ownership evidence", diagnostics)
        if diagnostic["row_multiplication_risk"]:
            return _result("row_multiplication_risk", "path traverses one-to-many from metric grain", diagnostics)

    return _result("grain_preserved", "all edges traverse many-to-one from metric grain", diagnostics)


def _result(status: str, reason: str, edge_diagnostics: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "status": status,
        "grain_preserved": status == "grain_preserved",
        "row_multiplication_risk": status == "row_multiplication_risk",
        "unknown_cardinality": status == "unknown_cardinality",
        "unsupported_bridge_path": status == "unsupported_bridge_path",
        "reason": reason,
        "edge_diagnostics": edge_diagnostics or [],
    }


def _matching_graph_edge(edge: dict[str, Any], graph: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    from_table = str(edge.get("from_table") or "")
    to_table = str(edge.get("to_table") or "")
    from_column = str(edge.get("from_column") or "")
    to_column = str(edge.get("to_column") or "")
    for candidate in sorted(graph.get(from_table, {}).get("edges", []) or [], key=lambda item: str(item)):
        if (
            str(candidate.get("to_table") or "") == to_table
            and str(candidate.get("from_column") or "") == from_column
            and str(candidate.get("to_column") or "") == to_column
        ):
            return dict(candidate)
    return None


def _is_real_safe_fk(edge: dict[str, Any]) -> bool:
    return bool(
        edge.get("safe_for_planner") is True
        and str(edge.get("relationship_type") or "").lower() == "foreign_key"
        and str(edge.get("source") or "").lower() == "database_metadata"
        and not edge.get("is_inferred")
        and not edge.get("is_fallback")
    )


def _is_bridge_edge(edge: dict[str, Any]) -> bool:
    relationship_type = str(edge.get("relationship_type") or "").lower()
    return "bridge" in relationship_type or "many_to_many" in relationship_type or "many-to-many" in relationship_type


def _edge_diagnostic(edge: dict[str, Any], graph_edge: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    fk_table = str(graph_edge.get("authoritative_from_table") or "")
    fk_column = str(graph_edge.get("authoritative_from_column") or "")
    parent_table = str(graph_edge.get("authoritative_to_table") or "")
    parent_column = str(graph_edge.get("authoritative_to_column") or "")
    from_table = str(edge.get("from_table") or "")
    to_table = str(edge.get("to_table") or "")

    fk_ok = _has_fk(schema, fk_table, fk_column, parent_table, parent_column)
    pk_ok = parent_column in set(schema.get(parent_table, {}).get("primary_keys") or [])
    traversal = "many_to_one" if (from_table, to_table) == (fk_table, parent_table) else "one_to_many"
    if not fk_ok or not pk_ok:
        status = "missing_pk_fk_evidence"
    else:
        status = "grain_preserved" if traversal == "many_to_one" else "row_multiplication_risk"
    return {
        "from_table": from_table,
        "to_table": to_table,
        "foreign_key_side": {"table": fk_table, "column": fk_column},
        "referenced_side": {"table": parent_table, "column": parent_column},
        "traversal_direction": traversal,
        "preserves_metric_grain": status == "grain_preserved",
        "row_multiplication_risk": status == "row_multiplication_risk",
        "status": status,
    }


def _has_fk(schema: dict[str, Any], table: str, column: str, target_table: str, target_column: str) -> bool:
    for foreign_key in schema.get(table, {}).get("foreign_keys", []) or []:
        if (
            str(foreign_key.get("column") or foreign_key.get("from_column") or "") == column
            and str(foreign_key.get("referenced_table") or foreign_key.get("to_table") or "") == target_table
            and str(foreign_key.get("referenced_column") or foreign_key.get("to_column") or "") == target_column
        ):
            return True
    return False
