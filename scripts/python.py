r"""Verify SQLSense business benchmark NLP/planner behavior on an already-created MySQL database.

Database expected to already exist:
    sqlsense_business_benchmark_lab

This script DOES NOT create, drop, or modify the database. It only:
1. connects SQLSense to the existing DB,
2. asks all base-to-advanced benchmark questions through AppService,
3. checks whether SQL was generated or correctly blocked,
4. checks generated SQL for required semantic patterns from the expected SQL,
5. optionally executes generated SQL through SQLSense,
6. optionally compares generated SQL results with direct expected-SQL oracle results.

Run from anywhere, passing the SQLSense project root:

PowerShell example:
    $env:DB_USER="root"
    $env:DB_PASSWORD="your_password"
    python verify_sqlsense_business_benchmark_nlp.py --project-root "C:\Users\JAYDEEP PATIL\OneDrive\Desktop\SQL-Sense"

Useful modes:
    python verify_sqlsense_business_benchmark_nlp.py --project-root "..." --questions-only
    python verify_sqlsense_business_benchmark_nlp.py --project-root "..." --verbose
    python verify_sqlsense_business_benchmark_nlp.py --project-root "..." --execute
    python verify_sqlsense_business_benchmark_nlp.py --project-root "..." --execute --compare-oracle
    python verify_sqlsense_business_benchmark_nlp.py --project-root "..." --strict-sql

Important:
    This is a Phase 6H benchmark-hardening verifier.
    Do NOT use it to start Phase 7.
    Do NOT add BFS/multi-hop joins to make blocked cases pass.
"""

from __future__ import annotations

import argparse
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass, field
from decimal import Decimal
import os
from pathlib import Path
import re
import sys
import tempfile
from typing import Any, Iterator, Sequence

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine, URL


DEFAULT_DATABASE = "sqlsense_business_benchmark_lab"
DATABASE_NAME_RE = re.compile(r"^[A-Za-z0-9_]+$")
EXPECTED_ROUTE = "deterministic_sql_required"
BLOCKED_ROUTES = {"cannot_plan_safely", "blocked_unsafe"}


@dataclass(frozen=True)
class VerificationCase:
    case_id: str
    section: str
    question: str
    expected_kind: str  # "safe" or "blocked"
    expected_sql_or_behavior: str
    expected_route: str = EXPECTED_ROUTE
    required_sql: tuple[str, ...] = ()
    forbidden_sql: tuple[str, ...] = ()
    note: str = ""


@dataclass
class CaseResult:
    case: VerificationCase
    passed: bool = False
    actual_route: str = ""
    actual_shape: str = ""
    sql: str | None = None
    expected_result: Any = None
    actual_result: Any = None
    executed: bool = False
    validation: dict[str, Any] = field(default_factory=dict)
    reason: str = ""
    error: str = ""
    message: str = ""
    intent: dict[str, Any] = field(default_factory=dict)
    planner: dict[str, Any] = field(default_factory=dict)
    required_patterns: tuple[str, ...] = ()


def safe(
    case_id: str,
    section: str,
    question: str,
    *,
    expected_sql_or_behavior: str,
    note: str = "",
    required_sql: Sequence[str] = (),
    forbidden_sql: Sequence[str] = (),
) -> VerificationCase:
    return VerificationCase(
        case_id=case_id,
        section=section,
        question=question,
        expected_kind="safe",
        expected_sql_or_behavior=expected_sql_or_behavior,
        expected_route=EXPECTED_ROUTE,
        required_sql=tuple(required_sql),
        forbidden_sql=tuple(forbidden_sql),
        note=note,
    )


def blocked(
    case_id: str,
    section: str,
    question: str,
    *,
    expected_sql_or_behavior: str,
    note: str = "",
    route: str = "cannot_plan_safely",
) -> VerificationCase:
    return VerificationCase(
        case_id=case_id,
        section=section,
        question=question,
        expected_kind="blocked",
        expected_sql_or_behavior=expected_sql_or_behavior,
        expected_route=route,
        note=note,
    )


CASES: tuple[VerificationCase, ...] = (
    safe(
        'T01', 'A_BASIC', 'show customers',
        expected_sql_or_behavior='SELECT customer_id, customer_name, customer_city, customer_segment, customer_status, signup_date FROM customers LIMIT 50;',
        note='Browse customers',
    ),
    safe(
        'T02', 'A_BASIC', 'show products',
        expected_sql_or_behavior='SELECT product_id, product_name, category, brand, product_status, unit_price FROM products LIMIT 50;',
        note='Browse products',
    ),
    safe(
        'T03', 'A_BASIC', 'show orders',
        expected_sql_or_behavior='SELECT order_id, customer_id, order_date, shipped_date, order_status, order_amount FROM orders LIMIT 50;',
        note='Browse orders',
    ),
    safe(
        'T04', 'A_BASIC', 'show payments',
        expected_sql_or_behavior='SELECT payment_id, order_id, payment_date, payment_method, payment_status, payment_amount FROM payments LIMIT 50;',
        note='Browse payments',
    ),
    safe(
        'T05', 'B_FILTERS', 'show all active customers',
        expected_sql_or_behavior="SELECT customer_id, customer_name, customer_city, customer_segment, customer_status, signup_date FROM customers WHERE customer_status = 'Active' LIMIT 50;",
        note='Single-table list',
    ),
    safe(
        'T06', 'B_FILTERS', 'show customers from Mumbai',
        expected_sql_or_behavior="SELECT customer_id, customer_name, customer_city, customer_segment, customer_status, signup_date FROM customers WHERE customer_city = 'Mumbai' LIMIT 50;",
        note='Single-table filter',
    ),
    safe(
        'T07', 'B_FILTERS', 'show retail customers',
        expected_sql_or_behavior="SELECT customer_id, customer_name, customer_city, customer_segment, customer_status, signup_date FROM customers WHERE customer_segment = 'Retail' LIMIT 50;",
        note='Single-table filter',
    ),
    safe(
        'T08', 'B_FILTERS', 'show products with unit price above 5000',
        expected_sql_or_behavior='SELECT product_id, product_name, category, brand, product_status, unit_price FROM products WHERE unit_price > 5000 LIMIT 50;',
        note='Numeric comparison',
    ),
    safe(
        'T09', 'B_FILTERS', 'show pending orders',
        expected_sql_or_behavior="SELECT order_id, customer_id, order_date, shipped_date, order_status, order_amount FROM orders WHERE order_status = 'Pending' LIMIT 50;",
        note='Status filter',
    ),
    safe(
        'T10', 'B_FILTERS', 'show orders with order amount greater than 10000',
        expected_sql_or_behavior='SELECT order_id, customer_id, order_date, shipped_date, order_status, order_amount FROM orders WHERE order_amount > 10000 LIMIT 50;',
        note='Amount filter',
    ),
    safe(
        'T11', 'B_FILTERS', 'show paid payments',
        expected_sql_or_behavior="SELECT payment_id, order_id, payment_date, payment_method, payment_status, payment_amount FROM payments WHERE payment_status = 'Paid' LIMIT 50;",
        note='Payment filter',
    ),
    safe(
        'T12', 'C_GROUPED_ANALYTICS', 'count customers by customer city',
        expected_sql_or_behavior='SELECT customer_city, COUNT(*) AS customer_count FROM customers GROUP BY customer_city;',
        note='Grouped count',
    ),
    safe(
        'T13', 'C_GROUPED_ANALYTICS', 'count customers by customer segment',
        expected_sql_or_behavior='SELECT customer_segment, COUNT(*) AS customer_count FROM customers GROUP BY customer_segment;',
        note='Grouped count',
    ),
    safe(
        'T14', 'C_GROUPED_ANALYTICS', 'show total order amount by order status',
        expected_sql_or_behavior='SELECT order_status, SUM(order_amount) AS total_order_amount FROM orders GROUP BY order_status;',
        note='Grouped SUM',
    ),
    safe(
        'T15', 'C_GROUPED_ANALYTICS', 'show average product unit price by category',
        expected_sql_or_behavior='SELECT category, AVG(unit_price) AS average_unit_price FROM products GROUP BY category;',
        note='Grouped AVG',
    ),
    safe(
        'T16', 'C_GROUPED_ANALYTICS', 'show total payment amount by payment method for paid payments',
        expected_sql_or_behavior="SELECT payment_method, SUM(payment_amount) AS total_payment_amount FROM payments WHERE payment_status = 'Paid' GROUP BY payment_method;",
        note='Grouped SUM with WHERE',
    ),
    safe(
        'T17', 'C_GROUPED_ANALYTICS', 'show order statuses with total order amount greater than 50000',
        expected_sql_or_behavior='SELECT order_status, SUM(order_amount) AS total_order_amount FROM orders GROUP BY order_status HAVING SUM(order_amount) > 50000;',
        note='HAVING',
    ),
    safe(
        'T18', 'C_GROUPED_ANALYTICS', 'show customer cities having more than 2 customers',
        expected_sql_or_behavior='SELECT customer_city, COUNT(*) AS customer_count FROM customers GROUP BY customer_city HAVING COUNT(*) > 2;',
        note='HAVING count',
    ),
    safe(
        'T19', 'D_RANKING', 'show top 5 orders by order amount',
        expected_sql_or_behavior='SELECT order_id, customer_id, order_date, shipped_date, order_status, order_amount FROM orders ORDER BY order_amount DESC LIMIT 5;',
        note='Top N rows',
    ),
    safe(
        'T20', 'D_RANKING', 'show bottom 5 products by unit price',
        expected_sql_or_behavior='SELECT product_id, product_name, category, brand, product_status, unit_price FROM products ORDER BY unit_price ASC LIMIT 5;',
        note='Bottom N rows',
    ),
    safe(
        'T21', 'D_RANKING', 'top 3 order statuses by total order amount',
        expected_sql_or_behavior='SELECT order_status, SUM(order_amount) AS total_order_amount FROM orders GROUP BY order_status ORDER BY total_order_amount DESC LIMIT 3;',
        note='Top grouped SUM',
    ),
    safe(
        'T22', 'D_RANKING', 'bottom 3 categories by average unit price',
        expected_sql_or_behavior='SELECT category, AVG(unit_price) AS average_unit_price FROM products GROUP BY category ORDER BY average_unit_price ASC LIMIT 3;',
        note='Bottom grouped AVG',
    ),
    safe(
        'T23', 'D_RANKING', 'top 3 customer cities by customer count',
        expected_sql_or_behavior='SELECT customer_city, COUNT(*) AS customer_count FROM customers GROUP BY customer_city ORDER BY customer_count DESC LIMIT 3;',
        note='Top grouped count',
    ),
    safe(
        'T24', 'D_RANKING', 'top 5 paid payments by payment amount',
        expected_sql_or_behavior="SELECT payment_id, order_id, payment_date, payment_method, payment_status, payment_amount FROM payments WHERE payment_status = 'Paid' ORDER BY payment_amount DESC LIMIT 5;",
        note='Ranking with WHERE',
    ),
    safe(
        'T25', 'E_DIRECT_JOIN_LOOKUP', 'show orders with customer details',
        expected_sql_or_behavior='SELECT orders.order_id AS orders__order_id, orders.customer_id AS orders__customer_id, orders.order_date AS orders__order_date, orders.shipped_date AS orders__shipped_date, orders.order_status AS orders__order_status, orders.order_amount AS orders__order_amount, customers.customer_id AS customers__customer_id, customers.customer_name AS customers__customer_name, customers.customer_city AS customers__customer_city, customers.customer_segment AS customers__customer_segment, customers.customer_status AS customers__customer_status, customers.signup_date AS customers__signup_date FROM orders INNER JOIN customers ON orders.customer_id = customers.customer_id LIMIT 50;',
        note='Direct joined lookup',
    ),
    safe(
        'T26', 'E_DIRECT_JOIN_LOOKUP', 'show payments with order details',
        expected_sql_or_behavior='SELECT payments.payment_id AS payments__payment_id, payments.order_id AS payments__order_id, payments.payment_date AS payments__payment_date, payments.payment_method AS payments__payment_method, payments.payment_status AS payments__payment_status, payments.payment_amount AS payments__payment_amount, orders.order_id AS orders__order_id, orders.customer_id AS orders__customer_id, orders.order_date AS orders__order_date, orders.shipped_date AS orders__shipped_date, orders.order_status AS orders__order_status, orders.order_amount AS orders__order_amount FROM payments INNER JOIN orders ON payments.order_id = orders.order_id LIMIT 50;',
        note='Direct joined lookup',
    ),
    safe(
        'T27', 'E_DIRECT_JOIN_LOOKUP', 'show order items with product details',
        expected_sql_or_behavior='SELECT order_items.order_item_id AS order_items__order_item_id, order_items.order_id AS order_items__order_id, order_items.product_id AS order_items__product_id, order_items.quantity AS order_items__quantity, order_items.unit_price AS order_items__unit_price, order_items.line_amount AS order_items__line_amount, products.product_id AS products__product_id, products.product_name AS products__product_name, products.category AS products__category, products.brand AS products__brand, products.product_status AS products__product_status, products.unit_price AS products__unit_price FROM order_items INNER JOIN products ON order_items.product_id = products.product_id LIMIT 50;',
        note='Direct joined lookup',
    ),
    safe(
        'T28', 'E_DIRECT_JOIN_LOOKUP', 'show orders with customer details for customers from Mumbai',
        expected_sql_or_behavior="SELECT orders.order_id AS orders__order_id, orders.customer_id AS orders__customer_id, orders.order_date AS orders__order_date, orders.shipped_date AS orders__shipped_date, orders.order_status AS orders__order_status, orders.order_amount AS orders__order_amount, customers.customer_id AS customers__customer_id, customers.customer_name AS customers__customer_name, customers.customer_city AS customers__customer_city, customers.customer_segment AS customers__customer_segment, customers.customer_status AS customers__customer_status, customers.signup_date AS customers__signup_date FROM orders INNER JOIN customers ON orders.customer_id = customers.customer_id WHERE customers.customer_city = 'Mumbai' LIMIT 50;",
        note='Joined lookup with related filter',
    ),
    safe(
        'T29', 'E_DIRECT_JOIN_LOOKUP', 'show payments with order details for paid payments',
        expected_sql_or_behavior="SELECT payments.payment_id AS payments__payment_id, payments.order_id AS payments__order_id, payments.payment_date AS payments__payment_date, payments.payment_method AS payments__payment_method, payments.payment_status AS payments__payment_status, payments.payment_amount AS payments__payment_amount, orders.order_id AS orders__order_id, orders.customer_id AS orders__customer_id, orders.order_date AS orders__order_date, orders.shipped_date AS orders__shipped_date, orders.order_status AS orders__order_status, orders.order_amount AS orders__order_amount FROM payments INNER JOIN orders ON payments.order_id = orders.order_id WHERE payments.payment_status = 'Paid' LIMIT 50;",
        note='Joined lookup with base filter',
    ),
    safe(
        'T30', 'F_DIRECT_JOIN_ANALYTICS', 'show total order amount by customer city',
        expected_sql_or_behavior='SELECT customers.customer_city, SUM(orders.order_amount) AS total_order_amount FROM orders INNER JOIN customers ON orders.customer_id = customers.customer_id GROUP BY customers.customer_city;',
        note='Direct joined SUM',
    ),
    safe(
        'T31', 'F_DIRECT_JOIN_ANALYTICS', 'count orders by customer segment',
        expected_sql_or_behavior='SELECT customers.customer_segment, COUNT(*) AS order_count FROM orders INNER JOIN customers ON orders.customer_id = customers.customer_id GROUP BY customers.customer_segment;',
        note='Direct joined COUNT',
    ),
    safe(
        'T32', 'F_DIRECT_JOIN_ANALYTICS', 'show average order amount by customer segment',
        expected_sql_or_behavior='SELECT customers.customer_segment, AVG(orders.order_amount) AS average_order_amount FROM orders INNER JOIN customers ON orders.customer_id = customers.customer_id GROUP BY customers.customer_segment;',
        note='Direct joined AVG',
    ),
    safe(
        'T33', 'F_DIRECT_JOIN_ANALYTICS', 'show total order amount by customer city for active customers',
        expected_sql_or_behavior="SELECT customers.customer_city, SUM(orders.order_amount) AS total_order_amount FROM orders INNER JOIN customers ON orders.customer_id = customers.customer_id WHERE customers.customer_status = 'Active' GROUP BY customers.customer_city;",
        note='Direct joined SUM with related filter',
    ),
    safe(
        'T34', 'F_DIRECT_JOIN_ANALYTICS', 'show total order amount by customer city for delivered orders',
        expected_sql_or_behavior="SELECT customers.customer_city, SUM(orders.order_amount) AS total_order_amount FROM orders INNER JOIN customers ON orders.customer_id = customers.customer_id WHERE orders.order_status = 'Delivered' GROUP BY customers.customer_city;",
        note='Direct joined SUM with base filter',
    ),
    safe(
        'T35', 'F_DIRECT_JOIN_ANALYTICS', 'top 3 customers by total order amount',
        expected_sql_or_behavior='SELECT customers.customer_name, SUM(orders.order_amount) AS total_order_amount FROM orders INNER JOIN customers ON orders.customer_id = customers.customer_id GROUP BY customers.customer_name ORDER BY total_order_amount DESC LIMIT 3;',
        note='Direct joined ranking',
    ),
    safe(
        'T36', 'F_DIRECT_JOIN_ANALYTICS', 'bottom 3 customer cities by average order amount',
        expected_sql_or_behavior='SELECT customers.customer_city, AVG(orders.order_amount) AS average_order_amount FROM orders INNER JOIN customers ON orders.customer_id = customers.customer_id GROUP BY customers.customer_city ORDER BY average_order_amount ASC LIMIT 3;',
        note='Direct joined bottom ranking',
    ),
    safe(
        'T37', 'F_DIRECT_JOIN_ANALYTICS', 'show customer segments with total order amount greater than 100000',
        expected_sql_or_behavior='SELECT customers.customer_segment, SUM(orders.order_amount) AS total_order_amount FROM orders INNER JOIN customers ON orders.customer_id = customers.customer_id GROUP BY customers.customer_segment HAVING SUM(orders.order_amount) > 100000;',
        note='Direct joined HAVING',
    ),
    safe(
        'T38', 'F_DIRECT_JOIN_ANALYTICS', 'show total line amount by product category',
        expected_sql_or_behavior='SELECT products.category, SUM(order_items.line_amount) AS total_line_amount FROM order_items INNER JOIN products ON order_items.product_id = products.product_id GROUP BY products.category;',
        note='Direct joined SUM',
    ),
    safe(
        'T39', 'F_DIRECT_JOIN_ANALYTICS', 'count order items by product brand',
        expected_sql_or_behavior='SELECT products.brand, COUNT(*) AS order_item_count FROM order_items INNER JOIN products ON order_items.product_id = products.product_id GROUP BY products.brand;',
        note='Direct joined COUNT',
    ),
    safe(
        'T40', 'F_DIRECT_JOIN_ANALYTICS', 'show total payment amount by order status',
        expected_sql_or_behavior='SELECT orders.order_status, SUM(payments.payment_amount) AS total_payment_amount FROM payments INNER JOIN orders ON payments.order_id = orders.order_id GROUP BY orders.order_status;',
        note='Direct joined SUM with direct edge',
    ),
    safe(
        'T41', 'G_SMART_MATCHING', 'show total order amount by customer segment',
        expected_sql_or_behavior='SELECT customers.customer_segment, SUM(orders.order_amount) AS total_order_amount FROM orders INNER JOIN customers ON orders.customer_id = customers.customer_id GROUP BY customers.customer_segment;',
        note='Owner-qualified exact matching',
    ),
    safe(
        'T42', 'G_SMART_MATCHING', 'show total order amount by customer city',
        expected_sql_or_behavior='SELECT customers.customer_city, SUM(orders.order_amount) AS total_order_amount FROM orders INNER JOIN customers ON orders.customer_id = customers.customer_id GROUP BY customers.customer_city;',
        note='Owner-qualified exact matching',
    ),
    safe(
        'T43', 'G_SMART_MATCHING', 'show total line amount by product brand',
        expected_sql_or_behavior='SELECT products.brand, SUM(order_items.line_amount) AS total_line_amount FROM order_items INNER JOIN products ON order_items.product_id = products.product_id GROUP BY products.brand;',
        note='Owner-qualified exact matching',
    ),
    safe(
        'T44', 'G_SMART_MATCHING', 'show delivered order amount by customer city',
        expected_sql_or_behavior="SELECT customers.customer_city, SUM(orders.order_amount) AS total_order_amount FROM orders INNER JOIN customers ON orders.customer_id = customers.customer_id WHERE orders.order_status = 'Delivered' GROUP BY customers.customer_city;",
        note='Sample-backed modifier filter',
    ),
    safe(
        'T45', 'G_SMART_MATCHING', 'show active product sales by product category',
        expected_sql_or_behavior="SELECT products.category, SUM(order_items.line_amount) AS total_line_amount FROM order_items INNER JOIN products ON order_items.product_id = products.product_id WHERE products.product_status = 'Active' GROUP BY products.category;",
        note='Sample-backed modifier filter',
    ),
    safe(
        'T46', 'H_DATE_INTERVALS', 'show payments after 2026-02-10',
        expected_sql_or_behavior="SELECT payment_id, order_id, payment_date, payment_method, payment_status, payment_amount FROM payments WHERE payment_date > '2026-02-10' LIMIT 50;",
        note='Single-table date interval',
    ),
    safe(
        'T47', 'H_DATE_INTERVALS', 'show payments between 2026-02-01 and 2026-02-28',
        expected_sql_or_behavior="SELECT payment_id, order_id, payment_date, payment_method, payment_status, payment_amount FROM payments WHERE payment_date BETWEEN '2026-02-01' AND '2026-02-28' LIMIT 50;",
        note='Single-table date interval',
    ),
    safe(
        'T48', 'H_DATE_INTERVALS', 'show orders where order date after 2026-01-15',
        expected_sql_or_behavior="SELECT order_id, customer_id, order_date, shipped_date, order_status, order_amount FROM orders WHERE order_date > '2026-01-15' LIMIT 50;",
        note='Explicit date role',
    ),
    safe(
        'T49', 'H_DATE_INTERVALS', 'show orders where shipped date before 2026-02-01',
        expected_sql_or_behavior="SELECT order_id, customer_id, order_date, shipped_date, order_status, order_amount FROM orders WHERE shipped_date < '2026-02-01' LIMIT 50;",
        note='Explicit date role',
    ),
    safe(
        'T50', 'H_DATE_INTERVALS', 'show orders where order date in January 2026',
        expected_sql_or_behavior="SELECT order_id, customer_id, order_date, shipped_date, order_status, order_amount FROM orders WHERE order_date BETWEEN '2026-01-01' AND '2026-01-31' LIMIT 50;",
        note='Month interval',
    ),
    safe(
        'T51', 'H_DATE_INTERVALS', 'show total order amount by customer city for order date in January 2026',
        expected_sql_or_behavior="SELECT customers.customer_city, SUM(orders.order_amount) AS total_order_amount FROM orders INNER JOIN customers ON orders.customer_id = customers.customer_id WHERE orders.order_date BETWEEN '2026-01-01' AND '2026-01-31' GROUP BY customers.customer_city;",
        note='Joined aggregate with date',
    ),
    safe(
        'T52', 'H_DATE_INTERVALS', 'count orders by customer segment for order date in February 2026',
        expected_sql_or_behavior="SELECT customers.customer_segment, COUNT(*) AS order_count FROM orders INNER JOIN customers ON orders.customer_id = customers.customer_id WHERE orders.order_date BETWEEN '2026-02-01' AND '2026-02-28' GROUP BY customers.customer_segment;",
        note='Joined count with date',
    ),
    safe(
        'T53', 'H_DATE_INTERVALS', 'top 3 customers by total order amount in 2026',
        expected_sql_or_behavior="SELECT customers.customer_name, SUM(orders.order_amount) AS total_order_amount FROM orders INNER JOIN customers ON orders.customer_id = customers.customer_id WHERE orders.order_date BETWEEN '2026-01-01' AND '2026-12-31' GROUP BY customers.customer_name ORDER BY total_order_amount DESC LIMIT 3;",
        note='Ranking with year interval',
    ),
    safe(
        'T54', 'H_DATE_INTERVALS', 'bottom 2 customer cities by average order amount for order date between 2026-01-01 and 2026-03-31',
        expected_sql_or_behavior="SELECT customers.customer_city, AVG(orders.order_amount) AS average_order_amount FROM orders INNER JOIN customers ON orders.customer_id = customers.customer_id WHERE orders.order_date BETWEEN '2026-01-01' AND '2026-03-31' GROUP BY customers.customer_city ORDER BY average_order_amount ASC LIMIT 2;",
        note='Ranking with between interval',
    ),
    safe(
        'T55', 'H_DATE_INTERVALS', 'show total payment amount by payment status in February 2026',
        expected_sql_or_behavior="SELECT payment_status, SUM(payment_amount) AS total_payment_amount FROM payments WHERE payment_date BETWEEN '2026-02-01' AND '2026-02-28' GROUP BY payment_status;",
        note='Single-table aggregate with month',
    ),
    safe(
        'T56', 'H_DATE_INTERVALS', 'show delivered orders from last 30 days',
        expected_sql_or_behavior="SELECT order_id, customer_id, order_date, shipped_date, order_status, order_amount FROM orders WHERE order_status = 'Delivered' AND order_date BETWEEN '2026-06-11' AND '2026-07-10' LIMIT 50;",
        note='Date interval + sample filter',
    ),
    blocked(
        'T57', 'H_DATE_INTERVALS', 'show orders after 2026-01-01',
        expected_sql_or_behavior='NO SQL. orders has multiple date columns: order_date and shipped_date. User must specify date role.',
        note='Ambiguous fieldless date',
    ),
    blocked(
        'T58', 'H_DATE_INTERVALS', 'show orders between January and pending',
        expected_sql_or_behavior="NO SQL. Invalid date interval; 'pending' is not a date boundary.",
        note='Invalid interval phrase',
    ),
    blocked(
        'T59', 'H_DATE_INTERVALS', 'show total order amount by customer city in festival season',
        expected_sql_or_behavior="NO SQL. 'festival season' is not a deterministic date interval.",
        note='Unsupported business date',
    ),
    blocked(
        'T60', 'H_DATE_INTERVALS', 'show orders where order status after 2026-01-01',
        expected_sql_or_behavior='NO SQL. order_status is text/status, not a date column.',
        note='Invalid date role',
    ),
    blocked(
        'T61', 'H_DATE_INTERVALS', 'delete payments before 2026-02-01',
        expected_sql_or_behavior='NO SQL. DELETE is not allowed.',
        note='Unsafe command with date',
    ),
    blocked(
        'T62', 'I_SAFETY', 'delete cancelled orders',
        expected_sql_or_behavior='NO SQL. Must be blocked because DELETE is not allowed.',
        note='Unsafe command',
    ),
    blocked(
        'T63', 'I_SAFETY', 'show total customer segment',
        expected_sql_or_behavior='NO SQL. customer_segment is text/category, not a numeric metric.',
        note='Metric guard',
    ),
    blocked(
        'T64', 'I_SAFETY', 'show payments with customer details',
        expected_sql_or_behavior='NO SQL in Phase 5/6. payments -> customers requires payments -> orders -> customers multi-hop, which is future Phase 7.',
        note='No direct graph path',
    ),
    blocked(
        'T65', 'I_SAFETY', 'show total payment amount by customer city',
        expected_sql_or_behavior='NO SQL in Phase 6. payments -> orders -> customers is multi-hop and must wait for Phase 7.',
        note='Multi-hop blocked',
    ),
    blocked(
        'T66', 'I_SAFETY', 'show total amount by customer city',
        expected_sql_or_behavior="NO SQL. Generic 'amount' is ambiguous across orders.order_amount, order_items.line_amount, payments.payment_amount.",
        note='Ambiguous metric',
    ),
    blocked(
        'T67', 'I_SAFETY', 'show total customer status by customer city',
        expected_sql_or_behavior='NO SQL. customer_status is not numeric and cannot be SUM metric.',
        note='Numeric metric guard',
    ),
    blocked(
        'T68', 'I_SAFETY', 'show status by amount',
        expected_sql_or_behavior="NO SQL. 'status' and 'amount' are too generic without owner/metric/dimension clarity.",
        note='Fail-closed ambiguity',
    ),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", required=True, help="Path to SQLSense project root, e.g. C:\\...\\SQL-Sense")
    parser.add_argument("--host", default=os.getenv("DB_HOST", "localhost"))
    parser.add_argument("--port", type=int, default=int(os.getenv("DB_PORT", "3306")))
    parser.add_argument("--user", default=os.getenv("DB_USER", ""))
    parser.add_argument("--database", default=os.getenv("SQLSENSE_TEST_DB", DEFAULT_DATABASE))
    parser.add_argument("--use-ai-enrichment", action="store_true", help="Optional KB enrichment; default is off for deterministic diagnosis.")
    parser.add_argument("--execute", action="store_true", help="Execute generated SQL through SQLSense after validation.")
    parser.add_argument("--compare-oracle", action="store_true", help="Compare actual result with expected SQL result. Implies --execute.")
    parser.add_argument("--strict-sql", action="store_true", help="Require normalized generated SQL to exactly match expected SQL.")
    parser.add_argument("--questions-only", action="store_true", help="Only print all benchmark questions and exit.")
    parser.add_argument("--case", help="Run one case id, for example T06.")
    parser.add_argument("--section", help="Run one section, for example B_FILTERS.")
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args()


def selected_cases(args: argparse.Namespace) -> tuple[VerificationCase, ...]:
    cases = CASES
    if args.case:
        cases = tuple(case for case in cases if case.case_id == args.case)
    if args.section:
        cases = tuple(case for case in cases if case.section == args.section)
    return cases


def database_url(args: argparse.Namespace, *, include_database: bool) -> URL:
    password = os.getenv("DB_PASSWORD", "")
    if not args.user or not password:
        raise RuntimeError("DB_USER and DB_PASSWORD must be set in the environment.")
    if not DATABASE_NAME_RE.fullmatch(args.database):
        raise RuntimeError("Database name must contain only letters, digits, and underscores.")
    return URL.create(
        "mysql+pymysql",
        username=args.user,
        password=password,
        host=args.host,
        port=args.port,
        database=args.database if include_database else None,
    )


def assert_existing_fixture(engine: Engine) -> None:
    schema = inspect(engine)
    expected_tables = {"customers", "products", "orders", "order_items", "payments"}
    actual_tables = set(schema.get_table_names())
    if not expected_tables.issubset(actual_tables):
        raise RuntimeError(
            f"Existing DB does not look like {DEFAULT_DATABASE}. "
            f"Expected tables {sorted(expected_tables)}, found {sorted(actual_tables)}. "
            "This script does not create the DB; load the benchmark DB first."
        )

    with engine.connect() as connection:
        counts = {
            name: connection.execute(text(f"SELECT COUNT(*) FROM `{name}`")).scalar_one()
            for name in sorted(expected_tables)
        }

    expected_counts = {
        "customers": 12,
        "products": 10,
        "orders": 180,
        "order_items": 360,
        "payments": 180,
    }
    if counts != expected_counts:
        raise RuntimeError(
            f"Fixture row counts do not match expected {expected_counts}; got {counts}. "
            "This script will not modify the DB. Reload the benchmark SQL if needed."
        )


@contextmanager
def isolated_app(args: argparse.Namespace) -> Iterator[Any]:
    project_root = Path(args.project_root).expanduser().resolve()
    if not (project_root / "core" / "app_service.py").exists():
        raise RuntimeError(f"--project-root does not look like SQLSense root: {project_root}")

    original_cwd = Path.cwd()
    old_path = list(sys.path)
    isolated_env_names = (
        "CHROMA_INDEX_DIR",
        "VECTOR_INDEX_DIR",
        "CUDA_VISIBLE_DEVICES",
        "EMBEDDING_BACKEND",
        "ANONYMIZED_TELEMETRY",
        "CHROMA_ANONYMIZED_TELEMETRY",
    )
    old_env = {name: os.environ.get(name) for name in isolated_env_names}

    with tempfile.TemporaryDirectory(prefix="sqlsense_business_nlp_verify_", ignore_cleanup_errors=True) as temp_dir:
        temp_path = Path(temp_dir)
        os.environ["ANONYMIZED_TELEMETRY"] = "False"
        os.environ["CHROMA_ANONYMIZED_TELEMETRY"] = "False"
        os.environ["CHROMA_INDEX_DIR"] = str(temp_path / "chroma")
        os.environ["VECTOR_INDEX_DIR"] = str(temp_path / "vector")
        os.environ["CUDA_VISIBLE_DEVICES"] = ""
        os.environ["EMBEDDING_BACKEND"] = "deterministic"

        sys.path.insert(0, str(project_root))
        os.chdir(temp_path)
        app = None
        try:
            from core.app_service import AppService

            app = AppService()
            success, message, report = app.connect_database_and_prepare(
                db_type="mysql",
                host=args.host,
                port=args.port,
                username=args.user,
                password=os.environ["DB_PASSWORD"],
                database=args.database,
                use_ai_enrichment=bool(args.use_ai_enrichment),
            )
            if not success or not app.is_database_ready():
                raise RuntimeError(
                    "SQLSense preparation failed: "
                    f"{message}; connected={report.get('connected')} "
                    f"kb_built={report.get('kb_built')} vector_status={report.get('vector_status')}"
                )
            yield app
        finally:
            if app is not None:
                try:
                    engine = app.database_service.get_engine()
                    if engine is not None:
                        engine.dispose()
                except Exception:
                    pass
            os.chdir(original_cwd)
            sys.path[:] = old_path
            for name, value in old_env.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value


def normalize_sql(sql: str | None) -> str:
    return " ".join(str(sql or "").strip().rstrip(";").split())


def normalize_for_exact(sql: str | None) -> str:
    return normalize_sql(sql).lower()


def _contains_normalized(sql: str, pattern: str) -> bool:
    return normalize_sql(pattern).lower() in sql.lower()


def extract_required_patterns(expected_sql: str) -> tuple[str, ...]:
    """
    Build SQL meaning checks from expected SQL.

    This intentionally avoids alias exactness but catches the real NLP/planner
    errors:
      - dropped WHERE filters
      - numeric comparison parsed as output field
      - grouped ranking converted into row ranking
      - HAVING dropped
      - JOIN/ON missing
      - wrong table selected
    """
    sql = normalize_sql(expected_sql)
    upper = sql.upper()
    patterns: list[str] = []

    # SQL keywords / structure.
    for keyword in ("FROM", "INNER JOIN", "WHERE", "GROUP BY", "HAVING", "ORDER BY", "LIMIT"):
        if keyword in upper:
            patterns.append(keyword)

    # Tables after FROM/JOIN.
    for table in re.findall(r"\b(?:FROM|JOIN)\s+([A-Za-z_][A-Za-z0-9_]*)", sql, flags=re.IGNORECASE):
        patterns.append(table)

    # Aggregate functions.
    for fn in ("COUNT", "SUM", "AVG", "MIN", "MAX"):
        if re.search(rf"\b{fn}\s*\(", upper):
            patterns.append(fn + "(")

    # Qualified identifiers and key unqualified identifiers.
    for ident in re.findall(r"\b[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*\b", sql):
        patterns.append(ident)

    # Important unqualified benchmark columns.
    important_columns = (
        "customer_id", "customer_name", "customer_city", "customer_segment", "customer_status", "signup_date",
        "product_id", "product_name", "category", "brand", "product_status", "unit_price",
        "order_id", "order_date", "shipped_date", "order_status", "order_amount",
        "order_item_id", "quantity", "line_amount",
        "payment_id", "payment_date", "payment_method", "payment_status", "payment_amount",
    )
    for column in important_columns:
        if re.search(rf"\b{re.escape(column)}\b", sql, flags=re.IGNORECASE):
            patterns.append(column)

    # String literals and numeric thresholds/dates.
    for literal in re.findall(r"'([^']*)'", sql):
        if literal:
            patterns.append(literal)
    for number in re.findall(r"(?<![A-Za-z_])\b\d{2,}(?:\.\d+)?\b", sql):
        patterns.append(number)

    # Operators that matter for filters.
    for op in (">=", "<=", "<>", "!=", ">", "<", "="):
        if op in sql:
            patterns.append(op)

    # Dedupe while preserving order.
    seen = set()
    deduped = []
    for pattern in patterns:
        key = pattern.lower()
        if key not in seen:
            seen.add(key)
            deduped.append(pattern)
    return tuple(deduped)


def normalize_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return ("decimal", format(value.quantize(Decimal("0.01")), "f"))
    if value is None:
        return None
    return value


def normalize_rows(rows: Sequence[Any], *, ordered: bool) -> Any:
    normalized: list[tuple[Any, ...]] = []
    for row in rows:
        values = list(row.values()) if isinstance(row, dict) else list(row)
        normalized.append(tuple(normalize_value(value) for value in values))
    if ordered:
        return normalized
    return Counter(normalized)


def sql_is_ordered(sql: str) -> bool:
    return " ORDER BY " in (" " + normalize_sql(sql).upper() + " ")


def run_expected_sql(engine: Engine, expected_sql: str) -> Any:
    ordered = sql_is_ordered(expected_sql)
    with engine.connect() as connection:
        rows = connection.execute(text(expected_sql)).all()
    return normalize_rows(rows, ordered=ordered)


def _payload_route(payload: dict[str, Any], context: dict[str, Any]) -> str:
    return str(
        payload.get("route")
        or payload.get("route_used")
        or context.get("route_recommendation")
        or context.get("route")
        or ""
    )


def run_case(app: Any, oracle_engine: Engine, case: VerificationCase, args: argparse.Namespace) -> CaseResult:
    result = CaseResult(case=case)

    # Important for stale planner/state leakage debugging.
    if hasattr(app, "reset_conversation"):
        app.reset_conversation()

    payload = app.process_question(case.question)
    context = payload.get("query_context") or {}

    result.planner = context
    result.intent = context.get("intent") or {}
    result.actual_route = _payload_route(payload, context)
    result.actual_shape = str(context.get("query_shape") or "")
    result.sql = payload.get("generated_sql") or payload.get("sql")
    result.validation = payload.get("validation_result") or {}
    result.error = str(payload.get("error") or "")
    result.message = str(payload.get("message") or "")

    if case.expected_kind == "blocked":
        if result.sql:
            result.reason = "blocked case returned SQL"
            return result
        if result.actual_route not in BLOCKED_ROUTES:
            result.reason = f"expected blocked route {sorted(BLOCKED_ROUTES)}, got {result.actual_route}"
            return result
        result.passed = True
        return result

    # Safe SQL case.
    if result.actual_route != EXPECTED_ROUTE:
        result.reason = f"expected route {EXPECTED_ROUTE}, got {result.actual_route}; message={result.message or result.error}"
        return result
    if not result.sql:
        result.reason = f"safe case returned no SQL: {result.error or result.message}"
        return result
    if result.validation and result.validation.get("is_valid") is not True:
        result.reason = f"generated SQL did not validate: {result.validation.get('reason')}"
        return result

    normalized_actual = normalize_sql(result.sql)
    normalized_expected = normalize_sql(case.expected_sql_or_behavior)

    if args.strict_sql and normalize_for_exact(normalized_actual) != normalize_for_exact(normalized_expected):
        result.reason = "strict SQL mismatch"
        return result

    required_patterns = tuple(case.required_sql) or extract_required_patterns(case.expected_sql_or_behavior)
    result.required_patterns = required_patterns
    for required in required_patterns:
        if not _contains_normalized(normalized_actual, required):
            result.reason = f"generated SQL is missing required pattern: {required}"
            return result

    for forbidden in case.forbidden_sql:
        if _contains_normalized(normalized_actual, forbidden):
            result.reason = f"generated SQL contains forbidden pattern: {forbidden}"
            return result

    # Avoid accidental unsafe SQL even if app returns it.
    if not normalized_actual.upper().startswith("SELECT "):
        result.reason = "generated SQL is not SELECT"
        return result

    should_execute = bool(args.execute or args.compare_oracle)
    if should_execute:
        executed, message, rows = app.execute_sql(result.sql, revalidate=True)
        result.executed = bool(executed)
        if not executed:
            result.reason = f"validated SQL did not execute: {message}"
            return result
        result.actual_result = normalize_rows(list(rows or []), ordered=sql_is_ordered(result.sql))

    if args.compare_oracle:
        result.expected_result = run_expected_sql(oracle_engine, case.expected_sql_or_behavior)
        if result.actual_result != result.expected_result:
            result.reason = "execution result differs from expected SQL oracle"
            return result

    result.passed = True
    return result


def clipped(value: Any, limit: int = 1800) -> str:
    rendered = repr(value)
    return rendered if len(rendered) <= limit else rendered[:limit] + "...<truncated>"


def print_questions(cases: Sequence[VerificationCase]) -> None:
    print(f"Database: {DEFAULT_DATABASE}")
    print(f"Questions: {len(cases)}")
    print("=" * 100)
    for case in cases:
        print(f"{case.case_id} | {case.section} | {case.note}")
        print(case.question)
        print()


def print_results(results: Sequence[CaseResult], *, verbose: bool) -> None:
    print("\nSQLSense business benchmark NLP/planner verification")
    print("=" * 150)
    print(f"{'ID':<5} {'OK':<4} {'SECTION':<34} {'ROUTE':<28} {'SHAPE':<24} QUESTION")
    print("-" * 150)
    for result in results:
        print(
            f"{result.case.case_id:<5} {'PASS' if result.passed else 'FAIL':<4} "
            f"{result.case.section:<34} {result.actual_route:<28} {result.actual_shape:<24} "
            f"{result.case.question}"
        )

    failures = [result for result in results if not result.passed]
    if failures:
        print("\nFailure diagnostics")
        print("=" * 150)
        print("Failed question IDs: " + ", ".join(result.case.case_id for result in failures))

    for result in failures:
        print(f"\n[{result.case.case_id}] {result.case.question}")
        print(f"Section: {result.case.section}")
        print(f"Category: {result.case.note}")
        print(f"Expected kind: {result.case.expected_kind}")
        print(f"Reason: {result.reason}")
        print(f"Expected SQL / behavior: {result.case.expected_sql_or_behavior}")
        print(f"Actual route/shape: {result.actual_route or '-'} / {result.actual_shape or '-'}")
        print(f"Generated SQL: {result.sql}")
        print(f"Required SQL patterns: {list(result.required_patterns)}")
        print(f"Expected result: {clipped(result.expected_result)}")
        print(f"Actual result: {clipped(result.actual_result)}")
        print(f"Executed: {result.executed}")
        print(f"Validation: {result.validation}")
        print(f"Error/message: {result.error or result.message}")
        print(f"Intent: {clipped(result.intent)}")
        print(f"Selected evidence: {clipped(result.planner.get('selected_evidence'))}")
        print(f"Clause plan: {clipped(result.planner.get('clause_plan'))}")
        print(f"Selected join path: {clipped(result.planner.get('selected_join_path'))}")
        if verbose:
            print(f"Full planner context: {clipped(result.planner, limit=5000)}")

    passed = sum(result.passed for result in results)
    print("\nSummary")
    print("=" * 150)
    print(f"Database: {DEFAULT_DATABASE}")
    print(f"Question cases: {len(results)}")
    print(f"Passed: {passed}")
    print(f"Failed: {len(results) - passed}")
    print("Skipped: 0")


def main() -> int:
    args = parse_args()
    cases = selected_cases(args)

    if args.questions_only:
        print_questions(cases)
        return 0

    oracle_engine: Engine | None = None
    try:
        oracle_engine = create_engine(database_url(args, include_database=True))
        assert_existing_fixture(oracle_engine)

        with isolated_app(args) as app:
            results = [run_case(app, oracle_engine, case, args) for case in cases]

        print_results(results, verbose=bool(args.verbose))
        return 0 if all(result.passed for result in results) else 1
    except Exception as exc:
        print("\nSQLSense business benchmark verification could not start.")
        print(f"Reason: {exc}")
        print("This script does not create the DB. Load sqlsense_business_benchmark_lab first, then rerun.")
        return 1
    finally:
        if oracle_engine is not None:
            oracle_engine.dispose()


if __name__ == "__main__":
    sys.exit(main())
