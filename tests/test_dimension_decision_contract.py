from query_pipeline.planner.role_resolver import build_dimension_decision_contract


def _dimension(table, column, *, semantic_type="text", terms=None, score=0.99, **extra):
    return {
        "table": table,
        "column": column,
        "semantic_type": semantic_type,
        "is_dimension": True,
        "matched_terms": terms or [column.replace("_", " ")],
        "score": score,
        **extra,
    }


def _path(base, *joined):
    return {"base_table": base, "joined_tables": list(joined), "edges": [], "path_source": "relationship_graph"}


def test_generic_status_uses_owner_context():
    decision = build_dimension_decision_contract(
        dimension_phrase="status",
        dimension_candidates=[
            _dimension("service_orders", "order_status", semantic_type="status", terms=["status"]),
            _dimension("customers", "customer_status", semantic_type="status", terms=["status"]),
        ],
        owner_context="service orders",
    )

    assert decision["status"] == "resolved"
    assert decision["table"] == "service_orders"
    assert decision["column"] == "order_status"


def test_explicit_owner_dimension_beats_metric_owner_context():
    decision = build_dimension_decision_contract(
        dimension_phrase="customer status",
        dimension_candidates=[
            _dimension("service_orders", "order_status", semantic_type="status", terms=["status"]),
            _dimension("customers", "customer_status", semantic_type="status", terms=["customer status"]),
        ],
        owner_context="service orders",
    )

    assert decision["status"] == "resolved"
    assert decision["table"] == "customers"
    assert decision["column"] == "customer_status"


def test_generic_status_without_context_remains_ambiguous():
    decision = build_dimension_decision_contract(
        dimension_phrase="status",
        dimension_candidates=[
            _dimension("orders", "order_status", semantic_type="status", terms=["status"]),
            _dimension("payments", "payment_status", semantic_type="status", terms=["status"]),
        ],
    )

    assert decision["status"] == "ambiguous"
    assert decision["reason_code"] == "dimension_evidence_ambiguous"


def test_dimension_outside_selected_path_is_rejected():
    decision = build_dimension_decision_contract(
        dimension_phrase="payment status",
        dimension_candidates=[_dimension("payments", "payment_status", semantic_type="status", terms=["payment status"])],
        selected_dimension=_dimension("payments", "payment_status", semantic_type="status", terms=["payment status"]),
        selected_join_path=_path("orders", "customers"),
    )

    assert decision["status"] == "unsupported"
    assert decision["reason_code"] == "dimension_table_outside_selected_path"


def test_grouping_by_numeric_metric_column_is_rejected():
    decision = build_dimension_decision_contract(
        dimension_phrase="total amount",
        dimension_candidates=[
            _dimension("orders", "total_amount", semantic_type="money", terms=["total amount"], is_measure=True),
        ],
        selected_dimension=_dimension("orders", "total_amount", semantic_type="money", terms=["total amount"], is_measure=True),
    )

    assert decision["status"] == "unsupported"
    assert decision["reason_code"] == "dimension_not_grouping_eligible"


def test_id_loses_to_display_label_for_entity_dimension():
    decision = build_dimension_decision_contract(
        dimension_phrase="customer",
        dimension_candidates=[
            _dimension("customers", "customer_id", semantic_type="id", terms=["customer"], primary_key=True),
            _dimension("customers", "customer_name", semantic_type="name", terms=["customer"]),
        ],
        owner_context="customers",
    )

    assert decision["status"] == "resolved"
    assert decision["column"] == "customer_name"
