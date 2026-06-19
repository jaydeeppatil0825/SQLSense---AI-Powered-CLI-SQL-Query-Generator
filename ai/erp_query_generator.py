"""
Legacy compatibility wrapper for the retired ERP-specific SQL generator.

SQLSense now routes SQL generation through dynamic runtime schema evidence from
the query pipeline. This module remains only to preserve import compatibility
for older callers while ensuring no fixed ERP templates are active.
"""

from __future__ import annotations


def generate_erp_sql(question: str, knowledge_base: dict, query_plan: dict | None = None) -> str | None:
    """
    Legacy no-op entry point.

    Returns None so the active dynamic SQL generation path can decide how to
    proceed using runtime KB, glossary, retrieval, join paths, and validator
    checks.
    """
    return None
