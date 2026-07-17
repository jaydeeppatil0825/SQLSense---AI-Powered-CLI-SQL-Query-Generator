"""Compatibility shim for supported semantic-mapper imports."""

import sys

from kb_pipeline import semantic_mapper as _impl

sys.modules[__name__] = _impl
