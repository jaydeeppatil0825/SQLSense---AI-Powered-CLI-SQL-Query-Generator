"""Compatibility shim for older tests.

Active relationship graph code lives in :mod:`kb_pipeline.relationship_graph`.
"""

import sys

from kb_pipeline import relationship_graph as _impl

sys.modules[__name__] = _impl
