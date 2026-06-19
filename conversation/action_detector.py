"""Backward-compatible module alias for the Query Planning action detector."""

from importlib import import_module
import sys

_impl = import_module("query_pipeline.conversation.action_detector")
sys.modules[__name__] = _impl
