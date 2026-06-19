"""
core/query_pipeline.py
======================
Thin orchestration layer for question-to-SQL processing.

The pipeline keeps the current planner/generator/validator behavior intact
while exposing one structured entry point with stage-level debug output.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, Optional

from core.context_retriever import retrieve_context
from core.intent_builder import build_intent
from core.query_planner import build_query_context
from utils.question_normalizer import normalize_question


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

    def to_process_tuple(self) -> tuple[bool, str, Optional[str], Optional[str]]:
        """Return the legacy tuple used by the CLI service layer."""
        return self.success, self.message, self.sql, self.error

    def to_dict(self) -> Dict[str, Any]:
        """Serialize the pipeline result for debugging or tests."""
        return asdict(self)


class QueryPipeline:
    """Stage-oriented wrapper around the existing question service flow."""

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

        success, message, sql, error = self.question_service.process_question(
            question=question,
            knowledge_base=knowledge_base,
            business_glossary=business_glossary,
            vector_retriever=vector_retriever,
            ai_backend=ai_backend,
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
