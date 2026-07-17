"""Compatibility shim for older tests.

Active AI semantic enrichment code lives in :mod:`kb_pipeline.ai_semantic_enricher`.
"""

import sys

from kb_pipeline import ai_semantic_enricher as _impl

sys.modules[__name__] = _impl
