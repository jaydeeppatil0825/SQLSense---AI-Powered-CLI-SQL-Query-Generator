"""
core/query_pipeline.py
======================
Query Planning Pipeline entry point.

This module belongs to the User Question Understanding pipeline. It
normalizes the question, builds intent, retrieves dynamic KB evidence,
builds the planner context, and returns planner output only.

It must not generate SQL directly or call SQL runtime orchestration.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
import os
from typing import Any, Dict, Optional

from infrastructure.observability.events import event as observe_event, timed_stage
from query_pipeline.intent_builder import build_intent
from query_pipeline.planner.phase9c_cache import (
    cached_planner_evidence_summary,
    cached_retrieve_context as _cached_retrieve_context,
)
from query_pipeline.query_planner import build_query_context
from query_pipeline.question_normalizer import normalize_question


RETRIEVAL_CONTRACT_VERSION = "phase3c-retrieval-v1"
PLANNER_INPUT_CONTRACT_VERSION = "phase3c-planner-input-v1"
PIPELINE_RESULT_CONTRACT_VERSION = "phase3c-pipeline-result-v1"


def retrieve_context(
    *,
    cache_store: Any | None,
    database_identity: dict[str, Any] | None,
    normalized_question: str,
    intent: dict[str, Any],
    knowledge_base: dict[str, Any],
    business_glossary: dict[str, Any] | None,
    vector_retriever: Any | None,
    require_normalized_vector_evidence: bool,
) -> dict[str, Any]:
    """Official retrieval boundary for QueryPipeline callers and tests.

    Caching is an implementation detail of the retrieval boundary. Callers get
    the same evidence contract for cache hit, miss, disabled, or fallback.
    """
    return _cached_retrieve_context(
        cache_store=cache_store,
        database_identity=database_identity,
        normalized_question=normalized_question,
        intent=intent,
        knowledge_base=knowledge_base,
        business_glossary=business_glossary,
        vector_retriever=vector_retriever,
        require_normalized_vector_evidence=require_normalized_vector_evidence,
    )


def cached_retrieve_context(**kwargs: Any) -> dict[str, Any]:
    """Compatibility wrapper; use retrieve_context for new callers."""
    return retrieve_context(**kwargs)


@dataclass
class QueryPipelineResult:
    """Structured result returned by the query pipeline."""

    success: bool
    message: str
    sql: Optional[str]
    error: Optional[str]
    normalized_question: str
    intent: Dict[str, Any]
    retrieved_context: Dict[str, Any]
    plan: Dict[str, Any]
    generated_sql: Optional[str]
    validation_result: Dict[str, Any]
    route: Optional[str]
    route_recommendation: Optional[str]
    complex_sql_plan: Optional[Dict[str, Any]]
    formula_evidence: list[Dict[str, Any]]
    evidence_sources: list[str]
    query_context: Dict[str, Any]
    query_shape: str
    clause_plan: Dict[str, Any]
    route_reason: str
    can_plan: bool
    retrieval_contract_version: str
    planner_input_contract_version: str
    pipeline_result_contract_version: str

    def to_process_tuple(self) -> tuple[bool, str, Optional[str], Optional[str]]:
        """Return a legacy-compatible tuple without performing SQL generation."""
        return self.success, self.message, self.sql, self.error

    def to_pipeline_context(self) -> Dict[str, Any]:
        """Build the context payload consumed later by QuestionService."""
        return {
            "question": self.query_context.get("plan", {}).get("question") or self.normalized_question,
            "normalized_question": self.normalized_question,
            "intent": dict(self.intent or {}),
            "retrieved_context": dict(self.retrieved_context or {}),
            "query_context": dict(self.query_context or {}),
            "plan": dict(self.plan or {}),
            "route_recommendation": self.route_recommendation,
            "clause_plan": dict(self.clause_plan or {}),
            "complex_sql_plan": dict(self.complex_sql_plan or {}),
            "formula_evidence": list(self.formula_evidence or []),
            "evidence_sources": list(self.evidence_sources or []),
            "retrieval_contract_version": self.retrieval_contract_version,
            "planner_input_contract_version": self.planner_input_contract_version,
            "pipeline_result_contract_version": self.pipeline_result_contract_version,
        }

    def to_dict(self) -> Dict[str, Any]:
        """Serialize the pipeline result for debugging or tests."""
        return asdict(self)


class QueryPipeline:
    """Stage-oriented planning wrapper that returns deterministic planner output."""

    def __init__(self, question_service: Any | None = None, retrieval_provider: Any | None = None):
        # Retained for backward compatibility with existing constructors.
        self.question_service = question_service
        self.retrieval_provider = retrieval_provider

    def run(
        self,
        question: str,
        knowledge_base: Dict[str, Any],
        business_glossary: Optional[Dict[str, Any]] = None,
        vector_retriever: Optional[Any] = None,
        ai_backend: str = "local",
        cache_store: Any | None = None,
        cache_database_identity: dict[str, Any] | None = None,
    ) -> QueryPipelineResult:
        # ai_backend is accepted only for backward compatibility.
        # Query pipeline must not call AI or SQL runtime orchestration.
        del ai_backend

        with timed_stage("question_normalized", component="query_pipeline", stage="question.normalize", span_name="question.normalize"):
            normalized_question, _ = normalize_question(question)
        with timed_stage("intent_completed", component="query_pipeline", stage="intent.build", span_name="intent.build") as obs:
            intent = build_intent(normalized_question, today=_fixed_today_from_env())
            obs["intent_type"] = str(intent.get("intent_type") or "")
            obs["query_shape"] = str(intent.get("query_shape") or intent.get("intent_shape") or "")
        retrieval_provider = self.retrieval_provider or retrieve_context
        with timed_stage("retrieval_completed", component="query_pipeline", stage="context.retrieve", span_name="context.retrieve") as obs:
            retrieved_context = retrieval_provider(
                cache_store=cache_store,
                database_identity=cache_database_identity,
                normalized_question=normalized_question,
                intent=intent,
                knowledge_base=knowledge_base,
                business_glossary=business_glossary,
                vector_retriever=vector_retriever,
                require_normalized_vector_evidence=True,
            )
            obs["table_candidate_count"] = len(retrieved_context.get("matched_tables") or [])
            obs["column_candidate_count"] = len(retrieved_context.get("matched_columns") or [])
        with timed_stage("planner_completed", component="query_pipeline", stage="planner.plan", span_name="planner.plan") as obs:
            query_context = self._build_context_preview(
                normalized_question,
                knowledge_base,
                intent=intent,
                retrieved_context=retrieved_context,
                business_glossary=business_glossary,
                vector_retriever=vector_retriever,
                cache_store=cache_store,
                cache_database_identity=cache_database_identity,
            )
            obs["route"] = str(query_context.get("route_recommendation") or "")
            obs["query_shape"] = str(query_context.get("query_shape") or "")
        cached_planner_evidence_summary(
            cache_store=cache_store,
            database_identity=cache_database_identity,
            normalized_question=normalized_question,
            knowledge_base=knowledge_base,
            business_glossary=business_glossary,
            vector_retriever=vector_retriever,
            retrieved_context=retrieved_context,
            query_context=query_context,
        )
        formula_evidence = self._extract_formula_evidence(query_context, retrieved_context)
        evidence_sources = self._extract_evidence_sources(query_context, retrieved_context)

        route_recommendation = str(query_context.get("route_recommendation") or "").strip()
        route_reason = str(query_context.get("route_reason") or "").strip()
        query_shape = str(query_context.get("query_shape") or "unknown").strip()
        can_plan = bool(query_context.get("can_plan"))
        success = route_recommendation == "deterministic_sql_required"
        observe_event(
            "planner_completed" if success else "planner_failed_closed",
            component="query_pipeline",
            stage="planner.route",
            status="success" if success else "blocked",
            reason_code=route_reason,
            route=route_recommendation,
            query_shape=query_shape,
        )
        message = self._pipeline_message(route_recommendation, route_reason)
        error = None if success else message

        return QueryPipelineResult(
            success=success,
            message=message,
            sql=None,
            error=error,
            normalized_question=normalized_question,
            intent=intent,
            retrieved_context=retrieved_context,
            plan=dict(query_context.get("plan") or {}),
            generated_sql=None,
            validation_result={},
            route=route_recommendation,
            route_recommendation=route_recommendation,
            complex_sql_plan=dict(query_context.get("complex_sql_plan") or {}),
            formula_evidence=formula_evidence,
            evidence_sources=evidence_sources,
            query_context=query_context,
            query_shape=query_shape,
            clause_plan=dict(query_context.get("clause_plan") or {}),
            route_reason=route_reason,
            can_plan=can_plan,
            retrieval_contract_version=RETRIEVAL_CONTRACT_VERSION,
            planner_input_contract_version=PLANNER_INPUT_CONTRACT_VERSION,
            pipeline_result_contract_version=PIPELINE_RESULT_CONTRACT_VERSION,
        )

    def _build_context_preview(
        self,
        question: str,
        knowledge_base: Dict[str, Any],
        *,
        intent: Dict[str, Any],
        retrieved_context: Dict[str, Any],
        business_glossary: Optional[Dict[str, Any]],
        vector_retriever: Optional[Any],
        cache_store: Any | None,
        cache_database_identity: dict[str, Any] | None,
    ) -> Dict[str, Any]:
        try:
            return build_query_context(
                question,
                knowledge_base,
                business_glossary,
                vector_retriever=vector_retriever,
                intent=intent,
                retrieved_context=retrieved_context,
                cache_store=cache_store,
                cache_database_identity=cache_database_identity,
            )
        except Exception as exc:
            if str(os.getenv("SQLSENSE_DEBUG_RERAISE_PLANNER", "")).strip().lower() in {"1", "true", "yes", "on"}:
                raise
            return {
                "normalized_question": question,
                "intent": dict(intent or {}),
                "query_shape": "unknown",
                "clause_plan": {
                    "clause_shape": "unsupported",
                    "requires": {
                        "aggregate": False,
                        "metric": False,
                        "dimension": False,
                        "where": False,
                        "having": False,
                        "order_by": False,
                        "limit": False,
                    },
                    "decision_path": [
                        {"node": "unsafe_check", "status": "resolved", "reason": "request reached planner context construction"},
                        {"node": "table_scope", "status": "blocked", "reason": "planner context construction failed"},
                        {"node": "query_shape", "status": "blocked", "reason": "query shape could not be resolved"},
                        {"node": "aggregate", "status": "not_required", "reason": "query shape was unresolved"},
                        {"node": "metric", "status": "not_required", "reason": "query shape was unresolved"},
                        {"node": "dimension", "status": "not_required", "reason": "query shape was unresolved"},
                        {"node": "where", "status": "not_required", "reason": "query shape was unresolved"},
                        {"node": "having", "status": "not_required", "reason": "query shape was unresolved"},
                        {"node": "order_by", "status": "not_required", "reason": "query shape was unresolved"},
                        {"node": "limit", "status": "not_required", "reason": "query shape was unresolved"},
                        {"node": "clause_shape", "status": "blocked", "reason": "query shape was unresolved"},
                        {"node": "route", "status": "blocked", "reason": "planner context construction failed"},
                    ],
                },
                "route_recommendation": "cannot_plan_safely",
                "route_reason": "query pipeline could not build planner context",
                "selected_table_names": [],
                "selected_columns": [],
                "selected_tables": [],
                "selected_join_path": None,
                "selected_output_columns": [],
                "metric_candidates": [],
                "dimension_candidates": [],
                "filter_candidates": [],
                "join_candidates": [],
                "required_joins": [],
                "group_by_candidates": [],
                "order_by_candidates": [],
                "limit": None,
                "complex_sql_plan": {},
                "required_evidence": ["selected_table"],
                "missing_evidence": ["pipeline_preview_error"],
                "ambiguities": [],
                "can_plan": False,
                "debug_trace": [{"stage": "pipeline_preview_error", "value": str(exc)}],
                "vector_results": {},
                "vector_used": False,
                "plan": {"question": question},
                "join_paths": [],
                "warnings": [],
                "pipeline_preview_error": str(exc),
            }

    def _extract_formula_evidence(
        self,
        query_context: Dict[str, Any],
        retrieved_context: Dict[str, Any],
    ) -> list[Dict[str, Any]]:
        candidates = []
        for value in (
            query_context.get("formula_evidence"),
            (query_context.get("plan") or {}).get("formula_evidence"),
            retrieved_context.get("formula_evidence"),
        ):
            if isinstance(value, list):
                candidates.extend(entry for entry in value if isinstance(entry, dict))
        return candidates

    def _extract_evidence_sources(
        self,
        query_context: Dict[str, Any],
        retrieved_context: Dict[str, Any],
    ) -> list[str]:
        sources: list[str] = []
        for value in (
            query_context.get("evidence_sources"),
            (query_context.get("plan") or {}).get("evidence_sources"),
            retrieved_context.get("retrieval_sources"),
        ):
            if isinstance(value, list):
                sources.extend(str(entry).strip() for entry in value if str(entry).strip())
        deduped: list[str] = []
        seen = set()
        for source in sources:
            key = source.lower()
            if key in seen:
                continue
            seen.add(key)
            deduped.append(source)
        return deduped

    def _pipeline_message(self, route_recommendation: str, route_reason: str) -> str:
        if route_recommendation == "deterministic_sql_required":
            return "Query planned successfully for deterministic SQL generation."
        if route_recommendation == "blocked_unsafe":
            return "Unsafe request blocked before SQL generation."
        if route_reason:
            return f"Planner could not route the question safely: {route_reason}"
        return "Planner could not route the question safely."


def _fixed_today_from_env() -> date | None:
    try:
        return date.fromisoformat(os.getenv("SQLSENSE_FIXED_TODAY", "").strip())
    except ValueError:
        return None
