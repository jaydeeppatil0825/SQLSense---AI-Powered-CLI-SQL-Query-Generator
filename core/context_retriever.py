"""Backward-compatible module alias for the Query Planning context retriever."""

from importlib import import_module
import sys

_impl = import_module("query_pipeline.context_retriever")
sys.modules[__name__] = _impl
