"""
utils/question_normalizer.py
============================
Performs lightweight cleanup on natural-language questions before planning.

This module is intentionally schema-agnostic: it prepares text only and does
not rewrite business language into inferred tables, columns, or intents.
"""

from __future__ import annotations

import re
from typing import Tuple

from utils.logger import get_logger


_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1F\x7F]")
_WHITESPACE_RE = re.compile(r"\s+")


def normalize_question(user_input: str) -> Tuple[str, bool]:
    """
    Clean a user question without changing its business meaning.

    Normalization is limited to:
    - trimming leading/trailing whitespace
    - removing control characters
    - collapsing repeated whitespace

    Args:
        user_input: The raw user input string.

    Returns:
        Tuple of (normalized_question, was_normalized).
    """
    logger = get_logger()

    if user_input is None:
        return user_input, False

    cleaned = _sanitize_question_text(user_input)
    was_normalized = cleaned != user_input

    if was_normalized:
        logger.debug("Normalized question text from %r to %r", user_input, cleaned)
    else:
        logger.debug("Question text required no normalization: %r", cleaned)

    return cleaned, was_normalized


def _sanitize_question_text(user_input: str) -> str:
    """Return a whitespace-normalized, control-character-safe question."""
    cleaned = _CONTROL_CHARS_RE.sub(" ", user_input)
    cleaned = _WHITESPACE_RE.sub(" ", cleaned).strip()
    return cleaned


def _is_already_clear_question(question: str) -> bool:
    """
    Check if the input already looks like a complete natural-language request.

    This helper stays generic and is used only for tests and ambiguity checks.
    """
    action_verbs = {
        "show", "list", "display", "get", "give", "find", "search",
        "count", "calculate", "sum", "average",
        "what", "which", "who", "when", "where", "how",
    }

    words = question.split()
    if words and words[0] in action_verbs:
        return True

    if question.startswith("how many") or question.startswith("how much"):
        return True

    if question.startswith("can you"):
        return True

    if len(words) < 2:
        return False

    if "all" in words:
        return True

    return False


def is_too_ambiguous(user_input: str) -> bool:
    """
    Check if the input is too ambiguous to normalize safely.

    Args:
        user_input: The raw user input string.

    Returns:
        True if input is too ambiguous and requires clarification.
    """
    if not user_input or not user_input.strip():
        return True

    if len(user_input.strip()) <= 2:
        return True

    if re.match(r"^[\W_]+$", user_input.strip()):
        return True

    return False
