"""Backward-compatible module alias for the Query Planning question rewriter."""

from importlib import import_module
import sys

_impl = import_module("query_pipeline.conversation.question_rewriter")
sys.modules[__name__] = _impl
