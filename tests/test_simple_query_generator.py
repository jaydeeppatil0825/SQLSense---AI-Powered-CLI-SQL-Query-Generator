"""Tests for the deterministic simple query generator."""

import pytest

# Minimal knowledge base fixture that mirrors ai_sales_demo structure.
DEMO_KB = {
    "customers": {
        "columns": [
            {"name": "customer_id", "type": "INTEGER", "nullable": False},
            {"name": "customer_name", "type": "VARCHAR(100)", "nullable": False},
            {"name": "status", "type": "VARCHAR(20)", "nullable": True},
            {"name": "signup_date", "type": "DATE", "nullable": True},
            {"name": "city", "type": "VARCHAR(50)", "nullable": True},
            {"name": "customer_type", "type": "VARCHAR(30)", "nullable": True},
        ],
        "primary_keys": ["customer_id"],
        "foreign_keys": [],
    },
    "orders": {
        "columns": [
            {"name": "order_id", "type": "INTEGER", "nullable": False},
            {"name": "customer_id", "type": "INTEGER", "nullable": False},
            {"name": "order_date", "type": "DATE", "nullable": False},
            {"name": "order_status", "type": "VARCHAR(30)", "nullable": True},
            {"name": "payment_status", "type": "VARCHAR(30)", "nullable": True},
            {"name": "final_amount", "type": "DECIMAL(12,2)", "nullable": True},
            {"name": "total_amount", "type": "DECIMAL(12,2)", "nullable": True},
        ],
        "primary_keys": ["order_id"],
        "foreign_keys": [{"column": "customer_id", "referenced_table": "customers", "referenced_column": "customer_id"}],
    },
    "payments": {
        "columns": [
            {"name": "payment_id", "type": "INTEGER", "nullable": False},
            {"name": "order_id", "type": "INTEGER", "nullable": False},
            {"name": "paid_amount", "type": "DECIMAL(12,2)", "nullable": True},
            {"name": "payment_status", "type": "VARCHAR(30)", "nullable": True},
            {"name": "payment_date", "type": "DATE", "nullable": True},
            {"name": "payment_method", "type": "VARCHAR(30)", "nullable": True},
        ],
        "primary_keys": ["payment_id"],
        "foreign_keys": [{"column": "order_id", "referenced_table": "orders", "referenced_column": "order_id"}],
    },
    "employees": {
        "columns": [
            {"name": "employee_id", "type": "INTEGER", "nullable": False},
            {"name": "employee_name", "type": "VARCHAR(100)", "nullable": True},
            {"name": "salary", "type": "DECIMAL(12,2)", "nullable": True},
            {"name": "joining_date", "type": "DATE", "nullable": True},
        ],
        "primary_keys": ["employee_id"],
        "foreign_keys": [],
    },
    "products": {
        "columns": [
            {"name": "product_id", "type": "INTEGER", "nullable": False},
            {"name": "product_name", "type": "VARCHAR(100)", "nullable": False},
            {"name": "category", "type": "VARCHAR(50)", "nullable": True},
            {"name": "unit_price", "type": "DECIMAL(10,2)", "nullable": False},
        ],
        "primary_keys": ["product_id"],
        "foreign_keys": [],
    },
    "order_items": {
        "columns": [
            {"name": "order_item_id", "type": "INTEGER", "nullable": False},
            {"name": "order_id", "type": "INTEGER", "nullable": False},
            {"name": "product_id", "type": "INTEGER", "nullable": False},
            {"name": "quantity", "type": "INTEGER", "nullable": False},
            {"name": "unit_price", "type": "DECIMAL(10,2)", "nullable": True},
            {"name": "line_total", "type": "DECIMAL(12,2)", "nullable": True},
        ],
        "primary_keys": ["order_item_id"],
        "foreign_keys": [
            {"column": "order_id", "referenced_table": "orders", "referenced_column": "order_id"},
            {"column": "product_id", "referenced_table": "products", "referenced_column": "product_id"},
        ],
    },
    "support_tickets": {
        "columns": [
            {"name": "ticket_id", "type": "INTEGER", "nullable": False},
            {"name": "customer_id", "type": "INTEGER", "nullable": False},
            {"name": "order_id", "type": "INTEGER", "nullable": True},
            {"name": "ticket_status", "type": "VARCHAR(20)", "nullable": True},
            {"name": "priority", "type": "VARCHAR(20)", "nullable": True},
            {"name": "subject", "type": "VARCHAR(200)", "nullable": True},
        ],
        "primary_keys": ["ticket_id"],
        "foreign_keys": [
            {"column": "customer_id", "referenced_table": "customers", "referenced_column": "customer_id"},
            {"column": "order_id", "referenced_table": "orders", "referenced_column": "order_id"},
        ],
    },
}


from ai.simple_query_generator import generate_simple_sql


def test_show_all_customers():
    sql = generate_simple_sql("Show all customers", DEMO_KB)
    assert sql == "SELECT * FROM customers LIMIT 50;"


def test_show_all_active_customers():
    """Status filter must beat show-all."""
    sql = generate_simple_sql("Show all active customers", DEMO_KB)
    assert sql == "SELECT * FROM customers WHERE status = 'Active' LIMIT 50;"


def test_show_active_customers_no_all():
    sql = generate_simple_sql("Show active customers", DEMO_KB)
    assert sql == "SELECT * FROM customers WHERE status = 'Active' LIMIT 50;"


def test_show_inactive_customers():
    sql = generate_simple_sql("Show inactive customers", DEMO_KB)
    assert sql == "SELECT * FROM customers WHERE status = 'Inactive' LIMIT 50;"


def test_show_paid_orders():
    sql = generate_simple_sql("Show paid orders", DEMO_KB)
    assert sql == "SELECT * FROM orders WHERE payment_status = 'Paid' LIMIT 50;"


def test_show_pending_payments():
    sql = generate_simple_sql("Show pending payments", DEMO_KB)
    assert sql == "SELECT * FROM payments WHERE payment_status = 'Pending' LIMIT 50;"


def test_show_cancelled_orders():
    sql = generate_simple_sql("Show cancelled orders", DEMO_KB)
    assert sql == "SELECT * FROM orders WHERE order_status = 'Cancelled' LIMIT 50;"


def test_count_total_orders():
    sql = generate_simple_sql("Count total orders", DEMO_KB)
    assert sql == "SELECT COUNT(*) AS total_orders FROM orders;"


def test_count_how_many_customers():
    sql = generate_simple_sql("How many customers", DEMO_KB)
    assert sql == "SELECT COUNT(*) AS total_customers FROM customers;"


def test_show_total_sales():
    sql = generate_simple_sql("Show total sales", DEMO_KB)
    assert sql == "SELECT SUM(final_amount) AS total_sales FROM orders;"


def test_show_total_paid_amount():
    sql = generate_simple_sql("Show total paid amount", DEMO_KB)
    assert sql == "SELECT SUM(paid_amount) AS total_paid_amount FROM payments;"


def test_show_total_salary():
    sql = generate_simple_sql("Show total salary", DEMO_KB)
    assert sql == "SELECT SUM(salary) AS total_salary FROM employees;"


def test_show_average_salary():
    sql = generate_simple_sql("Show average salary", DEMO_KB)
    assert sql == "SELECT AVG(salary) AS average_salary FROM employees;"


def test_show_average_product_price():
    sql = generate_simple_sql("Show average product price", DEMO_KB)
    assert sql == "SELECT AVG(unit_price) AS average_product_price FROM products;"


def test_show_latest_10_orders():
    sql = generate_simple_sql("Show latest 10 orders", DEMO_KB)
    assert sql == "SELECT * FROM orders ORDER BY order_date DESC LIMIT 10;"


def test_show_recent_customers():
    sql = generate_simple_sql("Show recent customers", DEMO_KB)
    assert sql == "SELECT * FROM customers ORDER BY signup_date DESC LIMIT 50;"


def test_top_5_customers_by_sales():
    sql = generate_simple_sql("Show top 5 customers by total sales", DEMO_KB)
    assert sql == (
        "SELECT c.customer_id, c.customer_name AS customer_name, "
        "SUM(o.final_amount) AS total_sales FROM customers c "
        "JOIN orders o ON c.customer_id = o.customer_id "
        "GROUP BY c.customer_id, c.customer_name "
        "ORDER BY total_sales DESC LIMIT 5;"
    )


def test_monthly_sales():
    sql = generate_simple_sql("Show monthly sales", DEMO_KB)
    assert sql == (
        "SELECT DATE_FORMAT(order_date, '%Y-%m') AS month, "
        "SUM(final_amount) AS total_sales FROM orders "
        "GROUP BY DATE_FORMAT(order_date, '%Y-%m') "
        "ORDER BY month LIMIT 50;"
    )


def test_total_sales_by_city():
    sql = generate_simple_sql("Show total sales by city", DEMO_KB)
    assert sql == (
        "SELECT c.city, SUM(o.final_amount) AS total_sales "
        "FROM customers c JOIN orders o ON c.customer_id = o.customer_id "
        "GROUP BY c.city ORDER BY total_sales DESC LIMIT 50;"
    )


def test_total_salary_all_employees():
    """'all' should not prevent total aggregation from running."""
    sql = generate_simple_sql("Show total salary of all employees", DEMO_KB)
    assert sql == "SELECT SUM(salary) AS total_salary FROM employees;"


def test_average_order_value():
    sql = generate_simple_sql("Show average order value", DEMO_KB)
    assert sql == "SELECT AVG(final_amount) AS average_order_value FROM orders;"


def test_highest_and_lowest_order_amount():
    assert generate_simple_sql("Show highest order amount", DEMO_KB) == (
        "SELECT MAX(final_amount) AS highest_order_amount FROM orders;"
    )
    assert generate_simple_sql("Show lowest order amount", DEMO_KB) == (
        "SELECT MIN(final_amount) AS lowest_order_amount FROM orders;"
    )


def test_high_value_orders_above_threshold():
    sql = generate_simple_sql("Show high value orders above 50000", DEMO_KB)
    assert sql == (
        "SELECT * FROM orders WHERE final_amount > 50000 "
        "ORDER BY final_amount DESC LIMIT 50;"
    )


def test_month_specific_sales_and_orders():
    assert generate_simple_sql("Show sales in January 2025", DEMO_KB) == (
        "SELECT SUM(final_amount) AS total_sales FROM orders "
        "WHERE order_date >= '2025-01-01' AND order_date < '2025-02-01';"
    )
    assert generate_simple_sql("Show orders in February 2025", DEMO_KB) == (
        "SELECT * FROM orders WHERE order_date >= '2025-02-01' "
        "AND order_date < '2025-03-01' LIMIT 50;"
    )


def test_orders_and_sales_by_status():
    assert generate_simple_sql("Show orders by status", DEMO_KB) == (
        "SELECT order_status, COUNT(*) AS total_orders "
        "FROM orders GROUP BY order_status "
        "ORDER BY total_orders DESC LIMIT 50;"
    )
    assert generate_simple_sql("Show total sales by payment status", DEMO_KB) == (
        "SELECT payment_status, SUM(final_amount) AS total_sales "
        "FROM orders GROUP BY payment_status "
        "ORDER BY total_sales DESC LIMIT 50;"
    )


def test_customer_and_product_groupings():
    assert generate_simple_sql("Show customers by city", DEMO_KB) == (
        "SELECT city, COUNT(*) AS total_customers "
        "FROM customers GROUP BY city "
        "ORDER BY total_customers DESC LIMIT 50;"
    )
    assert generate_simple_sql("Show products by category", DEMO_KB) == (
        "SELECT category, COUNT(*) AS total_products "
        "FROM products GROUP BY category "
        "ORDER BY total_products DESC LIMIT 50;"
    )
    assert generate_simple_sql("Show sales by customer type", DEMO_KB) == (
        "SELECT c.customer_type, SUM(o.final_amount) AS total_sales "
        "FROM customers c JOIN orders o ON c.customer_id = o.customer_id "
        "GROUP BY c.customer_type ORDER BY total_sales DESC LIMIT 50;"
    )


def test_orders_and_payments_with_customer_names():
    assert generate_simple_sql("Show orders with customer names", DEMO_KB) == (
        "SELECT o.order_id, c.customer_name AS customer_name, o.order_date, "
        "o.order_status, o.payment_status, o.final_amount FROM orders o "
        "JOIN customers c ON o.customer_id = c.customer_id LIMIT 50;"
    )
    assert generate_simple_sql("Show payment details with customer names", DEMO_KB) == (
        "SELECT p.payment_id, p.order_id, c.customer_name AS customer_name, "
        "p.payment_date, p.payment_method, p.paid_amount, p.payment_status "
        "FROM payments p JOIN orders o ON p.order_id = o.order_id "
        "JOIN customers c ON o.customer_id = c.customer_id LIMIT 50;"
    )


def test_product_sales_questions():
    assert generate_simple_sql("Show sales by product category", DEMO_KB) == (
        "SELECT p.category, SUM(oi.line_total) AS total_sales "
        "FROM products p JOIN order_items oi ON p.product_id = oi.product_id "
        "GROUP BY p.category ORDER BY total_sales DESC LIMIT 50;"
    )
    assert generate_simple_sql("Show top 3 selling products", DEMO_KB) == (
        "SELECT p.product_id, p.product_name AS product_name, "
        "SUM(oi.quantity) AS total_quantity "
        "FROM products p JOIN order_items oi ON p.product_id = oi.product_id "
        "GROUP BY p.product_id, p.product_name "
        "ORDER BY total_quantity DESC LIMIT 3;"
    )


def test_pending_payments_and_support_tickets():
    assert generate_simple_sql("Show customers with pending payments", DEMO_KB) == (
        "SELECT DISTINCT c.customer_id, c.customer_name AS customer_name "
        "FROM customers c "
        "JOIN orders o ON c.customer_id = o.customer_id "
        "JOIN payments p ON o.order_id = p.order_id "
        "WHERE p.payment_status = 'Pending' LIMIT 50;"
    )
    assert generate_simple_sql("Show open support tickets", DEMO_KB) == (
        "SELECT * FROM support_tickets WHERE ticket_status = 'Open' LIMIT 50;"
    )
    assert generate_simple_sql("Show support tickets by priority", DEMO_KB) == (
        "SELECT priority, COUNT(*) AS total_tickets "
        "FROM support_tickets GROUP BY priority "
        "ORDER BY total_tickets DESC LIMIT 50;"
    )
    assert generate_simple_sql("Show customers who raised support tickets", DEMO_KB) == (
        "SELECT DISTINCT c.customer_id, c.customer_name AS customer_name "
        "FROM customers c "
        "JOIN support_tickets st ON c.customer_id = st.customer_id "
        "LIMIT 50;"
    )
