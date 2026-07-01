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


def test_intent_contract_is_versioned_and_preserves_legacy_fields():
    intent = build_intent("show all bills", ai_backend="local")

    assert intent["intent_contract_version"] == "1.0"
    assert intent["structured_filters"] == []
    assert intent["parse_diagnostics"]["has_issues"] is False
    assert "deterministic_pattern_match" in intent["confidence_reasons"]
    assert {
        "intent_type",
        "requested_metrics",
        "requested_dimensions",
        "requested_filters",
        "requested_sort",
        "source_scope",
        "limit",
        "unsafe",
        "target_entity_phrase",
        "metric_phrase",
    } <= set(intent)


def test_show_count_of_bills_uses_count_contract():
    intent = build_intent("show count of bills", ai_backend="local")

    assert intent["intent_type"] == "count"
    assert intent["aggregate_function"] == "count"
    assert intent["target_entity_phrase"] == "bills"
    assert intent["unsafe"] is False
    assert "explicit_count_phrase" in intent["confidence_reasons"]


def test_multi_filter_question_preserves_legacy_and_structured_filters():
    intent = build_intent(
        "show bills where status is pending and amount greater than 5000",
        ai_backend="local",
    )

    assert intent["intent_type"] == "filter"
    assert intent["requested_filters"] == ["status is pending", "amount greater than 5000"]
    assert intent["structured_filters"] == [
        {
            "raw_phrase": "status is pending",
            "field": "status",
            "field_phrase": "status",
            "operator": "eq",
            "value": "pending",
            "value_phrase": "pending",
            "values": ["pending"],
            "conjunction": None,
        },
        {
            "raw_phrase": "amount greater than 5000",
            "field": "amount",
            "field_phrase": "amount",
            "operator": "gt",
            "value": "5000",
            "value_phrase": "5000",
            "values": ["5000"],
            "conjunction": "and",
        },
    ]


def test_or_filter_and_clause_boundary_are_preserved():
    intent = build_intent(
        "show bills where status is pending or status is overdue sorted by amount desc",
        ai_backend="local",
    )

    assert intent["requested_filters"] == ["status is pending", "status is overdue"]
    assert intent["structured_filters"][1]["conjunction"] == "or"
    assert intent["requested_sort"] == {"direction": "desc", "terms": "amount"}


def test_grouping_after_filter_is_preserved():
    intent = build_intent(
        "show sum amount from bills where status is pending group by partner",
        ai_backend="local",
    )

    assert intent["intent_type"] == "grouped_summary"
    assert intent["metric_phrase"] == "amount"
    assert intent["source_scope"] == ["bills"]
    assert intent["filter_phrase"] == "status is pending"
    assert intent["grouping_phrase"] == "partner"


@pytest.mark.parametrize("ranking_word", ["lowest", "bottom"])
def test_lowest_and_bottom_n_are_ranking_with_ascending_sort(ranking_word):
    intent = build_intent(f"show {ranking_word} 5 paid value from bills", ai_backend="local")

    assert intent["intent_type"] == "ranking"
    assert intent["limit"] == 5
    assert intent["metric_phrase"] == "paid value"
    assert intent["source_scope"] == ["bills"]
    assert intent["requested_sort"] == {"direction": "asc", "terms": "paid value"}


@pytest.mark.parametrize(
    ("question", "expected_sort"),
    [
        ("show bills sorted by amount descending", {"direction": "desc", "terms": "amount"}),
        ("show bills ordered by amount asc", {"direction": "asc", "terms": "amount"}),
    ],
)
def test_explicit_sort_direction_is_detected(question, expected_sort):
    intent = build_intent(question, ai_backend="local")

    assert intent["intent_type"] == "sorted_list"
    assert intent["requested_sort"] == expected_sort


@pytest.mark.parametrize(
    ("question", "scope"),
    [
        ("show sum amount in bills", "bills"),
        ("show average value for invoices", "invoices"),
    ],
)
def test_safe_in_and_for_source_scope_is_detected(question, scope):
    intent = build_intent(question, ai_backend="local")

    assert intent["intent_type"] == "aggregate"
    assert intent["source_scope"] == [scope]
    assert intent["source_scope_phrase"] == scope


def test_generic_metric_and_unsafe_diagnostics_are_additive():
    aggregate_intent = build_intent("show sum amount from bills", ai_backend="local")
    unsafe_intent = build_intent("delete bills", ai_backend="local")

    assert aggregate_intent["parse_diagnostics"]["ambiguous_phrases"] == ["generic_metric_phrase"]
    assert aggregate_intent["parse_diagnostics"]["has_issues"] is True
    assert unsafe_intent["intent_type"] == "unsafe"
    assert unsafe_intent["unsafe_operation"] == "delete"
    assert "explicit_unsafe_operation" in unsafe_intent["confidence_reasons"]


def test_parse_diagnostics_reports_missing_metric_phrase():
    intent = build_intent("show top 5", ai_backend="local")

    assert "metric_phrase" in intent["missing_phrases"]
    assert intent["parse_diagnostics"]["missing_phrases"] == intent["missing_phrases"]
    assert intent["parse_diagnostics"]["has_issues"] is True

