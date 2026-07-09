"""Compatibility alias for older imports."""

import sys

from kb_pipeline.vector import persistence as _impl

sys.modules[__name__] = _impl
