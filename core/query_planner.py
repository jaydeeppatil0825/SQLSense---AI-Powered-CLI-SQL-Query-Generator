"""Compatibility shim for tests and older imports.

Active planner code lives in :mod:`query_pipeline.query_planner`.
"""

from query_pipeline.intent_builder import build_intent
from query_pipeline.query_planner import *  # noqa: F401,F403
