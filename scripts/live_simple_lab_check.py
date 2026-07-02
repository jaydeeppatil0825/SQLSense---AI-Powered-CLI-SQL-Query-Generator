#!/usr/bin/env python3
"""
live_simple_lab_check.py

Run this from the SQLSense project root:

    python scripts/live_simple_lab_check.py --setup --user root --password "<local-root-password>" --host localhost --port 3306

Purpose:
- Creates/refreshes a small MySQL database: sqlsense_simple_lab
- Runs the normal SQLSense app flow:
  connect -> build KB/Chroma -> ask question -> generate SQL -> validate -> execute
- Verifies current supported deterministic shapes:
  single_table_list, single_table_count, single_table_aggregate
- Proves explicit metric phrases like "billed value" select billed_value, not ambiguous fallback.

This script is intentionally a live/e2e check. It is not a replacement for unit tests.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL


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
('B001', 'Aarav Traders', 'paid',     1000.00, 1000.00, '2026-01-05'),
('B002', 'Bright Retail', 'partial',  2500.00, 1500.00, '2026-01-08'),
('B003', 'City Mart', 'pending',      3000.00,    0.00, '2026-01-12'),
('B004', 'Delta Stores', 'paid',      4500.00, 4500.00, '2026-01-18'),
('B005', 'Elite Bazaar', 'partial',   5200.00, 2000.00, '2026-01-22'),
('B006', 'Fresh Point', 'paid',       1800.00, 1800.00, '2026-02-03'),
('B007', 'Global Shop', 'pending',    7600.00,    0.00, '2026-02-10'),
('B008', 'Hari Om Sales', 'paid',     2200.00, 2200.00, '2026-02-15'),
('B009', 'India Wholesale', 'partial',6400.00, 3000.00, '2026-02-20'),
('B010', 'Jai Market', 'paid',        3500.00, 3500.00, '2026-03-01');
"""


SUPPORTED_CASES: List[Dict[str, Any]] = [
    {
        "name": "list bills",
        "question": "show all bills",
        "query_shape": "single_table_list",
        "sql_must_contain": ["SELECT", "FROM", "bills"],
        "expected_kind": "row_count",
        "expected_value": 10,
    },
    {
        "name": "count bills",
        "question": "count bills",
        "query_shape": "single_table_count",
        "sql_must_contain": ["COUNT", "FROM", "bills"],
        "expected_kind": "scalar",
        "expected_value": Decimal("10"),
    },
    {
        "name": "sum billed value",
        "question": "show sum billed value from bills",
        "query_shape": "single_table_aggregate",
        "selected_metric": "billed_value",
        "sql_must_contain": ["SUM", "billed_value", "FROM", "bills"],
        "expected_kind": "scalar",
        "expected_value": Decimal("37700.00"),
    },
    {
        "name": "total billed value",
        "question": "show total billed value from bills",
        "query_shape": "single_table_aggregate",
        "selected_metric": "billed_value",
        "sql_must_contain": ["SUM", "billed_value", "FROM", "bills"],
        "expected_kind": "scalar",
        "expected_value": Decimal("37700.00"),
    },
    {
        "name": "sum paid value",
        "question": "show sum paid value from bills",
        "query_shape": "single_table_aggregate",
        "selected_metric": "paid_value",
        "sql_must_contain": ["SUM", "paid_value", "FROM", "bills"],
        "expected_kind": "scalar",
        "expected_value": Decimal("19500.00"),
    },
    {
        "name": "average billed value",
        "question": "average billed value from bills",
        "query_shape": "single_table_aggregate",
        "selected_metric": "billed_value",
        "sql_must_contain": ["AVG", "billed_value", "FROM", "bills"],
        "expected_kind": "scalar",
        "expected_value": Decimal("3770.00"),
    },
    {
        "name": "highest paid value",
        "question": "highest paid value from bills",
        "query_shape": "single_table_aggregate",
        "selected_metric": "paid_value",
        "sql_must_contain": ["MAX", "paid_value", "FROM", "bills"],
        "expected_kind": "scalar",
        "expected_value": Decimal("4500.00"),
    },
    {
        "name": "lowest billed value",
        "question": "lowest billed value from bills",
        "query_shape": "single_table_aggregate",
        "selected_metric": "billed_value",
        "sql_must_contain": ["MIN", "billed_value", "FROM", "bills"],
        "expected_kind": "scalar",
        "expected_value": Decimal("1000.00"),
    },
]

BLOCKING_CASES: List[Dict[str, Any]] = [
    {
        "name": "generic amount ambiguity",
        "question": "show sum amount from bills",
        "expected_route": "cannot_plan_safely",
        "must_mention": ["billed_value", "paid_value"],
    },
    {
        "name": "generic value ambiguity",
        "question": "show sum value from bills",
        "expected_route": "cannot_plan_safely",
        "must_mention": ["billed_value", "paid_value"],
    },
    {
        "name": "unsafe delete",
        "question": "delete bills",
        "expected_route": "blocked_unsafe",
        "must_mention": ["blocked", "unsafe"],
    },
]


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _add_project_to_path() -> None:
    root = _project_root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))


def _mysql_url(user: str, password: str, host: str, port: int, database: Optional[str] = None) -> URL:
    return URL.create(
        drivername="mysql+pymysql",
        username=user,
        password=password,
        host=host,
        port=port,
        database=database,
    )


def setup_database(user: str, password: str, host: str, port: int) -> None:
    print("[SETUP] Creating sqlsense_simple_lab ...")
    engine = create_engine(_mysql_url(user, password, host, port), future=True)
    statements = [stmt.strip() for stmt in SETUP_SQL.split(";") if stmt.strip()]
    with engine.begin() as conn:
        for stmt in statements:
            conn.execute(text(stmt))
    print("[SETUP] Done.")


def connect_app(args: argparse.Namespace):
    _add_project_to_path()

    # Keep live check deterministic and avoid local LLM noise.
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
        raise AssertionError("SQLSense database preparation failed or database is not ready.")

    return app


def normalize_sql(sql: str) -> str:
    return re.sub(r"\s+", " ", str(sql or "")).strip().lower()


def extract_scalar(rows: Optional[List[Dict[str, Any]]]) -> Decimal:
    if not rows:
        raise AssertionError("Expected at least one result row, got none.")
    first = rows[0]
    if not isinstance(first, dict) or not first:
        raise AssertionError(f"Expected a result dict, got: {first!r}")
    value = next(iter(first.values()))
    return Decimal(str(value)).quantize(Decimal("0.01"))


def result_text(result: Dict[str, Any]) -> str:
    parts = [
        str(result.get("message") or ""),
        str(result.get("error") or ""),
        str(result.get("route") or ""),
        str(result.get("route_used") or ""),
    ]
    ctx = result.get("query_context") or {}
    parts.append(str(ctx.get("ambiguities") or ""))
    parts.append(str(ctx.get("ambiguity_choices") or ""))
    parts.append(str(ctx.get("missing_evidence") or ""))
    return " ".join(parts).lower()


def selected_metric_from_context(ctx: Dict[str, Any]) -> str:
    candidates = [
        ctx.get("selected_metric"),
        (ctx.get("planner_contract") or {}).get("selected_metric"),
        (ctx.get("plan") or {}).get("metric"),
    ]
    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
        if isinstance(candidate, dict):
            value = candidate.get("column") or candidate.get("name")
            if value:
                return str(value).strip()
    for entry in ctx.get("selected_columns") or []:
        if isinstance(entry, dict) and entry.get("role") == "metric":
            return str(entry.get("column") or "").strip()
    return ""


def run_supported_case(app: Any, case: Dict[str, Any]) -> Tuple[bool, str]:
    question = case["question"]
    result = app.process_question(question)
    ctx = result.get("query_context") or {}
    sql = result.get("generated_sql") or result.get("sql")

    if not result.get("success") or not sql:
        route = result.get("route") or result.get("route_used") or ctx.get("route") or ctx.get("route_used")
        message = result.get("message") or result.get("error") or ""
        return False, (
            f"{case['name']}: expected SQL success, got success={result.get('success')!r} "
            f"route={route!r} message={message!r}"
        )

    validation = result.get("validation_result") or {}
    if validation.get("is_valid") is not True:
        return False, (
            f"{case['name']}: generated SQL did not pass normal-flow validation. "
            f"validation={validation!r} SQL={sql}"
        )

    query_shape = str(ctx.get("query_shape") or (ctx.get("planner_contract") or {}).get("query_shape") or "")
    if case.get("query_shape") and query_shape and query_shape != case["query_shape"]:
        return False, f"{case['name']}: expected query_shape={case['query_shape']}, got {query_shape!r}"

    if case.get("selected_metric"):
        selected = selected_metric_from_context(ctx)
        if selected and selected != case["selected_metric"]:
            return False, f"{case['name']}: expected selected_metric={case['selected_metric']}, got {selected!r}"

    norm = normalize_sql(sql)
    for fragment in case["sql_must_contain"]:
        if fragment.lower() not in norm:
            return False, f"{case['name']}: SQL missing {fragment!r}. SQL={sql}"

    ok, message, rows = app.execute_sql(sql)
    if not ok:
        return False, f"{case['name']}: generated SQL failed execution. SQL={sql} message={message}"

    if case["expected_kind"] == "row_count":
        actual_count = len(rows or [])
        if actual_count != case["expected_value"]:
            return False, f"{case['name']}: expected {case['expected_value']} rows, got {actual_count}. SQL={sql}"

    elif case["expected_kind"] == "scalar":
        actual = extract_scalar(rows)
        expected = Decimal(str(case["expected_value"])).quantize(Decimal("0.01"))
        if actual != expected:
            return False, f"{case['name']}: expected {expected}, got {actual}. SQL={sql}, rows={rows}"

    return True, f"{case['name']}: PASS | SQL={sql}"


def run_blocking_case(app: Any, case: Dict[str, Any]) -> Tuple[bool, str]:
    question = case["question"]
    result = app.process_question(question)
    sql = result.get("generated_sql") or result.get("sql")
    ctx = result.get("query_context") or {}
    route = str(result.get("route") or result.get("route_used") or ctx.get("route") or ctx.get("route_used") or "")

    if sql:
        intent = ctx.get("intent") if isinstance(ctx.get("intent"), dict) else {}
        candidates = [
            str(entry.get("column") or entry.get("column_name") or "").strip()
            for entry in (ctx.get("metric_candidates") or [])
            if isinstance(entry, dict)
        ]
        return False, (
            f"{case['name']}: expected no SQL, but got SQL={sql} "
            f"metric_is_generic={intent.get('metric_is_generic')!r} candidates={candidates!r}"
        )

    expected_route = case.get("expected_route")
    if expected_route and route and route != expected_route:
        return False, f"{case['name']}: expected route={expected_route}, got {route!r}."

    text_blob = result_text(result)
    for fragment in case.get("must_mention", []):
        if fragment.lower() not in text_blob:
            return False, (
                f"{case['name']}: expected message/context to mention {fragment!r}. "
                f"route={route!r} text={text_blob!r}"
            )

    return True, f"{case['name']}: PASS | blocked as expected"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=3306)
    parser.add_argument("--user", default="root")
    parser.add_argument("--password", default="")
    parser.add_argument("--database", default=DB_NAME_DEFAULT)
    parser.add_argument("--setup", action="store_true", help="Drop/recreate sqlsense_simple_lab before running checks.")
    args = parser.parse_args()

    if args.setup:
        setup_database(args.user, args.password, args.host, args.port)

    app = connect_app(args)

    failures: List[str] = []

    print("\n[CHECK] Supported deterministic SQL cases")
    for case in SUPPORTED_CASES:
        ok, message = run_supported_case(app, case)
        print(("PASS " if ok else "FAIL ") + message)
        if not ok:
            failures.append(message)

    print("\n[CHECK] Ambiguity and unsafe blocking cases")
    for case in BLOCKING_CASES:
        ok, message = run_blocking_case(app, case)
        print(("PASS " if ok else "FAIL ") + message)
        if not ok:
            failures.append(message)

    print("\n[SUMMARY]")
    if failures:
        print(f"FAILED: {len(failures)} case(s)")
        for failure in failures:
            print(f"- {failure}")
        return 1

    print("PASSED: all live simple-lab checks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
