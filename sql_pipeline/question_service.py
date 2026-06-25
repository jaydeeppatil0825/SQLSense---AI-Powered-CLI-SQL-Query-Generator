"""
core/question_service.py
========================
SQL Generation Pipeline orchestrator.

This service remains the central orchestrator for question-to-SQL flow in
the CLI. It consumes pipeline-produced context, chooses rule-based or AI
generation, validates SQL, applies safe deterministic repair when allowed,
and returns only safe SELECT output.

It should not rebuild independent business meaning when pipeline evidence
already exists.
"""

from copy import deepcopy
from typing import Optional, Tuple, Dict, Any
import inspect
import re

from query_pipeline.query_planner import build_query_context
from sql_pipeline.deterministic_sql_generator import generate_single_table_aggregate_sql
from sql_pipeline.simple_query_generator import generate_simple_sql
from sql_pipeline.sql_generator import (
    generate_sql as _blocked_generate_sql,
    generate_sql_with_retry as _blocked_generate_sql_with_retry,
)
from query_pipeline.question_normalizer import normalize_question, is_too_ambiguous
from sql_pipeline.sql_validator import validate_sql, validate_sql_structure, add_limit_if_missing, extract_requested_limit
from query_pipeline.conversation.followup_detector import detect_follow_up
from query_pipeline.conversation.question_rewriter import rewrite_follow_up_question
from query_pipeline.conversation.action_detector import detect_conversation_action
from query_pipeline.conversation.conversation_memory import ConversationMemory
from utils.logger import get_logger
from kb_pipeline.schema_facts import (
    column_business_description,
    column_business_terms,
    column_sample_values,
    resolved_semantic_type,
)
from kb_pipeline.vector.retriever import VectorRetriever

logger = get_logger()

# Backward-compatible module exports for older tests and wrappers.
# Runtime SQL generation does not call these helpers anymore.
generate_sql = _blocked_generate_sql
generate_sql_with_retry = _blocked_generate_sql_with_retry

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
_ROUTING_FILLER_TERMS = {
    "show",
    "count",
    "list",
    "display",
    "get",
    "fetch",
    "tell",
    "me",
    "all",
    "from",
    "in",
    "of",
    "for",
    "to",
    "with",
    "by",
    "sorted",
    "sort",
    "latest",
    "recent",
    "top",
    "first",
    "last",
}
_RANKING_QUERY_RE = re.compile(
    r"\b(top|highest|lowest|best|worst|latest|recent|newest|oldest)\b",
    re.IGNORECASE,
)


def _looks_like_generic_select(sql: str) -> bool:
    return bool(_GENERIC_SELECT_RE.match(str(sql or "").strip()))


def _scope_glossary_to_knowledge_base(
    business_glossary: Optional[Dict[str, Any]],
    knowledge_base: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Keep glossary mappings that belong to the active runtime schema only."""
    if not business_glossary:
        return business_glossary

    active_tables = {str(table_name) for table_name in (knowledge_base or {}).keys()}
    if not active_tables:
        return None

    scoped: Dict[str, Any] = {}
    for term, term_data in business_glossary.items():
        if not isinstance(term_data, dict):
            continue

        mappings = [
            mapping
            for mapping in (term_data.get("mapped_columns") or [])
            if str(mapping.get("table", "")).strip() in active_tables
        ]
        if not mappings:
            continue

        scoped_term = dict(term_data)
        scoped_term["mapped_columns"] = mappings
        scoped[term] = scoped_term

    return scoped


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

    question = str((query_context.get("plan") or {}).get("question") or "")
    top_table = str(selected_tables[0].get("table") or "")
    if top_table:
        table_tokens = _routing_tokens(top_table)
        question_tokens = _routing_tokens(question)
        if table_tokens and table_tokens & question_tokens and float(selected_tables[0].get("confidence") or 0.0) >= 0.75:
            return True

    primary_confidence = float(selected_tables[0].get("confidence") or 0.0)
    secondary_confidence = float(selected_tables[1].get("confidence") or 0.0)
    return primary_confidence >= 0.75 and (
        (primary_confidence - secondary_confidence) >= 0.15 or secondary_confidence < 0.65
    )


def _routing_tokens(value: str) -> set[str]:
    tokens = {token for token in re.split(r"[^a-z0-9]+", str(value or "").lower()) if token}
    expanded = set(tokens)
    for token in list(tokens):
        if token.endswith("ies") and len(token) > 3:
            expanded.add(token[:-3] + "y")
        elif token.endswith("ses") and len(token) > 3:
            expanded.add(token[:-2])
        elif token.endswith("s") and not token.endswith("ss") and len(token) > 1:
            expanded.add(token[:-1]) 
    return expanded


def _has_unresolved_simple_terms(query_context: dict[str, Any]) -> bool:
    plan = query_context.get("plan") or {}
    query_terms = {
        term for term in (plan.get("question_terms") or [])
        if term and term not in _ROUTING_FILLER_TERMS
    }
    if not query_terms:
        return False

    supported_terms: set[str] = set()
    for table_name in query_context.get("selected_table_names") or []:
        supported_terms.update(_routing_tokens(table_name))
    for column_entry in query_context.get("selected_columns") or []:
        supported_terms.update(_routing_tokens(column_entry.get("column", "")))
    for filter_data in plan.get("filters") or []:
        supported_terms.update(_routing_tokens(filter_data.get("column", "")))
        supported_terms.update(_routing_tokens(filter_data.get("term", "")))
        supported_terms.update(_routing_tokens(filter_data.get("value", "")))
    if plan.get("date_range"):
        supported_terms.update(_routing_tokens(plan["date_range"].get("label", "")))
        supported_terms.update(_routing_tokens(plan["date_range"].get("start", "")))
        supported_terms.update(_routing_tokens(plan["date_range"].get("end_exclusive", "")))
    if plan.get("sorting"):
        supported_terms.update(_routing_tokens(plan["sorting"].get("by", "")))
    if plan.get("limit") is not None:
        supported_terms.update(_routing_tokens(str(plan.get("limit"))))
    if str(plan.get("intent") or "") == "count":
        supported_terms.add("count")
    if str(plan.get("intent") or "") == "total":
        supported_terms.update({"total", "sum"})
    if str(plan.get("intent") or "") == "average":
        supported_terms.update({"average", "avg", "mean"})

    unresolved = query_terms - supported_terms
    return bool(unresolved)


def _should_try_rule_based_first(query_context: dict[str, Any]) -> tuple[bool, str]:
    plan = query_context.get("plan") or {}
    selected_tables = list(query_context.get("selected_tables") or [])
    vector_results = query_context.get("vector_results") or {}
    overall_confidence = float(query_context.get("confidence") or 0.0)
    top_confidence = float(selected_tables[0].get("confidence") or 0.0) if selected_tables else 0.0
    intent = str(plan.get("intent") or "list")
    
    # Debug logging for routing decision
    logger.debug(f"[DEBUG ROUTING] Normalized question: {query_context.get('plan', {}).get('question', 'N/A')}")
    logger.debug(f"[DEBUG ROUTING] Selected tables: {[t.get('table') for t in selected_tables]}")
    logger.debug(f"[DEBUG ROUTING] Selected columns: {[(c.get('table'), c.get('column')) for c in query_context.get('selected_columns', [])[:5]]}")
    logger.debug(f"[DEBUG ROUTING] Vector table candidates: {vector_results.get('tables', [])[:3]}")
    logger.debug(f"[DEBUG ROUTING] Overall confidence: {overall_confidence}")
    logger.debug(f"[DEBUG ROUTING] Top confidence: {top_confidence}")
    logger.debug(f"[DEBUG ROUTING] Intent: {intent}")
    logger.debug(f"[DEBUG ROUTING] Dimension: {plan.get('dimension')}")
    logger.debug(f"[DEBUG ROUTING] Grouping: {plan.get('grouping')}")
    normalized_question = str(plan.get("question") or "")

    if intent not in {"list", "count"}:
        logger.debug(f"[DEBUG ROUTING] Rule-based skipped: intent '{intent}' is routed to AI for complex SQL generation")
        return False, f"intent '{intent}' needs richer reasoning"
    if _RANKING_QUERY_RE.search(normalized_question):
        logger.debug("[DEBUG ROUTING] Rule-based skipped: ranking-style question should use AI")
        return False, "ranking-style question should use AI"
    if plan.get("dimension") or plan.get("grouping"):
        logger.debug(f"[DEBUG ROUTING] Rule-based skipped: question asks for grouped or dimensional output")
        return False, "question asks for grouped or dimensional output"
    if intent == "list" and plan.get("metric") and not plan.get("filters") and not plan.get("date_range") and not plan.get("sorting"):
        logger.debug("[DEBUG ROUTING] Rule-based skipped: list question still implies a metric without a simple deterministic filter/date pattern")
        return False, "list question still implies a metric without a simple deterministic filter/date pattern"
    if not _has_clear_primary_table(query_context):
        logger.debug(f"[DEBUG ROUTING] Rule-based skipped: planner could not isolate one primary table with enough confidence")
        return False, "planner could not isolate one primary table with enough confidence"
    if _has_unresolved_simple_terms(query_context):
        logger.debug("[DEBUG ROUTING] Rule-based skipped: query still has unresolved terms outside the deterministic table/column/filter context")
        return False, "query still has unresolved terms outside the deterministic table/column/filter context"
    if overall_confidence < 0.5 or top_confidence < 0.5:
        logger.debug(f"[DEBUG ROUTING] Rule-based skipped: planner confidence is too low for deterministic SQL (overall: {overall_confidence}, top: {top_confidence})")
        return False, "planner confidence is too low for deterministic SQL"
    
    logger.debug(f"[DEBUG ROUTING] Rule-based selected: simple single-table question with sufficient planner confidence")
    return True, "simple single-table question with sufficient planner confidence"


def _needs_simple_table_clarification(query_context: dict[str, Any]) -> bool:
    plan = query_context.get("plan") or {}
    if str(plan.get("intent") or "") not in {"list", "count"}:
        return False
    if plan.get("dimension") or plan.get("grouping") or plan.get("filters") or plan.get("date_range"):
        return False
    if plan.get("metric"):
        return False

    selected_tables = list(query_context.get("selected_tables") or [])
    if len(selected_tables) < 2:
        return False
    if _has_clear_primary_table(query_context):
        return False

    top_confidence = float(selected_tables[0].get("confidence") or 0.0)
    second_confidence = float(selected_tables[1].get("confidence") or 0.0)
    return top_confidence >= 0.55 and abs(top_confidence - second_confidence) < 0.12


def _clarification_message(query_context: dict[str, Any]) -> str:
    candidates = [
        str(entry.get("table", "")).strip()
        for entry in (query_context.get("selected_tables") or [])[:3]
        if str(entry.get("table", "")).strip()
    ]
    if not candidates:
        return "Question is ambiguous. Please specify which table or record type you want."
    return (
        "Question is ambiguous across multiple possible tables. "
        f"Please specify one of: {', '.join(candidates)}."
    )


def _set_route(query_context: dict[str, Any], route_used: str, route_reason: str) -> None:
    query_context["route_used"] = route_used
    query_context["route_reason"] = route_reason


def _prune_to_primary_table(query_context: dict[str, Any]) -> dict[str, Any]:
    selected_tables = list(query_context.get("selected_tables") or [])
    if not selected_tables:
        return query_context

    primary_entry = dict(selected_tables[0])
    primary_table = str(primary_entry.get("table") or "").strip()
    if not primary_table:
        return query_context

    primary_columns = [
        dict(column_entry)
        for column_entry in list(primary_entry.get("selected_columns") or [])
        if str(column_entry.get("column") or "").strip()
    ]
    primary_entry["selected_columns"] = primary_columns

    query_context["selected_tables"] = [primary_entry]
    query_context["selected_table_names"] = [primary_table]
    query_context["selected_columns"] = [
        {"table": primary_table, **column_entry}
        for column_entry in primary_columns
    ]
    query_context["join_paths"] = []
    query_context["complex_sql_plan"] = None

    if isinstance(query_context.get("selected_knowledge_base"), dict):
        selected_kb = query_context["selected_knowledge_base"]
        if primary_table in selected_kb:
            query_context["selected_knowledge_base"] = {primary_table: selected_kb[primary_table]}

    if isinstance(query_context.get("measure_candidates"), list):
        query_context["measure_candidates"] = [
            entry for entry in query_context["measure_candidates"]
            if str(entry.get("table") or "").strip() == primary_table
        ]
    if isinstance(query_context.get("dimension_candidates"), list):
        query_context["dimension_candidates"] = [
            entry for entry in query_context["dimension_candidates"]
            if str(entry.get("table") or "").strip() == primary_table
        ]
    if isinstance(query_context.get("filter_candidates"), list):
        query_context["filter_candidates"] = [
            entry for entry in query_context["filter_candidates"]
            if str(entry.get("table") or "").strip() == primary_table
        ]
    return query_context


def _apply_simple_query_guard(query_context: dict[str, Any]) -> tuple[bool, str]:
    plan = query_context.get("plan") or {}
    intent = str(plan.get("intent") or "").strip().lower()
    if intent not in {"list", "count"}:
        return False, "intent is not simple list/count"
    if plan.get("dimension") or plan.get("grouping") or plan.get("date_range"):
        return False, "query requires grouping or date-aware handling"
    if len(query_context.get("selected_tables") or []) < 1:
        return False, "no selected tables available"
    if not _has_clear_primary_table(query_context):
        return False, "planner did not isolate one clear primary table"
    if _has_unresolved_simple_terms(query_context):
        return False, "simple query still has unresolved terms"

    _prune_to_primary_table(query_context)
    return True, "simple single-table list/count query pruned to primary runtime table"


def _pipeline_context_matches_question(
    pipeline_context: Optional[dict[str, Any]],
    rewritten_question: str,
) -> bool:
    if not isinstance(pipeline_context, dict):
        return False
    if str(pipeline_context.get("normalized_question") or "").strip() != str(rewritten_question or "").strip():
        return False

    query_context = pipeline_context.get("query_context")
    if not isinstance(query_context, dict):
        return False

    has_structured_context = bool(
        query_context.get("selected_table_names")
        or query_context.get("selected_tables")
        or query_context.get("join_paths")
    )
    if not has_structured_context:
        return False

    unresolved_metrics = list((pipeline_context.get("plan") or {}).get("unresolved_metrics") or [])
    if unresolved_metrics:
        has_measure_evidence = bool(
            query_context.get("measure_candidates")
            or (pipeline_context.get("retrieved_context") or {}).get("measure_candidates")
            or pipeline_context.get("formula_evidence")
        )
        if not has_measure_evidence:
            return False

    return True


def _merge_pipeline_metadata(
    query_context: dict[str, Any],
    pipeline_context: Optional[dict[str, Any]],
) -> dict[str, Any]:
    if not isinstance(query_context, dict):
        query_context = {}
    if not isinstance(pipeline_context, dict):
        return query_context

    query_context["pipeline_context"] = pipeline_context

    if isinstance(pipeline_context.get("intent"), dict):
        query_context.setdefault("intent", pipeline_context["intent"])
    if isinstance(pipeline_context.get("retrieved_context"), dict):
        query_context.setdefault("retrieved_context", pipeline_context["retrieved_context"])
    if isinstance(pipeline_context.get("formula_evidence"), list):
        query_context.setdefault("formula_evidence", list(pipeline_context["formula_evidence"]))
    if isinstance(pipeline_context.get("evidence_sources"), list):
        query_context.setdefault("evidence_sources", list(pipeline_context["evidence_sources"]))
    if isinstance(pipeline_context.get("complex_sql_plan"), dict):
        query_context.setdefault("complex_sql_plan", dict(pipeline_context["complex_sql_plan"]))
    if str(pipeline_context.get("normalized_question") or "").strip():
        query_context.setdefault("normalized_question", str(pipeline_context["normalized_question"]).strip())
    if str(pipeline_context.get("route_recommendation") or "").strip():
        query_context.setdefault("route_recommendation", str(pipeline_context["route_recommendation"]).strip())

    pipeline_plan = pipeline_context.get("plan")
    if isinstance(pipeline_plan, dict):
        current_plan = query_context.get("plan")
        if isinstance(current_plan, dict):
            merged_plan = dict(pipeline_plan)
            merged_plan.update(current_plan)
            query_context["plan"] = merged_plan
        else:
            query_context["plan"] = dict(pipeline_plan)

    return query_context


def _route_recommendation(query_context: dict[str, Any]) -> str:
    route = str(query_context.get("route_recommendation") or "").strip()
    if route:
        return route
    pipeline_context = query_context.get("pipeline_context")
    if isinstance(pipeline_context, dict):
        route = str(pipeline_context.get("route_recommendation") or "").strip()
        if route:
            return route
    return ""


def _planning_block_message(query_context: dict[str, Any], route_recommendation: str) -> str:
    if route_recommendation == "needs_clarification":
        return _clarification_message(query_context)
    return (
        "Question could not be planned safely from the current schema context. "
        "Please be more specific or rebuild the knowledge base."
    )


def _should_try_rule_based_from_pipeline(query_context: dict[str, Any]) -> tuple[bool, str]:
    route_recommendation = _route_recommendation(query_context)
    if route_recommendation == "simple_rule_ok":
        return True, "query pipeline recommended deterministic SQL from strong runtime evidence"
    if route_recommendation == "ai_sql_required":
        heuristic_ok, heuristic_reason = _should_try_rule_based_first(query_context)
        if heuristic_ok:
            return True, "query pipeline selected one strong table for a simple list/count question"
        return False, "query pipeline recommended AI SQL for this question"
    return _should_try_rule_based_first(query_context)


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
    retrieved_context = query_context.get("retrieved_context") or {}
    join_paths = list(retrieved_context.get("possible_join_paths") or query_context.get("join_paths") or [])
    join_conditions = [
        edge.get("join_condition")
        or (
            f"{edge.get('from_table')}.{edge.get('from_column')} = "
            f"{edge.get('to_table')}.{edge.get('to_column')}"
        )
        for join_path in join_paths[:6]
        for edge in join_path.get("path", [])
        if edge.get("from_column") and edge.get("to_table") and edge.get("to_column")
    ]
    join_skeletons = []
    for join_path in join_paths[:6]:
        current = join_path.get("from_table")
        if not current:
            continue
        parts = [f"FROM {current}"]
        for edge in join_path.get("path", []):
            to_table = edge.get("to_table")
            condition = edge.get("join_condition")
            if not condition and edge.get("from_table") and edge.get("from_column") and edge.get("to_column"):
                condition = f"{edge.get('from_table')}.{edge.get('from_column')} = {to_table}.{edge.get('to_column')}"
            if to_table and condition:
                parts.append(f"JOIN {to_table} ON {condition}")
        if len(parts) > 1:
            join_skeletons.append(" ".join(parts))
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
        "fk_relationships": [
            {
                "table": table_name,
                "foreign_keys": [
                    {
                        "column": fk.get("column"),
                        "referenced_table": fk.get("referenced_table"),
                        "referenced_column": fk.get("referenced_column"),
                    }
                    for fk in table_data.get("foreign_keys", [])[:8]
                ],
            }
            for table_name, table_data in list((query_context.get("selected_knowledge_base") or {}).items())[:6]
        ],
        "join_paths": join_paths,
        "join_conditions": join_conditions,
        "join_skeletons": join_skeletons,
        "measure_candidates": list(query_context.get("measure_candidates") or retrieved_context.get("measure_candidates") or [])[:10],
        "dimension_candidates": list(query_context.get("dimension_candidates") or retrieved_context.get("dimension_candidates") or [])[:10],
        "filter_candidates": list(query_context.get("filter_candidates") or query_context.get("filters") or retrieved_context.get("filter_candidates") or [])[:10],
        "formula_evidence": list(query_context.get("formula_evidence") or retrieved_context.get("formula_evidence") or [])[:10],
        "complex_sql_plan": dict(query_context.get("complex_sql_plan") or {}),
        "evidence_sources": list(query_context.get("evidence_sources") or retrieved_context.get("retrieval_sources") or [])[:10],
    }


def _possible_join_paths(query_context: dict[str, Any]) -> list[dict[str, Any]]:
    retrieved_context = query_context.get("retrieved_context") or {}
    return list(retrieved_context.get("possible_join_paths") or query_context.get("join_paths") or [])


def _formula_evidence_payload(query_context: dict[str, Any]) -> list[dict[str, Any]]:
    evidence_entries: list[dict[str, Any]] = []
    pipeline_context = query_context.get("pipeline_context")
    if isinstance(pipeline_context, dict):
        for value in (
            pipeline_context.get("formula_evidence"),
            (pipeline_context.get("plan") or {}).get("formula_evidence"),
            (pipeline_context.get("retrieved_context") or {}).get("formula_evidence"),
        ):
            if isinstance(value, list):
                evidence_entries.extend(entry for entry in value if isinstance(entry, dict))

    for value in (
        query_context.get("formula_evidence"),
        (query_context.get("plan") or {}).get("formula_evidence"),
        (query_context.get("retrieved_context") or {}).get("formula_evidence"),
    ):
        if isinstance(value, list):
            evidence_entries.extend(entry for entry in value if isinstance(entry, dict))

    deduped: list[dict[str, Any]] = []
    seen = set()
    for entry in evidence_entries:
        key = (
            str(entry.get("table") or ""),
            str(entry.get("primary_column") or entry.get("column") or ""),
            str(entry.get("operation") or entry.get("formula_operation") or ""),
            str(entry.get("secondary_column") or entry.get("secondary") or ""),
            str(entry.get("alias") or entry.get("formula_alias") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(entry)
    return deduped


def _evidence_sources_payload(query_context: dict[str, Any]) -> list[str]:
    sources: list[str] = []
    pipeline_context = query_context.get("pipeline_context")
    if isinstance(pipeline_context, dict):
        for value in (
            pipeline_context.get("evidence_sources"),
            (pipeline_context.get("plan") or {}).get("evidence_sources"),
            (pipeline_context.get("retrieved_context") or {}).get("retrieval_sources"),
        ):
            if isinstance(value, list):
                sources.extend(str(entry).strip() for entry in value if str(entry).strip())

    for value in (
        query_context.get("evidence_sources"),
        (query_context.get("plan") or {}).get("evidence_sources"),
        (query_context.get("retrieved_context") or {}).get("retrieval_sources"),
    ):
        if isinstance(value, list):
            sources.extend(str(entry).strip() for entry in value if str(entry).strip())

    deduped: list[str] = []
    seen = set()
    for entry in sources:
        key = entry.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(entry)
    return deduped


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
    join_candidates = ", ".join(retry_context.get("join_conditions") or []) or "none"
    return (
        "Could not generate a valid SQL query.\n"
        f"Question: {question}\n"
        f"Generated SQL: {generated_sql or '(empty)'}\n"
        f"Validation reason: {validation_reason}\n"
        f"Relevant table candidates: {table_candidates}\n"
        f"Relevant column candidates: {column_candidates}\n"
        f"Relevant join candidates: {join_candidates}"
    )


def _is_repairable_structural_failure(validation_reason: str | None) -> bool:
    reason = str(validation_reason or "").lower()
    if not reason:
        return False
    return any(
        token in reason
        for token in (
            "missing a from clause",
            "missing a table name after from",
            "missing a valid table name after from",
            "does not start with select",
            "only select queries are allowed",
            "non-sql content outside the select statement",
            "incomplete join",
            "join without an on or using condition",
        )
    )


def _is_safe_sql_identifier(identifier: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", str(identifier or "")))


def _qualified_column(table_name: str, column_name: str) -> str | None:
    if not _is_safe_sql_identifier(table_name) or not _is_safe_sql_identifier(column_name):
        return None
    return f"{table_name}.{column_name}"


def _sql_literal(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"


def _semantic_type_for_column(knowledge_base: dict[str, Any], table_name: str, column_name: str) -> str:
    for column in knowledge_base.get(table_name, {}).get("columns", []):
        if str(column.get("name", "")) == str(column_name):
            return resolved_semantic_type(column)
    return "unknown"


def _question_terms(query_context: dict[str, Any]) -> set[str]:
    plan = query_context.get("plan") or {}
    terms = set(plan.get("question_terms") or [])
    question = str(plan.get("question") or "")
    terms.update(token for token in re.split(r"[^a-z0-9]+", question.lower()) if token)
    return terms


def _identifier_tokens(value: str) -> set[str]:
    return {token for token in re.split(r"[^a-z0-9]+", str(value or "").lower()) if token}


def _find_dimension_column(query_context: dict[str, Any]) -> dict[str, Any] | None:
    plan = query_context.get("plan") or {}
    dimension = str(plan.get("dimension") or "").strip()
    dimension_tokens = _identifier_tokens(dimension)
    selected_columns = list(query_context.get("selected_columns") or [])

    ranked: list[tuple[float, dict[str, Any]]] = []
    for entry in selected_columns:
        table_name = str(entry.get("table") or "")
        column_name = str(entry.get("column") or "")
        semantic_type = str(entry.get("semantic_type") or "").lower()
        table_tokens = _identifier_tokens(table_name)
        column_tokens = _identifier_tokens(column_name)

        score = float(entry.get("confidence") or 0.0)
        if dimension_tokens and (dimension_tokens & table_tokens):
            score += 1.4
        if dimension_tokens and (dimension_tokens & column_tokens):
            score += 1.0
        if semantic_type in {"name", "text", "code", "reference"}:
            score += 1.2
        if column_tokens & {"name", "label", "title", "display", "segment", "code"}:
            score += 0.8
        if semantic_type == "id":
            score -= 0.5

        if score > 0:
            ranked.append((score, entry))

    ranked.sort(key=lambda item: (-item[0], str(item[1].get("table")), str(item[1].get("column"))))
    return ranked[0][1] if ranked else None


def _find_measure_column(
    query_context: dict[str, Any],
    dimension_column: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    selected_columns = list(query_context.get("selected_columns") or [])
    question_terms = _question_terms(query_context)
    dimension_table = str((dimension_column or {}).get("table") or "")
    ranked: list[tuple[float, dict[str, Any]]] = []
    for entry in selected_columns:
        table_name = str(entry.get("table") or "")
        column_name = str(entry.get("column") or "")
        semantic_type = str(entry.get("semantic_type") or "").lower()
        if semantic_type not in {"money", "quantity", "percentage"}:
            continue
        score = float(entry.get("confidence") or 0.0)
        if semantic_type == "money":
            score += 0.3
        table_tokens = _identifier_tokens(table_name)
        column_tokens = _identifier_tokens(column_name)
        score += 1.2 * len(column_tokens & question_terms)
        score += 0.5 * len(table_tokens & question_terms)
        if table_name and table_name == dimension_table:
            score -= 1.5
        if _join_path_between(query_context, dimension_table, table_name) is not None:
            score += 0.8
        ranked.append((score, entry))

    ranked.sort(key=lambda item: (-item[0], str(item[1].get("table")), str(item[1].get("column"))))
    return ranked[0][1] if ranked else None


def _money_columns_for_table(query_context: dict[str, Any], table_name: str) -> list[dict[str, Any]]:
    columns_by_name: dict[str, dict[str, Any]] = {}
    for entry in query_context.get("selected_columns") or []:
        if str(entry.get("table") or "") != table_name:
            continue
        if str(entry.get("semantic_type") or "").lower() == "money":
            columns_by_name[str(entry.get("column") or "")] = dict(entry)

    for kb_name in ("selected_knowledge_base", "knowledge_base"):
        knowledge_base = query_context.get(kb_name) or {}
        for column in knowledge_base.get(table_name, {}).get("columns", []):
            column_name = str(column.get("name", "")).strip()
            if not column_name or resolved_semantic_type(column) != "money":
                continue
            if column_name in columns_by_name:
                continue
            columns_by_name[column_name] = {
                "table": table_name,
                "column": column_name,
                "semantic_type": "money",
                "confidence": 0.6,
                "reason": "available money column from runtime schema metadata",
            }

    columns = list(columns_by_name.values())
    columns.sort(key=lambda entry: (-float(entry.get("confidence") or 0.0), str(entry.get("column") or "")))
    return columns


def _has_same_table_state_context(query_context: dict[str, Any], table_name: str) -> bool:
    plan = query_context.get("plan") or {}
    for filter_data in plan.get("filters") or []:
        if str(filter_data.get("table") or "") == table_name:
            return True
    for entry in query_context.get("selected_columns") or []:
        if str(entry.get("table") or "") != table_name:
            continue
        if str(entry.get("semantic_type") or "").lower() == "status":
            return True

    question_terms = _question_terms(query_context)
    for kb_name in ("selected_knowledge_base", "knowledge_base"):
        knowledge_base = query_context.get(kb_name) or {}
        for column in knowledge_base.get(table_name, {}).get("columns", []):
            if resolved_semantic_type(column) != "status":
                continue
            status_terms = set(_identifier_tokens(str(column.get("name", ""))))
            status_terms.update(_identifier_tokens(column_business_description(column)))
            for term in column_business_terms(column):
                status_terms.update(_identifier_tokens(str(term)))
            for sample_value in column_sample_values(column):
                status_terms.update(_identifier_tokens(str(sample_value)))
            if status_terms & question_terms:
                return True
    return False


def _column_metadata(query_context: dict[str, Any], table_name: str, column_name: str) -> dict[str, Any]:
    for kb_name in ("selected_knowledge_base", "knowledge_base"):
        knowledge_base = query_context.get(kb_name) or {}
        for column in knowledge_base.get(table_name, {}).get("columns", []):
            if str(column.get("name", "")) == str(column_name):
                return column
    return {}


def _formula_evidence_entries(
    query_context: dict[str, Any],
    table_name: str,
    column_name: str,
) -> list[dict[str, Any]]:
    evidence_entries: list[dict[str, Any]] = []

    pipeline_context = query_context.get("pipeline_context")
    if isinstance(pipeline_context, dict):
        pipeline_formula_evidence = pipeline_context.get("formula_evidence")
        if isinstance(pipeline_formula_evidence, list):
            evidence_entries.extend(entry for entry in pipeline_formula_evidence if isinstance(entry, dict))

        pipeline_plan = pipeline_context.get("plan")
        if isinstance(pipeline_plan, dict):
            pipeline_plan_evidence = pipeline_plan.get("formula_evidence")
            if isinstance(pipeline_plan_evidence, list):
                evidence_entries.extend(entry for entry in pipeline_plan_evidence if isinstance(entry, dict))

        pipeline_retrieved_context = pipeline_context.get("retrieved_context")
        if isinstance(pipeline_retrieved_context, dict):
            pipeline_retrieved_evidence = pipeline_retrieved_context.get("formula_evidence")
            if isinstance(pipeline_retrieved_evidence, list):
                evidence_entries.extend(entry for entry in pipeline_retrieved_evidence if isinstance(entry, dict))

    for key in ("formula_evidence",):
        value = query_context.get(key)
        if isinstance(value, list):
            evidence_entries.extend(entry for entry in value if isinstance(entry, dict))

    plan = query_context.get("plan") or {}
    plan_evidence = plan.get("formula_evidence")
    if isinstance(plan_evidence, list):
        evidence_entries.extend(entry for entry in plan_evidence if isinstance(entry, dict))

    retrieved_context = query_context.get("retrieved_context") or {}
    retrieved_evidence = retrieved_context.get("formula_evidence")
    if isinstance(retrieved_evidence, list):
        evidence_entries.extend(entry for entry in retrieved_evidence if isinstance(entry, dict))

    column_metadata = _column_metadata(query_context, table_name, column_name)
    metadata_evidence = column_metadata.get("formula_evidence")
    if isinstance(metadata_evidence, list):
        evidence_entries.extend(entry for entry in metadata_evidence if isinstance(entry, dict))

    metadata_components = column_metadata.get("formula_components")
    if isinstance(metadata_components, dict):
        evidence_entries.append(
            {
                "table": table_name,
                "column": column_name,
                "operation": column_metadata.get("formula_operation") or metadata_components.get("operation"),
                "secondary_column": metadata_components.get("secondary_column"),
                "alias": column_metadata.get("formula_alias"),
                "source": "column_metadata",
            }
        )

    applicable: list[dict[str, Any]] = []
    for entry in evidence_entries:
        entry_table = str(entry.get("table") or table_name).strip()
        entry_column = str(entry.get("primary_column") or entry.get("column") or column_name).strip()
        if entry_table != table_name or entry_column != column_name:
            continue
        applicable.append(entry)
    return applicable


def _formula_expression_from_evidence(
    query_context: dict[str, Any],
    measure_column: dict[str, Any],
    money_columns: list[dict[str, Any]],
) -> tuple[str | None, str | None]:
    table_name = str(measure_column.get("table") or "")
    column_name = str(measure_column.get("column") or "")
    primary_sql = _qualified_column(table_name, column_name)
    if not primary_sql:
        return None, None

    secondary_lookup = {
        str(entry.get("column") or ""): entry
        for entry in money_columns
        if str(entry.get("column") or "")
    }

    for evidence in _formula_evidence_entries(query_context, table_name, column_name):
        operation = str(
            evidence.get("operation")
            or evidence.get("formula_operation")
            or evidence.get("expression_type")
            or ""
        ).strip().lower()
        secondary_name = str(
            evidence.get("secondary_column")
            or evidence.get("secondary")
            or evidence.get("other_column")
            or ""
        ).strip()
        if operation not in {"difference", "subtract", "minus"} or secondary_name not in secondary_lookup:
            continue

        secondary_sql = _qualified_column(table_name, secondary_name)
        if not secondary_sql:
            continue

        alias = str(evidence.get("alias") or evidence.get("formula_alias") or "").strip()
        if not _is_safe_sql_identifier(alias):
            alias = f"derived_{column_name}" if _is_safe_sql_identifier(column_name) else "derived_value"
        return f"({primary_sql} - COALESCE({secondary_sql}, 0))", alias

    return None, None


def _measure_expression(
    query_context: dict[str, Any],
    measure_column: dict[str, Any],
) -> tuple[str | None, str]:
    table_name = str(measure_column.get("table") or "")
    column_name = str(measure_column.get("column") or "")
    primary_sql = _qualified_column(table_name, column_name)
    if not primary_sql:
        return None, "total_value"

    semantic_type = str(measure_column.get("semantic_type") or "").lower()
    if semantic_type != "money":
        alias = f"total_{column_name}" if _is_safe_sql_identifier(column_name) else "total_value"
        return primary_sql, alias

    money_columns = [
        entry
        for entry in _money_columns_for_table(query_context, table_name)
        if str(entry.get("column") or "") != column_name
    ]
    formula_sql, formula_alias = _formula_expression_from_evidence(query_context, measure_column, money_columns)
    if formula_sql and formula_alias:
        return formula_sql, formula_alias

    if not _has_same_table_state_context(query_context, table_name):
        alias = f"total_{column_name}" if _is_safe_sql_identifier(column_name) else "total_value"
        return primary_sql, alias

    if money_columns:
        return None, "unresolved_value"

    if len(money_columns) != 1:
        alias = f"total_{column_name}" if _is_safe_sql_identifier(column_name) else "total_value"
        return primary_sql, alias
    alias = f"total_{column_name}" if _is_safe_sql_identifier(column_name) else "total_value"
    return primary_sql, alias


def _is_simple_repair_blocked(query_context: dict[str, Any]) -> bool:
    plan = query_context.get("plan") or {}
    return (
        str(plan.get("intent") or "") in {"list", "count"}
        and not plan.get("dimension")
        and not plan.get("grouping")
        and not plan.get("filters")
        and not plan.get("date_range")
        and not (set(plan.get("semantic_hints") or set()) & {"money", "quantity", "percentage", "date", "status"})
    )


def _reverse_join_edge(edge: dict[str, Any]) -> dict[str, Any]:
    from_table = edge.get("to_table")
    from_column = edge.get("to_column")
    to_table = edge.get("from_table")
    to_column = edge.get("from_column")
    return {
        "from_table": from_table,
        "from_column": from_column,
        "to_table": to_table,
        "to_column": to_column,
        "join_condition": f"{from_table}.{from_column} = {to_table}.{to_column}",
    }


def _join_path_between(query_context: dict[str, Any], start_table: str, end_table: str) -> list[dict[str, Any]] | None:
    if start_table == end_table:
        return []
    for join_path in query_context.get("join_paths") or []:
        path = list(join_path.get("path") or [])
        if join_path.get("from_table") == start_table and join_path.get("to_table") == end_table:
            return path
        if join_path.get("from_table") == end_table and join_path.get("to_table") == start_table:
            return [_reverse_join_edge(edge) for edge in reversed(path)]
    return None


def _build_joined_aggregate_repair_sql(query_context: dict[str, Any], knowledge_base: dict[str, Any]) -> str | None:
    """Build a conservative grouped aggregate SQL from selected schema and FK paths."""
    plan = query_context.get("plan") or {}
    if _is_simple_repair_blocked(query_context):
        return None
    if plan.get("unresolved_metrics"):
        return None
    if not plan.get("dimension") and not plan.get("grouping"):
        return None

    dimension_column = _find_dimension_column(query_context)
    measure_column = _find_measure_column(query_context, dimension_column)
    if not dimension_column or not measure_column:
        return None

    dimension_table = str(dimension_column.get("table") or "")
    dimension_name = str(dimension_column.get("column") or "")
    measure_table = str(measure_column.get("table") or "")
    measure_name = str(measure_column.get("column") or "")
    dimension_sql = _qualified_column(dimension_table, dimension_name)
    measure_sql, alias = _measure_expression(query_context, measure_column)
    if not dimension_sql or not measure_sql:
        return None

    path = _join_path_between(query_context, dimension_table, measure_table)
    if path is None:
        return None

    joined_tables = {dimension_table}
    join_clauses: list[str] = []
    current_table = dimension_table
    for edge in path:
        from_table = str(edge.get("from_table") or "")
        from_column = str(edge.get("from_column") or "")
        to_table = str(edge.get("to_table") or "")
        to_column = str(edge.get("to_column") or "")
        if current_table == to_table:
            edge = _reverse_join_edge(edge)
            from_table = str(edge.get("from_table") or "")
            from_column = str(edge.get("from_column") or "")
            to_table = str(edge.get("to_table") or "")
            to_column = str(edge.get("to_column") or "")
        if current_table != from_table:
            return None
        left = _qualified_column(from_table, from_column)
        right = _qualified_column(to_table, to_column)
        if not left or not right or not _is_safe_sql_identifier(to_table):
            return None
        join_clauses.append(f"JOIN {to_table} ON {left} = {right}")
        joined_tables.add(to_table)
        current_table = to_table

    if measure_table not in joined_tables:
        return None

    where_clauses: list[str] = []
    for filter_data in plan.get("filters") or []:
        table_name = str(filter_data.get("table") or "")
        column_name = str(filter_data.get("column") or "")
        if table_name not in joined_tables:
            continue
        qualified = _qualified_column(table_name, column_name)
        if not qualified:
            continue
        where_clauses.append(f"{qualified} = {_sql_literal(filter_data.get('value'))}")

    sql_parts = [
        f"SELECT {dimension_sql} AS group_value, SUM({measure_sql}) AS {alias}",
        f"FROM {dimension_table}",
        *join_clauses,
    ]
    if where_clauses:
        sql_parts.append("WHERE " + " AND ".join(where_clauses))
    sql_parts.append(f"GROUP BY {dimension_sql}")
    return " ".join(sql_parts) + ";"


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
        pipeline_context: Optional[Dict[str, Any]] = None,
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
        self.last_query_context = None

        if _UNSAFE_NL_RE.search(question):
            return False, "Unsafe request blocked. Only SELECT questions are allowed.", None, None

        business_glossary = _scope_glossary_to_knowledge_base(business_glossary, knowledge_base)

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

        if _pipeline_context_matches_question(pipeline_context, rewritten_question):
            query_context = deepcopy(pipeline_context.get("query_context") or {})
            logger.debug("Reusing query context from QueryPipeline for this question")
        else:
            query_context = build_query_context(
                rewritten_question,
                knowledge_base,
                business_glossary,
                vector_retriever=vector_retriever,
            )
        query_context = _merge_pipeline_metadata(query_context, pipeline_context)
        query_context = query_context if isinstance(query_context, dict) else {}
        query_context["plan"] = query_context.get("plan") if isinstance(query_context.get("plan"), dict) else {}
        query_context["vector_results"] = (
            query_context.get("vector_results")
            if isinstance(query_context.get("vector_results"), dict)
            else {}
        )
        query_context["retrieved_context"] = (
            query_context.get("retrieved_context")
            if isinstance(query_context.get("retrieved_context"), dict)
            else {}
        )
        self.last_query_context = query_context
        logger.info(
            "[PIPELINE RESULT] route=%s selected_tables=%s join_paths=%s complex_sql_plan=%s",
            query_context.get("route_recommendation"),
            [entry.get("table") for entry in (query_context.get("selected_tables") or [])],
            len(query_context.get("join_paths") or []),
            bool(query_context.get("complex_sql_plan")),
        )
        route_recommendation = _route_recommendation(query_context)
        if isinstance(pipeline_context, dict) and route_recommendation in {"needs_clarification", "cannot_plan_safely"}:
            route_reason = (
                "query pipeline requested clarification before SQL generation"
                if route_recommendation == "needs_clarification"
                else "query pipeline could not plan this question safely from runtime evidence"
            )
            _set_route(query_context, "fallback-failed", route_reason)
            return False, _planning_block_message(query_context, route_recommendation), None, None
        if _needs_simple_table_clarification(query_context):
            _set_route(
                query_context,
                "fallback-failed",
                "simple table-browse question matched multiple tables with similar confidence",
            )
            return False, _clarification_message(query_context), None, None
        # Safe context normalization (Phase 2 Step 4)
        query_context = query_context or {}
        intent = query_context.get("intent") or {}
        plan = query_context.get("plan") or {}
        retrieved_context = query_context.get("retrieved_context") or {}
        route_recommendation = query_context.get("route_recommendation") or {}
        complex_sql_plan = query_context.get("complex_sql_plan") or {}
        if not isinstance(intent, dict):
            intent = {"intent_type": str(intent or plan.get("intent") or "").strip().lower()}

        # Determine route (Phase 2 Step 5)
        route = (
            query_context.get("route")
            or query_context.get("route_used")
            or (route_recommendation if isinstance(route_recommendation, str) else route_recommendation.get("route") if isinstance(route_recommendation, dict) else None)
            or plan.get("route")
        )
        
        # Simple single-table guard (Phase 2 Bug Fix)
        # Route simple list/count queries before complex_sql_plan block
        selected_tables = query_context.get("selected_tables") or []
        intent_type = intent.get("intent_type", "")
        is_simple_list_count = (
            len(selected_tables) == 1
            and intent_type in {"list", "count"}
            and not query_context.get("join_paths")
        )
        
        if is_simple_list_count:
            logger.info(f"Simple single-table list/count detected: intent={intent_type}, table={selected_tables[0].get('table') if selected_tables else 'unknown'}")
            route = "simple_rule_based"
        
        # Block complex queries (Phase 2 Step 6)
        if complex_sql_plan and not is_simple_list_count:
            _set_route(query_context, "complex_deterministic_not_ready", "Complex SQL not implemented in Phase 2")
            return False, (
                "Complex deterministic SQL generation is not implemented yet. "
                "The query was planned, but SQL was not generated because AI SQL generation is disabled."
            ), None, None
        
        # Block unsafe/missing evidence
        if route in {"needs_clarification", "cannot_plan_safely"}:
            _set_route(query_context, route, "Cannot plan safely from available evidence")
            return False, "Cannot plan this query safely from available schema evidence.", None, None
        
        # Simple query path - use simple_query_generator only (Phase 2 Step 5)
        if route in {"simple_rule_based", "simple_rule_ok", "rule_based", "simple"}:
            try:
                simple_sql = generate_simple_sql(
                    rewritten_question,
                    knowledge_base,
                    query_plan=plan,
                    business_glossary=business_glossary,
                    selected_tables=query_context.get("selected_tables"),
                    vector_results=query_context.get("vector_results"),
                )
                if simple_sql:
                    # Validate SQL
                    safety_ok, safety_reason = validate_sql(simple_sql)
                    struct_ok, struct_reason = validate_sql_structure(simple_sql, knowledge_base)
                    
                    if safety_ok and struct_ok:
                        _set_route(query_context, "rule-based", "Simple rule-based SQL generation")
                        self.conversation_memory.add_turn(
                            user_question=question,
                            is_follow_up=is_follow_up,
                            rewritten_question=rewritten_question,
                            generated_sql=simple_sql,
                        )
                        return True, "SQL generated successfully (rule-based)", simple_sql, None
                    else:
                        fail_reason = safety_reason if not safety_ok else struct_reason
                        return False, f"SQL validation failed: {fail_reason}", None, None
                else:
                    return False, "Simple SQL generator returned no SQL for this question.", None, None
            except Exception as e:
                logger.error(f"Simple SQL generation failed: {e}")
                return False, f"Simple SQL generation failed: {str(e)}", None, None

        deterministic_aggregate = generate_single_table_aggregate_sql(
            query_context=query_context,
            knowledge_base=knowledge_base,
        )
        if deterministic_aggregate.status == "generated" and deterministic_aggregate.sql:
            safety_ok, safety_reason = validate_sql(deterministic_aggregate.sql)
            struct_ok, struct_reason = validate_sql_structure(deterministic_aggregate.sql, knowledge_base)
            if safety_ok and struct_ok:
                _set_route(query_context, "deterministic-aggregate", deterministic_aggregate.reason)
                self.conversation_memory.add_turn(
                    user_question=question,
                    is_follow_up=is_follow_up,
                    rewritten_question=rewritten_question,
                    generated_sql=deterministic_aggregate.sql,
                )
                return True, "SQL generated successfully (deterministic aggregate)", deterministic_aggregate.sql, None
            fail_reason = safety_reason if not safety_ok else struct_reason
            return False, f"SQL validation failed: {fail_reason}", None, None

        if deterministic_aggregate.status == "cannot_plan_safely":
            _set_route(query_context, "cannot_plan_safely", deterministic_aggregate.reason)
            return False, "Cannot plan this query safely from available schema evidence.", None, None
        
        # Default: complex query not implemented
        _set_route(query_context, "complex_deterministic_not_ready", "Complex SQL not implemented in Phase 2")
        return False, "Complex deterministic SQL generation is not implemented yet. Please try a simpler query.", None, None
    
    def _build_success_result(
        self,
        *,
        question: str,
        sql: str,
        route: str,
        validation_result: dict | None = None,
        query_context: dict | None = None,
        rows: list | None = None,
    ) -> dict:
        return {
            "success": True,
            "question": question,
            "generated_sql": sql,
            "sql": sql,
            "route": route,
            "route_used": route,
            "validation_result": validation_result or {},
            "query_context": query_context or {},
            "rows": rows or [],
            "error": None,
        }

    def _build_failure_result(
        self,
        *,
        question: str,
        error: str,
        route: str = "cannot_plan_safely",
        validation_result: dict | None = None,
        query_context: dict | None = None,
    ) -> dict:
        return {
            "success": False,
            "question": question,
            "generated_sql": None,
            "sql": None,
            "route": route,
            "route_used": route,
            "validation_result": validation_result or {},
            "query_context": query_context or {},
            "rows": [],
            "error": error,
        }

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
