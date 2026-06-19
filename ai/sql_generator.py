"""Backward-compatible module alias for the SQL Generation pipeline generator."""

from importlib import import_module
import sys

_impl = import_module("sql_pipeline.sql_generator")
sys.modules[__name__] = _impl
