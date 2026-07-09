# SQLSense Phase 6G Interval Logic Lab — Questions + Expected Answers

Database: `sqlsense_phase6g_interval_lab`

Purpose: test **Phase 6G only** — deterministic date/range interval handling. This DB intentionally has:

- `orders.order_date` and `orders.shipped_date` to test date-column ambiguity.
- `payments.payment_date` as a single-date-column table for fieldless interval positives.
- `orders -> customers` as one direct graph edge for joined aggregate interval tests.
- `payments -> orders` as another edge, but multi-hop should still not be used for Phase 6 joined aggregates.

Load:

```powershell
mysql -u root -p < sqlsense_phase6g_interval_lab.sql
```

Then connect SQLSense to:

```text
sqlsense_phase6g_interval_lab
```

---

## Positive Phase 6G questions

### Q1. Single-table fieldless interval, one date column

Question:

```text
show payments after 2026-02-10
```

Expected shape:

```text
filtered_query
```

Expected SQL pattern:

```sql
FROM payments
WHERE payments.payment_date > '2026-02-10'
```

Expected answer by `payment_id`:

| payment_id | payment_status | payment_amount | payment_date |
|---:|---|---:|---|
| 205 | Refunded | 0.00 | 2026-02-16 |
| 206 | Paid | 1200.00 | 2026-02-24 |
| 207 | Paid | 4000.00 | 2026-03-12 |
| 208 | Paid | 2200.00 | 2026-04-05 |
| 209 | Paid | 3500.00 | 2026-05-20 |
| 210 | Paid | 2800.00 | 2026-06-25 |
| 211 | Paid | 1600.00 | 2026-07-08 |

---Enter your question: show payments after 2026-02-10
  Question could not be planned safely from the current schema context: filter evidence is missing; missing evidence: missing filter column.

### Q2. Single-table BETWEEN interval

Question:

```text

```

Expected shape:

```text
filtered_query
```

Expected SQL pattern:

```sql
FROM payments
WHERE payments.payment_date BETWEEN '2026-02-01' AND '2026-02-28'
```

Expected answer by `payment_id`:

| payment_id | payment_status | payment_amount | payment_date |
|---:|---|---:|---|
| 204 | Paid | 3000.00 | 2026-02-06 |
| 205 | Refunded | 0.00 | 2026-02-16 |
| 206 | Paid | 1200.00 | 2026-02-24 |

---

### Q3. Explicit date column in table with multiple date columns

Question:

```text
show orders where order date after 2026-01-15
```

Expected shape:

```text
filtered_query
```

Expected SQL pattern:

```sql
FROM orders
WHERE orders.order_date > '2026-01-15'
```

Expected answer by `order_id`:

```text
103, 104, 105, 106, 107, 108, 109, 110, 111, 112
```
Enter your question: show orders where order date after 2026-01-15
2026-07-09 15:17:07 - ERROR - Unexpected error in handle_choice: name '_tokenize' is not defined
  Unexpected error: name '_tokenize' is not defined

---

### Q4. Explicit shipped-date interval

Question:

```text
show orders where shipped date before 2026-02-01
```

  Enter your question: show orders where shipped date before 2026-02-01
2026-07-09 15:18:07 - ERROR - Unexpected error in handle_choice: name '_tokenize' is not defined
  Unexpected error: name '_tokenize' is not defined
Expected shape:

```text
filtered_query
```

Expected SQL pattern:

```sql
FROM orders
WHERE orders.shipped_date < '2026-02-01'
```

Expected answer by `order_id`:

```text
101, 102
```

---

### Q5. Month/year expansion on explicit date column

Question:

```text
show orders where order date in January 2026
```
 Enter your question: show orders where order date in January 2026
  Cannot choose filter field safely. Choices: shipped_date, order_id, order_date, customer_id, order_status. Please specify one.

Expected shape:

```text
filtered_query
```

Expected SQL pattern:

```sql
FROM orders
WHERE orders.order_date BETWEEN '2026-01-01' AND '2026-01-31'
```

Expected answer by `order_id`:

```text
101, 102, 103
```

---

### Q6. Joined aggregate with interval filter

Question:

```text
show total order amount by customer city for order date in January 2026
`` Enter your question: show total order amount by customer city for order date in January 2026
  Cannot choose dimension safely. Choices: city, signup_date, customer_status, customer_name, customer_segment, order_date. Please specify one.`

Expected shape:

```text
joined_aggregate
```

Expected SQL pattern:

```sql
SELECT customers.city AS customers__city,
       SUM(orders.order_amount) AS sum__orders__order_amount
FROM orders
INNER JOIN customers ON orders.customer_id = customers.customer_id
WHERE orders.order_date BETWEEN '2026-01-01' AND '2026-01-31'
GROUP BY customers.city
```

Expected answer:

| customers__city | sum__orders__order_amount |
|---|---:|
| Pune | 2500.00 |
| Mumbai | 2000.00 |

---

### Q7. Joined aggregate COUNT with interval filter

Question:

```text
count orders by customer segment for order date in February 2026
```
Enter your question: count orders by customer segment for order date in February 2026 
  Cannot choose dimension safely. Choices: customer_segment, signup_date, city, customer_name, customer_status, shipped_date. Please specify one.
Expected shape:

```text
joined_aggregate
```

Expected SQL pattern:

```sql
COUNT(*) AS count__orders__rows
FROM orders
INNER JOIN customers ON orders.customer_id = customers.customer_id
WHERE orders.order_date BETWEEN '2026-02-01' AND '2026-02-28'
GROUP BY customers.customer_segment
```

Expected answer:

| customers__customer_segment | count__orders__rows |
|---|---:|
| Enterprise | 1 |
| Retail | 1 |
| Wholesale | 1 |

---

### Q8. Ranking joined aggregate with interval filter

Question:

```text
top 3 customers by total order amount in 2026
```
Enter your question: top 3 customers by total order amount in 2026
  Question could not be planned safely from the current schema context: joined date interval evidence is ambiguous; missing evidence: missing filter column, where; ambiguous evidence: order by selection, where.

Expected shape:

```text
joined_aggregate
```

Expected SQL pattern:

```sql
SUM(orders.order_amount) AS sum__orders__order_amount
FROM orders
INNER JOIN customers ON orders.customer_id = customers.customer_id
WHERE orders.order_date BETWEEN '2026-01-01' AND '2026-12-31'
GROUP BY customers.customer_name
ORDER BY sum__orders__order_amount DESC
LIMIT 3
```

Expected answer:

| customers__customer_name | sum__orders__order_amount |
|---|---:|
| Eagle Supply | 6800.00 |
| Crown Mart | 6500.00 |
| Beta Stores | 4800.00 |

---

### Q9. Bottom ranking joined aggregate with BETWEEN interval

Question:

```text
bottom 2 customer cities by average order amount for order date between 2026-01-01 and 2026-03-31
```
Enter your question: bottom 2 customer cities by average order amount for order date between 2026-01-01 and 2026-03-31
2026-07-09 15:20:43 - ERROR - Unexpected error in handle_choice: name '_tokenize' is not defined
  Unexpected error: name '_tokenize' is not defined
Expected shape:

```text
joined_aggregate
```

Expected SQL pattern:

```sql
AVG(orders.order_amount) AS avg__orders__order_amount
FROM orders
INNER JOIN customers ON orders.customer_id = customers.customer_id
WHERE orders.order_date BETWEEN '2026-01-01' AND '2026-03-31'
GROUP BY customers.city
ORDER BY avg__orders__order_amount ASC
LIMIT 2
```

Expected answer:

| customers__city | avg__orders__order_amount |
|---|---:|
| Mumbai | 1600.00 |
| Pune | 1833.33 |

---

### Q10. Grouped aggregate on one-date table with month/year interval

Question:

```text
show total payment amount by payment status in February 2026
```
Enter your question: show total payment amount by payment status in February 2026
  Question could not be planned safely from the current schema context: filter evidence is missing; missing evidence: missing filter column.

Expected shape:

```text
grouped_aggregate
```

Expected SQL pattern:

```sql
FROM payments
WHERE payments.payment_date BETWEEN '2026-02-01' AND '2026-02-28'
GROUP BY payments.payment_status
```

Expected answer:

| payments__payment_status | sum__payments__payment_amount |
|---|---:|
| Paid | 4200.00 |
| Refunded | 0.00 |

---

## Optional relative-date checks

These depend on the runtime clock. Use only when SQLSense runs with a fixed clock/current date of `2026-07-09`.

### R1. Last 30 days

Question:

```text
show delivered orders from last 30 days
```
Enter your question: show delivered orders from last 30 days
  Question could not be planned safely from the current schema context: filter evidence is missing; missing evidence: missing filter column.

Expected if current date is `2026-07-09`:

```sql
WHERE orders.order_date BETWEEN '2026-06-10' AND '2026-07-09'
  AND orders.order_status = 'Delivered'
```

Expected order IDs:

```text
111, 112
```

---

## Negative/fail-closed Phase 6G questions

### N1. Ambiguous date column

Question:

```text
show orders after 2026-01-01
```

Expected:

```text
cannot_plan_safely
```

Reason: `orders` has both `order_date` and `shipped_date`. The user did not specify which date role.

---

### N2. Invalid BETWEEN value

Question:

```text
show orders between January and pending
```

Expected:

```text
cannot_plan_safely
```

Reason: `pending` is not a valid interval endpoint.

---

### N3. Unknown period

Question:

```text
show total order amount by customer city in festival season
```

Expected:

```text
cannot_plan_safely
```

Reason: unknown interval phrase. Do not guess.

---

### N4. Date literal applied to non-date role

Question:

```text
show orders where order status after 2026-01-01
```

Expected:

```text
cannot_plan_safely
```

Reason: `order_status` is not a date column.

---

### N5. Unsafe interval write

Question:

```text
delete payments before 2026-02-01
```

Expected:

```text
blocked_unsafe
```

Reason: unsafe write/delete intent must be blocked before SQL execution.
