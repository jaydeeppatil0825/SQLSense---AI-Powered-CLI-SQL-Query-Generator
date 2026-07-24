# SQLSense Current-Scope Question Report

- Run timestamp: 2026-07-24T05:14:55.391197+00:00
- Git: codex/review-project-overview f2314e82
- Python: 3.12.10
- MySQL: 8.0.46
- Database: sqlsense_current_scope_business_lab
- Table count: 8
- KB build: passed
- AI semantic enrichment: completed
- AI fallback used: False
- Total tests: 58
- Supported: 46 passed / 4 failed
- Fail-closed: 8 passed / 0 failed
- Final verdict: failed

## Artifact Fingerprints
- `glossary_fingerprint`: ``
- `graph_fingerprint`: ``
- `kb_fingerprint`: ``
- `schema_fingerprint`: `b51701a7eaa0c14fb2f50408f397ada7bd43f5f63a23b49e90d5d845539e572a`
- `vector_fingerprint`: ``

## Row Counts
- `categories`: 7
- `customers`: 12
- `order_items`: 43
- `orders`: 15
- `payments`: 15
- `products`: 15
- `shipments`: 15
- `suppliers`: 8

## Questions

### TEST 01 - PASS
- Question: show all active customers
- Expected category: supported
- Route/shape: deterministic_sql_required / filtered_query
- Executable: True
- Validation/execution: passed / passed
- Expected/actual rows: 10 / 10
- Result comparison: match
- Join path length: 0
- Reason code: deterministic SQL generated for filtered_query/where_only: single-table where_only SQL generated deterministically

Generated SQL:
```sql
SELECT * FROM customers WHERE customer_status = 'active' LIMIT 50;
```

Expected SQL:
```sql
SELECT *
FROM customers
WHERE customer_status = 'Active'
LIMIT 50;
```

### TEST 02 - PASS
- Question: show customers in Pune
- Expected category: supported
- Route/shape: deterministic_sql_required / filtered_query
- Executable: True
- Validation/execution: passed / passed
- Expected/actual rows: 2 / 2
- Result comparison: match
- Join path length: 0
- Reason code: deterministic SQL generated for filtered_query/where_only: single-table where_only SQL generated deterministically

Generated SQL:
```sql
SELECT * FROM customers WHERE city = 'Pune' LIMIT 50;
```

Expected SQL:
```sql
SELECT *
FROM customers
WHERE city = 'Pune'
LIMIT 50;
```

### TEST 03 - PASS
- Question: show inactive suppliers
- Expected category: supported
- Route/shape: deterministic_sql_required / filtered_query
- Executable: True
- Validation/execution: passed / passed
- Expected/actual rows: 2 / 2
- Result comparison: match
- Join path length: 0
- Reason code: deterministic SQL generated for filtered_query/where_only: single-table where_only SQL generated deterministically

Generated SQL:
```sql
SELECT * FROM suppliers WHERE supplier_status = 'inactive' LIMIT 50;
```

Expected SQL:
```sql
SELECT *
FROM suppliers
WHERE supplier_status = 'Inactive'
LIMIT 50;
```

### TEST 04 - PASS
- Question: show active products with stock quantity below 50
- Expected category: supported
- Route/shape: deterministic_sql_required / filtered_query
- Executable: True
- Validation/execution: passed / passed
- Expected/actual rows: 4 / 4
- Result comparison: match
- Join path length: 0
- Reason code: deterministic SQL generated for filtered_query/where_only: single-table where_only SQL generated deterministically

Generated SQL:
```sql
SELECT * FROM products WHERE product_status = 'active' AND stock_quantity < 50 LIMIT 50;
```

Expected SQL:
```sql
SELECT *
FROM products
WHERE product_status = 'Active'
  AND stock_quantity < 50
LIMIT 50;
```

### TEST 05 - PASS
- Question: show orders with total amount above 50000
- Expected category: supported
- Route/shape: deterministic_sql_required / filtered_query
- Executable: True
- Validation/execution: passed / passed
- Expected/actual rows: 9 / 9
- Result comparison: match
- Join path length: 0
- Reason code: deterministic SQL generated for filtered_query/where_only: single-table where_only SQL generated deterministically

Generated SQL:
```sql
SELECT * FROM orders WHERE total_amount > 50000 LIMIT 50;
```

Expected SQL:
```sql
SELECT *
FROM orders
WHERE total_amount > 50000
LIMIT 50;
```

### TEST 06 - PASS
- Question: show delivered orders from January 2026
- Expected category: supported
- Route/shape: deterministic_sql_required / filtered_query
- Executable: True
- Validation/execution: passed / passed
- Expected/actual rows: 2 / 2
- Result comparison: match
- Join path length: 0
- Reason code: deterministic SQL generated for filtered_query/where_only: single-table where_only SQL generated deterministically

Generated SQL:
```sql
SELECT * FROM orders WHERE order_status = 'Delivered' AND order_date BETWEEN '2026-01-01' AND '2026-01-31' LIMIT 50;
```

Expected SQL:
```sql
SELECT *
FROM orders
WHERE order_status = 'Delivered'
  AND order_date >= '2026-01-01'
  AND order_date < '2026-02-01'
LIMIT 50;
```

### TEST 07 - PASS
- Question: show payments after April 1 2026
- Expected category: supported
- Route/shape: deterministic_sql_required / filtered_query
- Executable: True
- Validation/execution: passed / passed
- Expected/actual rows: 9 / 9
- Result comparison: match
- Join path length: 0
- Reason code: deterministic SQL generated for filtered_query/where_only: single-table where_only SQL generated deterministically

Generated SQL:
```sql
SELECT * FROM payments WHERE payment_date > '2026-04-01' LIMIT 50;
```

Expected SQL:
```sql
SELECT *
FROM payments
WHERE payment_date > '2026-04-01'
LIMIT 50;
```

### TEST 08 - PASS
- Question: show products with unit price between 500 and 3000
- Expected category: supported
- Route/shape: deterministic_sql_required / filtered_query
- Executable: True
- Validation/execution: passed / passed
- Expected/actual rows: 6 / 6
- Result comparison: match
- Join path length: 0
- Reason code: deterministic SQL generated for filtered_query/where_only: single-table where_only SQL generated deterministically

Generated SQL:
```sql
SELECT * FROM products WHERE unit_price BETWEEN 500 AND 3000 LIMIT 50;
```

Expected SQL:
```sql
SELECT *
FROM products
WHERE unit_price BETWEEN 500 AND 3000
LIMIT 50;
```

### TEST 09 - PASS
- Question: show suppliers from Pune
- Expected category: supported
- Route/shape: deterministic_sql_required / filtered_query
- Executable: True
- Validation/execution: passed / passed
- Expected/actual rows: 1 / 1
- Result comparison: match
- Join path length: 0
- Reason code: deterministic SQL generated for filtered_query/where_only: single-table where_only SQL generated deterministically

Generated SQL:
```sql
SELECT * FROM suppliers WHERE city = 'Pune' LIMIT 50;
```

Expected SQL:
```sql
SELECT *
FROM suppliers
WHERE city = 'Pune'
LIMIT 50;
```

### TEST 10 - PASS
- Question: show shipments with shipping cost above 1500
- Expected category: supported
- Route/shape: deterministic_sql_required / filtered_query
- Executable: True
- Validation/execution: passed / passed
- Expected/actual rows: 8 / 8
- Result comparison: match
- Join path length: 0
- Reason code: deterministic SQL generated for filtered_query/where_only: single-table where_only SQL generated deterministically

Generated SQL:
```sql
SELECT * FROM shipments WHERE shipping_cost > 1500 LIMIT 50;
```

Expected SQL:
```sql
SELECT *
FROM shipments
WHERE shipping_cost > 1500
LIMIT 50;
```

### TEST 11 - PASS
- Question: count active customers
- Expected category: supported
- Route/shape: deterministic_sql_required / single_table_count
- Executable: True
- Validation/execution: passed / passed
- Expected/actual rows: 1 / 1
- Result comparison: match
- Join path length: 0
- Reason code: deterministic SQL generated for single_table_count via simple query generator

Generated SQL:
```sql
SELECT COUNT(*) AS total_customers FROM customers WHERE customer_status = 'active' AND customer_status = 'Active';
```

Expected SQL:
```sql
SELECT COUNT(*) AS count_customers
FROM customers
WHERE customer_status = 'Active';
```

### TEST 12 - PASS
- Question: total value of all orders
- Expected category: supported
- Route/shape: deterministic_sql_required / single_table_aggregate
- Executable: True
- Validation/execution: passed / passed
- Expected/actual rows: 1 / 1
- Result comparison: match
- Join path length: 0
- Reason code: deterministic SQL generated for single_table_aggregate: single-table aggregate_only SQL generated deterministically

Generated SQL:
```sql
SELECT SUM(total_amount) AS sum_total_amount FROM orders;
```

Expected SQL:
```sql
SELECT SUM(total_amount) AS sum_total_amount
FROM orders;
```

### TEST 13 - PASS
- Question: average product unit price
- Expected category: supported
- Route/shape: deterministic_sql_required / single_table_aggregate
- Executable: True
- Validation/execution: passed / passed
- Expected/actual rows: 1 / 1
- Result comparison: match
- Join path length: 0
- Reason code: deterministic SQL generated for single_table_aggregate: single-table aggregate_only SQL generated deterministically

Generated SQL:
```sql
SELECT AVG(unit_price) AS avg_unit_price FROM products;
```

Expected SQL:
```sql
SELECT AVG(unit_price) AS avg_unit_price
FROM products;
```

### TEST 14 - PASS
- Question: maximum payment amount
- Expected category: supported
- Route/shape: deterministic_sql_required / single_table_aggregate
- Executable: True
- Validation/execution: passed / passed
- Expected/actual rows: 1 / 1
- Result comparison: match
- Join path length: 0
- Reason code: deterministic SQL generated for single_table_aggregate: single-table aggregate_only SQL generated deterministically

Generated SQL:
```sql
SELECT MAX(payment_amount) AS max_payment_amount FROM payments;
```

Expected SQL:
```sql
SELECT MAX(payment_amount) AS max_payment_amount
FROM payments;
```

### TEST 15 - PASS
- Question: count orders by order status
- Expected category: supported
- Route/shape: deterministic_sql_required / grouped_aggregate
- Executable: True
- Validation/execution: passed / passed
- Expected/actual rows: 4 / 4
- Result comparison: match
- Join path length: 0
- Reason code: deterministic SQL generated for grouped_aggregate/group_by: single-table group_by SQL generated deterministically

Generated SQL:
```sql
SELECT order_status, COUNT(*) AS count_rows FROM orders GROUP BY order_status;
```

Expected SQL:
```sql
SELECT order_status, COUNT(*) AS count_orders
FROM orders
GROUP BY order_status;
```

### TEST 16 - PASS
- Question: total order amount by order status
- Expected category: supported
- Route/shape: deterministic_sql_required / grouped_aggregate
- Executable: True
- Validation/execution: passed / passed
- Expected/actual rows: 4 / 4
- Result comparison: match
- Join path length: 0
- Reason code: deterministic SQL generated for grouped_aggregate/group_by: single-table group_by SQL generated deterministically

Generated SQL:
```sql
SELECT order_status, SUM(total_amount) AS sum_total_amount FROM orders GROUP BY order_status;
```

Expected SQL:
```sql
SELECT order_status, SUM(total_amount) AS sum_total_amount
FROM orders
GROUP BY order_status;
```

### TEST 17 - PASS
- Question: average product unit price by product status
- Expected category: supported
- Route/shape: deterministic_sql_required / grouped_aggregate
- Executable: True
- Validation/execution: passed / passed
- Expected/actual rows: 2 / 2
- Result comparison: match
- Join path length: 0
- Reason code: deterministic SQL generated for grouped_aggregate/group_by: single-table group_by SQL generated deterministically

Generated SQL:
```sql
SELECT product_status, AVG(unit_price) AS avg_unit_price FROM products GROUP BY product_status;
```

Expected SQL:
```sql
SELECT product_status, AVG(unit_price) AS avg_unit_price
FROM products
GROUP BY product_status;
```

### TEST 18 - FAIL
- Question: payment methods with total payment amount above 100000
- Expected category: supported
- Route/shape: deterministic_sql_required / grouped_aggregate
- Executable: False
- Validation/execution: rejected / not_executed
- Expected/actual rows: 0 / 0
- Result comparison: no_actual_sql
- Join path length: 0
- Reason code: metric evidence is ambiguous
- Failed stage: generator
- Error summary: Cannot choose metric safely. Choices: quantity, unit_price, line_amount, stock_quantity. Please specify one.

Expected SQL:
```sql
SELECT payment_method, SUM(payment_amount) AS sum_payment_amount
FROM payments
GROUP BY payment_method
HAVING SUM(payment_amount) > 100000;
```

### TEST 19 - FAIL
- Question: customer regions with more than 2 active customers
- Expected category: supported
- Route/shape: deterministic_sql_required / grouped_aggregate
- Executable: False
- Validation/execution: rejected / not_executed
- Expected/actual rows: 0 / 0
- Result comparison: no_actual_sql
- Join path length: 0
- Reason code: metric evidence is ambiguous
- Failed stage: generator
- Error summary: Cannot choose metric safely. Choices: stock_quantity, unit_price, quantity, line_amount. Please specify one.

Expected SQL:
```sql
SELECT region, COUNT(*) AS count_customers
FROM customers
WHERE customer_status = 'Active'
GROUP BY region
HAVING COUNT(*) > 2;
```

### TEST 20 - PASS
- Question: total stock quantity by product status
- Expected category: supported
- Route/shape: deterministic_sql_required / grouped_aggregate
- Executable: True
- Validation/execution: passed / passed
- Expected/actual rows: 2 / 2
- Result comparison: match
- Join path length: 0
- Reason code: deterministic SQL generated for grouped_aggregate/group_by: single-table group_by SQL generated deterministically

Generated SQL:
```sql
SELECT product_status, SUM(stock_quantity) AS sum_stock_quantity FROM products GROUP BY product_status;
```

Expected SQL:
```sql
SELECT product_status, SUM(stock_quantity) AS sum_stock_quantity
FROM products
GROUP BY product_status;
```

### TEST 21 - PASS
- Question: top 5 orders by total amount
- Expected category: supported
- Route/shape: deterministic_sql_required / ranking_query
- Executable: True
- Validation/execution: passed / passed
- Expected/actual rows: 5 / 5
- Result comparison: match
- Join path length: 0
- Reason code: deterministic SQL generated for ranking_query/list_only: single-table list_only SQL generated deterministically

Generated SQL:
```sql
SELECT order_id, customer_id, order_date, order_status, total_amount FROM orders ORDER BY total_amount DESC LIMIT 5;
```

Expected SQL:
```sql
SELECT *
FROM orders
ORDER BY total_amount DESC
LIMIT 5;
```

### TEST 22 - PASS
- Question: bottom 3 products by stock quantity
- Expected category: supported
- Route/shape: deterministic_sql_required / ranking_query
- Executable: True
- Validation/execution: passed / passed
- Expected/actual rows: 3 / 3
- Result comparison: match
- Join path length: 0
- Reason code: deterministic SQL generated for ranking_query/list_only: single-table list_only SQL generated deterministically

Generated SQL:
```sql
SELECT product_id, product_name, category_id, supplier_id, unit_price, stock_quantity, product_status FROM products ORDER BY stock_quantity ASC LIMIT 3;
```

Expected SQL:
```sql
SELECT *
FROM products
ORDER BY stock_quantity ASC
LIMIT 3;
```

### TEST 23 - PASS
- Question: top 3 payment methods by total payment amount
- Expected category: supported
- Route/shape: deterministic_sql_required / ranking_query
- Executable: True
- Validation/execution: passed / passed
- Expected/actual rows: 3 / 3
- Result comparison: match
- Join path length: 0
- Reason code: deterministic SQL generated for ranking_query/group_by: single-table group_by SQL generated deterministically

Generated SQL:
```sql
SELECT payment_method, SUM(payment_amount) AS sum_payment_amount FROM payments GROUP BY payment_method ORDER BY SUM(payment_amount) DESC LIMIT 3;
```

Expected SQL:
```sql
SELECT payment_method, SUM(payment_amount) AS sum_payment_amount
FROM payments
GROUP BY payment_method
ORDER BY SUM(payment_amount) DESC
LIMIT 3;
```

### TEST 24 - PASS
- Question: top 4 customer regions by customer count
- Expected category: supported
- Route/shape: deterministic_sql_required / ranking_query
- Executable: True
- Validation/execution: passed / passed
- Expected/actual rows: 3 / 3
- Result comparison: match
- Join path length: 0
- Reason code: deterministic SQL generated for ranking_query/group_by: single-table group_by SQL generated deterministically

Generated SQL:
```sql
SELECT region, COUNT(*) AS count_rows FROM customers GROUP BY region ORDER BY COUNT(*) DESC LIMIT 4;
```

Expected SQL:
```sql
SELECT region, COUNT(*) AS count_customers
FROM customers
GROUP BY region
ORDER BY COUNT(*) DESC
LIMIT 4;
```

### TEST 25 - PASS
- Question: show products ordered by unit price descending
- Expected category: supported
- Route/shape: deterministic_sql_required / ranking_query
- Executable: True
- Validation/execution: passed / passed
- Expected/actual rows: 15 / 15
- Result comparison: match
- Join path length: 0
- Reason code: deterministic SQL generated for ranking_query/list_only: single-table list_only SQL generated deterministically

Generated SQL:
```sql
SELECT product_id, product_name, category_id, supplier_id, unit_price, stock_quantity, product_status FROM products ORDER BY unit_price DESC;
```

Expected SQL:
```sql
SELECT *
FROM products
ORDER BY unit_price DESC
LIMIT 50;
```

### TEST 26 - PASS
- Question: show orders with customer details
- Expected category: supported
- Route/shape: deterministic_sql_required / joined_lookup
- Executable: True
- Validation/execution: passed / passed
- Expected/actual rows: 15 / 15
- Result comparison: match
- Join path length: 1
- Reason code: deterministic SQL generated for joined_lookup/joined_lookup: joined lookup SQL generated from planner-selected Relationship Graph evidence

Generated SQL:
```sql
SELECT orders.order_id AS orders__order_id, orders.customer_id AS orders__customer_id, orders.order_date AS orders__order_date, orders.order_status AS orders__order_status, orders.total_amount AS orders__total_amount, customers.customer_id AS customers__customer_id, customers.customer_name AS customers__customer_name, customers.city AS customers__city, customers.region AS customers__region, customers.segment AS customers__segment, customers.customer_status AS customers__customer_status, customers.created_date AS customers__created_date FROM orders INNER JOIN customers ON orders.customer_id = customers.customer_id LIMIT 50;
```

Expected SQL:
```sql
SELECT orders.*, customers.*
FROM orders
INNER JOIN customers ON orders.customer_id = customers.customer_id
LIMIT 50;
```

### TEST 27 - PASS
- Question: show payments with order details
- Expected category: supported
- Route/shape: deterministic_sql_required / joined_lookup
- Executable: True
- Validation/execution: passed / passed
- Expected/actual rows: 15 / 15
- Result comparison: match
- Join path length: 1
- Reason code: deterministic SQL generated for joined_lookup/joined_lookup: joined lookup SQL generated from planner-selected Relationship Graph evidence

Generated SQL:
```sql
SELECT payments.payment_id AS payments__payment_id, payments.order_id AS payments__order_id, payments.payment_date AS payments__payment_date, payments.payment_method AS payments__payment_method, payments.payment_status AS payments__payment_status, payments.payment_amount AS payments__payment_amount, orders.order_id AS orders__order_id, orders.customer_id AS orders__customer_id, orders.order_date AS orders__order_date, orders.order_status AS orders__order_status, orders.total_amount AS orders__total_amount FROM payments INNER JOIN orders ON payments.order_id = orders.order_id LIMIT 50;
```

Expected SQL:
```sql
SELECT payments.*, orders.*
FROM payments
INNER JOIN orders ON payments.order_id = orders.order_id
LIMIT 50;
```

### TEST 28 - PASS
- Question: show products with supplier details
- Expected category: supported
- Route/shape: deterministic_sql_required / joined_lookup
- Executable: True
- Validation/execution: passed / passed
- Expected/actual rows: 15 / 15
- Result comparison: match
- Join path length: 1
- Reason code: deterministic SQL generated for joined_lookup/joined_lookup: joined lookup SQL generated from planner-selected Relationship Graph evidence

Generated SQL:
```sql
SELECT products.product_id AS products__product_id, products.product_name AS products__product_name, products.category_id AS products__category_id, products.supplier_id AS products__supplier_id, products.unit_price AS products__unit_price, products.stock_quantity AS products__stock_quantity, products.product_status AS products__product_status, suppliers.supplier_id AS suppliers__supplier_id, suppliers.supplier_name AS suppliers__supplier_name, suppliers.city AS suppliers__city, suppliers.region AS suppliers__region, suppliers.supplier_status AS suppliers__supplier_status, suppliers.rating AS suppliers__rating FROM products INNER JOIN suppliers ON products.supplier_id = suppliers.supplier_id LIMIT 50;
```

Expected SQL:
```sql
SELECT products.*, suppliers.*
FROM products
INNER JOIN suppliers ON products.supplier_id = suppliers.supplier_id
LIMIT 50;
```

### TEST 29 - PASS
- Question: show order items with product details
- Expected category: supported
- Route/shape: deterministic_sql_required / joined_lookup
- Executable: True
- Validation/execution: passed / passed
- Expected/actual rows: 43 / 43
- Result comparison: match
- Join path length: 1
- Reason code: deterministic SQL generated for joined_lookup/joined_lookup: joined lookup SQL generated from planner-selected Relationship Graph evidence

Generated SQL:
```sql
SELECT order_items.order_item_id AS order_items__order_item_id, order_items.order_id AS order_items__order_id, order_items.product_id AS order_items__product_id, order_items.quantity AS order_items__quantity, order_items.unit_price AS order_items__unit_price, order_items.line_amount AS order_items__line_amount, products.product_id AS products__product_id, products.product_name AS products__product_name, products.category_id AS products__category_id, products.supplier_id AS products__supplier_id, products.unit_price AS products__unit_price, products.stock_quantity AS products__stock_quantity, products.product_status AS products__product_status FROM order_items INNER JOIN products ON order_items.product_id = products.product_id LIMIT 50;
```

Expected SQL:
```sql
SELECT order_items.*, products.*
FROM order_items
INNER JOIN products ON order_items.product_id = products.product_id
LIMIT 50;
```

### TEST 30 - PASS
- Question: show shipments with order details
- Expected category: supported
- Route/shape: deterministic_sql_required / joined_lookup
- Executable: True
- Validation/execution: passed / passed
- Expected/actual rows: 15 / 15
- Result comparison: match
- Join path length: 1
- Reason code: deterministic SQL generated for joined_lookup/joined_lookup: joined lookup SQL generated from planner-selected Relationship Graph evidence

Generated SQL:
```sql
SELECT shipments.shipment_id AS shipments__shipment_id, shipments.order_id AS shipments__order_id, shipments.shipped_date AS shipments__shipped_date, shipments.delivery_city AS shipments__delivery_city, shipments.shipment_status AS shipments__shipment_status, shipments.shipping_cost AS shipments__shipping_cost, orders.order_id AS orders__order_id, orders.customer_id AS orders__customer_id, orders.order_date AS orders__order_date, orders.order_status AS orders__order_status, orders.total_amount AS orders__total_amount FROM shipments INNER JOIN orders ON shipments.order_id = orders.order_id LIMIT 50;
```

Expected SQL:
```sql
SELECT shipments.*, orders.*
FROM shipments
INNER JOIN orders ON shipments.order_id = orders.order_id
LIMIT 50;
```

### TEST 31 - PASS
- Question: total order amount by customer city
- Expected category: supported
- Route/shape: deterministic_sql_required / joined_aggregate
- Executable: True
- Validation/execution: passed / passed
- Expected/actual rows: 11 / 11
- Result comparison: match
- Join path length: 1
- Reason code: deterministic SQL generated for joined_aggregate/group_by: joined aggregate SQL generated from planner-selected Relationship Graph evidence

Generated SQL:
```sql
SELECT customers.city AS customers__city, SUM(orders.total_amount) AS sum__orders__total_amount FROM orders INNER JOIN customers ON orders.customer_id = customers.customer_id GROUP BY customers.city;
```

Expected SQL:
```sql
SELECT customers.city, SUM(orders.total_amount) AS sum_total_amount
FROM orders
INNER JOIN customers ON orders.customer_id = customers.customer_id
GROUP BY customers.city;
```

### TEST 32 - PASS
- Question: count orders by customer segment
- Expected category: supported
- Route/shape: deterministic_sql_required / joined_aggregate
- Executable: True
- Validation/execution: passed / passed
- Expected/actual rows: 7 / 7
- Result comparison: match
- Join path length: 1
- Reason code: deterministic SQL generated for joined_aggregate/group_by: joined aggregate SQL generated from planner-selected Relationship Graph evidence

Generated SQL:
```sql
SELECT customers.segment AS customers__segment, COUNT(*) AS count__orders__rows FROM orders INNER JOIN customers ON orders.customer_id = customers.customer_id GROUP BY customers.segment;
```

Expected SQL:
```sql
SELECT customers.segment, COUNT(*) AS count_orders
FROM orders
INNER JOIN customers ON orders.customer_id = customers.customer_id
GROUP BY customers.segment;
```

### TEST 33 - PASS
- Question: total line amount by product name
- Expected category: supported
- Route/shape: deterministic_sql_required / joined_aggregate
- Executable: True
- Validation/execution: passed / passed
- Expected/actual rows: 14 / 14
- Result comparison: match
- Join path length: 1
- Reason code: deterministic SQL generated for joined_aggregate/group_by: joined aggregate SQL generated from planner-selected Relationship Graph evidence

Generated SQL:
```sql
SELECT products.product_name AS products__product_name, SUM(order_items.line_amount) AS sum__order_items__line_amount FROM order_items INNER JOIN products ON order_items.product_id = products.product_id GROUP BY products.product_name;
```

Expected SQL:
```sql
SELECT products.product_name, SUM(order_items.line_amount) AS sum_line_amount
FROM order_items
INNER JOIN products ON order_items.product_id = products.product_id
GROUP BY products.product_name;
```

### TEST 34 - PASS
- Question: average payment amount by order status
- Expected category: supported
- Route/shape: deterministic_sql_required / joined_aggregate
- Executable: True
- Validation/execution: passed / passed
- Expected/actual rows: 4 / 4
- Result comparison: match
- Join path length: 1
- Reason code: deterministic SQL generated for joined_aggregate/group_by: joined aggregate SQL generated from planner-selected Relationship Graph evidence

Generated SQL:
```sql
SELECT orders.order_status AS orders__order_status, AVG(payments.payment_amount) AS avg__payments__payment_amount FROM payments INNER JOIN orders ON payments.order_id = orders.order_id GROUP BY orders.order_status;
```

Expected SQL:
```sql
SELECT orders.order_status, AVG(payments.payment_amount) AS avg_payment_amount
FROM payments
INNER JOIN orders ON payments.order_id = orders.order_id
GROUP BY orders.order_status;
```

### TEST 35 - PASS
- Question: total stock quantity by supplier city
- Expected category: supported
- Route/shape: deterministic_sql_required / joined_aggregate
- Executable: True
- Validation/execution: passed / passed
- Expected/actual rows: 8 / 8
- Result comparison: match
- Join path length: 1
- Reason code: deterministic SQL generated for joined_aggregate/group_by: joined aggregate SQL generated from planner-selected Relationship Graph evidence

Generated SQL:
```sql
SELECT suppliers.city AS suppliers__city, SUM(products.stock_quantity) AS sum__products__stock_quantity FROM products INNER JOIN suppliers ON products.supplier_id = suppliers.supplier_id GROUP BY suppliers.city;
```

Expected SQL:
```sql
SELECT suppliers.city, SUM(products.stock_quantity) AS sum_stock_quantity
FROM products
INNER JOIN suppliers ON products.supplier_id = suppliers.supplier_id
GROUP BY suppliers.city;
```

### TEST 36 - PASS
- Question: show payments with customer details
- Expected category: supported
- Route/shape: deterministic_sql_required / joined_lookup
- Executable: True
- Validation/execution: passed / passed
- Expected/actual rows: 15 / 15
- Result comparison: match
- Join path length: 2
- Reason code: deterministic SQL generated for joined_lookup/joined_lookup: joined lookup SQL generated from planner-selected Relationship Graph evidence

Generated SQL:
```sql
SELECT payments.payment_id AS payments__payment_id, payments.order_id AS payments__order_id, payments.payment_date AS payments__payment_date, payments.payment_method AS payments__payment_method, payments.payment_status AS payments__payment_status, payments.payment_amount AS payments__payment_amount, orders.order_id AS orders__order_id, orders.customer_id AS orders__customer_id, orders.order_date AS orders__order_date, orders.order_status AS orders__order_status, orders.total_amount AS orders__total_amount, customers.customer_id AS customers__customer_id, customers.customer_name AS customers__customer_name, customers.city AS customers__city, customers.region AS customers__region, customers.segment AS customers__segment, customers.customer_status AS customers__customer_status, customers.created_date AS customers__created_date FROM payments INNER JOIN orders ON payments.order_id = orders.order_id INNER JOIN customers ON orders.customer_id = customers.customer_id LIMIT 50;
```

Expected SQL:
```sql
SELECT payments.*, orders.*, customers.*
FROM payments
INNER JOIN orders ON payments.order_id = orders.order_id
INNER JOIN customers ON orders.customer_id = customers.customer_id
LIMIT 50;
```

### TEST 37 - PASS
- Question: show order items with customer details
- Expected category: supported
- Route/shape: deterministic_sql_required / joined_lookup
- Executable: True
- Validation/execution: passed / passed
- Expected/actual rows: 43 / 43
- Result comparison: match
- Join path length: 2
- Reason code: deterministic SQL generated for joined_lookup/joined_lookup: joined lookup SQL generated from planner-selected Relationship Graph evidence

Generated SQL:
```sql
SELECT order_items.order_item_id AS order_items__order_item_id, order_items.order_id AS order_items__order_id, order_items.product_id AS order_items__product_id, order_items.quantity AS order_items__quantity, order_items.unit_price AS order_items__unit_price, order_items.line_amount AS order_items__line_amount, orders.order_id AS orders__order_id, orders.customer_id AS orders__customer_id, orders.order_date AS orders__order_date, orders.order_status AS orders__order_status, orders.total_amount AS orders__total_amount, customers.customer_id AS customers__customer_id, customers.customer_name AS customers__customer_name, customers.city AS customers__city, customers.region AS customers__region, customers.segment AS customers__segment, customers.customer_status AS customers__customer_status, customers.created_date AS customers__created_date FROM order_items INNER JOIN orders ON order_items.order_id = orders.order_id INNER JOIN customers ON orders.customer_id = customers.customer_id LIMIT 50;
```

Expected SQL:
```sql
SELECT order_items.*, orders.*, customers.*
FROM order_items
INNER JOIN orders ON order_items.order_id = orders.order_id
INNER JOIN customers ON orders.customer_id = customers.customer_id
LIMIT 50;
```

### TEST 38 - PASS
- Question: show order items with category details
- Expected category: supported
- Route/shape: deterministic_sql_required / joined_lookup
- Executable: True
- Validation/execution: passed / passed
- Expected/actual rows: 43 / 43
- Result comparison: match
- Join path length: 2
- Reason code: deterministic SQL generated for joined_lookup/joined_lookup: joined lookup SQL generated from planner-selected Relationship Graph evidence

Generated SQL:
```sql
SELECT order_items.order_item_id AS order_items__order_item_id, order_items.order_id AS order_items__order_id, order_items.product_id AS order_items__product_id, order_items.quantity AS order_items__quantity, order_items.unit_price AS order_items__unit_price, order_items.line_amount AS order_items__line_amount, products.product_id AS products__product_id, products.product_name AS products__product_name, products.category_id AS products__category_id, products.supplier_id AS products__supplier_id, products.unit_price AS products__unit_price, products.stock_quantity AS products__stock_quantity, products.product_status AS products__product_status, categories.category_id AS categories__category_id, categories.category_name AS categories__category_name, categories.category_status AS categories__category_status FROM order_items INNER JOIN products ON order_items.product_id = products.product_id INNER JOIN categories ON products.category_id = categories.category_id LIMIT 50;
```

Expected SQL:
```sql
SELECT order_items.*, products.*, categories.*
FROM order_items
INNER JOIN products ON order_items.product_id = products.product_id
INNER JOIN categories ON products.category_id = categories.category_id
LIMIT 50;
```

### TEST 39 - PASS
- Question: show shipments with customer details
- Expected category: supported
- Route/shape: deterministic_sql_required / joined_lookup
- Executable: True
- Validation/execution: passed / passed
- Expected/actual rows: 15 / 15
- Result comparison: match
- Join path length: 2
- Reason code: deterministic SQL generated for joined_lookup/joined_lookup: joined lookup SQL generated from planner-selected Relationship Graph evidence

Generated SQL:
```sql
SELECT shipments.shipment_id AS shipments__shipment_id, shipments.order_id AS shipments__order_id, shipments.shipped_date AS shipments__shipped_date, shipments.delivery_city AS shipments__delivery_city, shipments.shipment_status AS shipments__shipment_status, shipments.shipping_cost AS shipments__shipping_cost, orders.order_id AS orders__order_id, orders.customer_id AS orders__customer_id, orders.order_date AS orders__order_date, orders.order_status AS orders__order_status, orders.total_amount AS orders__total_amount, customers.customer_id AS customers__customer_id, customers.customer_name AS customers__customer_name, customers.city AS customers__city, customers.region AS customers__region, customers.segment AS customers__segment, customers.customer_status AS customers__customer_status, customers.created_date AS customers__created_date FROM shipments INNER JOIN orders ON shipments.order_id = orders.order_id INNER JOIN customers ON orders.customer_id = customers.customer_id LIMIT 50;
```

Expected SQL:
```sql
SELECT shipments.*, orders.*, customers.*
FROM shipments
INNER JOIN orders ON shipments.order_id = orders.order_id
INNER JOIN customers ON orders.customer_id = customers.customer_id
LIMIT 50;
```

### TEST 40 - PASS
- Question: total payment amount by customer city
- Expected category: supported
- Route/shape: deterministic_sql_required / joined_aggregate
- Executable: True
- Validation/execution: passed / passed
- Expected/actual rows: 11 / 11
- Result comparison: match
- Join path length: 2
- Reason code: deterministic SQL generated for joined_aggregate/group_by: joined aggregate SQL generated from planner-selected Relationship Graph evidence

Generated SQL:
```sql
SELECT customers.city AS customers__city, SUM(payments.payment_amount) AS sum__payments__payment_amount FROM payments INNER JOIN orders ON payments.order_id = orders.order_id INNER JOIN customers ON orders.customer_id = customers.customer_id GROUP BY customers.city;
```

Expected SQL:
```sql
SELECT customers.city, SUM(payments.payment_amount) AS sum_payment_amount
FROM payments
INNER JOIN orders ON payments.order_id = orders.order_id
INNER JOIN customers ON orders.customer_id = customers.customer_id
GROUP BY customers.city;
```

### TEST 41 - PASS
- Question: total line amount by product category
- Expected category: supported
- Route/shape: deterministic_sql_required / joined_aggregate
- Executable: True
- Validation/execution: passed / passed
- Expected/actual rows: 7 / 7
- Result comparison: match
- Join path length: 2
- Reason code: deterministic SQL generated for joined_aggregate/group_by: joined aggregate SQL generated from planner-selected Relationship Graph evidence

Generated SQL:
```sql
SELECT categories.category_name AS categories__category_name, SUM(order_items.line_amount) AS sum__order_items__line_amount FROM order_items INNER JOIN products ON order_items.product_id = products.product_id INNER JOIN categories ON products.category_id = categories.category_id GROUP BY categories.category_name;
```

Expected SQL:
```sql
SELECT categories.category_name, SUM(order_items.line_amount) AS sum_line_amount
FROM order_items
INNER JOIN products ON order_items.product_id = products.product_id
INNER JOIN categories ON products.category_id = categories.category_id
GROUP BY categories.category_name;
```

### TEST 42 - PASS
- Question: count order items by supplier city
- Expected category: supported
- Route/shape: deterministic_sql_required / joined_aggregate
- Executable: True
- Validation/execution: passed / passed
- Expected/actual rows: 8 / 8
- Result comparison: match
- Join path length: 2
- Reason code: deterministic SQL generated for joined_aggregate/group_by: joined aggregate SQL generated from planner-selected Relationship Graph evidence

Generated SQL:
```sql
SELECT suppliers.city AS suppliers__city, COUNT(*) AS count__order_items__rows FROM order_items INNER JOIN products ON order_items.product_id = products.product_id INNER JOIN suppliers ON products.supplier_id = suppliers.supplier_id GROUP BY suppliers.city;
```

Expected SQL:
```sql
SELECT suppliers.city, COUNT(*) AS count_order_items
FROM order_items
INNER JOIN products ON order_items.product_id = products.product_id
INNER JOIN suppliers ON products.supplier_id = suppliers.supplier_id
GROUP BY suppliers.city;
```

### TEST 43 - PASS
- Question: top 3 customer cities by total payment amount
- Expected category: supported
- Route/shape: deterministic_sql_required / joined_aggregate
- Executable: True
- Validation/execution: passed / passed
- Expected/actual rows: 3 / 3
- Result comparison: match
- Join path length: 2
- Reason code: deterministic SQL generated for joined_aggregate/group_by: joined aggregate SQL generated from planner-selected Relationship Graph evidence

Generated SQL:
```sql
SELECT customers.city AS customers__city, SUM(payments.payment_amount) AS sum__payments__payment_amount FROM payments INNER JOIN orders ON payments.order_id = orders.order_id INNER JOIN customers ON orders.customer_id = customers.customer_id GROUP BY customers.city ORDER BY sum__payments__payment_amount DESC LIMIT 3;
```

Expected SQL:
```sql
SELECT customers.city, SUM(payments.payment_amount) AS sum_payment_amount
FROM payments
INNER JOIN orders ON payments.order_id = orders.order_id
INNER JOIN customers ON orders.customer_id = customers.customer_id
GROUP BY customers.city
ORDER BY SUM(payments.payment_amount) DESC
LIMIT 3;
```

### TEST 44 - FAIL
- Question: total line amount by supplier name for active products
- Expected category: supported
- Route/shape: cannot_plan_safely / joined_aggregate
- Executable: False
- Validation/execution: rejected / not_executed
- Expected/actual rows: 0 / 0
- Result comparison: no_actual_sql
- Join path length: 0
- Reason code: joined aggregate filters must belong to the selected join path
- Failed stage: generator
- Error summary: Question could not be planned safely from the current schema context: joined aggregate filters must belong to the selected join path; missing evidence: where; ambiguous evidence: where.

Expected SQL:
```sql
SELECT suppliers.supplier_name, SUM(order_items.line_amount) AS sum_line_amount
FROM order_items
INNER JOIN products ON order_items.product_id = products.product_id
INNER JOIN suppliers ON products.supplier_id = suppliers.supplier_id
WHERE products.product_status = 'Active'
GROUP BY suppliers.supplier_name;
```

### TEST 45 - PASS
- Question: count payments by customer region where payment status is completed
- Expected category: supported
- Route/shape: deterministic_sql_required / joined_aggregate
- Executable: True
- Validation/execution: passed / passed
- Expected/actual rows: 3 / 3
- Result comparison: match
- Join path length: 2
- Reason code: deterministic SQL generated for joined_aggregate/where_group_by: joined aggregate SQL generated from planner-selected Relationship Graph evidence

Generated SQL:
```sql
SELECT customers.region AS customers__region, COUNT(*) AS count__payments__rows FROM payments INNER JOIN orders ON payments.order_id = orders.order_id INNER JOIN customers ON orders.customer_id = customers.customer_id WHERE payments.payment_status = 'Completed' GROUP BY customers.region;
```

Expected SQL:
```sql
SELECT customers.region, COUNT(*) AS count_payments
FROM payments
INNER JOIN orders ON payments.order_id = orders.order_id
INNER JOIN customers ON orders.customer_id = customers.customer_id
WHERE payments.payment_status = 'Completed'
GROUP BY customers.region;
```

### TEST 46 - PASS
- Question: average line amount by product category
- Expected category: supported
- Route/shape: deterministic_sql_required / joined_aggregate
- Executable: True
- Validation/execution: passed / passed
- Expected/actual rows: 7 / 7
- Result comparison: match
- Join path length: 2
- Reason code: deterministic SQL generated for joined_aggregate/group_by: joined aggregate SQL generated from planner-selected Relationship Graph evidence

Generated SQL:
```sql
SELECT categories.category_name AS categories__category_name, AVG(order_items.line_amount) AS avg__order_items__line_amount FROM order_items INNER JOIN products ON order_items.product_id = products.product_id INNER JOIN categories ON products.category_id = categories.category_id GROUP BY categories.category_name;
```

Expected SQL:
```sql
SELECT categories.category_name, AVG(order_items.line_amount) AS avg_line_amount
FROM order_items
INNER JOIN products ON order_items.product_id = products.product_id
INNER JOIN categories ON products.category_id = categories.category_id
GROUP BY categories.category_name;
```

### TEST 47 - PASS
- Question: total shipping cost by customer city
- Expected category: supported
- Route/shape: deterministic_sql_required / joined_aggregate
- Executable: True
- Validation/execution: passed / passed
- Expected/actual rows: 11 / 11
- Result comparison: match
- Join path length: 2
- Reason code: deterministic SQL generated for joined_aggregate/group_by: joined aggregate SQL generated from planner-selected Relationship Graph evidence

Generated SQL:
```sql
SELECT customers.city AS customers__city, SUM(shipments.shipping_cost) AS sum__shipments__shipping_cost FROM shipments INNER JOIN orders ON shipments.order_id = orders.order_id INNER JOIN customers ON orders.customer_id = customers.customer_id GROUP BY customers.city;
```

Expected SQL:
```sql
SELECT customers.city, SUM(shipments.shipping_cost) AS sum_shipping_cost
FROM shipments
INNER JOIN orders ON shipments.order_id = orders.order_id
INNER JOIN customers ON orders.customer_id = customers.customer_id
GROUP BY customers.city;
```

### TEST 48 - PASS
- Question: top 3 product categories by total line amount
- Expected category: supported
- Route/shape: deterministic_sql_required / joined_aggregate
- Executable: True
- Validation/execution: passed / passed
- Expected/actual rows: 3 / 3
- Result comparison: match
- Join path length: 2
- Reason code: deterministic SQL generated for joined_aggregate/group_by: joined aggregate SQL generated from planner-selected Relationship Graph evidence

Generated SQL:
```sql
SELECT categories.category_name AS categories__category_name, SUM(order_items.line_amount) AS sum__order_items__line_amount FROM order_items INNER JOIN products ON order_items.product_id = products.product_id INNER JOIN categories ON products.category_id = categories.category_id GROUP BY categories.category_name ORDER BY sum__order_items__line_amount DESC LIMIT 3;
```

Expected SQL:
```sql
SELECT categories.category_name, SUM(order_items.line_amount) AS sum_line_amount
FROM order_items
INNER JOIN products ON order_items.product_id = products.product_id
INNER JOIN categories ON products.category_id = categories.category_id
GROUP BY categories.category_name
ORDER BY SUM(order_items.line_amount) DESC
LIMIT 3;
```

### TEST 49 - PASS
- Question: show order items for products supplied by suppliers in Pune
- Expected category: supported
- Route/shape: deterministic_sql_required / joined_lookup
- Executable: True
- Validation/execution: passed / passed
- Expected/actual rows: 5 / 5
- Result comparison: match
- Join path length: 2
- Reason code: deterministic SQL generated for joined_lookup/joined_lookup: joined lookup SQL generated from planner-selected Relationship Graph evidence

Generated SQL:
```sql
SELECT order_items.order_item_id AS order_items__order_item_id, order_items.order_id AS order_items__order_id, order_items.product_id AS order_items__product_id, order_items.quantity AS order_items__quantity, order_items.unit_price AS order_items__unit_price, order_items.line_amount AS order_items__line_amount, products.product_id AS products__product_id, products.product_name AS products__product_name, products.category_id AS products__category_id, products.supplier_id AS products__supplier_id, products.unit_price AS products__unit_price, products.stock_quantity AS products__stock_quantity, products.product_status AS products__product_status, suppliers.supplier_id AS suppliers__supplier_id, suppliers.supplier_name AS suppliers__supplier_name, suppliers.city AS suppliers__city, suppliers.region AS suppliers__region, suppliers.supplier_status AS suppliers__supplier_status, suppliers.rating AS suppliers__rating FROM order_items INNER JOIN products ON order_items.product_id = products.product_id INNER JOIN suppliers ON products.supplier_id = suppliers.supplier_id WHERE suppliers.city = 'Pune' LIMIT 50;
```

Expected SQL:
```sql
SELECT order_items.*, products.*, suppliers.*
FROM order_items
INNER JOIN products ON order_items.product_id = products.product_id
INNER JOIN suppliers ON products.supplier_id = suppliers.supplier_id
WHERE suppliers.city = 'Pune'
LIMIT 50;
```

### TEST 50 - FAIL
- Question: total order amount by customer segment for delivered orders
- Expected category: supported
- Route/shape: cannot_plan_safely / joined_aggregate
- Executable: False
- Validation/execution: rejected / not_executed
- Expected/actual rows: 0 / 0
- Result comparison: no_actual_sql
- Join path length: 0
- Reason code: joined aggregate filters must belong to the selected join path
- Failed stage: generator
- Error summary: Question could not be planned safely from the current schema context: joined aggregate filters must belong to the selected join path; missing evidence: where; ambiguous evidence: where.

Expected SQL:
```sql
SELECT customers.segment, SUM(orders.total_amount) AS sum_total_amount
FROM orders
INNER JOIN customers ON orders.customer_id = customers.customer_id
WHERE orders.order_status = 'Delivered'
GROUP BY customers.segment;
```

### TEST 51 - PASS
- Question: change inactive suppliers to active
- Expected category: fail_closed
- Route/shape: blocked_unsafe / blocked_unsafe
- Executable: False
- Validation/execution: rejected / not_executed
- Expected/actual rows: 0 / 0
- Result comparison: blocked
- Join path length: 0
- Reason code: planner blocked this request before SQL generation

### TEST 52 - PASS
- Question: delete cancelled orders
- Expected category: fail_closed
- Route/shape: blocked_unsafe / blocked_unsafe
- Executable: False
- Validation/execution: rejected / not_executed
- Expected/actual rows: 0 / 0
- Result comparison: blocked
- Join path length: 0
- Reason code: unsafe natural-language request blocked before planning

### TEST 53 - PASS
- Question: insert a new customer named Rahul
- Expected category: fail_closed
- Route/shape: blocked_unsafe / blocked_unsafe
- Executable: False
- Validation/execution: rejected / not_executed
- Expected/actual rows: 0 / 0
- Result comparison: blocked
- Join path length: 0
- Reason code: unsafe natural-language request blocked before planning

### TEST 54 - PASS
- Question: count distinct orders by customer city
- Expected category: fail_closed
- Route/shape: cannot_plan_safely / grouped_aggregate
- Executable: False
- Validation/execution: rejected / not_executed
- Expected/actual rows: 0 / 0
- Result comparison: blocked
- Join path length: 0
- Reason code: intent contains an unsupported deterministic construct

### TEST 55 - PASS
- Question: show customers with products they purchased
- Expected category: fail_closed
- Route/shape: cannot_plan_safely / filtered_query
- Executable: False
- Validation/execution: rejected / not_executed
- Expected/actual rows: 0 / 0
- Result comparison: blocked
- Join path length: 0
- Reason code: filter evidence is ambiguous

### TEST 56 - PASS
- Question: average supplier city
- Expected category: fail_closed
- Route/shape: cannot_plan_safely / single_table_aggregate
- Executable: False
- Validation/execution: rejected / not_executed
- Expected/actual rows: 0 / 0
- Result comparison: blocked
- Join path length: 0
- Reason code: requested aggregate metric is not numeric

### TEST 57 - PASS
- Question: total payment amount by product category
- Expected category: fail_closed
- Route/shape: cannot_plan_safely / joined_aggregate
- Executable: False
- Validation/execution: rejected / not_executed
- Expected/actual rows: 0 / 0
- Result comparison: blocked
- Join path length: 0
- Reason code: multi-hop joined aggregate path is unsupported_depth: safe path exceeds maximum supported depth

### TEST 58 - PASS
- Question: show top records
- Expected category: fail_closed
- Route/shape: cannot_plan_safely / unknown
- Executable: False
- Validation/execution: rejected / not_executed
- Expected/actual rows: 0 / 0
- Result comparison: blocked
- Join path length: 0
- Reason code: table evidence is missing or ambiguous
