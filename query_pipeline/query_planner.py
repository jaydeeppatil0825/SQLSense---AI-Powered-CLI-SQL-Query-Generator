"""Public compatibility facade for deterministic query planning.

Canonical implementation lives in ``query_pipeline.planner.orchestrator``.
"""

from __future__ import annotations

import sys as _sys

from query_pipeline.planner import orchestrator as _orchestrator


# Keep historical public and private imports working while avoiding duplicate
# planner logic in this entry-point module.
globals().update(
    {
        name: getattr(_orchestrator, name)
        for name in dir(_orchestrator)
        if not name.startswith("__")
    }
)


def __getattr__(name: str):
    return getattr(_orchestrator, name)


_sys.modules[__name__] = _orchestrator
