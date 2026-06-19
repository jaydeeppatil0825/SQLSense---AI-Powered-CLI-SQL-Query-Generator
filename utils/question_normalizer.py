"""Backward-compatible module alias for the Query Planning normalizer."""

from importlib import import_module
import sys

_impl = import_module("query_pipeline.question_normalizer")
sys.modules[__name__] = _impl
