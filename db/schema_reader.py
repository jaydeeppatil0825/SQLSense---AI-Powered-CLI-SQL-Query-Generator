"""Backward-compatible wrapper for the KB pipeline schema reader."""

import kb_pipeline.schema_reader as _impl
from kb_pipeline.schema_reader import *  # noqa: F401,F403


def __getattr__(name):
    return getattr(_impl, name)
