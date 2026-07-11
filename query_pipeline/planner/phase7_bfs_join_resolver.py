"""Future Phase 7 BFS/multi-hop join helpers.

This module is intentionally not used by the active Phase 6H retrieved-context
planner path. Phase 6H join authorization remains direct Relationship Graph
edge selection only.
"""

from __future__ import annotations

from collections import deque
from typing import Any

from kb_pipeline.schema_facts import resolved_semantic_type

PHASE7A_STATUSES = {
    "unique_safe_path",
    "no_safe_path",
    "ambiguous_path",
    "unsupported_depth",
    "invalid_graph_edge",
}


def resolve_safe_multi_hop_path(
    *,
    base_table: str,
    target_table: str,
    relationship_graph: dict[str, dict[str, Any]],
    schema: dict[str, Any],
    max_depth: int = 2,
) -> dict[str, Any]:
    """Resolve one safe graph-authorized path without integrating it into planning."""
    if base_table not in schema or target_table not in schema:
        return _phase7a_result("invalid_graph_edge", "base or target table is missing from schema")
    if base_table == target_table:
        return _phase7a_result("unique_safe_path", "base and target are the same table", path=[], tables=[base_table])

    queue = deque([(base_table, [base_table], [])])
    shortest_paths: list[list[dict[str, Any]]] = []
    over_depth_seen = False
    invalid_edge_seen = False

    while queue:
        current, tables, path = queue.popleft()
        if len(path) > max_depth:
            over_depth_seen = True
            continue
        if shortest_paths and len(path) >= len(shortest_paths[0]):
            continue

        for edge in _safe_sorted_edges(relationship_graph, current):
            next_table = str(edge.get("to_table") or "")
            if not next_table or next_table in tables:
                continue
            normalized_edge = _normalize_traversed_edge(current, edge)
            if not _edge_columns_exist(normalized_edge, schema):
                invalid_edge_seen = True
                continue

            next_path = [*path, normalized_edge]
            if len(next_path) > max_depth:
                over_depth_seen = True
                continue
            if next_table == target_table:
                if not shortest_paths or len(next_path) < len(shortest_paths[0]):
                    shortest_paths = [next_path]
                elif _path_signature(next_path) not in {_path_signature(item) for item in shortest_paths}:
                    shortest_paths.append(next_path)
                continue
            queue.append((next_table, [*tables, next_table], next_path))

    if len(shortest_paths) == 1:
        path = shortest_paths[0]
        return _phase7a_result(
            "unique_safe_path",
            "one safe relationship graph path resolved",
            path=path,
            tables=_tables_for_path(base_table, path),
        )
    if len(shortest_paths) > 1:
        return _phase7a_result("ambiguous_path", "multiple equally short safe relationship graph paths exist")
    if invalid_edge_seen:
        return _phase7a_result("invalid_graph_edge", "relationship graph edge references missing schema columns")
    if over_depth_seen:
        return _phase7a_result("unsupported_depth", "safe path exceeds maximum supported depth")
    return _phase7a_result("no_safe_path", "no safe relationship graph path exists")


def _phase7a_result(status: str, reason: str, *, path: list[dict[str, Any]] | None = None, tables: list[str] | None = None) -> dict[str, Any]:
    return {
        "status": status,
        "resolved": status == "unique_safe_path",
        "reason": reason,
        "path": path or [],
        "tables": tables or [],
        "edge_count": len(path or []),
        "path_source": "relationship_graph",
    }


def _safe_sorted_edges(graph: dict[str, dict[str, Any]], table_name: str) -> list[dict[str, Any]]:
    edges = [
        dict(edge)
        for edge in graph.get(table_name, {}).get("edges", []) or []
        if edge.get("safe_for_planner") is True
    ]
    return sorted(
        edges,
        key=lambda edge: (
            str(edge.get("to_table") or ""),
            str(edge.get("authoritative_from_table") or ""),
            str(edge.get("authoritative_from_column") or edge.get("from_column") or ""),
            str(edge.get("authoritative_to_table") or ""),
            str(edge.get("authoritative_to_column") or edge.get("to_column") or ""),
        ),
    )


def _normalize_traversed_edge(current_table: str, edge: dict[str, Any]) -> dict[str, Any]:
    return {
        "from_table": current_table,
        "from_column": str(edge.get("from_column") or ""),
        "to_table": str(edge.get("to_table") or ""),
        "to_column": str(edge.get("to_column") or ""),
        "authoritative_from_table": str(edge.get("authoritative_from_table") or current_table),
        "authoritative_from_column": str(edge.get("authoritative_from_column") or edge.get("from_column") or ""),
        "authoritative_to_table": str(edge.get("authoritative_to_table") or edge.get("to_table") or ""),
        "authoritative_to_column": str(edge.get("authoritative_to_column") or edge.get("to_column") or ""),
        "relationship_type": edge.get("relationship_type"),
        "source": edge.get("source"),
        "confidence": float(edge.get("confidence") or 0.0),
        "safe_for_planner": True,
        "evidence": list(edge.get("evidence") or []),
        "evidence_reasons": list(edge.get("evidence_reasons") or []),
    }


def _edge_columns_exist(edge: dict[str, Any], schema: dict[str, Any]) -> bool:
    return (
        edge["from_column"] in _schema_columns(schema, edge["from_table"])
        and edge["to_column"] in _schema_columns(schema, edge["to_table"])
    )


def _schema_columns(schema: dict[str, Any], table_name: str) -> set[str]:
    return {str(column.get("name") or "") for column in schema.get(table_name, {}).get("columns", []) or []}


def _path_signature(path: list[dict[str, Any]]) -> tuple[tuple[str, str, str, str], ...]:
    return tuple(
        (
            str(edge.get("from_table") or ""),
            str(edge.get("from_column") or ""),
            str(edge.get("to_table") or ""),
            str(edge.get("to_column") or ""),
        )
        for edge in path
    )


def _tables_for_path(base_table: str, path: list[dict[str, Any]]) -> list[str]:
    tables = [base_table]
    for edge in path:
        tables.append(str(edge.get("to_table") or ""))
    return tables


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
    if start not in graph or end not in graph:
        return None

    if start == end:
        return []

    queue = deque([(start, [])])
    visited = {start}

    while queue and len(queue[0][1]) < max_depth:
        current, path = queue.popleft()

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

    for index, table_a in enumerate(selected_tables):
        for table_b in selected_tables[index + 1:]:
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


def _selected_join_columns_for_table(
    table_name: str,
    table_data: dict[str, Any],
    join_paths: list[dict],
) -> list[dict[str, Any]]:
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
                "semantic_type": resolved_semantic_type(column, fallback="id"),
                "core_semantic_type": str(column.get("semantic_type", "id")).strip().lower() or "id",
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
    """Promote bridge tables that are required by FK join paths into context."""
    from query_pipeline.query_planner import _is_simple_primary_table_question

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


def _find_bridge_tables(
    selected_tables: list[str],
    knowledge_base: dict,
    max_bridges: int = 3,
) -> list[str]:
    """Find bridge tables that connect disconnected selected tables."""
    graph = _build_fk_relationship_graph(knowledge_base)
    bridge_tables = []

    if len(selected_tables) < 2:
        return bridge_tables

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

    disconnected = [table for table in selected_tables if table not in reachable]

    if not disconnected:
        return bridge_tables

    for disconnected_table in disconnected[:max_bridges]:
        path = _find_shortest_path(graph, start_table, disconnected_table, max_depth=5)
        if path and len(path) > 0:
            for edge in path[:-1]:
                bridge = edge["to_table"]
                if bridge not in selected_tables and bridge not in bridge_tables and bridge in knowledge_base:
                    bridge_tables.append(bridge)

    return bridge_tables
