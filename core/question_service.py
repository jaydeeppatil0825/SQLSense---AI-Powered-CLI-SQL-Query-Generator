"""
core/question_service.py
========================
Question service for SQL generation.

This service handles CLI question processing, SQL generation,
and validation.
"""

from typing import Optional, Tuple, Dict, Any
import re

from core.query_planner import build_query_context
from ai.simple_query_generator import generate_simple_sql
from ai.sql_generator import generate_sql, generate_sql_with_retry
from utils.question_normalizer import normalize_question, is_too_ambiguous
from utils.sql_validator import validate_sql, validate_sql_structure, add_limit_if_missing
from conversation.followup_detector import detect_follow_up
from conversation.question_rewriter import rewrite_follow_up_question
from conversation.action_detector import detect_conversation_action
from conversation.conversation_memory import ConversationMemory
from utils.logger import get_logger
from vector_store import VectorRetriever

logger = get_logger()

_UNSAFE_NL_RE = re.compile(
    r"\b(delete|drop|update|insert|alter|truncate|create|remove|destroy)\b",
    re.IGNORECASE,
)
_GENERIC_SELECT_RE = re.compile(
    r"^\s*SELECT\s+\*\s+FROM\s+[A-Za-z_][A-Za-z0-9_]*"
    r"(?:\s+(?:AS\s+)?[A-Za-z_][A-Za-z0-9_]*)?"
    r"(?:\s+WHERE\b.*)?"
    r"(?:\s+ORDER\s+BY\b.*)?"
    r"(?:\s+LIMIT\s+\d+\s*)?;?\s*$",
    re.IGNORECASE | re.DOTALL,
)


def _looks_like_generic_select(sql: str) -> bool:
    return bool(_GENERIC_SELECT_RE.match(str(sql or "").strip()))


def _estimate_generation_confidence(sql: str, query_context: dict[str, Any]) -> tuple[float, list[str], str]:
    if _looks_like_generic_select(sql):
        return (
            0.35,
            ["Could not generate a business-specific SQL query. Please review selected table or rebuild knowledge base."],
            "generic_fallback",
        )

    sql_upper = sql.upper()
    confidence = max(float(query_context.get("confidence") or 0.55), 0.55)
    generation_type = "specific_select"
    warnings: list[str] = []

    if "SUM(" in sql_upper or "COUNT(" in sql_upper or "AVG(" in sql_upper:
        confidence = max(confidence, 0.88)
        generation_type = "aggregated_business_sql"
    elif "GROUP BY" in sql_upper or "JOIN " in sql_upper:
        confidence = max(confidence, 0.82)
        generation_type = "joined_business_sql"
    elif " WHERE " in sql_upper:
        confidence = max(confidence, 0.72)

    return round(min(confidence, 0.99), 2), warnings, generation_type


def _attach_generation_feedback(query_context: dict[str, Any], sql: str) -> None:
    generation_confidence, extra_warnings, generation_type = _estimate_generation_confidence(sql, query_context)
    query_context["generation_confidence"] = generation_confidence
    query_context["generation_type"] = generation_type
    warnings = list(query_context.get("warnings") or [])
    for warning in extra_warnings:
        if warning not in warnings:
            warnings.append(warning)
    query_context["warnings"] = warnings


def _is_business_question(query_context: dict[str, Any]) -> bool:
    plan = query_context.get("plan") or {}
    if plan.get("metric") or plan.get("dimension") or plan.get("filters") or plan.get("date_range"):
        return True
    if plan.get("intent") in {"total", "average", "top_n", "trend", "comparison", "pending_outstanding", "low_stock"}:
        return True
    if len(query_context.get("selected_table_names") or []) > 1:
        return True
    return False


def _should_prefer_ai(query_context: dict[str, Any]) -> bool:
    plan = query_context.get("plan") or {}
    if _is_business_question(query_context):
        return True
    if plan.get("intent") == "count":
        return False
    if plan.get("intent") == "list" and not plan.get("filters") and not plan.get("grouping"):
        return False
    return False


def _validate_business_sql_fit(sql: str, query_context: dict[str, Any]) -> tuple[bool, str]:
    if not _is_business_question(query_context):
        return True, "Not a business-specific question."

    plan = query_context.get("plan") or {}
    sql_upper = str(sql or "").upper()

    if _looks_like_generic_select(sql):
        return False, "Generic SELECT * is not acceptable for this business question."

    intent = plan.get("intent")
    if intent == "total" and "SUM(" not in sql_upper:
        return False, "Total questions must use SUM()."
    if intent == "count" and "COUNT(" not in sql_upper:
        return False, "Count questions must use COUNT()."
    if intent == "average" and "AVG(" not in sql_upper:
        return False, "Average questions must use AVG()."
    if intent == "top_n" and ("ORDER BY" not in sql_upper or "LIMIT" not in sql_upper):
        return False, "Top-N questions must include ORDER BY and LIMIT."
    if intent == "trend":
        if "GROUP BY" not in sql_upper:
            return False, "Trend questions must group the results."
        if plan.get("dimension") == "month" and "DATE_FORMAT(" not in sql_upper:
            return False, "Month trend questions should group by a month expression."
    if intent == "pending_outstanding" and "WHERE" not in sql_upper and "HAVING" not in sql_upper:
        return False, "Pending or outstanding questions must apply business filters."
    if intent == "low_stock" and "WHERE" not in sql_upper:
        return False, "Low-stock questions must filter by stock threshold."

    if plan.get("metric") == "money" and not any(token in sql_upper for token in ("SUM(", "AVG(", "COUNT(", "MAX(", "MIN(")):
        if intent in {"total", "average", "top_n", "trend"}:
            return False, "Money-oriented business questions should use an aggregate."

    dimension = str(plan.get("dimension") or "").strip()
    if dimension and ("by " in str(query_context.get("plan", {}).get("question", "")).lower() or plan.get("grouping")):
        if "GROUP BY" not in sql_upper and "JOIN " not in sql_upper and intent in {"top_n", "trend", "comparison"}:
            return False, f"Questions by {dimension} should group or join by that business dimension."

    return True, "SQL matches the business query plan."


class QuestionService:  
    """Service for question processing and SQL generation."""
    
    def __init__(self):
        self.conversation_memory = ConversationMemory() 
        self.last_query_context: dict[str, Any] | None = None
    
    def process_question(
        self,
        question: str,
        knowledge_base: Dict[str, Any],
        business_glossary: Optional[Dict[str, Any]] = None,
        vector_retriever: Optional[VectorRetriever] = None,
        ai_backend: str = "local",
    ) -> Tuple[bool, str, Optional[str], Optional[str]]:
        """
        Process a natural language question and generate SQL.
        
        Args:
            question: User's natural language question
            knowledge_base: Knowledge base for SQL generation
            business_glossary: Business glossary for question rewriting
            ai_backend: AI backend to use for SQL generation
        
        Returns:
            (success, message, sql, error)
        """
        if _UNSAFE_NL_RE.search(question):
            return False, "Unsafe request blocked. Only SELECT questions are allowed.", None, None

        # Check for ambiguity
        if is_too_ambiguous(question):
            return False, "Question is too ambiguous. Please be more specific.", None, None
        
        # Normalize question
        normalized_question, was_normalized = normalize_question(question)
        if was_normalized:
            logger.info(f"Question normalized: '{question}' -> '{normalized_question}'")
            question = normalized_question
        
        # Check for conversation actions
        action = detect_conversation_action(question)
        if action:
            return False, f"Action detected: {action}", None, None
        
        # Detect follow-up
        is_follow_up, followup_reason = detect_follow_up(question, self.conversation_memory)
        rewritten_question = question
        
        if is_follow_up:
            logger.info(f"Follow-up detected: {followup_reason}")
            try:
                rewritten_question = rewrite_follow_up_question(
                    question,
                    self.conversation_memory,
                    knowledge_base,
                    business_glossary,
                    ai_backend,
                )
                logger.info(f"Rewritten question: {rewritten_question}")
            except Exception as e:
                logger.warning(f"Follow-up rewrite failed: {e}, using original question")
                rewritten_question = question

        query_context = build_query_context(
            rewritten_question,
            knowledge_base,
            business_glossary,
            vector_retriever=vector_retriever,
        )
        self.last_query_context = query_context
        scoped_knowledge_base = query_context.get("selected_knowledge_base", knowledge_base)
        full_knowledge_base = query_context.get("knowledge_base", knowledge_base)
        query_plan = query_context.get("plan")
        prefer_ai = _should_prefer_ai(query_context)
        ai_failure_reason: str | None = None

        def _validate_candidate_sql(candidate_sql: str, *, source_label: str) -> Tuple[bool, Optional[str], Optional[str]]:
            _attach_generation_feedback(query_context, candidate_sql)
            safety_ok, safety_reason = validate_sql(candidate_sql)
            struct_ok, struct_reason = validate_sql_structure(candidate_sql, knowledge_base)
            business_ok, business_reason = _validate_business_sql_fit(candidate_sql, query_context)

            if safety_ok and struct_ok and business_ok:
                logger.info(f"{source_label} SQL attempt passed validation")
                self.conversation_memory.add_turn(
                    user_question=question,
                    is_follow_up=is_follow_up,
                    rewritten_question=rewritten_question,
                    generated_sql=candidate_sql,
                )
                return True, None, None

            fail_reason = safety_reason if not safety_ok else struct_reason if not struct_ok else business_reason
            return False, fail_reason, source_label

        if prefer_ai:
            logger.info("Using AI as the primary SQL generator for this business question")
            try:
                try:
                    raw_sql = generate_sql(
                        rewritten_question,
                        scoped_knowledge_base,
                        backend=ai_backend,
                        query_plan=query_plan,
                        selected_tables=query_context.get("selected_tables"),
                        business_glossary=business_glossary,
                    )
                except TypeError as exc:
                    if "business_glossary" not in str(exc):
                        raise
                    raw_sql = generate_sql(
                        rewritten_question,
                        scoped_knowledge_base,
                        backend=ai_backend,
                        query_plan=query_plan,
                        selected_tables=query_context.get("selected_tables"),
                    )
                logger.info(f"SQL generated by AI: {raw_sql[:100]}...")
                candidate_sql = add_limit_if_missing(raw_sql.strip())
                ai_ok, ai_reason, _ = _validate_candidate_sql(candidate_sql, source_label="AI")
                if ai_ok:
                    return True, "SQL generated successfully (AI)", candidate_sql, None

                logger.warning(f"AI SQL did not meet validation requirements: {ai_reason}. Retrying...")
                try:
                    retry_raw = generate_sql_with_retry(
                        user_question=rewritten_question,
                        knowledge_base=scoped_knowledge_base,
                        backend=ai_backend,
                        first_attempt_sql=candidate_sql,
                        validation_reason=ai_reason or "AI SQL did not satisfy business validation.",
                        query_plan=query_plan,
                        selected_tables=query_context.get("selected_tables"),
                        business_glossary=business_glossary,
                    )
                except TypeError as exc:
                    if "business_glossary" not in str(exc):
                        raise
                    retry_raw = generate_sql_with_retry(
                        user_question=rewritten_question,
                        knowledge_base=scoped_knowledge_base,
                        backend=ai_backend,
                        first_attempt_sql=candidate_sql,
                        validation_reason=ai_reason or "AI SQL did not satisfy business validation.",
                        query_plan=query_plan,
                        selected_tables=query_context.get("selected_tables"),
                    )
                retry_sql = add_limit_if_missing(retry_raw.strip())
                retry_ok, retry_reason, _ = _validate_candidate_sql(retry_sql, source_label="AI retry")
                if retry_ok:
                    return True, "SQL generated successfully (AI, corrected)", retry_sql, None
                ai_failure_reason = retry_reason or ai_reason
                logger.warning(f"AI retry did not meet validation requirements: {ai_failure_reason}")
            except Exception as e:
                ai_failure_reason = str(e)
                logger.error(f"AI SQL generation failed: {e}")

        # Rule-based fallback remains available for deterministic guardrails and AI recovery.
        simple_sql = generate_simple_sql(
            rewritten_question,
            scoped_knowledge_base,
            query_plan=query_plan,
            business_glossary=business_glossary,
        )
        if simple_sql is None and scoped_knowledge_base is not full_knowledge_base:
            simple_sql = generate_simple_sql(
                rewritten_question,
                full_knowledge_base,
                query_plan=query_plan,
                business_glossary=business_glossary,
            )

        if simple_sql:
            logger.info("Using rule-based fallback SQL generator")
            simple_ok, simple_reason, _ = _validate_candidate_sql(simple_sql, source_label="Rule-based fallback")
            if simple_ok:
                message = "SQL generated successfully (rule-based fallback)" if prefer_ai else "SQL generated successfully (simple generator)"
                return True, message, simple_sql, None
            logger.error(f"Fallback SQL failed validation: {simple_reason}")
            return False, f"Generated SQL failed validation: {simple_reason}", None, None

        if ai_failure_reason:
            logger.error(f"SQL generation failed after AI and fallback checks: {ai_failure_reason}")
            return False, f"SQL generation failed: {ai_failure_reason}", None, None

        logger.error("Could not generate SQL from AI or fallback logic.")
        return False, "Could not generate a valid SQL query for this question.", None, None
    
    def validate_sql(self, sql: str, knowledge_base: Optional[Dict[str, Any]] = None) -> Tuple[bool, str]:
        """
        Validate SQL for safety and structure.
        
        Args:
            sql: SQL to validate
            knowledge_base: Knowledge base for structure validation
        
        Returns:
            (is_valid, reason)
        """
        # Validate safety
        is_valid, reason = validate_sql(sql)
        if not is_valid:
            return False, reason
        
        # Validate structure if knowledge base is available
        if knowledge_base:
            struct_ok, struct_reason = validate_sql_structure(sql, knowledge_base)
            if not struct_ok:
                return False, struct_reason
        
        return True, "SQL is valid"
    
    def detect_action(self, question: str) -> Optional[str]:
        """
        Detect if the question is a conversation action.
        
        Args:
            question: User's question
        
        Returns:
            Action string or None
        """
        return detect_conversation_action(question)
    
    def reset_conversation(self) -> None:
        """Reset conversation memory."""
        self.conversation_memory = ConversationMemory()
    
    def get_conversation_memory(self) -> ConversationMemory:
        """Get conversation memory."""
        return self.conversation_memory

    def get_last_query_context(self) -> Optional[Dict[str, Any]]:
        """Return the latest query plan / table-selection context."""
        return self.last_query_context
