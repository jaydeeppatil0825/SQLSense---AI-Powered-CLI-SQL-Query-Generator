"""Backward-compatible module alias for the Query Planning pipeline."""

from importlib import import_module
import sys

_impl = import_module("query_pipeline.query_pipeline")
sys.modules[__name__] = _impl
