"""
SQL Generation Pipeline package.

This package contains prompt construction, deterministic SQL generation,
AI-backed SQL generation, SQL validation, safe execution, and the central
question/result orchestration layer for the CLI flow.
"""

from importlib import import_module

__all__ = [
    "QuestionService",
    "ResultService",
    "generate_sql",
    "generate_sql_with_retry",
    "generate_simple_sql",
    "generate_erp_sql",
    "build_sql_prompt",
    "validate_sql",
    "validate_sql_structure",
    "execute_query",
]

_MODULE_MAP = {
    "QuestionService": "sql_pipeline.question_service",
    "ResultService": "sql_pipeline.result_service",
    "generate_sql": "sql_pipeline.sql_generator",
    "generate_sql_with_retry": "sql_pipeline.sql_generator",
    "generate_simple_sql": "sql_pipeline.simple_query_generator",
    "generate_erp_sql": "sql_pipeline.erp_query_generator",
    "build_sql_prompt": "sql_pipeline.prompt_builder",
    "validate_sql": "sql_pipeline.sql_validator",
    "validate_sql_structure": "sql_pipeline.sql_validator",
    "execute_query": "sql_pipeline.query_executor",
}


def __getattr__(name):
    module_name = _MODULE_MAP.get(name)
    if not module_name:
        raise AttributeError(name)
    return getattr(import_module(module_name), name)
