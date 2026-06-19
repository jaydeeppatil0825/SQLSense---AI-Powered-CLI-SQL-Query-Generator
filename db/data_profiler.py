"""Backward-compatible wrapper for the KB pipeline data profiler."""

import kb_pipeline.data_profiler as _impl
from kb_pipeline.data_profiler import *  # noqa: F401,F403


def __getattr__(name):
    return getattr(_impl, name)
