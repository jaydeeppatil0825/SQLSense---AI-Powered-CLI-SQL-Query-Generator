"""Fail-closed planner result compatibility exports."""

from __future__ import annotations

from query_pipeline.planner.join_resolver import (
    _join_failure_context,
    _joined_aggregate_failure_context,
)

__all__ = ["_join_failure_context", "_joined_aggregate_failure_context"]
