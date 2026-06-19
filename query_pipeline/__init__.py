"""
Query Planning Pipeline package.

This package contains question normalization, intent building, dynamic context
retrieval, structured planning, and follow-up/conversation helpers used before
SQL generation.
"""

from importlib import import_module

__all__ = [
    "QueryPipeline",
    "QueryPipelineResult",
    "build_intent",
    "retrieve_context",
    "build_query_context",
    "normalize_question",
    "is_too_ambiguous",
]

_MODULE_MAP = {
    "QueryPipeline": "query_pipeline.query_pipeline",
    "QueryPipelineResult": "query_pipeline.query_pipeline",
    "build_intent": "query_pipeline.intent_builder",
    "retrieve_context": "query_pipeline.context_retriever",
    "build_query_context": "query_pipeline.query_planner",
    "normalize_question": "query_pipeline.question_normalizer",
    "is_too_ambiguous": "query_pipeline.question_normalizer",
}


def __getattr__(name):
    module_name = _MODULE_MAP.get(name)
    if not module_name:
        raise AttributeError(name)
    return getattr(import_module(module_name), name)
