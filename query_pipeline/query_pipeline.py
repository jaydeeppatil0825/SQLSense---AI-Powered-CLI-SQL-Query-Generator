"""
core/query_pipeline.py
======================
Query Planning Pipeline entry point.

This module belongs to the User Question Understanding pipeline. It
normalizes the question, builds intent, retrieves dynamic KB evidence,
previews the query plan, and passes that structured context into the SQL
generation pipeline.

It must not generate SQL directly.
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
    formula_evidence: list[Dict[str, Any]]
    evidence_sources: list[str]

    def to_process_tuple(self) -> tuple[bool, str, Optional[str], Optional[str]]:
        """Return the legacy tuple used by the CLI service layer."""
        return self.success, self.message, self.sql, self.error

    def to_dict(self) -> Dict[str, Any]:
        """Serialize the pipeline result for debugging or tests."""
        return asdict(self)


class QueryPipeline:
    """Stage-oriented planning wrapper that passes evidence into SQL generation."""

    def __init__(self, question_service: Any):
        self.question_service = question_service

    def run(
        self,
        question: str,
        knowledge_base: Dict[str, Any],
        business_glossary: Optional[Dict[str, Any]] = None,
        vector_retriever: Optional[Any] = None,
        ai_backend: str = "local",
    ) -> QueryPipelineResult:
        normalized_question, _ = normalize_question(question)
        intent = build_intent(normalized_question, ai_backend=ai_backend)
        retrieved_context = retrieve_context(
            normalized_question,
            intent,
            knowledge_base,
            business_glossary=business_glossary,
            vector_retriever=vector_retriever,
        )
        preview_context = self._build_context_preview(
            normalized_question,
            knowledge_base,
            intent=intent,
            retrieved_context=retrieved_context,
            business_glossary=business_glossary,
            vector_retriever=vector_retriever,
        )
        formula_evidence = self._extract_formula_evidence(preview_context, retrieved_context)
        evidence_sources = self._extract_evidence_sources(preview_context, retrieved_context)
        pipeline_context = {
            "question": question,
            "normalized_question": normalized_question,
            "intent": intent,
            "retrieved_context": retrieved_context,
            "query_context": preview_context,
            "plan": dict(preview_context.get("plan") or {}),
            "route_recommendation": preview_context.get("route_recommendation"),
            "formula_evidence": formula_evidence,
            "evidence_sources": evidence_sources,
        }

        success, message, sql, error = self.question_service.process_question(
            question=question,
            knowledge_base=knowledge_base,
            business_glossary=business_glossary,
            vector_retriever=vector_retriever,
            ai_backend=ai_backend,
            pipeline_context=pipeline_context,
        )

        final_context = self.question_service.get_last_query_context() or preview_context
        validation_result = self._build_validation_result(sql, final_context, knowledge_base, error, message)

        return QueryPipelineResult(
            success=success,
            message=message,
            sql=sql,
            error=error,
            normalized_question=normalized_question,
            intent=intent,
            retrieved_context=retrieved_context,
            plan=dict((final_context or preview_context or {}).get("plan") or {}),
            generated_sql=sql,
            validation_result=validation_result,
            route=(final_context or {}).get("route_used"),
            route_recommendation=(final_context or preview_context or {}).get("route_recommendation"),
            formula_evidence=formula_evidence,
            evidence_sources=evidence_sources,
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
                "plan": {"question": question},
                "selected_table_names": [],
                "selected_columns": [],
                "selected_tables": [],
                "join_paths": [],
                "vector_results": {},
                "pipeline_preview_error": str(exc),
            }

    def _build_validation_result(
        self,
        sql: Optional[str],
        query_context: Dict[str, Any],
        knowledge_base: Dict[str, Any],
        error: Optional[str],
        message: str,
    ) -> Dict[str, Any]:
        if not sql:
            return {
                "is_valid": False,
                "reason": error or message,
            }

        scoped_knowledge_base = query_context.get("selected_knowledge_base") or knowledge_base
        is_valid, reason = self.question_service.validate_sql(sql, scoped_knowledge_base)
        return {
            "is_valid": is_valid,
            "reason": reason,
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
