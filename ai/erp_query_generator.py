"""Backward-compatible module alias for the retired ERP SQL generator."""

from importlib import import_module
import sys

_impl = import_module("sql_pipeline.erp_query_generator")
sys.modules[__name__] = _impl
