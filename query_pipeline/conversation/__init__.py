"""
Conversation helpers for the Query Planning Pipeline.
"""

from importlib import import_module

__all__ = [
    "detect_conversation_action",
    "detect_follow_up",
    "rewrite_follow_up_question",
    "ConversationMemory",
]

_MODULE_MAP = {
    "detect_conversation_action": "query_pipeline.conversation.action_detector",
    "detect_follow_up": "query_pipeline.conversation.followup_detector",
    "rewrite_follow_up_question": "query_pipeline.conversation.question_rewriter",
    "ConversationMemory": "query_pipeline.conversation.conversation_memory",
}


def __getattr__(name):
    module_name = _MODULE_MAP.get(name)
    if not module_name:
        raise AttributeError(name)
    return getattr(import_module(module_name), name)
