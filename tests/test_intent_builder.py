import pytest

from query_pipeline.intent_builder import build_intent


def test_fallback_intent_builder_handles_simple_browse_query():
    intent = build_intent("show all bills", ai_backend="local")

    assert intent["intent_type"] == "list"
    assert intent["business_operation"] == "browse"
    assert intent["requested_dimensions"] == []
    assert intent["requested_metrics"] == []
    assert intent["needs_grouping"] is False
    assert intent["needs_aggregation"] is False
    assert "bills" in intent["raw_business_terms"]


def test_fallback_intent_builder_handles_count_query():
    intent = build_intent("count bills", ai_backend="local")

    assert intent["intent_type"] == "count"
    assert intent["business_operation"] == "count"
    assert intent["aggregate_function"] == "count"
    assert intent["requested_dimensions"] == []
    assert intent["requested_metrics"] == []
    assert intent["needs_aggregation"] is True
    assert intent["limit"] is None
    assert intent["target_entity_phrase"] == "bills"
    assert "bills" in intent["raw_business_terms"]


def test_fallback_intent_builder_handles_ranking_query():
    intent = build_intent("top 5 partners by amount", ai_backend="local")

    assert intent["intent_type"] == "ranking"
    assert intent["limit"] == 5
    assert intent["requested_metrics"] == ["amount"]
    assert intent["requested_dimensions"] == ["partners"]
    assert intent["needs_grouping"] is True
    assert intent["needs_aggregation"] is True
    assert intent["needs_join"] == "likely"
    assert intent["requested_sort"] == {"direction": "desc", "terms": "amount"}


def test_fallback_intent_builder_handles_grouped_metric_query():
    intent = build_intent("total amount by partner", ai_backend="local")

    assert intent["intent_type"] == "grouped_summary"
    assert intent["requested_metrics"] == ["amount"]
    assert intent["requested_dimensions"] == ["partner"]
    assert intent["needs_grouping"] is True
    assert intent["needs_aggregation"] is True
    assert intent["aggregate_function"] == "sum"


@pytest.mark.parametrize(
    ("question", "aggregate_function"),
    [
        ("show total amount from bills", "sum"),
        ("show sum amount from bills", "sum"),
        ("show average amount from bills", "avg"),
        ("show highest amount from bills", "max"),
        ("show lowest amount from bills", "min"),
    ],
)
def test_fallback_intent_builder_detects_generic_aggregate_queries(question, aggregate_function):
    intent = build_intent(question, ai_backend="local")

    assert intent["intent_type"] == "aggregate"
    assert intent["aggregate_function"] == aggregate_function
    assert intent["requested_metrics"] == ["amount"]
    assert intent["metric_phrase"] == "amount"
    assert intent["metric_is_generic"] is True
    assert intent["source_scope"] == ["bills"]
    assert intent["source_scope_phrase"] == "bills"
    assert intent["requested_filters"] == []
    assert intent["needs_grouping"] is False
    assert intent["needs_aggregation"] is True


def test_fallback_intent_builder_keeps_filtered_aggregate_intent_generic():
    intent = build_intent("total amount from bills where status is pending", ai_backend="local")

    assert intent["intent_type"] == "aggregate"
    assert intent["aggregate_function"] == "sum"
    assert intent["requested_metrics"] == ["amount"]
    assert intent["source_scope"] == ["bills"]
    assert intent["requested_filters"] == ["status is pending"]
    assert intent["needs_grouping"] is False
    assert intent["needs_aggregation"] is True


def test_fallback_intent_builder_preserves_business_terms_for_grouped_amount():
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
    assert intent["grouping_phrase"] == "storage point"
    assert intent["needs_grouping"] is True
    assert intent["needs_aggregation"] is True


def test_fallback_intent_builder_detects_unsafe_operations_early():
    intent = build_intent("delete bills", ai_backend="local")

    assert intent["intent_type"] == "unsafe"
    assert intent["business_operation"] == "block"
    assert intent["unsafe"] is True
    assert intent["unsafe_operation"] == "delete"
    assert intent["requested_metrics"] == []
    assert intent["requested_dimensions"] == []


def test_fallback_intent_builder_preserves_structured_phrases_for_grouped_aggregate():
    intent = build_intent("show sum paid value from bills by partner", ai_backend="local")

    assert intent["intent_type"] == "grouped_summary"
    assert intent["aggregate_function"] == "sum"
    assert intent["metric_phrase"] == "paid value"
    assert intent["metric_is_generic"] is False
    assert intent["source_scope_phrase"] == "bills"
    assert intent["grouping_phrase"] == "partner"

