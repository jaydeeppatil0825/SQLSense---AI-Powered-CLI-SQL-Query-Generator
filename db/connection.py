"""Compatibility alias for older imports."""

import sys

from kb_pipeline import connection as _impl

sys.modules[__name__] = _impl
