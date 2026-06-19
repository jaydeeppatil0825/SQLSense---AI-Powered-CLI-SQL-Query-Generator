"""Backward-compatible module alias for the SQL Generation simple generator."""

from importlib import import_module
import sys

_impl = import_module("sql_pipeline.simple_query_generator")
sys.modules[__name__] = _impl
