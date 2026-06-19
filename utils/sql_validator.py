"""Backward-compatible module alias for the SQL Generation validator."""

from importlib import import_module
import sys

_impl = import_module("sql_pipeline.sql_validator")
sys.modules[__name__] = _impl
