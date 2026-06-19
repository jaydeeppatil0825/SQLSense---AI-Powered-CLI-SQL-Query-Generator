"""Backward-compatible module alias for the SQL Generation orchestrator."""

from importlib import import_module
import sys

_impl = import_module("sql_pipeline.question_service")
sys.modules[__name__] = _impl
