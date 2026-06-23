import pytest

from query_pipeline.intent_builder import build_intent


def test_fallback_intent_builder_handles_simple_browse_query():
    intent = build_intent("show all accounts", ai_backend="local")

    assert intent["intent_type"] == "list"
    assert intent["business_operation"] == "browse"
    assert intent["requested_dimensions"] == []
    assert intent["requested_metrics"] == []
    assert intent["needs_grouping"] is False
    assert intent["needs_aggregation"] is False
    assert "accounts" in intent["raw_business_terms"]


def test_fallback_intent_builder_handles_count_query():
    intent = build_intent("count accounts", ai_backend="local")

    assert intent["intent_type"] == "count"
    assert intent["business_operation"] == "count"
    assert intent["requested_dimensions"] == []
    assert intent["requested_metrics"] == []
    assert intent["needs_aggregation"] is True
    assert intent["limit"] is None
    assert "accounts" in intent["raw_business_terms"]


def test_fallback_intent_builder_handles_ranking_query():
    intent = build_intent("top 5 accounts by deal value", ai_backend="local")

    assert intent["intent_type"] == "ranking"
    assert intent["limit"] == 5
    assert intent["requested_metrics"] == ["deal value"]
    assert intent["requested_dimensions"] == ["accounts"]
    assert intent["needs_grouping"] is True
    assert intent["needs_aggregation"] is True
    assert intent["needs_join"] == "likely"


def test_fallback_intent_builder_handles_grouped_metric_query():
    intent = build_intent("deal value by account", ai_backend="local")

    assert intent["intent_type"] == "grouped_summary"
    assert intent["requested_metrics"] == ["deal value"]
    assert intent["requested_dimensions"] == ["account"]
    assert intent["needs_grouping"] is True
    assert intent["needs_aggregation"] is True


def test_fallback_intent_builder_preserves_business_terms_for_pending_grouped_amount():
    intent = build_intent("pending billed amount by account", ai_backend="local")

    assert intent["requested_metrics"] == ["pending billed amount"]
    assert intent["requested_dimensions"] == ["account"]
    assert "pending billed amount" in intent["raw_business_terms"]
    assert intent["needs_grouping"] is True
    assert intent["needs_join"] == "likely"


def test_fallback_intent_builder_handles_stock_by_dimension_without_mapping():
    intent = build_intent("show current stock by storage point", ai_backend="local")

    assert intent["requested_metrics"] == ["current stock"]
    assert intent["requested_dimensions"] == ["storage point"]
    assert intent["needs_grouping"] is True
    assert intent["needs_aggregation"] is True


