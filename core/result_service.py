"""Backward-compatible module alias for the SQL Generation result service."""

from importlib import import_module
import sys

_impl = import_module("sql_pipeline.result_service")
sys.modules[__name__] = _impl
