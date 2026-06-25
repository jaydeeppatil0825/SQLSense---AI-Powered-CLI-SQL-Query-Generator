from sql_pipeline.deterministic_sql_generator import generate_single_table_aggregate_sql


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


def _single_table_context(question: str, *, intent: str, column_names: list[str] | None = None):
    column_names = column_names or ["amount_total", "tax_total"]
    return {
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
    result = generate_single_table_aggregate_sql(
        query_context=_single_table_context("show total from bills", intent="total"),
        knowledge_base=_bills_kb(),
    )

    assert result.status == "cannot_plan_safely"
    assert result.sql is None


def test_grouped_query_is_not_applicable():
    context = _single_table_context("show total amount by bill type", intent="total")
    context["plan"]["grouping"] = ["bill type"]

    result = generate_single_table_aggregate_sql(
        query_context=context,
        knowledge_base=_bills_kb(),
    )

    assert result.status == "not_applicable"


def test_join_query_is_not_applicable():
    context = _single_table_context("show total amount with accounts", intent="total")
    context["join_paths"] = [{"from_table": "bills", "to_table": "accounts", "path": [], "length": 1}]

    result = generate_single_table_aggregate_sql(
        query_context=context,
        knowledge_base=_bills_kb(),
    )

    assert result.status == "not_applicable"
