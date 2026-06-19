"""Backward-compatible wrapper for the KB pipeline connection module."""

import kb_pipeline.connection as _impl
from kb_pipeline.connection import *  # noqa: F401,F403


def __getattr__(name):
    return getattr(_impl, name)
