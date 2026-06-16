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
    if plan.get("intent") in {"total", "average", "top_n", "trend", "comparison"}:
        return True
    if plan.get("dimension") or plan.get("grouping"):
        return True
    if plan.get("metric") and plan.get("intent") not in {"list", "count"}:
        return True
    if plan.get("filters") or plan.get("date_range"):
        return True
    if len(query_context.get("selected_table_names") or []) > 1:
        return True
    return False


def _has_clear_primary_table(query_context: dict[str, Any]) -> bool:
    selected_tables = list(query_context.get("selected_tables") or [])
    if not selected_tables:
        return False
    if len(selected_tables) == 1:
        return True

    primary_confidence = float(selected_tables[0].get("confidence") or 0.0)
    secondary_confidence = float(selected_tables[1].get("confidence") or 0.0)
    return primary_confidence >= 0.75 and (
        (primary_confidence - secondary_confidence) >= 0.15 or secondary_confidence < 0.65
    )


def _should_try_rule_based_first(query_context: dict[str, Any]) -> tuple[bool, str]:
    plan = query_context.get("plan") or {}
    selected_tables = list(query_context.get("selected_tables") or [])
    overall_confidence = float(query_context.get("confidence") or 0.0)
    top_confidence = float(selected_tables[0].get("confidence") or 0.0) if selected_tables else 0.0
    intent = str(plan.get("intent") or "list")
    
    # Debug logging for routing decision
    logger.debug(f"[DEBUG ROUTING] Normalized question: {query_context.get('plan', {}).get('question', 'N/A')}")
    logger.debug(f"[DEBUG ROUTING] Selected tables: {[t.get('table') for t in selected_tables]}")
    logger.debug(f"[DEBUG ROUTING] Selected columns: {[(c.get('table'), c.get('column')) for c in query_context.get('selected_columns', [])[:5]]}")
    logger.debug(f"[DEBUG ROUTING] Vector table candidates: {query_context.get('vector_results', {}).get('tables', [])[:3]}")
    logger.debug(f"[DEBUG ROUTING] Overall confidence: {overall_confidence}")
    logger.debug(f"[DEBUG ROUTING] Top confidence: {top_confidence}")
    logger.debug(f"[DEBUG ROUTING] Intent: {intent}")
    logger.debug(f"[DEBUG ROUTING] Dimension: {plan.get('dimension')}")
    logger.debug(f"[DEBUG ROUTING] Grouping: {plan.get('grouping')}")

    if plan.get("intent") in {"top_n", "trend", "comparison"}:
        logger.debug(f"[DEBUG ROUTING] Rule-based skipped: intent '{intent}' needs richer reasoning")
        return False, f"intent '{intent}' needs richer reasoning"
    if plan.get("dimension") or plan.get("grouping"):
        logger.debug(f"[DEBUG ROUTING] Rule-based skipped: question asks for grouped or dimensional output")
        return False, "question asks for grouped or dimensional output"
    if not _has_clear_primary_table(query_context):
        logger.debug(f"[DEBUG ROUTING] Rule-based skipped: planner could not isolate one primary table with enough confidence")
        return False, "planner could not isolate one primary table with enough confidence"
    if overall_confidence < 0.5 or top_confidence < 0.5:
        logger.debug(f"[DEBUG ROUTING] Rule-based skipped: planner confidence is too low for deterministic SQL (overall: {overall_confidence}, top: {top_confidence})")
        return False, "planner confidence is too low for deterministic SQL"
    
    logger.debug(f"[DEBUG ROUTING] Rule-based selected: simple single-table question with sufficient planner confidence")
    return True, "simple single-table question with sufficient planner confidence"


def _set_route(query_context: dict[str, Any], route_used: str, route_reason: str) -> None:
    query_context["route_used"] = route_used
    query_context["route_reason"] = route_reason


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

    if plan.get("filters") and "WHERE" not in sql_upper and "HAVING" not in sql_upper:
        return False, "Filtered questions must apply WHERE or HAVING conditions."
    if plan.get("date_range") and "WHERE" not in sql_upper and "HAVING" not in sql_upper:
        return False, "Date-filtered questions must apply WHERE or HAVING conditions."

    if plan.get("metric") == "money" and not any(token in sql_upper for token in ("SUM(", "AVG(", "COUNT(", "MAX(", "MIN(")):
        if intent in {"total", "average", "top_n", "trend"}:
            return False, "Money-oriented business questions should use an aggregate."

    dimension = str(plan.get("dimension") or "").strip()
    if dimension and ("by " in str(query_context.get("plan", {}).get("question", "")).lower() or plan.get("grouping")):
        if "GROUP BY" not in sql_upper and "JOIN " not in sql_upper and intent in {"top_n", "trend", "comparison"}:
            return False, f"Questions by {dimension} should group or join by that business dimension."

    return True, "SQL matches the business query plan."


def _build_validation_retry_context(query_context: dict[str, Any]) -> dict[str, Any]:
    """Build compact dynamic schema/vector context for AI correction prompts."""
    vector_results = query_context.get("vector_results") or {}
    return {
        "selected_tables": [
            {
                "table": entry.get("table"),
                "confidence": entry.get("confidence"),
                "reason": entry.get("reason"),
            }
            for entry in (query_context.get("selected_tables") or [])[:6]
        ],
        "selected_columns": [
            {
                "table": entry.get("table"),
                "column": entry.get("column"),
                "confidence": entry.get("confidence"),
                "reason": entry.get("reason"),
            }
            for entry in (query_context.get("selected_columns") or [])[:10]
        ],
        "vector_tables": list(vector_results.get("table_names") or [])[:8],
        "vector_columns": [
            {
                "table": entry.get("table_name"),
                "column": entry.get("column_name"),
                "semantic_type": entry.get("semantic_type"),
            }
            for entry in (vector_results.get("columns") or [])[:10]
        ],
        "vector_glossary_terms": [
            entry.get("term")
            for entry in (vector_results.get("glossary_terms") or [])[:6]
            if entry.get("term")
        ],
        "vector_relationships": [
            {
                "from": f"{entry.get('from_table')}.{entry.get('from_column')}",
                "to": f"{entry.get('to_table')}.{entry.get('to_column')}",
            }
            for entry in (vector_results.get("relationships") or [])[:6]
            if entry.get("from_table") and entry.get("to_table")
        ],
    }


def _format_generation_failure(
    question: str,
    generated_sql: str | None,
    validation_reason: str,
    query_context: dict[str, Any],
) -> str:
    """Create a clean CLI error with dynamic schema/vector candidates."""
    retry_context = _build_validation_retry_context(query_context)
    table_candidates = ", ".join(retry_context["vector_tables"] or [entry["table"] for entry in retry_context["selected_tables"] if entry.get("table")]) or "none"
    column_candidates = ", ".join(
        f"{entry.get('table')}.{entry.get('column')}"
        for entry in retry_context["selected_columns"]
        if entry.get("table") and entry.get("column")
    ) or "none"
    return (
        "Could not generate a valid SQL query.\n"
        f"Question: {question}\n"
        f"Generated SQL: {generated_sql or '(empty)'}\n"
        f"Validation reason: {validation_reason}\n"
        f"Relevant table candidates: {table_candidates}\n"
        f"Relevant column candidates: {column_candidates}"
    )


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
        prefer_rule_based, route_reason = _should_try_rule_based_first(query_context)
        ai_failure_reason: str | None = None
        ai_rejected_sql: str | None = None
        last_rejected_sql: str | None = None
        retry_context = _build_validation_retry_context(query_context)
        simple_sql_cache: str | None = None
        simple_sql_loaded = False

        def _validate_candidate_sql(candidate_sql: str, *, source_label: str) -> Tuple[bool, Optional[str], Optional[str]]:
            nonlocal last_rejected_sql
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
            last_rejected_sql = candidate_sql
            return False, fail_reason, source_label

        def _get_simple_sql() -> str | None:
            nonlocal simple_sql_cache, simple_sql_loaded
            if simple_sql_loaded:
                return simple_sql_cache

            simple_sql_loaded = True
            simple_sql_cache = generate_simple_sql(
                rewritten_question,
                scoped_knowledge_base,
                query_plan=query_plan,
                business_glossary=business_glossary,
                selected_tables=query_context.get("selected_tables"),
                vector_results=query_context.get("vector_results"),
            )
            if simple_sql_cache is None and scoped_knowledge_base is not full_knowledge_base:
                simple_sql_cache = generate_simple_sql(
                    rewritten_question,
                    full_knowledge_base,
                    query_plan=query_plan,
                    business_glossary=business_glossary,
                    selected_tables=query_context.get("selected_tables"),
                    vector_results=query_context.get("vector_results"),
                )
            if simple_sql_cache:
                simple_sql_cache = add_limit_if_missing(simple_sql_cache.strip())
            return simple_sql_cache

        if prefer_rule_based:
            logger.info(f"Using rule-based SQL generator first: {route_reason}")
            simple_sql = _get_simple_sql()
            if simple_sql:
                simple_ok, simple_reason, _ = _validate_candidate_sql(simple_sql, source_label="Rule-based")
                if simple_ok:
                    _set_route(query_context, "rule-based", route_reason)
                    return True, "SQL generated successfully (rule-based)", simple_sql, None
                logger.warning(f"Rule-based SQL did not meet validation requirements: {simple_reason}. Escalating to AI...")
            else:
                logger.info("Rule-based route was selected first, but no deterministic SQL pattern matched. Escalating to AI...")

        if not prefer_rule_based or ai_failure_reason is not None or last_rejected_sql is not None or _get_simple_sql() is None:
            logger.info("Using AI as the primary SQL generator for this question")
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
                    _set_route(query_context, "ai", "AI was selected for a complex or low-confidence question")
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
                        validation_context=retry_context,
                    )
                except TypeError as exc:
                    if "business_glossary" not in str(exc) and "validation_context" not in str(exc):
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
                    _set_route(query_context, "ai-retry", "AI retry corrected the first invalid SQL attempt")
                    return True, "SQL generated successfully (AI, corrected)", retry_sql, None
                ai_failure_reason = retry_reason or ai_reason
                ai_rejected_sql = retry_sql
                logger.warning(f"AI retry did not meet validation requirements: {ai_failure_reason}")
            except Exception as e:
                ai_failure_reason = str(e)
                logger.error(f"AI SQL generation failed: {e}")

        # Rule-based fallback remains available for deterministic guardrails and AI recovery.
        simple_sql = _get_simple_sql()

        if simple_sql:
            logger.info("Using rule-based fallback SQL generator")
            simple_ok, simple_reason, _ = _validate_candidate_sql(simple_sql, source_label="Rule-based fallback")
            if simple_ok:
                _set_route(
                    query_context,
                    "rule-based",
                    "AI route did not produce a valid SQL statement, so rule-based SQL was used",
                )
                message = "SQL generated successfully (rule-based fallback)"
                return True, message, simple_sql, None
            logger.error(f"Fallback SQL failed validation: {simple_reason}")
            failure_sql = simple_sql
            failure_reason = simple_reason or "Generated SQL failed validation."
            if ai_failure_reason:
                failure_sql = ai_rejected_sql or last_rejected_sql or simple_sql
                failure_reason = ai_failure_reason
            _set_route(query_context, "fallback-failed", failure_reason)
            error_message = _format_generation_failure(
                rewritten_question,
                failure_sql,
                failure_reason,
                query_context,
            )
            return False, f"Generated SQL failed validation: {failure_reason}", None, error_message

        if ai_failure_reason:
            logger.error(f"SQL generation failed after AI and fallback checks: {ai_failure_reason}")
            _set_route(query_context, "fallback-failed", ai_failure_reason)
            error_message = _format_generation_failure(
                rewritten_question,
                ai_rejected_sql or last_rejected_sql,
                ai_failure_reason,
                query_context,
            )
            return False, f"SQL generation failed: {ai_failure_reason}", None, error_message

        logger.error("Could not generate SQL from AI or fallback logic.")
        _set_route(query_context, "fallback-failed", "no valid SQL could be generated from rule-based or AI paths")
        error_message = _format_generation_failure(
            rewritten_question,
            last_rejected_sql,
            "Could not generate a valid SQL query.",
            query_context,
        )
        return False, "Could not generate a valid SQL query for this question.", None, error_message
    
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
