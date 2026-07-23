"""Small planner-context utilities shared by orchestration boundaries."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


def copy_planner_context(context: dict[str, Any]) -> dict[str, Any]:
    return deepcopy(context)
