"""Backward-compatible module alias for the Query Planning intent builder."""

from importlib import import_module
import sys

_impl = import_module("query_pipeline.intent_builder")
sys.modules[__name__] = _impl
