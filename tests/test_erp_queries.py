import pytest
from datetime import date

from core.app_service import AppService
from semantic.business_glossary import generate_business_glossary


def _erp_knowledge_base():
    return {
        "customers": {
            "columns": [
                {"name": "customer_id", "type": "INTEGER", "nullable": False},
                {"name": "customer_name", "type": "VARCHAR(100)", "nullable": False},
            ],
            "primary_keys": ["customer_id"],
            "foreign_keys": [],
        },
        "vendors": {
            "columns": [
                {"name": "vendor_id", "type": "INTEGER", "nullable": False},
                {"name": "vendor_name", "type": "VARCHAR(100)", "nullable": False},
            ],
            "primary_keys": ["vendor_id"],
            "foreign_keys": [],
        },
        "warehouses": {
            "columns": [
                {"name": "warehouse_id", "type": "INTEGER", "nullable": False},
                {"name": "warehouse_name", "type": "VARCHAR(100)", "nullable": False},
            ],
            "primary_keys": ["warehouse_id"],
            "foreign_keys": [],
        },
        "items": {
            "columns": [
                {"name": "item_id", "type": "INTEGER", "nullable": False},
                {"name": "item_name", "type": "VARCHAR(100)", "nullable": False},
            ],
            "primary_keys": ["item_id"],
            "foreign_keys": [],
        },
        "sales_invoices": {
            "columns": [
                {"name": "invoice_id", "type": "INTEGER", "nullable": False},
                {"name": "customer_id", "type": "INTEGER", "nullable": False},
                {"name": "invoice_date", "type": "DATE", "nullable": False},
                {"name": "final_amount", "type": "DECIMAL(10,2)", "nullable": False},
                {"name": "gst_amount", "type": "DECIMAL(10,2)", "nullable": False},
                {"name": "status", "type": "VARCHAR(30)", "nullable": True},
                {"name": "outstanding_amount", "type": "DECIMAL(10,2)", "nullable": True},
            ],
            "primary_keys": ["invoice_id"],
            "foreign_keys": [
                {"column": "customer_id", "referenced_table": "customers", "referenced_column": "customer_id"}
            ],
        },
        "purchase_orders": {
            "columns": [
                {"name": "purchase_id", "type": "INTEGER", "nullable": False},
                {"name": "vendor_id", "type": "INTEGER", "nullable": False},
                {"name": "purchase_date", "type": "DATE", "nullable": False},
                {"name": "total_amount", "type": "DECIMAL(10,2)", "nullable": False},
            ],
            "primary_keys": ["purchase_id"],
            "foreign_keys": [
                {"column": "vendor_id", "referenced_table": "vendors", "referenced_column": "vendor_id"}
            ],
        },
        "inventory_balance": {
            "columns": [
                {"name": "balance_id", "type": "INTEGER", "nullable": False},
                {"name": "item_id", "type": "INTEGER", "nullable": False},
                {"name": "warehouse_id", "type": "INTEGER", "nullable": False},
                {"name": "stock_qty", "type": "DECIMAL(10,2)", "nullable": False},
                {"name": "reorder_level", "type": "DECIMAL(10,2)", "nullable": False},
            ],
            "primary_keys": ["balance_id"],
            "foreign_keys": [
                {"column": "item_id", "referenced_table": "items", "referenced_column": "item_id"},
                {"column": "warehouse_id", "referenced_table": "warehouses", "referenced_column": "warehouse_id"},
            ],
        },
        "vendor_payments": {
            "columns": [
                {"name": "payment_id", "type": "INTEGER", "nullable": False},
                {"name": "vendor_id", "type": "INTEGER", "nullable": False},
                {"name": "payment_date", "type": "DATE", "nullable": False},
                {"name": "payment_status", "type": "VARCHAR(30)", "nullable": True},
                {"name": "amount_due", "type": "DECIMAL(10,2)", "nullable": False},
            ],
            "primary_keys": ["payment_id"],
            "foreign_keys": [
                {"column": "vendor_id", "referenced_table": "vendors", "referenced_column": "vendor_id"}
            ],
        },
        "employees": {
            "columns": [
                {"name": "employee_id", "type": "INTEGER", "nullable": False},
                {"name": "department", "type": "VARCHAR(50)", "nullable": False},
                {"name": "salary", "type": "DECIMAL(10,2)", "nullable": False},
            ],
            "primary_keys": ["employee_id"],
            "foreign_keys": [],
        },
        "boms": {
            "columns": [
                {"name": "bom_id", "type": "INTEGER", "nullable": False},
                {"name": "bom_name", "type": "VARCHAR(100)", "nullable": False},
            ],
            "primary_keys": ["bom_id"],
            "foreign_keys": [],
        },
        "bom_items": {
            "columns": [
                {"name": "bom_item_id", "type": "INTEGER", "nullable": False},
                {"name": "bom_id", "type": "INTEGER", "nullable": False},
                {"name": "item_id", "type": "INTEGER", "nullable": False},
                {"name": "required_qty", "type": "DECIMAL(10,2)", "nullable": False},
            ],
            "primary_keys": ["bom_item_id"],
            "foreign_keys": [
                {"column": "bom_id", "referenced_table": "boms", "referenced_column": "bom_id"},
                {"column": "item_id", "referenced_table": "items", "referenced_column": "item_id"},
            ],
        },
        "production_orders": {
            "columns": [
                {"name": "production_id", "type": "INTEGER", "nullable": False},
                {"name": "bom_id", "type": "INTEGER", "nullable": False},
                {"name": "production_date", "type": "DATE", "nullable": False},
                {"name": "produced_qty", "type": "DECIMAL(10,2)", "nullable": False},
            ],
            "primary_keys": ["production_id"],
            "foreign_keys": [
                {"column": "bom_id", "referenced_table": "boms", "referenced_column": "bom_id"}
            ],
        },
    }


def _service():
    service = AppService()
    service.database_service.knowledge_base = _erp_knowledge_base()
    service.database_service.knowledge_base_origin = "built"
    service.database_service.business_glossary = generate_business_glossary(
        service.database_service.knowledge_base,
        use_ai_enrichment=False,
    )
    service.database_service.refresh_vector_index()
    return service


def _current_month_bounds() -> tuple[str, str]:
    start_date = date.today().replace(day=1)
    if start_date.month == 12:
        end_date = start_date.replace(year=start_date.year + 1, month=1)
    else:
        end_date = start_date.replace(month=start_date.month + 1)
    return start_date.isoformat(), end_date.isoformat()


def _stub_ai(monkeypatch, sql: str, retry_sql: str | None = None, capture: dict | None = None):
    def fake_generate_sql(user_question, knowledge_base, backend=None, query_plan=None, selected_tables=None):
        if capture is not None:
            capture["user_question"] = user_question
            capture["query_plan"] = query_plan
            capture["selected_tables"] = selected_tables
        return sql

    def fake_generate_sql_with_retry(
        user_question,
        knowledge_base,
        backend,
        first_attempt_sql,
        validation_reason,
        query_plan=None,
        selected_tables=None,
    ):
        if capture is not None:
            capture["retry_reason"] = validation_reason
        return retry_sql or sql

    monkeypatch.setattr("core.question_service.generate_sql", fake_generate_sql)
    monkeypatch.setattr("core.question_service.generate_sql_with_retry", fake_generate_sql_with_retry)


def test_erp_total_sales_this_month(monkeypatch):
    service = _service()
    start_date, end_date = _current_month_bounds()
    _stub_ai(
        monkeypatch,
        "SELECT SUM(final_amount) AS total_sales FROM sales_invoices "
        f"WHERE invoice_date >= '{start_date}' "
        f"AND invoice_date < '{end_date}';"
    )
    success, message, sql, error = service.process_question("show total sales this month", ai_backend="local")

    assert success is True
    assert "SELECT *" not in sql.upper()
    assert "SUM(final_amount)" in sql
    assert "FROM sales_invoices" in sql
    assert f"invoice_date >= '{start_date}'" in sql
    assert service.get_last_query_context()["route_used"] == "ai"
    assert service.get_last_query_context()["selected_table_names"] != list(_erp_knowledge_base().keys())


def test_erp_show_all_customers_uses_rule_based(monkeypatch):
    service = _service()
    monkeypatch.setattr(
        "core.question_service.generate_sql",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("AI should not be called for simple customer listing")),
    )

    success, message, sql, error = service.process_question("show all customers", ai_backend="local")

    assert success is True
    assert sql == "SELECT customer_id, customer_name FROM customers LIMIT 50;"
    assert service.get_last_query_context()["route_used"] == "rule-based"
    assert service.get_last_query_context()["selected_table_names"] == ["customers"]


def test_erp_purchase_by_vendor(monkeypatch):
    service = _service()
    _stub_ai(
        monkeypatch,
        "SELECT v.vendor_name AS vendor_name, SUM(p.total_amount) AS total_purchase "
        "FROM purchase_orders p JOIN vendors v ON p.vendor_id = v.vendor_id "
        "GROUP BY v.vendor_name ORDER BY total_purchase DESC LIMIT 50;"
    )
    success, message, sql, error = service.process_question("show purchase by vendor", ai_backend="local")

    assert success is True
    assert "SELECT *" not in sql.upper()
    assert "FROM purchase_orders p" in sql
    assert "JOIN vendors v" in sql
    assert "GROUP BY v.vendor_name" in sql


def test_erp_purchase_amount_by_supplier_is_not_generic(monkeypatch):
    service = _service()
    _stub_ai(
        monkeypatch,
        "SELECT v.vendor_name AS vendor_name, SUM(p.total_amount) AS total_purchase "
        "FROM purchase_orders p JOIN vendors v ON p.vendor_id = v.vendor_id "
        "GROUP BY v.vendor_name ORDER BY total_purchase DESC LIMIT 50;"
    )
    success, message, sql, error = service.process_question("show purchase amount by supplier", ai_backend="local")

    assert success is True
    assert "SELECT *" not in sql.upper()
    assert "SUM(" in sql
    assert "JOIN vendors v" in sql


def test_erp_current_stock_by_warehouse(monkeypatch):
    service = _service()
    _stub_ai(
        monkeypatch,
        "SELECT w.warehouse_name AS warehouse_name, SUM(s.stock_qty) AS current_stock "
        "FROM inventory_balance s JOIN warehouses w ON s.warehouse_id = w.warehouse_id "
        "GROUP BY w.warehouse_name ORDER BY w.warehouse_name LIMIT 50;"
    )
    success, message, sql, error = service.process_question("show current stock by warehouse", ai_backend="local")

    assert success is True
    assert "SELECT *" not in sql.upper()
    assert "FROM inventory_balance s" in sql
    assert "JOIN warehouses w" in sql
    assert "SUM(s.stock_qty) AS current_stock" in sql


def test_erp_low_stock_items(monkeypatch):
    service = _service()
    _stub_ai(
        monkeypatch,
        "SELECT i.item_name AS item_name, s.stock_qty AS current_stock, s.reorder_level AS reorder_level "
        "FROM inventory_balance s JOIN items i ON s.item_id = i.item_id "
        "WHERE s.stock_qty <= s.reorder_level ORDER BY s.stock_qty ASC LIMIT 50;"
    )
    success, message, sql, error = service.process_question("show low stock items", ai_backend="local")

    assert success is True
    assert "FROM inventory_balance s" in sql
    assert "JOIN items i" in sql
    assert "WHERE s.stock_qty <= s.reorder_level" in sql


def test_erp_unpaid_invoices(monkeypatch):
    service = _service()
    _stub_ai(
        monkeypatch,
        "SELECT invoice_id, invoice_date, status, outstanding_amount "
        "FROM sales_invoices WHERE status IN ('Pending', 'Unpaid', 'Outstanding') LIMIT 50;"
    )
    success, message, sql, error = service.process_question("show unpaid invoices", ai_backend="local")

    assert success is True
    assert "SELECT *" not in sql.upper()
    assert "FROM sales_invoices" in sql
    assert "IN (" in sql
    assert "'Unpaid'" in sql


def test_erp_customer_outstanding_balance(monkeypatch):
    service = _service()
    _stub_ai(
        monkeypatch,
        "SELECT c.customer_name AS customer_name, SUM(f.outstanding_amount) AS outstanding_balance "
        "FROM sales_invoices f JOIN customers c ON f.customer_id = c.customer_id "
        "WHERE f.outstanding_amount > 0 "
        "GROUP BY c.customer_name ORDER BY outstanding_balance DESC LIMIT 50;"
    )
    success, message, sql, error = service.process_question("show customer outstanding balance", ai_backend="local")

    assert success is True
    assert "FROM sales_invoices f" in sql
    assert "JOIN customers c" in sql
    assert "SUM(f.outstanding_amount) AS outstanding_balance" in sql
    assert "WHERE f.outstanding_amount > 0" in sql


def test_erp_vendor_pending_payments(monkeypatch):
    service = _service()
    _stub_ai(
        monkeypatch,
        "SELECT v.vendor_name AS vendor_name, SUM(p.amount_due) AS pending_amount "
        "FROM vendor_payments p JOIN vendors v ON p.vendor_id = v.vendor_id "
        "WHERE p.payment_status IN ('Pending', 'Unpaid', 'Outstanding') "
        "GROUP BY v.vendor_name ORDER BY pending_amount DESC LIMIT 50;"
    )
    success, message, sql, error = service.process_question("show vendor pending payments", ai_backend="local")

    assert success is True
    assert "FROM vendor_payments p" in sql
    assert "JOIN vendors v" in sql
    assert "SUM(p.amount_due) AS pending_amount" in sql


def test_erp_salary_by_department(monkeypatch):
    service = _service()
    _stub_ai(
        monkeypatch,
        "SELECT department, SUM(salary) AS total_salary "
        "FROM employees GROUP BY department ORDER BY total_salary DESC LIMIT 50;"
    )
    success, message, sql, error = service.process_question("show salary by department", ai_backend="local")

    assert success is True
    assert "FROM employees" in sql
    assert "GROUP BY department" in sql
    assert "SUM(salary) AS total_salary" in sql


def test_erp_tax_collected_by_month(monkeypatch):
    service = _service()
    _stub_ai(
        monkeypatch,
        "SELECT DATE_FORMAT(invoice_date, '%Y-%m') AS month, SUM(gst_amount) AS total_tax "
        "FROM sales_invoices GROUP BY DATE_FORMAT(invoice_date, '%Y-%m') ORDER BY month LIMIT 50;"
    )
    success, message, sql, error = service.process_question("show tax collected by month", ai_backend="local")

    assert success is True
    assert "SELECT *" not in sql.upper()
    assert "DATE_FORMAT(invoice_date, '%Y-%m') AS month" in sql
    assert "SUM(gst_amount) AS total_tax" in sql


def test_erp_production_by_bom(monkeypatch):
    service = _service()
    _stub_ai(
        monkeypatch,
        "SELECT b.bom_name AS bom_name, SUM(p.produced_qty) AS produced_quantity "
        "FROM production_orders p JOIN boms b ON p.bom_id = b.bom_id "
        "GROUP BY b.bom_name ORDER BY produced_quantity DESC LIMIT 50;"
    )
    success, message, sql, error = service.process_question("show production by bom", ai_backend="local")

    assert success is True
    assert "FROM production_orders p" in sql
    assert "JOIN boms b" in sql
    assert "SUM(p.produced_qty) AS produced_quantity" in sql


def test_generic_select_fallback_marks_low_generation_confidence():
    """Test that simple table-list questions use rule-based with explicit columns."""
    service = AppService()
    service.database_service.knowledge_base = {
        "notes": {
            "columns": [
                {"name": "note_id", "type": "INTEGER", "nullable": False},
                {"name": "note_text", "type": "VARCHAR(255)", "nullable": True},
            ],
            "primary_keys": ["note_id"],
            "foreign_keys": [],
        }
    }
    success, message, sql, error = service.process_question("show notes", ai_backend="local")

    assert success is True
    assert sql == "SELECT note_id, note_text FROM notes LIMIT 50;"
    query_context = service.get_last_query_context()
    # With explicit columns, confidence should be high
    assert query_context["generation_confidence"] >= 0.9
    assert query_context["route_used"] == "rule-based"


def test_business_question_passes_plan_and_selected_tables_to_ai(monkeypatch):
    service = _service()
    captured: dict = {}
    _stub_ai(
        monkeypatch,
        "SELECT DATE_FORMAT(invoice_date, '%Y-%m') AS month, SUM(gst_amount) AS total_tax "
        "FROM sales_invoices GROUP BY DATE_FORMAT(invoice_date, '%Y-%m') ORDER BY month LIMIT 50;",
        capture=captured,
    )

    success, message, sql, error = service.process_question("show tax collected by month", ai_backend="local")

    assert success is True
    assert captured["query_plan"]["metric"] == "money"
    assert captured["query_plan"]["intent"] == "trend"
    assert captured["selected_tables"]
    assert any(entry["table"] == "sales_invoices" for entry in captured["selected_tables"])


def test_business_question_uses_rule_based_fallback_when_ai_is_too_generic(monkeypatch):
    service = _service()
    captured: dict = {}
    _stub_ai(
        monkeypatch,
        "SELECT * FROM sales_invoices LIMIT 50;",
        retry_sql="SELECT * FROM sales_invoices LIMIT 50;",
        capture=captured,
    )

    success, message, sql, error = service.process_question("show total sales this month", ai_backend="local")

    assert success is True
    assert service.get_last_query_context()["route_used"] == "rule-based"
    assert "SUM(" in sql
    assert "FROM sales_invoices" in sql
    assert "invoice_date >=" in sql


def test_ai_retry_receives_validator_error_and_dynamic_context(monkeypatch):
    service = _service()
    captured: dict = {}

    def fake_generate_sql(user_question, knowledge_base, backend=None, query_plan=None, selected_tables=None, business_glossary=None):
        return "SELECT customer_name FROM LIMIT 50"

    def fake_generate_sql_with_retry(
        user_question,
        knowledge_base,
        backend,
        first_attempt_sql,
        validation_reason,
        query_plan=None,
        selected_tables=None,
        business_glossary=None,
        validation_context=None,
    ):
        captured["first_attempt_sql"] = first_attempt_sql
        captured["validation_reason"] = validation_reason
        captured["validation_context"] = validation_context
        return (
            "SELECT c.customer_name AS customer_name, SUM(f.outstanding_amount) AS outstanding_balance "
            "FROM sales_invoices f JOIN customers c ON f.customer_id = c.customer_id "
            "WHERE f.outstanding_amount > 0 "
            "GROUP BY c.customer_name ORDER BY outstanding_balance DESC LIMIT 50;"
        )

    def fake_generate_simple_sql(*args, **kwargs):
        return None  # Disable rule-based fallback to test AI retry

    monkeypatch.setattr("core.question_service.generate_sql", fake_generate_sql)
    monkeypatch.setattr("core.question_service.generate_sql_with_retry", fake_generate_sql_with_retry)
    monkeypatch.setattr("ai.simple_query_generator.generate_simple_sql", fake_generate_simple_sql)

    success, message, sql, error = service.process_question("show customer outstanding balance", ai_backend="local")

    assert success is True
    assert "SUM(f.outstanding_amount) AS outstanding_balance" in sql
    assert "table name after FROM" in captured["validation_reason"]
    assert captured["validation_context"]["selected_tables"]
    assert "vector_tables" in captured["validation_context"]


def test_failed_retry_returns_clean_validation_failure_details(monkeypatch):
    """Test that rule-based fallback provides valid SQL when AI fails."""
    service = _service()

    def fake_generate_sql(user_question, knowledge_base, backend=None, query_plan=None, selected_tables=None, business_glossary=None):
        return "DELETE FROM customers"  # Use unsafe SQL to force AI failure

    def fake_generate_sql_with_retry(
        user_question,
        knowledge_base,
        backend,
        first_attempt_sql,
        validation_reason,
        query_plan=None,
        selected_tables=None,
        business_glossary=None,
        validation_context=None,
    ):
        return "DELETE FROM customers"  # Use unsafe SQL to force AI retry failure

    monkeypatch.setattr("core.question_service.generate_sql", fake_generate_sql)
    monkeypatch.setattr("core.question_service.generate_sql_with_retry", fake_generate_sql_with_retry)

    success, message, sql, error = service.process_question("show customer outstanding balance", ai_backend="local")

    # With improved rule-based fallback, the query should now succeed
    assert success is True
    assert sql is not None
    assert service.get_last_query_context()["route_used"] == "rule-based"
    # Verify that the SQL is valid (not the unsafe DELETE)
    assert "DELETE" not in sql.upper()
