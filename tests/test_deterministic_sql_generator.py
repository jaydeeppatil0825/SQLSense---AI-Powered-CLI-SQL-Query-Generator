import pytest

from sql_pipeline.deterministic_sql_generator import (
    build_deterministic_sql_plan,
    generate_deterministic_sql,
    generate_single_table_aggregate_sql,
)


def _bills_kb():
    return {
        "bills": {
            "columns": [
                {"name": "bill_id", "type": "INTEGER", "nullable": False, "semantic_type": "id"},
                {"name": "amount_total", "type": "DECIMAL(12,2)", "nullable": True, "semantic_type": "numeric_candidate"},
                {"name": "tax_total", "type": "DECIMAL(12,2)", "nullable": True, "semantic_type": "numeric_candidate"},
                {"name": "status_code", "type": "VARCHAR(30)", "nullable": True, "semantic_type": "status"},
                {"name": "customer_name", "type": "VARCHAR(100)", "nullable": True, "semantic_type": "name"},
                {"name": "bill_date", "type": "DATE", "nullable": True, "semantic_type": "date"},
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


def test_generator_uses_full_kb_types_when_selected_projection_omits_them():
    context = _single_table_context(
        "show sum billed value from bills",
        intent="total",
        selected_metric="amount_total",
    )
    context["selected_knowledge_base"] = {
        "bills": {
            "columns": [
                {
                    "name": "amount_total",
                    "type": "",
                    "semantic_type": "numeric_candidate",
                }
            ],
            "primary_keys": [],
            "foreign_keys": [],
        }
    }

    result = generate_single_table_aggregate_sql(
        query_context=context,
        knowledge_base=_bills_kb(),
    )

    assert result.status == "generated"
    assert result.sql == "SELECT SUM(amount_total) AS sum_amount_total FROM bills;"


def _filtered_context(
    *,
    field: str,
    operator: str,
    value: object,
    aggregate_function: str | None = None,
    selected_metric: str | None = None,
):
    value_phrase = " and ".join(str(item) for item in value) if isinstance(value, list) else str(value)
    selected_filter = {
        "type": "value",
        "table": "bills",
        "column": field,
        "value": value,
        "operator": operator,
        "field_phrase": field.replace("_", " "),
        "value_phrase": value_phrase,
        "values": list(value) if isinstance(value, list) else ([value] if value != "" else []),
        "conjunction": None,
    }
    structured_filter = {
        "field": field.replace("_", " "),
        "field_phrase": field.replace("_", " "),
        "operator": operator,
        "value": value,
        "value_phrase": value_phrase,
        "values": list(value) if isinstance(value, list) else ([value] if value != "" else []),
        "conjunction": None,
    }
    return {
        "query_shape": "filtered_query",
        "intent": {"structured_filters": [structured_filter]},
        "aggregate_function": aggregate_function,
        "selected_metric": (
            {"table": "bills", "column": selected_metric}
            if selected_metric
            else None
        ),
        "plan": {
            "question": "filtered bills",
            "intent": "total" if aggregate_function else "list",
            "dimension": None,
            "grouping": [],
            "filters": [selected_filter],
            "date_range": None,
            "limit": 50,
        },
        "selected_tables": [{"table": "bills", "confidence": 0.9}],
        "selected_table_names": ["bills"],
        "limit": 50 if aggregate_function is None else None,
        "selected_filters": [selected_filter],
        "selected_knowledge_base": _bills_kb(),
        "join_paths": [],
        "formula_evidence": [],
    }


def _multi_filtered_context(
    filters: list[dict[str, object]],
    *,
    aggregate_function: str | None = None,
    selected_metric: str | None = None,
):
    context = _filtered_context(
        field=str(filters[0]["field"]),
        operator=str(filters[0]["operator"]),
        value=filters[0].get("value", ""),
        aggregate_function=aggregate_function,
        selected_metric=selected_metric,
    )
    selected_filters = []
    structured_filters = []
    for index, specification in enumerate(filters):
        field = str(specification["field"])
        value = specification.get("value", "")
        values = list(value) if isinstance(value, list) else ([value] if value != "" else [])
        conjunction = None if index == 0 else str(specification.get("conjunction") or "and")
        value_phrase = " and ".join(str(item) for item in values)
        selected_filters.append(
            {
                "type": "value",
                "table": "bills",
                "column": field,
                "value": value,
                "values": values,
                "operator": str(specification["operator"]),
                "field_phrase": field.replace("_", " "),
                "value_phrase": value_phrase,
                "conjunction": conjunction,
            }
        )
        structured_filters.append(
            {
                "field": field.replace("_", " "),
                "field_phrase": field.replace("_", " "),
                "operator": str(specification["operator"]),
                "value": value,
                "values": values,
                "value_phrase": value_phrase,
                "conjunction": conjunction,
            }
        )
    context["intent"]["structured_filters"] = structured_filters
    context["plan"]["filters"] = selected_filters
    context["selected_filters"] = selected_filters
    return context


def test_filtered_list_quotes_string_value_and_uses_schema_columns():
    result = generate_deterministic_sql(
        query_context=_filtered_context(field="status_code", operator="eq", value="pending"),
        knowledge_base=_bills_kb(),
    )

    assert result.status == "generated"
    assert result.sql == (
        "SELECT bill_id, amount_total, tax_total, status_code, customer_name, bill_date FROM bills "
        "WHERE status_code = 'pending' LIMIT 50;"
    )


def test_filtered_list_keeps_numeric_comparison_unquoted():
    result = generate_deterministic_sql(
        query_context=_filtered_context(field="amount_total", operator="gte", value="5000.00"),
        knowledge_base=_bills_kb(),
    )

    assert result.status == "generated"
    assert "WHERE amount_total >= 5000.00" in result.sql
    assert "'5000.00'" not in result.sql


@pytest.mark.parametrize(
    ("operator", "sql_operator"),
    [("gt", ">"), ("lt", "<"), ("gte", ">="), ("lte", "<=")],
)
def test_filtered_numeric_comparison_operators(operator, sql_operator):
    result = generate_deterministic_sql(
        query_context=_filtered_context(field="amount_total", operator=operator, value="5000"),
        knowledge_base=_bills_kb(),
    )

    assert result.status == "generated"
    assert f"WHERE amount_total {sql_operator} 5000" in result.sql


def test_filtered_list_supports_multiple_and_filters():
    context = _multi_filtered_context(
        [
            {"field": "status_code", "operator": "eq", "value": "pending"},
            {"field": "amount_total", "operator": "gt", "value": "1000", "conjunction": "and"},
        ]
    )

    result = generate_deterministic_sql(query_context=context, knowledge_base=_bills_kb())

    assert result.status == "generated"
    assert "WHERE status_code = 'pending' AND amount_total > 1000" in result.sql


def test_filtered_list_supports_uniform_or_filters():
    context = _multi_filtered_context(
        [
            {"field": "status_code", "operator": "eq", "value": "paid"},
            {"field": "status_code", "operator": "eq", "value": "partial", "conjunction": "or"},
        ]
    )

    result = generate_deterministic_sql(query_context=context, knowledge_base=_bills_kb())

    assert result.status == "generated"
    assert "WHERE status_code = 'paid' OR status_code = 'partial'" in result.sql


@pytest.mark.parametrize(
    ("field", "operator", "value", "predicate"),
    [
        ("amount_total", "between", ["1000", "5000"], "amount_total BETWEEN 1000 AND 5000"),
        ("bill_date", "between", ["2026-01-01", "2026-02-01"], "bill_date BETWEEN '2026-01-01' AND '2026-02-01'"),
        ("customer_name", "contains", "John", "customer_name LIKE '%John%'"),
        ("status_code", "neq", "paid", "status_code <> 'paid'"),
        ("bill_date", "after", "2026-02-01", "bill_date > '2026-02-01'"),
        ("bill_date", "before", "2026-03-01", "bill_date < '2026-03-01'"),
        ("bill_date", "eq", "2026-02-03", "bill_date = '2026-02-03'"),
        ("amount_total", "is_null", "", "amount_total IS NULL"),
        ("amount_total", "is_not_null", "", "amount_total IS NOT NULL"),
    ],
)
def test_filtered_list_supports_advanced_typed_predicates(field, operator, value, predicate):
    result = generate_deterministic_sql(
        query_context=_filtered_context(field=field, operator=operator, value=value),
        knowledge_base=_bills_kb(),
    )

    assert result.status == "generated"
    assert f"WHERE {predicate}" in result.sql


def test_filtered_aggregate_supports_multiple_and_filters():
    context = _multi_filtered_context(
        [
            {"field": "status_code", "operator": "eq", "value": "paid"},
            {"field": "amount_total", "operator": "gt", "value": "1000", "conjunction": "and"},
        ],
        aggregate_function="sum",
        selected_metric="amount_total",
    )

    result = generate_deterministic_sql(query_context=context, knowledge_base=_bills_kb())

    assert result.status == "generated"
    assert result.sql == (
        "SELECT SUM(amount_total) AS sum_amount_total FROM bills "
        "WHERE status_code = 'paid' AND amount_total > 1000;"
    )


def test_filtered_query_rejects_mixed_and_or_without_parentheses():
    context = _multi_filtered_context(
        [
            {"field": "status_code", "operator": "eq", "value": "paid"},
            {"field": "amount_total", "operator": "gt", "value": "1000", "conjunction": "and"},
            {"field": "status_code", "operator": "eq", "value": "partial", "conjunction": "or"},
        ]
    )

    result = generate_deterministic_sql(query_context=context, knowledge_base=_bills_kb())

    assert result.status == "cannot_plan_safely"
    assert result.sql is None
    assert "mixed or unsupported filter conjunctions" in result.reason


@pytest.mark.parametrize(
    ("field", "operator", "value"),
    [
        ("status_code", "gt", "paid"),
        ("bill_date", "after", "not-a-date"),
        ("amount_total", "between", ["1000"]),
    ],
)
def test_filtered_query_rejects_invalid_typed_predicates(field, operator, value):
    result = generate_deterministic_sql(
        query_context=_filtered_context(field=field, operator=operator, value=value),
        knowledge_base=_bills_kb(),
    )

    assert result.status == "cannot_plan_safely"
    assert result.sql is None


def test_filtered_aggregate_uses_planner_metric_and_filter():
    result = generate_deterministic_sql(
        query_context=_filtered_context(
            field="status_code",
            operator="eq",
            value="paid",
            aggregate_function="sum",
            selected_metric="amount_total",
        ),
        knowledge_base=_bills_kb(),
    )

    assert result.status == "generated"
    assert result.sql == (
        "SELECT SUM(amount_total) AS sum_amount_total FROM bills "
        "WHERE status_code = 'paid';"
    )


def test_filtered_query_rejects_column_not_in_schema():
    result = generate_deterministic_sql(
        query_context=_filtered_context(field="unknown_field", operator="eq", value="pending"),
        knowledge_base=_bills_kb(),
    )

    assert result.status == "cannot_plan_safely"
    assert result.sql is None
    assert result.reason == "filter_column_not_in_schema"


def test_filtered_query_rejects_nonnumeric_value_for_numeric_column():
    result = generate_deterministic_sql(
        query_context=_filtered_context(field="amount_total", operator="eq", value="paid"),
        knowledge_base=_bills_kb(),
    )

    assert result.status == "cannot_plan_safely"
    assert result.sql is None
    assert result.reason == "filter_value_type_mismatch"


def test_filtered_query_escapes_single_quote_in_string_value():
    result = generate_deterministic_sql(
        query_context=_filtered_context(field="status_code", operator="eq", value="owner's"),
        knowledge_base=_bills_kb(),
    )

    assert result.status == "generated"
    assert "WHERE status_code = 'owner''s'" in result.sql


def test_filtered_ranking_contract_remains_not_implemented():
    context = _filtered_context(field="status_code", operator="eq", value="pending")
    context["plan"]["sorting"] = {"direction": "desc", "by": "amount_total"}

    result = generate_deterministic_sql(
        query_context=context,
        knowledge_base=_bills_kb(),
    )

    assert result.status == "not_applicable"
    assert result.sql is None
    assert result.reason == "ranked filtered SQL is not implemented"


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
