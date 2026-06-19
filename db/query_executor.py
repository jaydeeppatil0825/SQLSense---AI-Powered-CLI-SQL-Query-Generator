"""Backward-compatible module alias for the SQL Generation executor."""

from importlib import import_module
import sys

_impl = import_module("sql_pipeline.query_executor")
sys.modules[__name__] = _impl
