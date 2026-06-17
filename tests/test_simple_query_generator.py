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


def test_show_all_uses_dynamic_alias_mapping_without_direct_table_name():
    sql = generate_simple_sql("Show all client", GENERIC_KB, business_glossary=GLOSSARY)
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


def test_list_uses_explicit_year_date_range_from_plan():
    query_plan = {
        "intent": "list",
        "date_range": {"start": "2025-01-01", "end_exclusive": "2026-01-01"},
    }
    sql = generate_simple_sql(
        "Show invoices in 2025",
        {"invoice_headers": GENERIC_KB["invoice_headers"]},
        query_plan=query_plan,
    )
    assert sql == (
        "SELECT invoice_id, client_id, invoice_date, workflow_status, total_due "
        "FROM invoice_headers WHERE invoice_date >= '2025-01-01' AND invoice_date < '2026-01-01' LIMIT 50;"
    )


def test_latest_uses_dynamic_date_column():
    sql = generate_simple_sql("Show latest 10 invoices", GENERIC_KB)
    assert sql == "SELECT invoice_id, client_id, invoice_date, workflow_status, total_due FROM invoice_headers ORDER BY invoice_date DESC LIMIT 10;"


def test_status_filter_uses_dynamic_status_column():
    query_plan = {"intent": "list", "filters": [{"type": "status", "value": "Pending", "term": "pending"}]}
    sql = generate_simple_sql("Show pending invoices", GENERIC_KB, query_plan=query_plan)
    assert sql == "SELECT invoice_id, client_id, invoice_date, workflow_status, total_due FROM invoice_headers WHERE workflow_status = 'Pending' LIMIT 50;"


def test_generic_sample_value_filter_uses_runtime_column():
    query_plan = {
        "intent": "list",
        "filters": [{"type": "value", "column": "client_name", "value": "Acme Retail", "term": "acme retail"}],
    }
    kb = {
        "client_directory": {
            "columns": [
                {"name": "client_id", "type": "INTEGER", "nullable": False, "semantic_type": "id"},
                {
                    "name": "client_name",
                    "type": "VARCHAR(100)",
                    "nullable": False,
                    "semantic_type": "name",
                    "sample_values": ["Acme Retail", "North Supply"],
                },
                {"name": "status_flag", "type": "VARCHAR(20)", "nullable": True, "semantic_type": "status"},
            ],
            "primary_keys": ["client_id"],
            "foreign_keys": [],
        }
    }
    sql = generate_simple_sql("Show clients from Acme Retail", kb, query_plan=query_plan)
    assert sql == "SELECT client_id, client_name, status_flag FROM client_directory WHERE client_name = 'Acme Retail' LIMIT 50;"


def test_list_intent_without_browse_verb_still_generates_single_table_select():
    query_plan = {
        "intent": "list",
        "filters": [{"type": "value", "column": "town", "value": "Mumbai", "term": "Mumbai"}],
    }
    kb = {
        "accounts": {
            "columns": [
                {"name": "account_id", "type": "INTEGER", "nullable": False, "semantic_type": "id"},
                {"name": "account_label", "type": "VARCHAR(100)", "nullable": False, "semantic_type": "name"},
                {
                    "name": "town",
                    "type": "VARCHAR(50)",
                    "nullable": True,
                    "semantic_type": "name",
                    "sample_values": ["Mumbai", "Chennai"],
                },
            ],
            "primary_keys": ["account_id"],
            "foreign_keys": [],
        }
    }

    sql = generate_simple_sql("Tell me accounts from Mumbai", kb, query_plan=query_plan)

    assert sql == "SELECT account_id, account_label, town FROM accounts WHERE town = 'Mumbai' LIMIT 50;"


def test_simple_generator_uses_quantity_columns_without_hardcoded_tables():
    query_plan = {"intent": "average", "semantic_hints": {"quantity"}}
    sql = generate_simple_sql(
        "Show average quantity on hand",
        {"stock_positions": GENERIC_KB["stock_positions"]},
        query_plan=query_plan,
    )
    assert sql == "SELECT AVG(quantity_on_hand) AS average_quantity_on_hand FROM stock_positions;"


def test_total_prefers_non_identifier_measure_column():
    kb = {
        "operating_costs": {
            "columns": [
                {"name": "cost_id", "type": "INTEGER", "nullable": False, "semantic_type": "money"},
                {"name": "cost_type", "type": "VARCHAR(50)", "nullable": False, "semantic_type": "money"},
                {"name": "spent_value", "type": "DECIMAL(12,2)", "nullable": False, "semantic_type": "money"},
            ],
            "primary_keys": ["cost_id"],
            "foreign_keys": [],
        }
    }
    query_plan = {"intent": "total", "semantic_hints": {"money"}}

    sql = generate_simple_sql("Show total operating cost", kb, query_plan=query_plan)

    assert sql == "SELECT SUM(spent_value) AS total_spent_value FROM operating_costs;"


def test_total_prefers_primary_selected_table_for_simple_single_table_aggregate():
    query_plan = {"intent": "total", "semantic_hints": {"money"}}
    selected_tables = [
        {"table": "invoice_headers", "confidence": 0.92, "selected_columns": [{"column": "total_due", "semantic_type": "money"}]},
        {"table": "stock_positions", "confidence": 0.66, "selected_columns": [{"column": "quantity_on_hand", "semantic_type": "quantity"}]},
    ]
    vector_results = {"table_names": ["stock_positions", "invoice_headers"]}

    sql = generate_simple_sql(
        "Show total due",
        GENERIC_KB,
        query_plan=query_plan,
        selected_tables=selected_tables,
        vector_results=vector_results,
    )

    assert sql == "SELECT SUM(total_due) AS total_total_due FROM invoice_headers;"


def test_list_sorting_uses_runtime_sort_column():
    query_plan = {"intent": "list", "sorting": {"direction": "asc", "by": "sell rate"}}
    kb = {
        "items": {
            "columns": [
                {"name": "item_id", "type": "INTEGER", "nullable": False, "semantic_type": "id"},
                {"name": "item_label", "type": "VARCHAR(100)", "nullable": False, "semantic_type": "name"},
                {"name": "sell_rate", "type": "DECIMAL(12,2)", "nullable": False, "semantic_type": "percentage"},
            ],
            "primary_keys": ["item_id"],
            "foreign_keys": [],
        }
    }

    sql = generate_simple_sql("Show items sorted by sell rate", kb, query_plan=query_plan)

    assert sql == "SELECT item_id, item_label, sell_rate FROM items ORDER BY sell_rate ASC LIMIT 50;"


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
