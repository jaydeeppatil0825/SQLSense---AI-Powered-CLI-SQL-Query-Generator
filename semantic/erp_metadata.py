"""Compatibility shim for supported schema-facts imports."""

import sys

from kb_pipeline import schema_facts as _impl

sys.modules[__name__] = _impl
