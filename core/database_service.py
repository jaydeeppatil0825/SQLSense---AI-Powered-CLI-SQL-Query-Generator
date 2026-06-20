"""Backward-compatible wrapper for the KB pipeline database service."""

from importlib import import_module
import sys

_MODULE = import_module("kb_pipeline.database_service")

__all__ = getattr(_MODULE, "__all__", [])

for _name in dir(_MODULE):
    if _name.startswith("_") and _name not in {"__doc__", "__all__"}:
        continue
    globals()[_name] = getattr(_MODULE, _name)

sys.modules[__name__] = _MODULE
