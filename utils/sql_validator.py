"""Compatibility alias for older imports."""

import sys

from sql_pipeline import sql_validator as _impl

sys.modules[__name__] = _impl
