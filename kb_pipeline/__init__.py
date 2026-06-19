"""
KB Pipeline package.

This package is the physical home of SQLSense's database knowledge pipeline.
It contains runtime database connection, schema/profile extraction, semantic
enrichment, glossary generation, relationship graph logic, and vector-index
support for the CLI flow.
"""

from importlib import import_module

__all__ = ["DatabaseService"]


def __getattr__(name):
    if name == "DatabaseService":
        return getattr(import_module("kb_pipeline.database_service"), name)
    raise AttributeError(name)
