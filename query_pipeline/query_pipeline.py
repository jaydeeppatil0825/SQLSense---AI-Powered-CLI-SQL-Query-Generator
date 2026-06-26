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
from typing import Any, Dict, Optional

from query_pipeline.context_retriever import retrieve_context
from query_pipeline.intent_builder import build_intent
from query_pipeline.query_planner import build_query_context
from query_pipeline.question_normalizer import normalize_question


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
    route_reason: str
    can_plan: bool

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
            "complex_sql_plan": dict(self.complex_sql_plan or {}),
            "formula_evidence": list(self.formula_evidence or []),
            "evidence_sources": list(self.evidence_sources or []),
        }

    def to_dict(self) -> Dict[str, Any]:
        """Serialize the pipeline result for debugging or tests."""
        return asdict(self)


class QueryPipeline:
    """Stage-oriented planning wrapper that returns deterministic planner output."""

    def __init__(self, question_service: Any | None = None):
        # Retained for backward compatibility with existing constructors.
        self.question_service = question_service

    def run(
        self,
        question: str,
        knowledge_base: Dict[str, Any],
        business_glossary: Optional[Dict[str, Any]] = None,
        vector_retriever: Optional[Any] = None,
        ai_backend: str = "local",
    ) -> QueryPipelineResult:
        # ai_backend is accepted only for backward compatibility.
        # Query pipeline must not call AI or SQL runtime orchestration.
        del ai_backend

        normalized_question, _ = normalize_question(question)
        intent = build_intent(normalized_question)
        retrieved_context = retrieve_context(
            normalized_question,
            intent,
            knowledge_base,
            business_glossary=business_glossary,
            vector_retriever=vector_retriever,
        )
        query_context = self._build_context_preview(
            normalized_question,
            knowledge_base,
            intent=intent,
            retrieved_context=retrieved_context,
            business_glossary=business_glossary,
            vector_retriever=vector_retriever,
        )
        formula_evidence = self._extract_formula_evidence(query_context, retrieved_context)
        evidence_sources = self._extract_evidence_sources(query_context, retrieved_context)

        route_recommendation = str(query_context.get("route_recommendation") or "").strip()
        route_reason = str(query_context.get("route_reason") or "").strip()
        query_shape = str(query_context.get("query_shape") or "unknown").strip()
        can_plan = bool(query_context.get("can_plan"))
        success = route_recommendation == "deterministic_sql_required"
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
            route_reason=route_reason,
            can_plan=can_plan,
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
    ) -> Dict[str, Any]:
        try:
            return build_query_context(
                question,
                knowledge_base,
                business_glossary,
                vector_retriever=vector_retriever,
                intent=intent,
                retrieved_context=retrieved_context,
            )
        except Exception as exc:
            return {
                "normalized_question": question,
                "intent": dict(intent or {}),
                "query_shape": "unknown",
                "route_recommendation": "cannot_plan_safely",
                "route_reason": "query pipeline could not build planner context",
                "selected_table_names": [],
                "selected_columns": [],
                "selected_tables": [],
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
