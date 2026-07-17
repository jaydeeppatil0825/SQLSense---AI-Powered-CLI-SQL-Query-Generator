from query_pipeline.planner.role_resolver import (
    build_role_candidate_debug,
    rank_role_candidates,
    score_role_candidate,
)


def _candidate(table, column, *, semantic_type="text", data_type="VARCHAR(100)", is_measure=False, is_dimension=True, score=0.8, terms=None):
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
