"""Compatibility alias for older imports."""

import sys

from query_pipeline import context_retriever as _impl

sys.modules[__name__] = _impl
