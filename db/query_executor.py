"""Compatibility alias for older imports."""

import sys

from sql_pipeline import query_executor as _impl

sys.modules[__name__] = _impl
