"""Backward-compatible module alias for the KB pipeline AI semantic enricher."""

from importlib import import_module
import sys

_impl = import_module("kb_pipeline.ai_semantic_enricher")
sys.modules[__name__] = _impl
