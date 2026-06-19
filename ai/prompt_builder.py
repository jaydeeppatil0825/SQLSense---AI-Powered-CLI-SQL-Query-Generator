"""Backward-compatible module alias for the SQL Generation prompt builder."""

from importlib import import_module
import sys

_impl = import_module("sql_pipeline.prompt_builder")
sys.modules[__name__] = _impl
