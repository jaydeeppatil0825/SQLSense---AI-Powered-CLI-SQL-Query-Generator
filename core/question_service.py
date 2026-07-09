"""Compatibility alias for older imports.

Active question-service code lives in :mod:`sql_pipeline.question_service`.
"""

import sys

from sql_pipeline import question_service as _impl

sys.modules[__name__] = _impl
