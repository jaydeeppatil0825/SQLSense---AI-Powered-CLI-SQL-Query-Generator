"""
conversation/followup_detector.py
===================================
Detects whether a user question is a follow-up to a previous question.
"""

from __future__ import annotations

import re

from utils.logger import get_logger


# Follow-up indicator words and phrases
_FOLLOWUP_INDICATORS = {
    "they",
    "them",
    "those",
    "these",
    "it",
    "that",
    "their",
    "same",
    "above",
    "previous",
    "now",
    "only",
    "filter",
    "compare",
    "what about",
    "where do they",
    "show their",
    "make it",
    "sort it",
    "change it",
    "from that",
    "for them",
    "this result",
    "this",
}

# Patterns for follow-up detection
_FOLLOWUP_PATTERNS = [
    r"\bwhere do (they|them) live\b",
    r"\bshow (their|their)\b",
    r"\bnow (only|just)\b",
    r"\bonly (paid|unpaid|pending|cancelled|delivered|shipped|processing|active|inactive|open|closed|resolved)\b",
    r"\bmake it top \d+\b",
    r"\bmake it \d+\b",
    r"\bsort (it|them|highest|lowest|first|last)\b",
    r"\bcompare with\b",
    r"\bwhat about\b",
    r"\bshow chart for this\b",
    r"\bgive insights for this\b",
    r"\bexplain this\b",
    r"\bsummarize this\b",
    r"\bfor them\b",
    r"\bfrom that\b",
]

# Common business terms that might indicate a new question
_NEW_QUESTION_INDICATORS = {
    "show",
    "list",
    "display",
    "count",
    "total",
    "sum",
    "average",
    "avg",
    "mean",
    "top",
    "bottom",
    "highest",
    "lowest",
    "latest",
    "recent",
    "newest",
    "oldest",
}


def detect_follow_up(user_question: str, conversation_memory) -> tuple[bool, str]:
    """
    Detect whether a user question is a follow-up to a previous question.
    
    Args:
        user_question: The user's question
        conversation_memory: The ConversationMemory instance
    
    Returns:
        Tuple of (is_follow_up, reason)
    """
    logger = get_logger()
    
    # Rule 1: If there is no previous question/context, return False
    context = conversation_memory.get_last_context()
    if context["turn_count"] == 0:
        logger.debug("No previous context, not a follow-up")
        return False, "no_previous_context"
    
    question_lower = user_question.lower().strip()
    
    # Rule 2: Check for follow-up indicator words
    words = set(question_lower.split())
    if words & _FOLLOWUP_INDICATORS:
        # Check if it's a clear new question with a table/business term
        # If it has a clear new table and no strong follow-up indicators, it might be new
        if _has_clear_new_table(question_lower) and not _has_strong_followup_indicators(question_lower):
            logger.debug(f"Has clear new table, treating as new question: {user_question}")
            return False, "new_table_detected"
        
        logger.debug(f"Follow-up detected via indicator words: {user_question}")
        return True, "followup_indicator"
    
    # Rule 3: Check for follow-up patterns
    for pattern in _FOLLOWUP_PATTERNS:
        if re.search(pattern, question_lower, re.IGNORECASE):
            logger.debug(f"Follow-up detected via pattern: {pattern}")
            return True, "followup_pattern"
    
    # Rule 4: If ambiguous, return False and let normal SQL flow handle it
    logger.debug(f"Ambiguous question, treating as new question: {user_question}")
    return False, "ambiguous"


def _has_clear_new_table(question: str) -> bool:
    """
    Check if the question has a clear new table/business term.
    
    Args:
        question: The lowercased question
    
    Returns:
        True if a clear new table is detected
    """
    # Common table names that might indicate a new question
    common_tables = {
        "customers",
        "orders",
        "products",
        "employees",
        "payments",
        "support_tickets",
        "tickets",
        "order_items",
    }
    
    words = set(question.split())
    return bool(words & common_tables)


def _has_strong_followup_indicators(question: str) -> bool:
    """
    Check if the question has strong follow-up indicators.
    
    Args:
        question: The lowercased question
    
    Returns:
        True if strong follow-up indicators are present
    """
    strong_indicators = {
        "they",
        "them",
        "their",
        "now",
        "make it",
        "sort it",
        "change it",
        "what about",
        "for them",
        "from that",
        "this result",
    }
    
    for indicator in strong_indicators:
        if indicator in question:
            return True
    
    return False
