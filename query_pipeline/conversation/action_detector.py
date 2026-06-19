"""
conversation/action_detector.py
===================================
Detects conversation actions like chart, insights, new chat, etc.
"""

from __future__ import annotations

import re

from utils.logger import get_logger


# Action patterns
_ACTION_PATTERNS = {
    "chart": [
        r"\bshow chart\b",
        r"\bgenerate chart\b",
        r"\bchart for this\b",
        r"\bcreate chart\b",
        r"\bplot this\b",
        r"\bvisualize this\b",
    ],
    "insights": [
        r"\bgive insight\b",
        r"\bgive insights\b",
        r"\bexplain this\b",
        r"\bsummarize this\b",
        r"\bsummarize this result\b",
        r"\banalyze this\b",
        r"\bget insights\b",
    ],
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
    """
    Detect if the user question is a conversation action.
    
    Args:
        user_question: The user's question
    
    Returns:
        Action name or None if not an action
    """
    logger = get_logger()
    question_lower = user_question.lower().strip()
    
    for action, patterns in _ACTION_PATTERNS.items():
        for pattern in patterns:
            if re.search(pattern, question_lower, re.IGNORECASE):
                logger.debug(f"Detected action: {action}")
                return action
    
    logger.debug("No action detected")
    return None
