"""
conversation/followup_detector.py
===================================
Detects whether a user question is a follow-up to a previous question.

Detection must stay generic and must not depend on business- or schema-
specific words.
"""

from __future__ import annotations

import re

from utils.logger import get_logger


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
    "next",
    "now",
    "only",
    "just",
    "filter",
    "compare",
    "more",
    "again",
    "this",
}

_FOLLOWUP_PATTERNS = [
    r"\bmake it top \d+\b",
    r"\bmake it \d+\b",
    r"\b(?:sort|order) (?:it|them|this)\b",
    r"\bcompare with\b",
    r"\bwhat about\b",
    r"\bshow only\b",
    r"\bnow only\b",
    r"\bonly [a-z0-9_ -]+\b",
    r"\bnext\b",
    r"\bprevious\b",
    r"\bmore like this\b",
]

_NEW_QUESTION_PREFIX_RE = re.compile(
    r"^\s*(?:show|list|display|get|count|total|sum|average|avg|max|maximum|min|minimum|top|limit|before|after|between|greater\s+than|less\s+than)\b",
    re.IGNORECASE,
)

_STRONG_FOLLOWUP_RE = re.compile(
    r"\b(?:what about|make it|sort it|order it|compare with|show only|now only|more like this|next|previous)\b",
    re.IGNORECASE,
)


def detect_follow_up(user_question: str, conversation_memory) -> tuple[bool, str]:
    """Detect whether a user question is a follow-up to a previous question."""
    logger = get_logger()
    context = conversation_memory.get_last_context()
    if context["turn_count"] == 0:
        logger.debug("No previous context, not a follow-up")
        return False, "no_previous_context"

    question_lower = user_question.lower().strip()

    if _STRONG_FOLLOWUP_RE.search(question_lower):
        logger.debug("Follow-up detected via strong generic follow-up phrase")
        return True, "followup_pattern"

    if _looks_like_standalone_query(question_lower) and not _has_pronoun_reference(question_lower):
        logger.debug("Standalone generic query detected; treating as a new question")
        return False, "standalone_query"

    words = set(question_lower.split())
    if words & _FOLLOWUP_INDICATORS:
        logger.debug("Follow-up detected via generic indicator words")
        return True, "followup_indicator"

    for pattern in _FOLLOWUP_PATTERNS:
        if re.search(pattern, question_lower, re.IGNORECASE):
            logger.debug(f"Follow-up detected via pattern: {pattern}")
            return True, "followup_pattern"

    logger.debug("Ambiguous question, treating as a new question")
    return False, "ambiguous"


def _looks_like_standalone_query(question: str) -> bool:
    return bool(_NEW_QUESTION_PREFIX_RE.search(question))


def _has_pronoun_reference(question: str) -> bool:
    pronouns = {"they", "them", "their", "it", "that", "those", "these", "this"}
    return any(token in question.split() for token in pronouns)
