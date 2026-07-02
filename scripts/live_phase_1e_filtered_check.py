#!/usr/bin/env python3
"""
live_phase_1e_filtered_check.py

Run from SQLSense project root:

    python scripts/live_phase_1e_filtered_check.py --setup --user root --password "YOUR_PASSWORD"

Purpose:
- Creates/refreshes sqlsense_simple_lab if --setup is used.
- Runs normal SQLSense app flow:
  connect -> KB/Chroma build -> ask question -> SQL generation -> validation -> execution
- Verifies Phase 1E filtered single-table list and aggregate queries.
- Fails if the app returns "missing filter column" for clear filters like:
  show bills where bill status is pending

This script is intentionally a live/e2e checker.
"""

from __future__ import annotations

import argparse
import getpass
import os
import re
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import create_engine, text


DB_NAME_DEFAULT = "sqlsense_simple_lab"

SETUP_SQL = """
DROP DATABASE IF EXISTS sqlsense_simple_lab;
CREATE DATABASE sqlsense_simple_lab;
USE sqlsense_simple_lab;

CREATE TABLE bills (
    bill_id INT PRIMARY KEY AUTO_INCREMENT,
    bill_no VARCHAR(30) NOT NULL,
    customer_name VARCHAR(100) NOT NULL,
    bill_status VARCHAR(30) NOT NULL,
    billed_value DECIMAL(12,2) NOT NULL,
    paid_value DECIMAL(12,2) NOT NULL,
    bill_date DATE NOT NULL
);

INSERT INTO bills (bill_no, customer_name, bill_status, billed_value, paid_value, bill_date) VALUES
('B001', 'Aarav Traders', 'paid',      1000.00, 1000.00, '2026-01-05'),
('B002', 'Bright Retail', 'partial',   2500.00, 1500.00, '2026-01-08'),
('B003', 'City Mart', 'pending',       3000.00,    0.00, '2026-01-12'),
('B004', 'Delta Stores', 'paid',       4500.00, 4500.00, '2026-01-18'),
('B005', 'Elite Bazaar', 'partial',    5200.00, 2000.00, '2026-01-22'),
('B006', 'Fresh Point', 'paid',        1800.00, 1800.00, '2026-02-03'),
('B007', 'Global Shop', 'pending',     7600.00,    0.00, '2026-02-10'),
('B008', 'Hari Om Sales', 'paid',      2200.00, 2200.00, '2026-02-15'),
('B009', 'India Wholesale', 'partial', 6400.00, 3000.00, '2026-02-20'),
('B010', 'Jai Market', 'paid',         3500.00, 3500.00, '2026-03-01');
"""


REGRESSION_CASES: List[Dict[str, Any]] = [
    {
        "name": "show all bills",
        "question": "show all bills",
        "expected_kind": "row_count",
        "expected_value": 10,
        "sql_must_contain": ["SELECT", "FROM", "bills"],
    },
    {
        "name": "count bills",
        "question": "count bills",
        "expected_kind": "scalar",
        "expected_value": Decimal("10.00"),
        "sql_must_contain": ["COUNT", "FROM", "bills"],
    },
    {
        "name": "sum billed value",
        "question": "show sum billed value from bills",
        "expected_kind": "scalar",
        "expected_value": Decimal("37700.00"),
        "sql_must_contain": ["SUM", "billed_value", "FROM", "bills"],
    },
    {
        "name": "sum paid value",
        "question": "show sum paid value from bills",
        "expected_kind": "scalar",
        "expected_value": Decimal("19500.00"),
        "sql_must_contain": ["SUM", "paid_value", "FROM", "bills"],
    },
]

FILTERED_CASES: List[Dict[str, Any]] = [
    {
        "name": "pending bills",
        "question": "show bills where bill status is pending",
        "expected_kind": "row_count",
        "expected_value": 2,
        "sql_must_contain": ["WHERE", "bill_status", "pending"],
    },
    {
        "name": "paid bills",
        "question": "show bills where bill status is paid",
        "expected_kind": "row_count",
        "expected_value": 5,
        "sql_must_contain": ["WHERE", "bill_status", "paid"],
    },
    {
        "name": "billed value greater than 5000",
        "question": "show bills where billed value greater than 5000",
        "expected_kind": "row_count",
        "expected_value": 3,
        "sql_must_contain": ["WHERE", "billed_value", ">"],
    },
    {
        "name": "paid value equals 0",
        "question": "show bills where paid value equals 0",
        "expected_kind": "row_count",
        "expected_value": 2,
        "sql_must_contain": ["WHERE", "paid_value", "="],
    },
    {
        "name": "sum billed paid status",
        "question": "show sum billed value from bills where bill status is paid",
        "expected_kind": "scalar",
        "expected_value": Decimal("13000.00"),
        "sql_must_contain": ["SUM", "billed_value", "WHERE", "bill_status", "paid"],
    },
    {
        "name": "sum paid paid status",
        "question": "show sum paid value from bills where bill status is paid",
        "expected_kind": "scalar",
        "expected_value": Decimal("13000.00"),
        "sql_must_contain": ["SUM", "paid_value", "WHERE", "bill_status", "paid"],
    },
    {
        "name": "sum billed partial status",
        "question": "show sum billed value from bills where bill status is partial",
        "expected_kind": "scalar",
        "expected_value": Decimal("14100.00"),
        "sql_must_contain": ["SUM", "billed_value", "WHERE", "bill_status", "partial"],
    },
    {
        "name": "average billed pending status",
        "question": "average billed value from bills where bill status is pending",
        "expected_kind": "scalar",
        "expected_value": Decimal("5300.00"),
        "sql_must_contain": ["AVG", "billed_value", "WHERE", "bill_status", "pending"],
    },
]

SAFE_FAILURE_CASES: List[Dict[str, Any]] = [
    {
        "name": "ambiguous filter field",
        "question": "show bills where amount is paid",
        "must_not_generate_sql": True,
        "must_mention": ["cannot", "filter"],
    },
    {
        "name": "unknown filter field",
        "question": "show bills where unknown field is pending",
        "must_not_generate_sql": True,
        "must_mention": ["cannot", "filter"],
    },
    {
        "name": "ambiguous metric with filter",
        "question": "show sum amount from bills where bill status is paid",
        "must_not_generate_sql": True,
        "must_mention": ["billed_value", "paid_value"],
    },
    {
        "name": "unsafe delete",
        "question": "delete bills",
        "must_not_generate_sql": True,
        "must_mention": ["blocked"],
    },
]


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def add_project_to_path() -> None:
    root = project_root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))


def mysql_url(user: str, password: str, host: str, port: int, database: Optional[str] = None) -> str:
    from urllib.parse import quote_plus

    auth = quote_plus(user)
    if password:
        auth += f":{quote_plus(password)}"
    db_part = f"/{database}" if database else ""
    return f"mysql+pymysql://{auth}@{host}:{port}{db_part}"


def setup_database(args: argparse.Namespace) -> None:
    print("[SETUP] Rebuilding sqlsense_simple_lab ...")
    engine = create_engine(mysql_url(args.user, args.password, args.host, args.port), future=True)
    statements = [stmt.strip() for stmt in SETUP_SQL.split(";") if stmt.strip()]
    with engine.begin() as conn:
        for stmt in statements:
            conn.execute(text(stmt))
    print("[SETUP] Done.")


def connect_app(args: argparse.Namespace):
    add_project_to_path()
    os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")
    os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")
    os.environ.setdefault("CHROMA_ANONYMIZED_TELEMETRY", "False")
    from core.app_service import AppService  # pylint: disable=import-error

    app = AppService()
    success, message, report = app.connect_database_and_prepare(
        db_type="mysql",
        host=args.host,
        port=args.port,
        username=args.user,
        password=args.password,
        database=args.database,
        use_ai_enrichment=False,
    )

    print(f"[CONNECT] success={success} message={message}")
    print(f"[CONNECT] report={report}")

    if not success or not app.is_database_ready():
        raise AssertionError("Database did not become ready.")

    return app


def normalize_sql(sql: str) -> str:
    return re.sub(r"\s+", " ", str(sql or "")).lower().strip()


def extract_scalar(rows: Optional[List[Dict[str, Any]]]) -> Decimal:
    if not rows:
        raise AssertionError("Expected scalar row, got no rows.")
    first = rows[0]
    value = next(iter(first.values()))
    return Decimal(str(value)).quantize(Decimal("0.01"))


def message_blob(result: Dict[str, Any]) -> str:
    ctx = result.get("query_context") or {}
    return " ".join(
        [
            str(result.get("message") or ""),
            str(result.get("error") or ""),
            str(result.get("route") or ""),
            str(result.get("route_used") or ""),
            str(ctx.get("ambiguities") or ""),
            str(ctx.get("ambiguity_choices") or ""),
            str(ctx.get("missing_evidence") or ""),
        ]
    ).lower()


def run_sql_case(app: Any, case: Dict[str, Any]) -> Tuple[bool, str]:
    question = case["question"]
    result = app.process_question(question)
    sql = result.get("generated_sql") or result.get("sql")

    if not result.get("success") or not sql:
        return False, f"{case['name']}: expected generated SQL, got result={result}"

    validation_result = result.get("validation_result") or {}
    if not validation_result.get("is_valid"):
        return False, (
            f"{case['name']}: generated SQL did not pass validation. "
            f"SQL={sql} reason={validation_result.get('reason')}"
        )

    norm = normalize_sql(sql)
    for fragment in case.get("sql_must_contain", []):
        if str(fragment).lower() not in norm:
            return False, f"{case['name']}: SQL missing {fragment!r}. SQL={sql}"

    ok, message, rows = app.execute_sql(sql)
    if not ok:
        return False, f"{case['name']}: SQL execution failed. SQL={sql} message={message}"

    actual: Any
    if case["expected_kind"] == "row_count":
        actual = len(rows or [])
        if actual != case["expected_value"]:
            return False, f"{case['name']}: expected {case['expected_value']} rows, got {actual}. SQL={sql}"

    elif case["expected_kind"] == "scalar":
        actual = extract_scalar(rows)
        expected = Decimal(str(case["expected_value"])).quantize(Decimal("0.01"))
        if actual != expected:
            return False, f"{case['name']}: expected {expected}, got {actual}. SQL={sql}, rows={rows}"

    return True, (
        f"{case['name']}: PASS | expected={case['expected_value']} "
        f"actual={actual} | SQL={sql}"
    )


def run_safe_failure_case(app: Any, case: Dict[str, Any]) -> Tuple[bool, str]:
    result = app.process_question(case["question"])
    sql = result.get("generated_sql") or result.get("sql")
    if sql:
        return False, f"{case['name']}: expected no SQL, got {sql}"

    blob = message_blob(result)
    for fragment in case.get("must_mention", []):
        if fragment.lower() not in blob:
            return False, f"{case['name']}: expected message/context to mention {fragment!r}. result={result}"

    return True, f"{case['name']}: PASS | blocked safely"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=3306)
    parser.add_argument("--user", default="root")
    parser.add_argument("--password", default=None)
    parser.add_argument("--database", default=DB_NAME_DEFAULT)
    parser.add_argument("--setup", action="store_true")
    args = parser.parse_args()

    if args.password is None:
        args.password = os.getenv("SQLSENSE_MYSQL_PASSWORD") or getpass.getpass("MySQL password: ")

    if args.setup:
        setup_database(args)

    app = connect_app(args)
    failures: List[str] = []

    print("\n[CHECK] Regression cases")
    for case in REGRESSION_CASES:
        ok, msg = run_sql_case(app, case)
        print(("PASS " if ok else "FAIL ") + msg)
        if not ok:
            failures.append(msg)

    print("\n[CHECK] Phase 1E filtered cases")
    for case in FILTERED_CASES:
        ok, msg = run_sql_case(app, case)
        print(("PASS " if ok else "FAIL ") + msg)
        if not ok:
            failures.append(msg)

    print("\n[CHECK] Safe failure cases")
    for case in SAFE_FAILURE_CASES:
        ok, msg = run_safe_failure_case(app, case)
        print(("PASS " if ok else "FAIL ") + msg)
        if not ok:
            failures.append(msg)

    print("\n[SUMMARY]")
    if failures:
        print(f"FAILED: {len(failures)} case(s)")
        for failure in failures:
            print(f"- {failure}")
        return 1

    print("PASSED: all Phase 1E live filtered checks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
