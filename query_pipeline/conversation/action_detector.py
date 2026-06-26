"""
conversation/action_detector.py
===================================
Detects non-SQL conversation actions that remain supported in deterministic
runtime mode.

Runtime insight, explanation, summary, and chart branches are intentionally
disabled here.
"""

from __future__ import annotations

import re

from utils.logger import get_logger


_ACTION_PATTERNS = {
    "new_chat": [
        r"\bnew chat\b",
        r"\bclear chat\b",
        r"\breset conversation\b",
        r"\bstart new conversation\b",
    ],
    "repeat_last_sql": [
        r"\bshow last sql\b",
        r"\brepeat sql\b",
        r"\bshow last query\b",
        r"\brepeat query\b",
    ],
    "show_history": [
        r"\bshow conversation history\b",
        r"\bshow chat history\b",
        r"\bshow history\b",
    ],
}


def detect_conversation_action(user_question: str) -> str | None:
    """Detect a supported deterministic conversation action."""
    logger = get_logger()
    question_lower = user_question.lower().strip()

    for action, patterns in _ACTION_PATTERNS.items():
        for pattern in patterns:
            if re.search(pattern, question_lower, re.IGNORECASE):
                logger.debug(f"Detected action: {action}")
                return action

    logger.debug("No supported deterministic conversation action detected")
    return None
