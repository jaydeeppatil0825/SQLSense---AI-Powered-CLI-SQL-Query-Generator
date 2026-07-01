from sql_pipeline.deterministic_sql_generator import (
    build_deterministic_sql_plan,
    generate_single_table_aggregate_sql,
)


def _bills_kb():
    return {
        "bills": {
            "columns": [
                {"name": "bill_id", "type": "INTEGER", "nullable": False, "semantic_type": "id"},
                {"name": "amount_total", "type": "DECIMAL(12,2)", "nullable": True, "semantic_type": "numeric_candidate"},
                {"name": "tax_total", "type": "DECIMAL(12,2)", "nullable": True, "semantic_type": "numeric_candidate"},
            ],
            "primary_keys": ["bill_id"],
            "foreign_keys": [],
        }
    }


def _single_table_context(
    question: str,
    *,
    intent: str,
    column_names: list[str] | None = None,
    selected_metric: str | None = "amount_total",
):
    column_names = column_names or ["amount_total", "tax_total"]
    aggregate_function = {
        "total": "sum",
        "average": "avg",
    }.get(intent)
    if aggregate_function is None:
        aggregate_function = "min" if "lowest" in question else "max"
    return {
        "query_shape": "single_table_aggregate",
        "aggregate_function": aggregate_function,
        "selected_metric": (
            {"table": "bills", "column": selected_metric}
            if selected_metric
            else None
        ),
        "plan": {
            "question": question,
            "intent": intent,
            "dimension": None,
            "grouping": [],
            "filters": [],
            "date_range": None,
        },
        "selected_tables": [
            {
                "table": "bills",
                "confidence": 0.9,
                "selected_columns": [{"column": name, "confidence": 0.7, "semantic_type": "money"} for name in column_names],
            }
        ],
        "selected_columns": [
            {"table": "bills", "column": name, "confidence": 0.7, "semantic_type": "money"}
            for name in column_names
        ],
        "selected_table_names": ["bills"],
        "selected_knowledge_base": _bills_kb(),
        "join_paths": [],
        "formula_evidence": [],
        "measure_candidates": [],
    }


def test_total_amount_from_bills_generates_sum():
    result = generate_single_table_aggregate_sql(
        query_context=_single_table_context("show total amount from bills", intent="total"),
        knowledge_base=_bills_kb(),
    )

    assert result.status == "generated"
    assert result.sql == "SELECT SUM(amount_total) AS sum_amount_total FROM bills;"


def test_single_table_aggregate_plan_is_normalized_for_future_shapes():
    plan = build_deterministic_sql_plan(
        query_context=_single_table_context("show total amount from bills", intent="total"),
        knowledge_base=_bills_kb(),
    )

    assert plan.query_shape == "single_table_aggregate"
    assert plan.status == "ready"
    assert plan.supported_now is True
    assert plan.base_table == "bills"
    assert plan.metric_columns == ["amount_total"]
    assert plan.group_by == []
    assert plan.order_by == []
    assert plan.where_clauses == []
    assert plan.joins == []
    assert plan.required_joins == []
    assert plan.can_render is True
    assert plan.sql_skeleton_type == "single_table_aggregate"
    assert plan.select_items[0]["expression"] == "SUM(amount_total)"


def test_average_amount_from_bills_generates_avg():
    result = generate_single_table_aggregate_sql(
        query_context=_single_table_context("show average amount from bills", intent="average"),
        knowledge_base=_bills_kb(),
    )

    assert result.status == "generated"
    assert result.sql == "SELECT AVG(amount_total) AS avg_amount_total FROM bills;"


def test_highest_amount_from_bills_generates_max():
    result = generate_single_table_aggregate_sql(
        query_context=_single_table_context("show highest amount from bills", intent="top_n"),
        knowledge_base=_bills_kb(),
    )

    assert result.status == "generated"
    assert result.sql == "SELECT MAX(amount_total) AS max_amount_total FROM bills;"


def test_lowest_amount_from_bills_generates_min():
    result = generate_single_table_aggregate_sql(
        query_context=_single_table_context("show lowest amount from bills", intent="top_n"),
        knowledge_base=_bills_kb(),
    )

    assert result.status == "generated"
    assert result.sql == "SELECT MIN(amount_total) AS min_amount_total FROM bills;"


def test_ambiguous_metric_columns_returns_cannot_plan_safely():
    context = _single_table_context(
        "show total from bills",
        intent="total",
        selected_metric=None,
    )
    context["ambiguities"] = ["metric_selection"]
    result = generate_single_table_aggregate_sql(
        query_context=context,
        knowledge_base=_bills_kb(),
    )

    assert result.status == "cannot_plan_safely"
    assert result.sql is None
    assert result.reason == "metric_ambiguous"


def test_missing_metric_returns_metric_not_found():
    kb = {
        "bills": {
            "columns": [
                {"name": "bill_id", "type": "INTEGER", "nullable": False, "semantic_type": "id"},
                {"name": "net_value", "type": "DECIMAL(12,2)", "nullable": True, "semantic_type": "numeric_candidate"},
            ],
            "primary_keys": ["bill_id"],
            "foreign_keys": [],
        }
    }
    context = {
        "plan": {
            "question": "show total amount from bills",
            "intent": "total",
            "dimension": None,
            "grouping": [],
            "filters": [],
            "date_range": None,
        },
        "selected_tables": [{"table": "bills", "confidence": 0.9, "selected_columns": [{"column": "net_value", "confidence": 0.7, "semantic_type": "money"}]}],
        "selected_columns": [{"table": "bills", "column": "net_value", "confidence": 0.7, "semantic_type": "money"}],
        "selected_table_names": ["bills"],
        "selected_metric": None,
        "aggregate_function": "sum",
        "selected_knowledge_base": kb,
        "join_paths": [],
        "formula_evidence": [],
        "measure_candidates": [],
    }

    result = generate_single_table_aggregate_sql(
        query_context=context,
        knowledge_base=kb,
    )

    assert result.status == "cannot_plan_safely"
    assert result.reason == "metric_not_found"


def test_generator_uses_planner_selected_metric_without_question_rescoring():
    context = _single_table_context(
        "show total tax from bills",
        intent="total",
        selected_metric="amount_total",
    )

    result = generate_single_table_aggregate_sql(
        query_context=context,
        knowledge_base=_bills_kb(),
    )

    assert result.status == "generated"
    assert result.sql == "SELECT SUM(amount_total) AS sum_amount_total FROM bills;"


def test_grouped_query_is_not_applicable():
    context = _single_table_context("show total amount by bill type", intent="total")
    context["plan"]["grouping"] = ["bill type"]

    plan = build_deterministic_sql_plan(
        query_context=context,
        knowledge_base=_bills_kb(),
    )
    result = generate_single_table_aggregate_sql(
        query_context=context,
        knowledge_base=_bills_kb(),
    )

    assert plan.query_shape == "grouped_aggregate"
    assert plan.status == "not_applicable"
    assert plan.can_render is False
    assert "grouping" in plan.missing_evidence
    assert result.status == "not_applicable"


def test_join_query_is_not_applicable():
    context = _single_table_context("show total amount with accounts", intent="total")
    context["join_paths"] = [{"from_table": "bills", "to_table": "accounts", "path": [], "length": 1}]

    plan = build_deterministic_sql_plan(
        query_context=context,
        knowledge_base=_bills_kb(),
    )
    result = generate_single_table_aggregate_sql(
        query_context=context,
        knowledge_base=_bills_kb(),
    )

    assert plan.query_shape == "multi_table_aggregate"
    assert plan.status == "not_applicable"
    assert plan.can_render is False
    assert "join_paths" in plan.missing_evidence
    assert result.status == "not_applicable"
