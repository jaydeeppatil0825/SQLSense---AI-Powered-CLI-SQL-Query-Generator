from query_pipeline.planner.role_resolver import build_metric_decision_contract


def _metric(table, column, *, semantic_type="money", data_type="DECIMAL(12,2)", terms=None):
    return {
        "table": table,
        "column": column,
        "semantic_type": semantic_type,
        "data_type": data_type,
        "is_measure": True,
        "matched_terms": terms or [column.replace("_", " ")],
        "score": 0.99,
    }


def _path(base, *joined):
    return {"base_table": base, "joined_tables": list(joined), "edges": [], "path_source": "relationship_graph"}


def test_owner_metric_resolves_numeric_metric_on_path():
    decision = build_metric_decision_contract(
        metric_phrase="payment amount",
        metric_candidates=[
            _metric("orders", "total_amount", terms=["order amount", "amount"]),
            _metric("payments", "payment_amount", terms=["payment amount", "amount"]),
        ],
        aggregate_function="sum",
        selected_join_path=_path("payments", "orders", "customers"),
    )

    assert decision["status"] == "resolved"
    assert decision["table"] == "payments"
    assert decision["column"] == "payment_amount"
    assert decision["metric_mode"] == "numeric_metric"


def test_generic_metric_ambiguity_fails_closed():
    decision = build_metric_decision_contract(
        metric_phrase="amount",
        metric_candidates=[
            _metric("orders", "total_amount", terms=["amount"]),
            _metric("payments", "payment_amount", terms=["amount"]),
        ],
        aggregate_function="sum",
    )

    assert decision["status"] == "ambiguous"
    assert decision["reason_code"] == "metric_evidence_ambiguous"


def test_non_numeric_metric_is_rejected():
    decision = build_metric_decision_contract(
        metric_phrase="payment status",
        metric_candidates=[
            _metric("payments", "payment_status", semantic_type="status", data_type="VARCHAR(30)", terms=["payment status"]),
        ],
        aggregate_function="sum",
    )

    assert decision["status"] == "missing"
    assert decision["reason_code"] == "metric_evidence_missing"


def test_metric_outside_selected_path_is_rejected():
    decision = build_metric_decision_contract(
        metric_phrase="payment amount",
        metric_candidates=[_metric("payments", "payment_amount", terms=["payment amount"])],
        aggregate_function="sum",
        selected_metric=_metric("payments", "payment_amount", terms=["payment amount"]),
        selected_join_path=_path("orders", "customers"),
    )

    assert decision["status"] == "unsupported"
    assert decision["reason_code"] == "metric_table_outside_selected_path"


def test_count_does_not_require_numeric_metric():
    decision = build_metric_decision_contract(
        metric_phrase="",
        metric_candidates=[],
        aggregate_function="count",
        selected_join_path=_path("payments", "orders", "customers"),
        count_base_table="payments",
    )

    assert decision["status"] == "resolved"
    assert decision["metric_mode"] == "entity_count"
    assert decision["numeric_eligible"] is True
