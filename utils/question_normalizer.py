"""Compatibility alias for older imports."""

import sys

from query_pipeline import question_normalizer as _impl

sys.modules[__name__] = _impl
