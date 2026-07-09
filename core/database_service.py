"""Compatibility alias for older imports.

Active database-service code lives in :mod:`kb_pipeline.database_service`.
"""

import sys

from kb_pipeline import database_service as _impl

sys.modules[__name__] = _impl
