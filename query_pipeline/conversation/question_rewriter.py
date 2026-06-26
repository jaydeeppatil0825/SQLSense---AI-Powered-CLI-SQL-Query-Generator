"""
conversation/question_rewriter.py
===================================
Rewrites follow-up questions into standalone questions using generic,
schema-agnostic patterns.

AI-based rewriting is disabled at runtime.
"""

from __future__ import annotations

import re

from utils.logger import get_logger


def rewrite_follow_up_question(
    user_question: str,
    conversation_memory,
    knowledge_base: dict,
    business_glossary: dict | None = None,
    ai_backend: str = "local",
) -> str:
    """
    Rewrite a follow-up question into a complete standalone question.

    The rewrite must stay generic and must not inject table names, column
    names, statuses, or business meaning.
    """
    del knowledge_base, business_glossary, ai_backend

    logger = get_logger()
    context = conversation_memory.get_last_context()
    last_question = context.get("last_rewritten_question") or context.get("last_user_question")

    if not last_question:
        logger.warning("No previous question found, returning original question")
        return user_question

    rewritten = _rewrite_with_rules(user_question, last_question)
    logger.info(f"Rule-based rewrite: '{user_question}' -> '{rewritten}'")
    return rewritten


def _rewrite_with_rules(user_question: str, last_question: str) -> str:
    question_lower = user_question.lower().strip()

    top_match = re.search(r"\bmake it top (\d+)\b", question_lower)
    if top_match:
        new_limit = top_match.group(1)
        if re.search(r"\btop \d+\b", last_question, re.IGNORECASE):
            return re.sub(r"\btop \d+\b", f"top {new_limit}", last_question, count=1, flags=re.IGNORECASE)
        if re.search(r"\blimit \d+\b", last_question, re.IGNORECASE):
            return re.sub(r"\blimit \d+\b", f"limit {new_limit}", last_question, count=1, flags=re.IGNORECASE)
        return f"{last_question} limit {new_limit}"

    limit_match = re.search(r"\bmake it (\d+)\b", question_lower)
    if limit_match:
        new_limit = limit_match.group(1)
        if re.search(r"\b(?:top|first|limit) \d+\b", last_question, re.IGNORECASE):
            return re.sub(r"\b(?:top|first|limit) \d+\b", lambda m: re.sub(r"\d+", new_limit, m.group(0)), last_question, count=1, flags=re.IGNORECASE)
        return f"{last_question} limit {new_limit}"

    only_match = re.search(r"\b(?:now\s+)?only\s+(.+)$", question_lower)
    if only_match:
        filter_value = only_match.group(1).strip(" .")
        return f"{last_question} filtered by {filter_value}"

    sort_match = re.search(r"\b(?:sort|order) (?:it|them|this)(?: by (.+))?$", question_lower)
    if sort_match:
        sort_value = str(sort_match.group(1) or "").strip(" .")
        if sort_value:
            return f"{last_question} sorted by {sort_value}"
        return f"{last_question} sorted"

    compare_match = re.search(r"\bcompare with (.+)$", question_lower)
    if compare_match:
        compare_value = compare_match.group(1).strip(" .")
        return f"{last_question} compared with {compare_value}"

    what_about_match = re.search(r"\bwhat about (.+)$", question_lower)
    if what_about_match:
        focus_value = what_about_match.group(1).strip(" .")
        return f"{last_question} focused on {focus_value}"

    show_attribute_match = re.search(r"\bshow (?:their|its|those|these) (.+)$", question_lower)
    if show_attribute_match:
        attribute = show_attribute_match.group(1).strip(" .")
        return f"{last_question} with {attribute}"

    next_match = re.search(r"\bnext\b", question_lower)
    if next_match:
        return f"{last_question} next"

    previous_match = re.search(r"\bprevious\b", question_lower)
    if previous_match:
        return f"{last_question} previous"

    return f"{last_question} {user_question}".strip()
