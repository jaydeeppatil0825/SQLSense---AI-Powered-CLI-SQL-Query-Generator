"""Backward-compatible module alias for the Query Planning conversation memory."""

from importlib import import_module
import sys

_impl = import_module("query_pipeline.conversation.conversation_memory")
sys.modules[__name__] = _impl
