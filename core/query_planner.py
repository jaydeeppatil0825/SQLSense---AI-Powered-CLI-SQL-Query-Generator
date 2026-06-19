"""Backward-compatible module alias for the Query Planning planner."""

from importlib import import_module
import sys

_impl = import_module("query_pipeline.query_planner")
sys.modules[__name__] = _impl
