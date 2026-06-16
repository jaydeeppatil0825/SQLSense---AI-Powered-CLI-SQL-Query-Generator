"""Tests for the dynamic simple query generator."""

from pathlib import Path

from ai.simple_query_generator import generate_simple_sql


GENERIC_KB = {
    "client_directory": {
        "columns": [
            {"name": "client_id", "type": "INTEGER", "nullable": False, "semantic_type": "id"},
            {"name": "client_name", "type": "VARCHAR(100)", "nullable": False, "semantic_type": "name"},
            {"name": "status_flag", "type": "VARCHAR(20)", "nullable": True, "semantic_type": "status", "sample_values": ["Active", "Inactive"]},
            {"name": "created_at", "type": "DATE", "nullable": True, "semantic_type": "date"},
        ],
        "primary_keys": ["client_id"],
        "foreign_keys": [],
    },
    "invoice_headers": {
        "columns": [
            {"name": "invoice_id", "type": "INTEGER", "nullable": False, "semantic_type": "id"},
            {"name": "client_id", "type": "INTEGER", "nullable": False, "semantic_type": "id"},
            {"name": "invoice_date", "type": "DATE", "nullable": False, "semantic_type": "date"},
            {"name": "workflow_status", "type": "VARCHAR(30)", "nullable": True, "semantic_type": "status", "sample_values": ["Pending", "Paid"]},
            {"name": "total_due", "type": "DECIMAL(12,2)", "nullable": True, "semantic_type": "money"},
        ],
        "primary_keys": ["invoice_id"],
        "foreign_keys": [{"column": "client_id", "referenced_table": "client_directory", "referenced_column": "client_id"}],
    },
    "stock_positions": {
        "columns": [
            {"name": "stock_id", "type": "INTEGER", "nullable": False, "semantic_type": "id"},
            {"name": "warehouse_code", "type": "VARCHAR(20)", "nullable": False, "semantic_type": "code"},
            {"name": "quantity_on_hand", "type": "INTEGER", "nullable": False, "semantic_type": "quantity"},
            {"name": "last_updated", "type": "DATE", "nullable": True, "semantic_type": "date"},
        ],
        "primary_keys": ["stock_id"],
        "foreign_keys": [],
    },
}

GLOSSARY = {
    "customer": {
        "description": "Client master records.",
        "mapped_columns": [{"table": "client_directory", "column": "client_name", "confidence": "high"}],
        "example_questions": ["Show customers"],
        "business_terms": ["client"],
    },
    "payables": {
        "description": "Open payable amount.",
        "mapped_columns": [{"table": "invoice_headers", "column": "total_due", "confidence": "high"}],
        "example_questions": ["Show current payables"],
        "business_terms": ["amount due"],
    },
}


def test_show_all_for_direct_table_name():
    sql = generate_simple_sql("Show client directory", GENERIC_KB)
    assert sql == "SELECT client_id, client_name, status_flag, created_at FROM client_directory LIMIT 50;"


def test_show_all_uses_dynamic_glossary_mapping():
    sql = generate_simple_sql("Show all customers", GENERIC_KB, business_glossary=GLOSSARY)
    assert sql == "SELECT client_id, client_name, status_flag, created_at FROM client_directory LIMIT 50;"


def test_without_glossary_old_business_aliases_do_not_resolve():
    sql = generate_simple_sql("Show all customers", GENERIC_KB)
    assert sql is None


def test_count_uses_selected_dynamic_table():
    sql = generate_simple_sql("How many invoices", GENERIC_KB)
    assert sql == "SELECT COUNT(*) AS total_invoice_headers FROM invoice_headers;"


def test_total_uses_dynamic_measure_column_from_glossary():
    query_plan = {"intent": "total", "semantic_hints": {"money"}}
    sql = generate_simple_sql(
        "Show total payables",
        GENERIC_KB,
        query_plan=query_plan,
        business_glossary=GLOSSARY,
    )
    assert sql == "SELECT SUM(total_due) AS total_total_due FROM invoice_headers;"


def test_total_uses_dynamic_date_filter_from_plan():
    query_plan = {
        "intent": "total",
        "semantic_hints": {"money"},
        "date_range": {"start": "2026-06-01", "end_exclusive": "2026-07-01"},
    }
    sql = generate_simple_sql(
        "Show total invoices this month",
        {"invoice_headers": GENERIC_KB["invoice_headers"]},
        query_plan=query_plan,
    )
    assert sql == (
        "SELECT SUM(total_due) AS total_total_due "
        "FROM invoice_headers WHERE invoice_date >= '2026-06-01' AND invoice_date < '2026-07-01';"
    )


def test_latest_uses_dynamic_date_column():
    sql = generate_simple_sql("Show latest 10 invoices", GENERIC_KB)
    assert sql == "SELECT invoice_id, client_id, invoice_date, workflow_status, total_due FROM invoice_headers ORDER BY invoice_date DESC LIMIT 10;"


def test_status_filter_uses_dynamic_status_column():
    query_plan = {"intent": "list", "filters": [{"type": "status", "value": "Pending", "term": "pending"}]}
    sql = generate_simple_sql("Show pending invoices", GENERIC_KB, query_plan=query_plan)
    assert sql == "SELECT invoice_id, client_id, invoice_date, workflow_status, total_due FROM invoice_headers WHERE workflow_status = 'Pending' LIMIT 50;"


def test_simple_generator_uses_quantity_columns_without_hardcoded_tables():
    query_plan = {"intent": "average", "semantic_hints": {"quantity"}}
    sql = generate_simple_sql(
        "Show average quantity on hand",
        {"stock_positions": GENERIC_KB["stock_positions"]},
        query_plan=query_plan,
    )
    assert sql == "SELECT AVG(quantity_on_hand) AS average_quantity_on_hand FROM stock_positions;"


def test_complex_questions_return_none_for_ai_path():
    query_plan = {"intent": "trend", "grouping": ["month"], "dimension": "warehouse"}
    sql = generate_simple_sql(
        "Show current stock by warehouse",
        GENERIC_KB,
        query_plan=query_plan,
        business_glossary=GLOSSARY,
    )
    assert sql is None


def test_list_uses_limit_from_query_plan():
    query_plan = {"intent": "list", "limit": 10}
    sql = generate_simple_sql("Show invoices", GENERIC_KB, query_plan=query_plan)
    assert sql == "SELECT invoice_id, client_id, invoice_date, workflow_status, total_due FROM invoice_headers LIMIT 10;"


def test_selected_table_metadata_overrides_heuristic_pick():
    selected_tables = [
        {
            "table": "invoice_headers",
            "confidence": 0.91,
            "selected_columns": [{"column": "total_due", "semantic_type": "money"}],
        }
    ]
    query_plan = {"intent": "total", "semantic_hints": {"money"}}
    sql = generate_simple_sql(
        "Show total amount",
        GENERIC_KB,
        query_plan=query_plan,
        selected_tables=selected_tables,
    )
    assert sql == "SELECT SUM(total_due) AS total_total_due FROM invoice_headers;"


def test_rule_based_generator_source_has_no_legacy_business_mappings():
    source = Path("ai/simple_query_generator.py").read_text(encoding="utf-8").lower()

    for banned_symbol in ("_table_aliases", "_business_term_table", "_try_pcsoft_business_sql"):
        assert banned_symbol not in source
