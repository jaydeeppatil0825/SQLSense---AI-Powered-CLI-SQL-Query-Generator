"""
Compatibility wrapper for the neutral KB schema-facts implementation.
"""

from importlib import import_module
import sys

_MODULE = import_module("kb_pipeline.schema_facts")

__all__ = getattr(_MODULE, "__all__", [])

for _name in __all__:
    globals()[_name] = getattr(_MODULE, _name)

sys.modules[__name__] = _MODULE
