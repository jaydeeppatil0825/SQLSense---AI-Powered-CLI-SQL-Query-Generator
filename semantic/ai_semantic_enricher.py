"""Backward-compatible wrapper for the KB pipeline AI semantic enricher."""

import kb_pipeline.ai_semantic_enricher as _impl
from kb_pipeline.ai_semantic_enricher import *  # noqa: F401,F403


def __getattr__(name):
    return getattr(_impl, name)
