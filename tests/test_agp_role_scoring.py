from query_pipeline.planner.role_resolver import (
    build_role_candidate_debug,
    rank_role_candidates,
    score_role_candidate,
)


def _candidate(
    table,
    column,
    *,
    semantic_type="text",
    data_type="VARCHAR(100)",
    is_measure=False,
    is_dimension=True,
    score=0.8,
    terms=None,
    planner_roles=None,
    primary_key=False,
    foreign_key=False,
):
    return {
        "table": table,
        "column": column,
        "semantic_type": semantic_type,
        "core_semantic_type": semantic_type,
        "data_type": data_type,
        "is_measure": is_measure,
        "is_dimension": is_dimension,
        "score": score,
        "matched_terms": list(terms or []),
        "source": "test",
        "planner_roles": dict(planner_roles or {}),
        "primary_key": primary_key,
        "foreign_key": foreign_key,
    }


def _path(base_table, *joined_tables):
    tables = [base_table, *joined_tables]
    return {
        "base_table": base_table,
        "joined_tables": list(joined_tables),
        "edges": [
            {
                "from_table": tables[index],
                "from_column": f"{tables[index]}_id",
                "to_table": tables[index + 1],
                "to_column": f"{tables[index]}_id",
                "relationship_type": "many_to_one",
                "safe_for_planner": True,
            }
            for index in range(len(tables) - 1)
        ],
        "path_source": "relationship_graph",
        "ambiguity_status": "resolved",
    }


def test_score_role_candidate_is_deterministic():
    candidate = _candidate("orders", "total_amount", semantic_type="money", data_type="DECIMAL(12,2)", is_measure=True)

    first = score_role_candidate("total amount", candidate, role="metric")
    second = score_role_candidate("total amount", candidate, role="metric")

    assert first is not None
    assert second is not None
    assert first.as_ranked_entry() == second.as_ranked_entry()


def test_owner_qualified_column_beats_generic_match():
    result = rank_role_candidates(
        "accounts display label",
        [
            _candidate("events", "display_label", terms=["display label"], score=0.99),
            _candidate("accounts", "display_label", terms=["display label"], score=0.7),
        ],
        role="dimension",
    )

    assert result["status"] == "resolved"
    assert result["selected"]["candidate"]["table"] == "accounts"
    assert result["selected"]["evidence_tier"] == "owner_qualified_exact"


def test_metric_role_rejects_non_numeric_candidate():
    result = rank_role_candidates(
        "amount",
        [
            _candidate("payments", "payment_amount", semantic_type="money", data_type="DECIMAL(12,2)", is_measure=True),
            _candidate("payments", "payment_status", semantic_type="status", data_type="VARCHAR(20)", is_measure=True),
        ],
        role="metric",
    )

    assert result["status"] == "resolved"
    assert result["selected"]["candidate"]["column"] == "payment_amount"
    assert [entry["candidate"]["column"] for entry in result["ranked"]] == ["payment_amount"]


def test_dimension_role_prefers_dimension_over_metric_only_column():
    result = rank_role_candidates(
        "status",
        [
            _candidate("orders", "total_amount", semantic_type="money", data_type="DECIMAL(12,2)", is_measure=True, is_dimension=False),
            _candidate("orders", "order_status", semantic_type="status", data_type="VARCHAR(20)", is_measure=False, is_dimension=True),
        ],
        role="dimension",
    )

    assert result["status"] == "resolved"
    assert result["selected"]["candidate"]["column"] == "order_status"
    assert [entry["candidate"]["column"] for entry in result["ranked"]] == ["order_status"]


def test_same_tier_ambiguity_fails_closed():
    result = rank_role_candidates(
        "status",
        [
            _candidate("orders", "status", semantic_type="status"),
            _candidate("payments", "status", semantic_type="status"),
        ],
        role="dimension",
    )

    assert result["status"] == "ambiguous"
    assert "ambiguous" in result["tie_reason"] or "multiple" in result["tie_reason"]


def test_role_candidate_debug_is_safe_and_compact():
    result = rank_role_candidates(
        "payment amount",
        [_candidate("payments", "payment_amount", semantic_type="money", data_type="DECIMAL(12,2)", is_measure=True)],
        role="metric",
    )

    debug = build_role_candidate_debug(result)

    assert debug["status"] == "resolved"
    assert debug["selected"]["table"] == "payments"
    assert debug["selected"]["column"] == "payment_amount"
    assert debug["selected"]["score_reasons"]


def test_display_name_beats_id_for_dimension_role():
    result = rank_role_candidates(
        "customer",
        [
            _candidate("customers", "customer_id", semantic_type="id", data_type="INT", is_dimension=True, primary_key=True),
            _candidate("customers", "customer_name", semantic_type="name", is_dimension=True),
        ],
        role="dimension",
    )

    assert result["status"] == "resolved"
    assert result["selected"]["candidate"]["column"] == "customer_name"
    assert result["selected"]["score"] > result["ranked"][1]["score"]
    assert result["ranked"][1]["penalties"] == ["dimension ID/join-key penalty"]


def test_foreign_key_loses_to_display_column_for_entity_dimension():
    result = rank_role_candidates(
        "customer",
        [
            _candidate("service_orders", "customer_id", semantic_type="id", data_type="INT", is_dimension=True, foreign_key=True),
            _candidate("customers", "customer_name", semantic_type="name", is_dimension=True),
        ],
        role="dimension",
    )

    assert result["status"] == "resolved"
    assert result["selected"]["candidate"]["table"] == "customers"
    assert result["selected"]["candidate"]["column"] == "customer_name"


def test_identifier_column_still_wins_when_explicitly_requested():
    result = rank_role_candidates(
        "customer id",
        [
            _candidate("customers", "customer_id", semantic_type="id", data_type="INT", is_dimension=True, primary_key=True),
            _candidate("customers", "customer_name", semantic_type="name", is_dimension=True),
        ],
        role="dimension",
    )

    assert result["status"] == "resolved"
    assert result["selected"]["candidate"]["column"] == "customer_id"
    assert result["selected"]["penalties"] == []


def test_display_dimension_words_are_boosted():
    for column in ("customer_status", "product_category", "city", "payment_method"):
        scored = score_role_candidate(column.replace("_", " "), _candidate("records", column), role="dimension")

        assert scored is not None
        assert scored.penalties == []
        assert any("display-friendly" in reason for reason in scored.score_reasons)


def test_same_tier_display_ambiguity_still_fails_closed():
    result = rank_role_candidates(
        "customer",
        [
            _candidate("customers", "customer_name", semantic_type="name", is_dimension=True),
            _candidate("customers", "customer_label", semantic_type="text", is_dimension=True),
        ],
        role="dimension",
    )

    assert result["status"] == "ambiguous"
    assert result["tie_reason"]


def test_metric_role_does_not_receive_dimension_display_boosts():
    scored = score_role_candidate(
        "status total",
        _candidate("facts", "status_total", semantic_type="numeric_candidate", data_type="DECIMAL(12,2)", is_measure=True),
        role="metric",
    )

    assert scored is not None
    assert scored.penalties == []
    assert not any("display-friendly" in reason for reason in scored.score_reasons)


def test_order_amount_prefers_order_owner_metric():
    result = rank_role_candidates(
        "order amount",
        [
            _candidate("order_items", "line_total", semantic_type="money", data_type="DECIMAL(12,2)", is_measure=True),
            _candidate("orders", "total_amount", semantic_type="money", data_type="DECIMAL(12,2)", is_measure=True),
        ],
        role="metric",
    )

    assert result["status"] == "resolved"
    assert result["selected"]["candidate"]["table"] == "orders"
    assert result["selected"]["candidate"]["column"] == "total_amount"


def test_payment_amount_prefers_payment_owner_metric():
    result = rank_role_candidates(
        "payment amount",
        [
            _candidate("orders", "total_amount", semantic_type="money", data_type="DECIMAL(12,2)", is_measure=True),
            _candidate("payments", "payment_amount", semantic_type="money", data_type="DECIMAL(12,2)", is_measure=True),
        ],
        role="metric",
    )

    assert result["status"] == "resolved"
    assert result["selected"]["candidate"]["table"] == "payments"
    assert result["selected"]["candidate"]["column"] == "payment_amount"


def test_orders_payment_status_uses_owner_context():
    result = rank_role_candidates(
        "payment status",
        [
            _candidate("orders", "payment_status", semantic_type="status"),
            _candidate("payments", "payment_status", semantic_type="status"),
        ],
        role="dimension",
        owner_context="orders",
    )

    assert result["status"] == "resolved"
    assert result["selected"]["candidate"]["table"] == "orders"
    assert result["selected"]["candidate"]["column"] == "payment_status"


def test_payments_payment_status_uses_owner_context():
    result = rank_role_candidates(
        "payment status",
        [
            _candidate("orders", "payment_status", semantic_type="status"),
            _candidate("payments", "payment_status", semantic_type="status"),
        ],
        role="dimension",
        owner_context="payments",
    )

    assert result["status"] == "resolved"
    assert result["selected"]["candidate"]["table"] == "payments"
    assert result["selected"]["candidate"]["column"] == "payment_status"


def test_completed_payments_filter_uses_payment_owner():
    result = rank_role_candidates(
        "completed",
        [
            _candidate("orders", "payment_status", semantic_type="status", terms=["completed"]),
            _candidate("payments", "payment_status", semantic_type="status", terms=["completed"]),
        ],
        role="filter",
        owner_context="payments",
    )

    assert result["status"] == "resolved"
    assert result["selected"]["candidate"]["table"] == "payments"
    assert result["selected"]["candidate"]["column"] == "payment_status"


def test_unpaid_orders_filter_uses_order_owner():
    result = rank_role_candidates(
        "unpaid",
        [
            _candidate("orders", "payment_status", semantic_type="status", terms=["unpaid"]),
            _candidate("payments", "payment_status", semantic_type="status", terms=["unpaid"]),
        ],
        role="filter",
        owner_context="orders",
    )

    assert result["status"] == "resolved"
    assert result["selected"]["candidate"]["table"] == "orders"
    assert result["selected"]["candidate"]["column"] == "payment_status"


def test_customer_city_selects_customer_owner_dimension():
    result = rank_role_candidates(
        "customer city",
        [
            _candidate("orders", "billing_city", semantic_type="city"),
            _candidate("customers", "city", semantic_type="city"),
        ],
        role="dimension",
    )

    assert result["status"] == "resolved"
    assert result["selected"]["candidate"]["table"] == "customers"
    assert result["selected"]["candidate"]["column"] == "city"


def test_payment_method_selects_payment_owner_dimension():
    result = rank_role_candidates(
        "payment method",
        [
            _candidate("orders", "payment_status", semantic_type="status"),
            _candidate("payments", "payment_method", semantic_type="method"),
        ],
        role="dimension",
    )

    assert result["status"] == "resolved"
    assert result["selected"]["candidate"]["table"] == "payments"
    assert result["selected"]["candidate"]["column"] == "payment_method"


def test_generic_amount_with_multiple_metrics_remains_ambiguous():
    result = rank_role_candidates(
        "amount",
        [
            _candidate("orders", "total_amount", semantic_type="money", data_type="DECIMAL(12,2)", is_measure=True),
            _candidate("payments", "payment_amount", semantic_type="money", data_type="DECIMAL(12,2)", is_measure=True),
        ],
        role="metric",
    )

    assert result["status"] == "ambiguous"
    assert "generic single-token" in result["tie_reason"]


def test_delivered_orders_filter_uses_order_owner():
    result = rank_role_candidates(
        "delivered",
        [
            _candidate("orders", "order_status", semantic_type="status", terms=["delivered"]),
            _candidate("payments", "payment_status", semantic_type="status", terms=["delivered"]),
        ],
        role="filter",
        owner_context="orders",
    )

    assert result["status"] == "resolved"
    assert result["selected"]["candidate"]["table"] == "orders"
    assert result["selected"]["candidate"]["column"] == "order_status"


def test_orders_customer_path_boosts_metric_and_dimension():
    selected_path = _path("orders", "customers")
    metric = rank_role_candidates(
        "total amount",
        [
            _candidate("invoices", "total_amount", semantic_type="money", data_type="DECIMAL(12,2)", is_measure=True),
            _candidate("orders", "total_amount", semantic_type="money", data_type="DECIMAL(12,2)", is_measure=True),
        ],
        role="metric",
        selected_join_path=selected_path,
    )
    dimension = rank_role_candidates(
        "city",
        [
            _candidate("customers", "city", semantic_type="city"),
            _candidate("suppliers", "city", semantic_type="city"),
        ],
        role="dimension",
        selected_join_path=selected_path,
    )

    assert metric["status"] == "resolved"
    assert metric["selected"]["candidate"]["table"] == "orders"
    assert dimension["status"] == "resolved"
    assert dimension["selected"]["candidate"]["table"] == "customers"


def test_order_items_product_path_boosts_metric_and_dimension():
    selected_path = _path("order_items", "products")
    metric = rank_role_candidates(
        "line total",
        [
            _candidate("returns", "line_total", semantic_type="money", data_type="DECIMAL(12,2)", is_measure=True),
            _candidate("order_items", "line_total", semantic_type="money", data_type="DECIMAL(12,2)", is_measure=True),
        ],
        role="metric",
        selected_join_path=selected_path,
    )
    dimension = rank_role_candidates(
        "product status",
        [
            _candidate("suppliers", "product_status", semantic_type="status"),
            _candidate("products", "product_status", semantic_type="status"),
        ],
        role="dimension",
        selected_join_path=selected_path,
    )

    assert metric["status"] == "resolved"
    assert metric["selected"]["candidate"]["table"] == "order_items"
    assert dimension["status"] == "resolved"
    assert dimension["selected"]["candidate"]["table"] == "products"


def test_payments_orders_customers_path_boosts_metric_and_dimension():
    selected_path = _path("payments", "orders", "customers")
    metric = rank_role_candidates(
        "payment amount",
        [
            _candidate("orders", "total_amount", semantic_type="money", data_type="DECIMAL(12,2)", is_measure=True),
            _candidate("payments", "payment_amount", semantic_type="money", data_type="DECIMAL(12,2)", is_measure=True),
        ],
        role="metric",
        selected_join_path=selected_path,
    )
    dimension = rank_role_candidates(
        "city",
        [
            _candidate("customers", "city", semantic_type="city"),
            _candidate("suppliers", "city", semantic_type="city"),
        ],
        role="dimension",
        selected_join_path=selected_path,
    )

    assert metric["status"] == "resolved"
    assert metric["selected"]["candidate"]["table"] == "payments"
    assert dimension["status"] == "resolved"
    assert dimension["selected"]["candidate"]["table"] == "customers"


def test_unrelated_table_candidate_loses_to_path_table_candidate():
    result = rank_role_candidates(
        "status",
        [
            _candidate("orders", "status", semantic_type="status"),
            _candidate("products", "status", semantic_type="status"),
        ],
        role="dimension",
        selected_join_path=_path("orders", "customers"),
    )

    assert result["status"] == "resolved"
    assert result["selected"]["candidate"]["table"] == "orders"


def test_path_boost_does_not_make_non_numeric_metric_valid():
    result = rank_role_candidates(
        "status",
        [_candidate("orders", "order_status", semantic_type="status", data_type="VARCHAR(20)", is_measure=False)],
        role="metric",
        selected_join_path=_path("orders", "customers"),
    )

    assert result["status"] == "missing"


def test_path_boost_does_not_override_same_tier_true_ambiguity():
    result = rank_role_candidates(
        "status",
        [
            _candidate("orders", "status", semantic_type="status"),
            _candidate("payments", "status", semantic_type="status"),
        ],
        role="dimension",
        selected_join_path=_path("orders", "payments"),
    )

    assert result["status"] == "ambiguous"
    assert result["tie_reason"]


def test_path_boost_does_not_authorize_missing_graph_path():
    result = rank_role_candidates(
        "city",
        [
            _candidate("customers", "city", semantic_type="city"),
            _candidate("suppliers", "city", semantic_type="city"),
        ],
        role="dimension",
    )

    assert result["status"] == "ambiguous"
