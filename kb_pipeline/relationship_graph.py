"""
semantic/relationship_graph.py
==============================
Dynamic relationship graph and BFS join path finding.

Builds a graph from persisted KB relationship evidence and uses BFS to find
shortest valid join paths between tables. Fallback discovery is opt-in for KB
construction and never runs through the default runtime path.

No hardcoding of DB/table/column names - uses only structural evidence.
"""

from __future__ import annotations

from collections import deque
from typing import Any

from kb_pipeline.schema_facts import (
    FALLBACK_RELATIONSHIP_MIN_CONFIDENCE,
    detect_relationships,
    real_foreign_key_relationships,
)


def _persisted_relationships(schema_data: dict[str, Any]) -> list[dict[str, Any]]:
    relationships = real_foreign_key_relationships(schema_data)
    seen = {
        (
            item.get("from_table"),
            item.get("from_column"),
            item.get("to_table"),
            item.get("to_column"),
        )
        for item in relationships
    }
    for table_name, table_data in (schema_data or {}).items():
        for raw in table_data.get("relationships", []) or []:
            if raw.get("direction") == "incoming" or raw.get("safe_for_planner") is False:
                continue
            relationship = dict(raw)
            relationship.setdefault("from_table", table_name)
            signature = (
                relationship.get("from_table"),
                relationship.get("from_column"),
                relationship.get("to_table"),
                relationship.get("to_column"),
            )
            if not all(signature) or signature in seen:
                continue
            is_fallback = bool(relationship.get("is_fallback") or relationship.get("is_inferred"))
            if is_fallback and float(relationship.get("confidence") or 0.0) < FALLBACK_RELATIONSHIP_MIN_CONFIDENCE:
                continue
            relationship.setdefault("relationship_type", "inferred" if is_fallback else "foreign_key")
            relationship.setdefault("safe_for_planner", True)
            seen.add(signature)
            relationships.append(relationship)
    return relationships


def build_relationship_graph(
    schema_data: dict[str, Any],
    *,
    infer_relationships: bool = False,
) -> dict[str, dict[str, Any]]:
    """
    Build a relationship graph from schema data.
    
    The graph is represented as an adjacency list where each node (table)
    has a list of edges (relationships) to other tables.
    
    Args:
        schema_data: Dictionary of table schema data with persisted relationships.
        infer_relationships: Enable fallback discovery only during KB construction.
        
    Returns:
        Dictionary mapping table names to their adjacency lists
    """
    graph: dict[str, dict[str, Any]] = {}
    relationships = (
        detect_relationships(schema_data)
        if infer_relationships
        else _persisted_relationships(schema_data)
    )
    
    # Initialize graph with all tables
    for table_name in schema_data.keys():
        graph[table_name] = {
            "edges": [],
            "table_name": table_name,
        }
    
    # Add edges from detected relationships
    for rel in relationships:
        from_table = rel.get("from_table")
        to_table = rel.get("to_table")
        
        if from_table not in graph or to_table not in graph:
            continue
        
        # Add forward edge (from_table -> to_table)
        graph[from_table]["edges"].append({
            "to_table": to_table,
            "from_column": rel.get("from_column"),
            "to_column": rel.get("to_column"),
            "direction": rel.get("direction"),
            "confidence": rel.get("confidence", 0.5),
            "source": rel.get("source", "unknown"),
            "reason": rel.get("reason", ""),
            "relationship_type": rel.get("relationship_type", "unknown"),
            "evidence": list(rel.get("evidence", []) or []),
            "evidence_reasons": list(rel.get("evidence_reasons", []) or []),
            "safe_for_planner": bool(rel.get("safe_for_planner", True)),
            "is_inferred": bool(rel.get("is_inferred", False)),
            "is_fallback": bool(rel.get("is_fallback", False)),
        })
        
        # Add reverse edge (to_table -> from_table) for bidirectional traversal
        graph[to_table]["edges"].append({
            "to_table": from_table,
            "from_column": rel.get("to_column"),
            "to_column": rel.get("from_column"),
            "direction": "one-to-many" if rel.get("direction") == "many-to-one" else "many-to-one",
            "confidence": rel.get("confidence", 0.5),
            "source": rel.get("source", "unknown"),
            "reason": rel.get("reason", ""),
            "relationship_type": rel.get("relationship_type", "unknown"),
            "evidence": list(rel.get("evidence", []) or []),
            "evidence_reasons": list(rel.get("evidence_reasons", []) or []),
            "safe_for_planner": bool(rel.get("safe_for_planner", True)),
            "is_inferred": bool(rel.get("is_inferred", False)),
            "is_fallback": bool(rel.get("is_fallback", False)),
        })
    
    return graph


def find_shortest_join_path(
    graph: dict[str, dict[str, Any]],
    start_table: str,
    end_table: str,
) -> dict[str, Any]:
    """
    Find shortest join path between two tables using BFS.
    
    BFS explores all paths level by level, guaranteeing the shortest path
    is found first. Only traverses detected edges - does not invent joins.
    
    Args:
        graph: Relationship graph from build_relationship_graph()
        start_table: Starting table name
        end_table: Target table name
        
    Returns:
        Dictionary with:
        - path: list of table names in the join path
        - join_columns: list of (from_column, to_column) tuples for each join
        - edge_sources: list of edge sources for each join
        - confidences: list of confidence scores for each join
        - path_length: number of joins in the path
        - total_confidence: product of all edge confidences
        - resolved: True if path found, False otherwise
    """
    if start_table not in graph or end_table not in graph:
        return {
            "path": [],
            "join_columns": [],
            "edge_sources": [],
            "confidences": [],
            "path_length": 0,
            "total_confidence": 0.0,
            "resolved": False,
            "reason": "Start or end table not in graph",
        }
    
    if start_table == end_table:
        return {
            "path": [start_table],
            "join_columns": [],
            "edge_sources": [],
            "confidences": [],
            "path_length": 0,
            "total_confidence": 1.0,
            "resolved": True,
            "reason": "Start and end table are the same",
        }
    
    # BFS to find shortest path
    queue = deque([(start_table, [start_table], [], [], [])])
    visited = {start_table}
    
    while queue:
        current_table, path, join_columns, edge_sources, confidences = queue.popleft()
        
        if current_table == end_table:
            # Calculate total confidence as product of edge confidences
            total_confidence = 1.0
            for conf in confidences:
                total_confidence *= conf
            
            return {
                "path": path,
                "join_columns": join_columns,
                "edge_sources": edge_sources,
                "confidences": confidences,
                "path_length": len(join_columns),
                "total_confidence": total_confidence,
                "resolved": True,
                "reason": "Path found via BFS",
            }
        
        # Explore neighbors
        for edge in graph.get(current_table, {}).get("edges", []):
            neighbor_table = edge["to_table"]
            
            if neighbor_table in visited:
                continue
            
            visited.add(neighbor_table)
            
            new_path = path + [neighbor_table]
            new_join_columns = join_columns + [(edge["from_column"], edge["to_column"])]
            new_edge_sources = edge_sources + [edge["source"]]
            new_confidences = confidences + [edge["confidence"]]
            
            queue.append((neighbor_table, new_path, new_join_columns, new_edge_sources, new_confidences))
    
    # No path found
    return {
        "path": [],
        "join_columns": [],
        "edge_sources": [],
        "confidences": [],
        "path_length": 0,
        "total_confidence": 0.0,
        "resolved": False,
        "reason": "No valid join path found between tables",
    }


def find_all_possible_join_paths(
    graph: dict[str, dict[str, Any]],
    start_table: str,
    end_table: str,
    max_paths: int = 5,
    max_hops: int = 4,
) -> list[dict[str, Any]]:
    """
    Find multiple possible join paths between two tables.
    
    Returns multiple paths sorted by:
    1. Path length (shorter first)
    2. Total confidence (higher first)
    3. Edge source preference (database_metadata > KB-build inference)
    
    Args:
        graph: Relationship graph from build_relationship_graph()
        start_table: Starting table name
        end_table: Target table name
        max_paths: Maximum number of paths to return
        max_hops: Maximum number of hops to explore
        
    Returns:
        List of join path dictionaries sorted by preference
    """
    if start_table not in graph or end_table not in graph:
        return []
    
    if start_table == end_table:
        return [{
            "path": [start_table],
            "join_columns": [],
            "edge_sources": [],
            "confidences": [],
            "path_length": 0,
            "total_confidence": 1.0,
            "resolved": True,
            "reason": "Start and end table are the same",
        }]
    
    # BFS to find all paths up to max_hops
    all_paths: list[dict[str, Any]] = []
    queue = deque([(start_table, [start_table], [], [], [])])
    visited = {start_table}
    
    while queue and len(all_paths) < max_paths * 2:  # Collect extra paths for ranking
        current_table, path, join_columns, edge_sources, confidences = queue.popleft()
        
        if current_table == end_table:
            total_confidence = 1.0
            for conf in confidences:
                total_confidence *= conf
            
            all_paths.append({
                "path": path,
                "join_columns": join_columns,
                "edge_sources": edge_sources,
                "confidences": confidences,
                "path_length": len(join_columns),
                "total_confidence": total_confidence,
                "resolved": True,
                "reason": "Path found via BFS",
            })
            continue
        
        if len(join_columns) >= max_hops:
            continue
        
        # Explore neighbors
        for edge in graph.get(current_table, {}).get("edges", []):
            neighbor_table = edge["to_table"]
            
            if neighbor_table in visited:
                continue
            
            # Use path-based visited to allow revisiting nodes in different paths
            if neighbor_table in path:
                continue
            
            new_path = path + [neighbor_table]
            new_join_columns = join_columns + [(edge["from_column"], edge["to_column"])]
            new_edge_sources = edge_sources + [edge["source"]]
            new_confidences = confidences + [edge["confidence"]]
            
            queue.append((neighbor_table, new_path, new_join_columns, new_edge_sources, new_confidences))
    
    # Sort paths by preference
    def path_score(path: dict[str, Any]) -> tuple:
        # Prefer: shorter paths, higher confidence, FK edges
        fk_count = sum(1 for source in path["edge_sources"] if source in {"database_metadata", "foreign_key"})
        return (
            path["path_length"],  # Shorter is better
            -path["total_confidence"],  # Higher confidence is better
            -fk_count,  # More FK edges is better
        )
    
    all_paths.sort(key=path_score)
    
    return all_paths[:max_paths]
