"""Phase 9B deterministic cache helpers for graph paths and grain analysis."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Callable

from core.cache_service import (
    CORRUPT,
    HIT,
    STALE,
    CacheIdentity,
    CacheStore,
    make_cache_key,
)
from kb_pipeline.relationship_graph import find_safe_direct_join_relationships
from query_pipeline.planner.phase8a_grain_analyzer import analyze_selected_path_grain


RELATIONSHIP_PATH_ARTIFACT_TYPE = "relationship_path"
RELATIONSHIP_PATH_ARTIFACT_VERSION = "phase9b-relationship-path-v1"
RELATIONSHIP_PATH_POLICY_VERSION = "phase9b-path-policy-v1"
GRAIN_ANALYSIS_ARTIFACT_TYPE = "grain_analysis"
GRAIN_ANALYSIS_ARTIFACT_VERSION = "phase9b-grain-analysis-v1"
GRAIN_ANALYSIS_POLICY_VERSION = "phase8a-grain-policy-v1"
ALLOWED_RELATIONSHIP_TYPES = ("foreign_key",)
SUPPORTED_AGGREGATES = {"sum", "avg", "count", "min", "max", ""}


def build_cache_identity(
    *,
    database_identity: dict[str, Any] | None,
    knowledge_base: dict[str, Any],
    relationship_graph: dict[str, dict[str, Any]],
) -> CacheIdentity:
    db = database_identity or {}
    return CacheIdentity(
        db_engine=str(db.get("db_engine") or db.get("database_type") or ""),
        db_host=str(db.get("db_host") or ""),
        db_port=str(db.get("db_port") or ""),
        db_name=str(db.get("db_name") or db.get("database_name") or ""),
        schema_hash=schema_fingerprint(knowledge_base),
        kb_fingerprint=kb_fingerprint(knowledge_base),
        graph_fingerprint=graph_fingerprint(relationship_graph),
    )


def schema_fingerprint(knowledge_base: dict[str, Any]) -> str:
    return _digest(
        {
            table: {
                "columns": [
                    {
                        "name": column.get("name"),
                        "type": column.get("type"),
                        "nullable": column.get("nullable"),
                        "semantic_type": column.get("semantic_type"),
                    }
                    for column in data.get("columns", []) or []
                ],
                "primary_keys": list(data.get("primary_keys", []) or []),
                "foreign_keys": list(data.get("foreign_keys", []) or []),
                "relationships": list(data.get("relationships", []) or []),
            }
            for table, data in (knowledge_base or {}).items()
        }
    )


def kb_fingerprint(knowledge_base: dict[str, Any]) -> str:
    return _digest(knowledge_base or {})


def graph_fingerprint(relationship_graph: dict[str, dict[str, Any]]) -> str:
    return _digest(
        {
            table: [
                _edge_identity(edge)
                for edge in node.get("edges", []) or []
                if isinstance(edge, dict)
            ]
            for table, node in (relationship_graph or {}).items()
        }
    )


def cached_direct_join_relationships(
    *,
    cache_store: CacheStore | None,
    database_identity: dict[str, Any] | None,
    knowledge_base: dict[str, Any],
    relationship_graph: dict[str, dict[str, Any]],
    source_table: str,
    target_table: str,
    candidate_tables: list[str] | set[str] | tuple[str, ...] | None = None,
    planner_options: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    identity = build_cache_identity(
        database_identity=database_identity,
        knowledge_base=knowledge_base,
        relationship_graph=relationship_graph,
    )
    normalized_input = {
        "resolver": "direct",
        "source_table": source_table,
        "target_table": target_table,
        "candidate_tables": list(candidate_tables or []),
        "max_edges": 1,
        "safe_edges_required": True,
        "allowed_relationship_types": list(ALLOWED_RELATIONSHIP_TYPES),
        "policy_version": RELATIONSHIP_PATH_POLICY_VERSION,
    }
    key = make_cache_key(
        identity=identity,
        artifact_type=RELATIONSHIP_PATH_ARTIFACT_TYPE,
        normalized_input=normalized_input,
        planner_options=planner_options or {},
    )
    if cache_store is not None:
        try:
            read = cache_store.get(key, artifact_version=RELATIONSHIP_PATH_ARTIFACT_VERSION)
            if read.state == HIT and _valid_direct_artifact(read.value, source_table, target_table, relationship_graph, identity):
                return [dict(edge) for edge in read.value.get("ordered_edges") or []]
            if read.state in {HIT, STALE, CORRUPT}:
                cache_store.delete(key)
        except Exception:
            pass

    edges = find_safe_direct_join_relationships(relationship_graph, source_table, target_table)
    artifact = _path_artifact(
        resolver="direct",
        identity=identity,
        source_table=source_table,
        target_table=target_table,
        status="resolved" if len(edges) == 1 else "rejected",
        resolver_status="unique_safe_path" if len(edges) == 1 else ("ambiguous_path" if len(edges) > 1 else "no_safe_path"),
        reason="one safe direct Relationship Graph edge resolved" if len(edges) == 1 else "direct Relationship Graph edge is missing or ambiguous",
        tables=[str(edges[0].get("from_table") or ""), str(edges[0].get("to_table") or "")] if len(edges) == 1 else [],
        edges=[dict(edges[0])] if len(edges) == 1 else [],
    )
    _safe_cache_set(cache_store, key, RELATIONSHIP_PATH_ARTIFACT_VERSION, artifact)
    return edges


def cached_multi_hop_path(
    *,
    cache_store: CacheStore | None,
    database_identity: dict[str, Any] | None,
    knowledge_base: dict[str, Any],
    relationship_graph: dict[str, dict[str, Any]],
    base_table: str,
    target_table: str,
    max_depth: int,
    compute: Callable[[], dict[str, Any]],
    candidate_tables: list[str] | set[str] | tuple[str, ...] | None = None,
    planner_options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    identity = build_cache_identity(
        database_identity=database_identity,
        knowledge_base=knowledge_base,
        relationship_graph=relationship_graph,
    )
    normalized_input = {
        "resolver": "bfs",
        "source_table": base_table,
        "target_table": target_table,
        "candidate_tables": list(candidate_tables or []),
        "max_edges": int(max_depth),
        "safe_edges_required": True,
        "allowed_relationship_types": list(ALLOWED_RELATIONSHIP_TYPES),
        "policy_version": RELATIONSHIP_PATH_POLICY_VERSION,
    }
    key = make_cache_key(
        identity=identity,
        artifact_type=RELATIONSHIP_PATH_ARTIFACT_TYPE,
        normalized_input=normalized_input,
        planner_options=planner_options or {},
    )
    if cache_store is not None:
        try:
            read = cache_store.get(key, artifact_version=RELATIONSHIP_PATH_ARTIFACT_VERSION)
            if read.state == HIT and _valid_bfs_artifact(read.value, base_table, target_table, max_depth, knowledge_base, relationship_graph, identity):
                return _artifact_to_bfs_result(read.value)
            if read.state in {HIT, STALE, CORRUPT}:
                cache_store.delete(key)
        except Exception:
            pass

    result = compute()
    artifact = _path_artifact(
        resolver="bfs",
        identity=identity,
        source_table=base_table,
        target_table=target_table,
        status="resolved" if result.get("status") == "unique_safe_path" else "rejected",
        resolver_status=str(result.get("status") or ""),
        reason=str(result.get("reason") or ""),
        tables=[str(table) for table in result.get("tables") or []],
        edges=[dict(edge) for edge in result.get("path") or [] if isinstance(edge, dict)],
    )
    _safe_cache_set(cache_store, key, RELATIONSHIP_PATH_ARTIFACT_VERSION, artifact)
    return result


def cached_grain_analysis(
    *,
    cache_store: CacheStore | None,
    database_identity: dict[str, Any] | None,
    knowledge_base: dict[str, Any],
    relationship_graph: dict[str, dict[str, Any]],
    metric_base_table: str,
    selected_join_path: dict[str, Any],
    aggregate_function: str | None,
    count_base_table: str | None,
    metric_column: str | None = None,
    dimension_table: str | None = None,
    dimension_column: str | None = None,
    planner_options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    identity = build_cache_identity(
        database_identity=database_identity,
        knowledge_base=knowledge_base,
        relationship_graph=relationship_graph,
    )
    normalized_input = {
        "policy_version": GRAIN_ANALYSIS_POLICY_VERSION,
        "selected_path": _selected_path_identity(selected_join_path),
        "metric_base_table": metric_base_table,
        "metric_column": metric_column or "",
        "dimension_table": dimension_table or "",
        "dimension_column": dimension_column or "",
        "aggregate_function": aggregate_function or "",
        "count_base_table": count_base_table or "",
    }
    key = make_cache_key(
        identity=identity,
        artifact_type=GRAIN_ANALYSIS_ARTIFACT_TYPE,
        normalized_input=normalized_input,
        planner_options=planner_options or {},
    )
    if cache_store is not None:
        try:
            read = cache_store.get(key, artifact_version=GRAIN_ANALYSIS_ARTIFACT_VERSION)
            if read.state == HIT and _valid_grain_artifact(
                read.value,
                identity,
                knowledge_base,
                relationship_graph,
                selected_join_path,
                metric_base_table,
                metric_column or "",
                dimension_table or "",
                dimension_column or "",
                aggregate_function or "",
                count_base_table or "",
            ):
                return dict(read.value["result"])
            if read.state in {HIT, STALE, CORRUPT}:
                cache_store.delete(key)
        except Exception:
            pass

    result = analyze_selected_path_grain(
        metric_base_table=metric_base_table,
        selected_join_path=selected_join_path,
        relationship_graph=relationship_graph,
        schema=knowledge_base,
        aggregate_function=aggregate_function,
        count_base_table=count_base_table,
    )
    artifact = {
        "artifact_type": GRAIN_ANALYSIS_ARTIFACT_TYPE,
        "artifact_version": GRAIN_ANALYSIS_ARTIFACT_VERSION,
        "policy_version": GRAIN_ANALYSIS_POLICY_VERSION,
        "graph_fingerprint": identity.graph_fingerprint,
        "selected_path_identity": _selected_path_identity(selected_join_path),
        "metric_base_table": metric_base_table,
        "metric_column": metric_column or "",
        "dimension_table": dimension_table or "",
        "dimension_column": dimension_column or "",
        "aggregate_function": aggregate_function or "",
        "count_base_table": count_base_table or "",
        "count_base_valid": (aggregate_function != "count") or count_base_table == metric_base_table,
        "result": dict(result),
    }
    _safe_cache_set(cache_store, key, GRAIN_ANALYSIS_ARTIFACT_VERSION, artifact)
    return result


def _path_artifact(
    *,
    resolver: str,
    identity: CacheIdentity,
    source_table: str,
    target_table: str,
    status: str,
    resolver_status: str,
    reason: str,
    tables: list[str],
    edges: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "artifact_type": RELATIONSHIP_PATH_ARTIFACT_TYPE,
        "artifact_version": RELATIONSHIP_PATH_ARTIFACT_VERSION,
        "policy_version": RELATIONSHIP_PATH_POLICY_VERSION,
        "resolver": resolver,
        "graph_fingerprint": identity.graph_fingerprint,
        "source_table": source_table,
        "target_table": target_table,
        "status": status,
        "resolver_status": resolver_status,
        "reason": reason,
        "ordered_tables": tables,
        "ordered_edges": [_artifact_edge(edge) for edge in edges],
        "path_source": "relationship_graph",
        "ambiguity_status": "resolved" if resolver_status == "unique_safe_path" else ("ambiguous" if resolver_status == "ambiguous_path" else "none"),
        "safe_rejection_code": "" if status == "resolved" else resolver_status,
        "safe_rejection_reason": "" if status == "resolved" else reason,
    }


def _valid_direct_artifact(
    artifact: Any,
    source_table: str,
    target_table: str,
    graph: dict[str, dict[str, Any]],
    identity: CacheIdentity,
) -> bool:
    if not _valid_path_header(artifact, identity) or artifact.get("resolver") != "direct":
        return False
    if artifact.get("source_table") != source_table or artifact.get("target_table") != target_table:
        return False
    if artifact.get("status") != "resolved":
        return False
    edges = [edge for edge in artifact.get("ordered_edges") or [] if isinstance(edge, dict)]
    tables = [str(table) for table in artifact.get("ordered_tables") or []]
    if len(edges) != 1 or len(tables) != 2 or len(set(tables)) != 2:
        return False
    if str(edges[0].get("from_table") or "") != tables[0] or str(edges[0].get("to_table") or "") != tables[1]:
        return False
    current_edges = find_safe_direct_join_relationships(graph, source_table, target_table)
    return len(current_edges) == 1 and _edge_identity(current_edges[0]) == _edge_identity(edges[0])


def _valid_bfs_artifact(
    artifact: Any,
    base_table: str,
    target_table: str,
    max_depth: int,
    schema: dict[str, Any],
    graph: dict[str, dict[str, Any]],
    identity: CacheIdentity,
) -> bool:
    if not _valid_path_header(artifact, identity) or artifact.get("resolver") != "bfs":
        return False
    if artifact.get("source_table") != base_table or artifact.get("target_table") != target_table:
        return False
    if artifact.get("status") != "resolved":
        return artifact.get("resolver_status") in {"no_safe_path", "ambiguous_path", "unsupported_depth", "invalid_graph_edge"}
    tables = [str(table) for table in artifact.get("ordered_tables") or []]
    edges = [edge for edge in artifact.get("ordered_edges") or [] if isinstance(edge, dict)]
    if not _valid_ordered_path(tables, edges, schema, graph):
        return False
    return len(edges) <= max_depth


def _valid_path_header(artifact: Any, identity: CacheIdentity) -> bool:
    return bool(
        isinstance(artifact, dict)
        and artifact.get("artifact_type") == RELATIONSHIP_PATH_ARTIFACT_TYPE
        and artifact.get("artifact_version") == RELATIONSHIP_PATH_ARTIFACT_VERSION
        and artifact.get("policy_version") == RELATIONSHIP_PATH_POLICY_VERSION
        and artifact.get("graph_fingerprint") == identity.graph_fingerprint
        and artifact.get("path_source") == "relationship_graph"
        and artifact.get("ambiguity_status") in {"resolved", "ambiguous", "none"}
    )


def _valid_grain_artifact(
    artifact: Any,
    identity: CacheIdentity,
    schema: dict[str, Any],
    graph: dict[str, dict[str, Any]],
    selected_path: dict[str, Any],
    metric_base_table: str,
    metric_column: str,
    dimension_table: str,
    dimension_column: str,
    aggregate_function: str,
    count_base_table: str,
) -> bool:
    if not isinstance(artifact, dict) or not isinstance(artifact.get("result"), dict):
        return False
    if artifact.get("artifact_type") != GRAIN_ANALYSIS_ARTIFACT_TYPE:
        return False
    if artifact.get("artifact_version") != GRAIN_ANALYSIS_ARTIFACT_VERSION:
        return False
    if artifact.get("policy_version") != GRAIN_ANALYSIS_POLICY_VERSION:
        return False
    if artifact.get("graph_fingerprint") != identity.graph_fingerprint:
        return False
    if artifact.get("selected_path_identity") != _selected_path_identity(selected_path):
        return False
    if artifact.get("metric_base_table") != metric_base_table or metric_base_table not in schema:
        return False
    if artifact.get("metric_column") != metric_column or (metric_column and metric_column not in _schema_columns(schema, metric_base_table)):
        return False
    if artifact.get("dimension_table") != dimension_table or artifact.get("dimension_column") != dimension_column:
        return False
    if dimension_table and dimension_column and dimension_column not in _schema_columns(schema, dimension_table):
        return False
    if artifact.get("aggregate_function") != aggregate_function or aggregate_function not in SUPPORTED_AGGREGATES:
        return False
    if artifact.get("count_base_table") != count_base_table:
        return False
    if aggregate_function == "count" and artifact.get("count_base_valid") is not True:
        return False
    result = artifact["result"]
    statuses = {"grain_preserved", "row_multiplication_risk", "unknown_cardinality", "unsupported_bridge_path"}
    if result.get("status") not in statuses or result.get("grain_preserved") != (result.get("status") == "grain_preserved"):
        return False
    tables = [str(selected_path.get("base_table") or ""), *[str(value) for value in selected_path.get("joined_tables") or []]]
    edges = [dict(edge) for edge in selected_path.get("edges") or [] if isinstance(edge, dict)]
    return _valid_ordered_path(tables, edges, schema, graph)


def _valid_ordered_path(
    tables: list[str],
    edges: list[dict[str, Any]],
    schema: dict[str, Any],
    graph: dict[str, dict[str, Any]],
) -> bool:
    if len(edges) not in {1, 2} or len(tables) != len(edges) + 1 or len(set(tables)) != len(tables):
        return False
    if any(table not in schema for table in tables):
        return False
    for index, edge in enumerate(edges):
        if str(edge.get("from_table") or "") != tables[index] or str(edge.get("to_table") or "") != tables[index + 1]:
            return False
        if edge.get("relationship_type") and str(edge.get("relationship_type")).lower() not in ALLOWED_RELATIONSHIP_TYPES:
            return False
        if not _schema_edge_exists(schema, edge) or not _graph_edge_exists(graph, edge):
            return False
    return True


def _artifact_to_bfs_result(artifact: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": artifact.get("resolver_status"),
        "resolved": artifact.get("resolver_status") == "unique_safe_path",
        "reason": artifact.get("reason"),
        "path": [dict(edge) for edge in artifact.get("ordered_edges") or []],
        "tables": [str(table) for table in artifact.get("ordered_tables") or []],
        "edge_count": len(artifact.get("ordered_edges") or []),
        "path_source": "relationship_graph",
    }


def _artifact_edge(edge: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "from_table",
        "from_column",
        "to_table",
        "to_column",
        "authoritative_from_table",
        "authoritative_from_column",
        "authoritative_to_table",
        "authoritative_to_column",
        "relationship_type",
        "source",
        "confidence",
        "safe_for_planner",
        "is_inferred",
        "is_fallback",
        "reason",
        "evidence",
        "evidence_reasons",
    }
    return {key: _json_safe(edge.get(key)) for key in sorted(allowed) if key in edge}


def _selected_path_identity(path: dict[str, Any]) -> dict[str, Any]:
    return {
        "base_table": str((path or {}).get("base_table") or ""),
        "joined_tables": [str(value) for value in (path or {}).get("joined_tables") or []],
        "edges": [_artifact_edge(dict(edge)) for edge in (path or {}).get("edges") or [] if isinstance(edge, dict)],
        "path_source": str((path or {}).get("path_source") or ""),
        "ambiguity_status": str((path or {}).get("ambiguity_status") or ""),
    }


def _edge_identity(edge: dict[str, Any]) -> dict[str, Any]:
    return {
        "from_table": str(edge.get("from_table") or ""),
        "from_column": str(edge.get("from_column") or ""),
        "to_table": str(edge.get("to_table") or ""),
        "to_column": str(edge.get("to_column") or ""),
        "authoritative_from_table": str(edge.get("authoritative_from_table") or ""),
        "authoritative_from_column": str(edge.get("authoritative_from_column") or ""),
        "authoritative_to_table": str(edge.get("authoritative_to_table") or ""),
        "authoritative_to_column": str(edge.get("authoritative_to_column") or ""),
        "relationship_type": str(edge.get("relationship_type") or ""),
        "source": str(edge.get("source") or ""),
        "safe_for_planner": edge.get("safe_for_planner") is True,
        "is_inferred": bool(edge.get("is_inferred")),
        "is_fallback": bool(edge.get("is_fallback")),
    }


def _schema_edge_exists(schema: dict[str, Any], edge: dict[str, Any]) -> bool:
    return (
        str(edge.get("from_column") or "") in _schema_columns(schema, str(edge.get("from_table") or ""))
        and str(edge.get("to_column") or "") in _schema_columns(schema, str(edge.get("to_table") or ""))
    )


def _graph_edge_exists(graph: dict[str, dict[str, Any]], edge: dict[str, Any]) -> bool:
    for candidate in graph.get(str(edge.get("from_table") or ""), {}).get("edges", []) or []:
        if (
            str(candidate.get("to_table") or "") == str(edge.get("to_table") or "")
            and str(candidate.get("from_column") or "") == str(edge.get("from_column") or "")
            and str(candidate.get("to_column") or "") == str(edge.get("to_column") or "")
            and candidate.get("safe_for_planner") is True
            and str(candidate.get("relationship_type") or "").lower() in ALLOWED_RELATIONSHIP_TYPES
        ):
            return True
    return False


def _schema_columns(schema: dict[str, Any], table_name: str) -> set[str]:
    return {str(column.get("name") or "") for column in schema.get(table_name, {}).get("columns", []) or []}


def _safe_cache_set(cache_store: CacheStore | None, key: Any, artifact_version: str, artifact: dict[str, Any]) -> None:
    if cache_store is None:
        return
    try:
        cache_store.set(key, artifact_version=artifact_version, value=artifact)
    except Exception:
        return


def _digest(value: Any) -> str:
    return hashlib.sha256(_stable_json(value).encode("utf-8")).hexdigest()


def _stable_json(value: Any) -> str:
    return json.dumps(_json_safe(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(value[key]) for key in sorted(value)}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return str(value)
