from datetime import date

from core.app_service import AppService


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
    return service


def test_erp_total_sales_this_month():
    service = _service()
    success, message, sql, error = service.process_question("show total sales this month", ai_backend="local")

    assert success is True
    assert "SUM(final_amount) AS total_sales" in sql
    assert "FROM sales_invoices" in sql
    assert f"invoice_date >= '{date.today().replace(day=1).isoformat()}'" in sql
    assert service.get_last_query_context()["selected_table_names"] != list(_erp_knowledge_base().keys())


def test_erp_purchase_by_vendor():
    service = _service()
    success, message, sql, error = service.process_question("show purchase by vendor", ai_backend="local")

    assert success is True
    assert "FROM purchase_orders p" in sql
    assert "JOIN vendors v" in sql
    assert "GROUP BY v.vendor_name" in sql


def test_erp_current_stock_by_warehouse():
    service = _service()
    success, message, sql, error = service.process_question("show current stock by warehouse", ai_backend="local")

    assert success is True
    assert "FROM inventory_balance s" in sql
    assert "JOIN warehouses w" in sql
    assert "SUM(s.stock_qty) AS current_stock" in sql


def test_erp_low_stock_items():
    service = _service()
    success, message, sql, error = service.process_question("show low stock items", ai_backend="local")

    assert success is True
    assert "FROM inventory_balance s" in sql
    assert "JOIN items i" in sql
    assert "WHERE s.stock_qty <= s.reorder_level" in sql


def test_erp_unpaid_invoices():
    service = _service()
    success, message, sql, error = service.process_question("show unpaid invoices", ai_backend="local")

    assert success is True
    assert "FROM sales_invoices" in sql
    assert "IN ('Pending', 'Unpaid', 'Outstanding')" in sql


def test_erp_customer_outstanding_balance():
    service = _service()
    success, message, sql, error = service.process_question("show customer outstanding balance", ai_backend="local")

    assert success is True
    assert "FROM sales_invoices f" in sql
    assert "JOIN customers c" in sql
    assert "SUM(f.outstanding_amount) AS outstanding_balance" in sql


def test_erp_vendor_pending_payments():
    service = _service()
    success, message, sql, error = service.process_question("show vendor pending payments", ai_backend="local")

    assert success is True
    assert "FROM vendor_payments p" in sql
    assert "JOIN vendors v" in sql
    assert "SUM(p.amount_due) AS pending_amount" in sql


def test_erp_salary_by_department():
    service = _service()
    success, message, sql, error = service.process_question("show salary by department", ai_backend="local")

    assert success is True
    assert "FROM employees" in sql
    assert "GROUP BY department" in sql
    assert "SUM(salary) AS total_salary" in sql


def test_erp_tax_collected_by_month():
    service = _service()
    success, message, sql, error = service.process_question("show tax collected by month", ai_backend="local")

    assert success is True
    assert "DATE_FORMAT(invoice_date, '%Y-%m') AS month" in sql
    assert "SUM(gst_amount) AS total_tax" in sql


def test_erp_production_by_bom():
    service = _service()
    success, message, sql, error = service.process_question("show production by bom", ai_backend="local")

    assert success is True
    assert "FROM production_orders p" in sql
    assert "JOIN boms b" in sql
    assert "SUM(p.produced_qty) AS produced_quantity" in sql
