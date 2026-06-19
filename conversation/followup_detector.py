"""Backward-compatible module alias for the Query Planning follow-up detector."""

from importlib import import_module
import sys

_impl = import_module("query_pipeline.conversation.followup_detector")
sys.modules[__name__] = _impl
