"""Compatibility alias for older imports."""

import sys

from kb_pipeline import data_profiler as _impl

sys.modules[__name__] = _impl
