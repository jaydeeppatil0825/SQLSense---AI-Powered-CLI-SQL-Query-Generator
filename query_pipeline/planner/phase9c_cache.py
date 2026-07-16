"""Phase 9C cache helpers for retrieval and planner evidence only."""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Callable

from core.cache_service import CORRUPT, HIT, STALE, CacheIdentity, CacheStore, make_cache_key
from query_pipeline.context_retriever import retrieve_context
from query_pipeline.planner.phase9b_cache import kb_fingerprint, schema_fingerprint


RETRIEVAL_EVIDENCE_ARTIFACT_TYPE = "retrieval_evidence"
RETRIEVAL_EVIDENCE_ARTIFACT_VERSION = "phase9c-retrieval-evidence-v1"
RETRIEVAL_POLICY_VERSION = "phase9c-retrieval-policy-v1"
PLANNER_EVIDENCE_ARTIFACT_TYPE = "planner_evidence"
PLANNER_EVIDENCE_ARTIFACT_VERSION = "phase9c-planner-evidence-v1"
PLANNER_EVIDENCE_POLICY_VERSION = "phase9c-planner-evidence-policy-v1"
FORBIDDEN_AUTHORITY_KEYS = {
    "route",
    "route_recommendation",
    "route_used",
    "query_shape",
    "selected_join_path",
    "selected_relationship_path",
    "phase8a_grain_analysis",
    "generated_sql",
    "sql",
    "validation_result",
    "executor_authorization",
    "result_rows",
    "rows",
}


def cached_retrieve_context(
    *,
    cache_store: CacheStore | None,
    database_identity: dict[str, Any] | None,
    normalized_question: str,
    intent: dict[str, Any],
    knowledge_base: dict[str, Any],
    business_glossary: dict[str, Any] | None,
    vector_retriever: Any | None,
    require_normalized_vector_evidence: bool,
) -> dict[str, Any]:
    fingerprints = _fingerprints(
        database_identity=database_identity,
        knowledge_base=knowledge_base,
        business_glossary=business_glossary,
        vector_retriever=vector_retriever,
    )
    retrieval_options = _retrieval_options(require_normalized_vector_evidence=require_normalized_vector_evidence)
    key = _key(
        fingerprints=fingerprints,
        artifact_type=RETRIEVAL_EVIDENCE_ARTIFACT_TYPE,
        normalized_question=normalized_question,
        normalized_input={
            "normalized_question_hash": _digest(normalized_question),
            "intent_identity": _intent_identity(intent),
            "retrieval_options": retrieval_options,
            "retrieval_policy_version": RETRIEVAL_POLICY_VERSION,
        },
        planner_options=retrieval_options,
    )

    if cache_store is not None:
        try:
            read = cache_store.get(key, artifact_version=RETRIEVAL_EVIDENCE_ARTIFACT_VERSION)
            if read.state == HIT and _valid_retrieval_artifact(read.value, fingerprints, knowledge_base):
                return dict(read.value["payload"])
            if read.state in {HIT, STALE, CORRUPT}:
                cache_store.delete(key)
        except Exception:
            pass

    result = retrieve_context(
        normalized_question,
        intent,
        knowledge_base,
        business_glossary=business_glossary,
        vector_retriever=vector_retriever,
        require_normalized_vector_evidence=require_normalized_vector_evidence,
    )
    artifact = {
        "artifact_type": RETRIEVAL_EVIDENCE_ARTIFACT_TYPE,
        "artifact_version": RETRIEVAL_EVIDENCE_ARTIFACT_VERSION,
        "policy_version": RETRIEVAL_POLICY_VERSION,
        "fingerprints": fingerprints,
        "normalized_question_hash": _digest(normalized_question),
        "retrieval_options": retrieval_options,
        "payload": _json_safe(result),
    }
    _safe_cache_set(cache_store, key, RETRIEVAL_EVIDENCE_ARTIFACT_VERSION, artifact)
    return result


def cached_planner_evidence_summary(
    *,
    cache_store: CacheStore | None,
    database_identity: dict[str, Any] | None,
    normalized_question: str,
    knowledge_base: dict[str, Any],
    business_glossary: dict[str, Any] | None,
    vector_retriever: Any | None,
    retrieved_context: dict[str, Any],
    query_context: dict[str, Any],
) -> dict[str, Any]:
    fingerprints = _fingerprints(
        database_identity=database_identity,
        knowledge_base=knowledge_base,
        business_glossary=business_glossary,
        vector_retriever=vector_retriever,
    )
    normalized_input = {
        "normalized_question_hash": _digest(normalized_question),
        "retrieved_context_fingerprint": _digest(retrieved_context),
        "planner_evidence_policy_version": PLANNER_EVIDENCE_POLICY_VERSION,
    }
    key = _key(
        fingerprints=fingerprints,
        artifact_type=PLANNER_EVIDENCE_ARTIFACT_TYPE,
        normalized_question=normalized_question,
        normalized_input=normalized_input,
        planner_options={"planner_evidence_policy_version": PLANNER_EVIDENCE_POLICY_VERSION},
    )

    if cache_store is not None:
        try:
            read = cache_store.get(key, artifact_version=PLANNER_EVIDENCE_ARTIFACT_VERSION)
            if read.state == HIT and _valid_planner_artifact(read.value, fingerprints, knowledge_base):
                return dict(read.value["payload"])
            if read.state in {HIT, STALE, CORRUPT}:
                cache_store.delete(key)
        except Exception:
            pass

    payload = _planner_evidence_payload(retrieved_context, query_context)
    artifact = {
        "artifact_type": PLANNER_EVIDENCE_ARTIFACT_TYPE,
        "artifact_version": PLANNER_EVIDENCE_ARTIFACT_VERSION,
        "policy_version": PLANNER_EVIDENCE_POLICY_VERSION,
        "fingerprints": fingerprints,
        "normalized_question_hash": _digest(normalized_question),
        "payload": payload,
    }
    _safe_cache_set(cache_store, key, PLANNER_EVIDENCE_ARTIFACT_VERSION, artifact)
    return payload


def _fingerprints(
    *,
    database_identity: dict[str, Any] | None,
    knowledge_base: dict[str, Any],
    business_glossary: dict[str, Any] | None,
    vector_retriever: Any | None,
) -> dict[str, Any]:
    vector_status = _vector_status(vector_retriever)
    embedding = vector_status.get("embedding") if isinstance(vector_status.get("embedding"), dict) else {}
    return {
        "database": _database_identity(database_identity),
        "schema": schema_fingerprint(knowledge_base),
        "kb": kb_fingerprint(knowledge_base),
        "data_profile": _data_profile_fingerprint(knowledge_base),
        "vector_index": _digest(vector_status),
        "embedding_model": _digest(embedding),
        "glossary": _digest(business_glossary or {}),
        "retrieval_policy": RETRIEVAL_POLICY_VERSION,
        "planner_evidence_policy": PLANNER_EVIDENCE_POLICY_VERSION,
    }


def _database_identity(database_identity: dict[str, Any] | None) -> dict[str, str]:
    db = database_identity or {}
    return {
        "db_engine": str(db.get("db_engine") or db.get("database_type") or ""),
        "db_host": str(db.get("db_host") or ""),
        "db_port": str(db.get("db_port") or ""),
        "db_name": str(db.get("db_name") or db.get("database_name") or ""),
    }


def _key(
    *,
    fingerprints: dict[str, Any],
    artifact_type: str,
    normalized_question: str,
    normalized_input: dict[str, Any],
    planner_options: dict[str, Any],
) -> Any:
    db = fingerprints["database"]
    identity = CacheIdentity(
        db_engine=db["db_engine"],
        db_host=db["db_host"],
        db_port=db["db_port"],
        db_name=db["db_name"],
        schema_hash=str(fingerprints["schema"]),
        kb_fingerprint=str(fingerprints["kb"]),
        graph_fingerprint=str(fingerprints["vector_index"]),
    )
    return make_cache_key(
        identity=identity,
        artifact_type=artifact_type,
        normalized_input={
            **normalized_input,
            "fingerprints": fingerprints,
            "normalized_question_hash": _digest(normalized_question),
        },
        planner_options=planner_options,
    )


def _retrieval_options(*, require_normalized_vector_evidence: bool) -> dict[str, Any]:
    return {
        "require_normalized_vector_evidence": bool(require_normalized_vector_evidence),
        "normalized_evidence_top_k": 8,
        "fallback_table_top_k": 6,
        "fallback_column_top_k": 10,
        "fallback_glossary_top_k": 6,
        "fallback_relationship_top_k": 6,
        "supplemental_column_top_k": 4,
        "metadata_filters": ["table", "column", "relationship", "glossary"],
    }


def _intent_identity(intent: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "intent_type",
        "requested_metrics",
        "requested_dimensions",
        "requested_filters",
        "requested_output_fields",
        "structured_filters",
        "join_lookup_request",
        "requested_sort",
    )
    return {key: _json_safe((intent or {}).get(key)) for key in keys if key in (intent or {})}


def _planner_evidence_payload(retrieved_context: dict[str, Any], query_context: dict[str, Any]) -> dict[str, Any]:
    return _json_safe(
        {
            "matched_tables": retrieved_context.get("matched_tables", []),
            "matched_columns": retrieved_context.get("matched_columns", []),
            "metric_candidates": query_context.get("metric_candidates", []),
            "dimension_candidates": query_context.get("dimension_candidates", []),
            "date_candidates": retrieved_context.get("date_candidates", []),
            "filter_candidates": query_context.get("filter_candidates", []),
            "join_candidates": query_context.get("join_candidates", []),
            "group_by_candidates": query_context.get("group_by_candidates", []),
            "order_by_candidates": query_context.get("order_by_candidates", []),
            "evidence_summary": query_context.get("evidence_summary", {}),
            "evidence_scores": retrieved_context.get("evidence_scores", {}),
            "ambiguity_candidates": retrieved_context.get("ambiguity_candidates", {}),
            "ambiguity_details": query_context.get("ambiguity_details", []),
            "missing_evidence_indicators": retrieved_context.get("missing_evidence_indicators", {}),
            "missing_evidence_flags": query_context.get("missing_evidence_flags", {}),
        }
    )


def _valid_retrieval_artifact(artifact: Any, fingerprints: dict[str, Any], schema: dict[str, Any]) -> bool:
    return bool(
        isinstance(artifact, dict)
        and artifact.get("artifact_type") == RETRIEVAL_EVIDENCE_ARTIFACT_TYPE
        and artifact.get("artifact_version") == RETRIEVAL_EVIDENCE_ARTIFACT_VERSION
        and artifact.get("policy_version") == RETRIEVAL_POLICY_VERSION
        and artifact.get("fingerprints") == fingerprints
        and isinstance(artifact.get("payload"), dict)
        and _valid_evidence_payload(artifact["payload"], schema)
    )


def _valid_planner_artifact(artifact: Any, fingerprints: dict[str, Any], schema: dict[str, Any]) -> bool:
    return bool(
        isinstance(artifact, dict)
        and artifact.get("artifact_type") == PLANNER_EVIDENCE_ARTIFACT_TYPE
        and artifact.get("artifact_version") == PLANNER_EVIDENCE_ARTIFACT_VERSION
        and artifact.get("policy_version") == PLANNER_EVIDENCE_POLICY_VERSION
        and artifact.get("fingerprints") == fingerprints
        and isinstance(artifact.get("payload"), dict)
        and _valid_evidence_payload(artifact["payload"], schema)
    )


def _valid_evidence_payload(payload: dict[str, Any], schema: dict[str, Any]) -> bool:
    if _has_forbidden_key(payload):
        return False
    if not _valid_scores(payload):
        return False
    for key in ("matched_tables",):
        for entry in payload.get(key, []) or []:
            if str(entry.get("table") or "") not in schema:
                return False
    for key in (
        "matched_columns",
        "metric_candidates",
        "dimension_candidates",
        "date_candidates",
        "filter_candidates",
        "group_by_candidates",
        "order_by_candidates",
    ):
        for entry in payload.get(key, []) or []:
            if isinstance(entry, dict) and entry.get("table") and entry.get("column"):
                table = str(entry.get("table") or "")
                column = str(entry.get("column") or "")
                if column not in _schema_columns(schema, table):
                    return False
                if key == "metric_candidates" and not _numeric_eligible(schema, table, column):
                    return False
                if key == "date_candidates" and not _date_eligible(schema, table, column):
                    return False
                if key == "dimension_candidates" and not _dimension_eligible(schema, table, column):
                    return False
    for entry in payload.get("possible_join_paths", []) or []:
        if isinstance(entry, dict) and entry.get("path_source") == "relationship_graph_authority":
            return False
        for edge in (entry.get("path") or []) if isinstance(entry, dict) else []:
            if not _valid_edge_reference(edge, schema):
                return False
    for entry in payload.get("matched_relationships", []) or []:
        if isinstance(entry, dict) and not _valid_edge_reference(entry, schema):
            return False
    return True


def _has_forbidden_key(value: Any) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key) in FORBIDDEN_AUTHORITY_KEYS:
                return True
            if _has_forbidden_key(item):
                return True
    if isinstance(value, list):
        return any(_has_forbidden_key(item) for item in value)
    return False


def _valid_scores(value: Any) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            key_name = str(key).lower()
            if key_name in {"score", "confidence", "support_score", "total_confidence"}:
                try:
                    number = float(item)
                except (TypeError, ValueError):
                    return False
                if not math.isfinite(number) or number < 0.0:
                    return False
                if key_name != "support_score" and number > 1.0:
                    return False
            if not _valid_scores(item):
                return False
    if isinstance(value, list):
        return all(_valid_scores(item) for item in value)
    return True


def _schema_columns(schema: dict[str, Any], table: str) -> set[str]:
    return {str(column.get("name") or "") for column in schema.get(table, {}).get("columns", []) or []}


def _valid_edge_reference(edge: Any, schema: dict[str, Any]) -> bool:
    if not isinstance(edge, dict):
        return False
    from_table = str(edge.get("from_table") or "")
    to_table = str(edge.get("to_table") or "")
    from_column = str(edge.get("from_column") or "")
    to_column = str(edge.get("to_column") or "")
    return bool(
        from_table in schema
        and to_table in schema
        and from_column in _schema_columns(schema, from_table)
        and to_column in _schema_columns(schema, to_table)
    )


def _column(schema: dict[str, Any], table: str, column: str) -> dict[str, Any]:
    for item in schema.get(table, {}).get("columns", []) or []:
        if str(item.get("name") or "") == column:
            return dict(item)
    return {}


def _numeric_eligible(schema: dict[str, Any], table: str, column: str) -> bool:
    item = _column(schema, table, column)
    semantic = str(item.get("semantic_type") or "").lower()
    data_type = str(item.get("type") or item.get("data_type") or "").lower()
    if semantic in {"id", "date", "boolean"}:
        return False
    return bool(
        item.get("is_measure")
        or semantic in {"money", "quantity", "percentage", "numeric_candidate"}
        or any(token in data_type for token in ("decimal", "numeric", "float", "double", "real", "int"))
    )


def _date_eligible(schema: dict[str, Any], table: str, column: str) -> bool:
    item = _column(schema, table, column)
    semantic = str(item.get("semantic_type") or "").lower()
    data_type = str(item.get("type") or item.get("data_type") or "").lower()
    return bool(item.get("is_date") or semantic == "date" or "date" in data_type or "time" in data_type)


def _dimension_eligible(schema: dict[str, Any], table: str, column: str) -> bool:
    item = _column(schema, table, column)
    semantic = str(item.get("semantic_type") or "").lower()
    if semantic in {"id", "boolean"}:
        return False
    return bool(item.get("is_dimension") or item.get("is_date") or semantic in {"name", "text", "status", "category_candidate", "text_candidate", "date"})


def _data_profile_fingerprint(knowledge_base: dict[str, Any]) -> str:
    return _digest(
        {
            table: [
                {
                    "name": column.get("name"),
                    "sample_values": column.get("sample_values", []),
                    "unique_count": column.get("unique_count"),
                    "null_count": column.get("null_count"),
                    "row_count": column.get("row_count"),
                }
                for column in data.get("columns", []) or []
            ]
            for table, data in (knowledge_base or {}).items()
        }
    )


def _vector_status(vector_retriever: Any | None) -> dict[str, Any]:
    if vector_retriever is None or not hasattr(vector_retriever, "get_status"):
        return {"backend": "none"}
    try:
        status = vector_retriever.get_status()
    except Exception:
        return {"backend": "unavailable"}
    return status if isinstance(status, dict) else {"backend": str(status)}


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
