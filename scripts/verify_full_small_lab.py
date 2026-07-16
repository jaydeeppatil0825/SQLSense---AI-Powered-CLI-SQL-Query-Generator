"""Verify SQLSense against the full-small-lab oracle list.

This script reads TEST_CASES from scripts/j.txt. It does not create, drop, or
reseed the database.
"""

from __future__ import annotations

import argparse
import ast
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Decimal
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from typing import Any, Iterator

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine, URL


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASE_FILE = ROOT / "scripts" / "j.txt"
DEFAULT_DATABASE = "sqlsense_full_small_lab"
SAFE_REJECTION_ROUTES = {"cannot_plan_safely", "blocked_unsafe", "clarification_required"}
DB_NAME_RE = re.compile(r"^[A-Za-z0-9_]+$")


@dataclass
class CaseResult:
    case_id: str
    question: str
    expected_type: str
    route: str = ""
    query_shape: str = ""
    generated_sql: str = ""
    oracle_sql: str = ""
    executed: bool = False
    passed: bool = False
    note: str = ""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", default=str(ROOT))
    parser.add_argument("--case-file", default=str(DEFAULT_CASE_FILE))
    parser.add_argument("--host", default=os.getenv("DB_HOST", "localhost"))
    parser.add_argument("--port", type=int, default=int(os.getenv("DB_PORT", "3306")))
    parser.add_argument("--user", default=os.getenv("DB_USER", "root"))
    parser.add_argument("--password", default=os.getenv("DB_PASSWORD", ""))
    parser.add_argument("--database", default=os.getenv("SQLSENSE_TEST_DB", DEFAULT_DATABASE))
    parser.add_argument("--json-output", help="Optional path for machine-readable results.")
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args()


def load_cases(path: Path) -> list[dict[str, Any]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "TEST_CASES":
                    cases = ast.literal_eval(node.value)
                    if isinstance(cases, list):
                        return cases
    raise RuntimeError(f"TEST_CASES list not found in {path}")


def db_url(args: argparse.Namespace) -> URL:
    if not args.password:
        raise RuntimeError("Set DB_PASSWORD or pass --password.")
    if not DB_NAME_RE.fullmatch(args.database):
        raise RuntimeError("Database name must contain only letters, digits, and underscores.")
    return URL.create(
        "mysql+pymysql",
        username=args.user,
        password=args.password,
        host=args.host,
        port=args.port,
        database=args.database,
    )


def assert_existing_lab(engine: Engine) -> None:
    required = {"customers", "orders", "payments", "products", "suppliers", "order_items"}
    found = set(inspect(engine).get_table_names())
    missing = sorted(required - found)
    if missing:
        raise RuntimeError(f"{DEFAULT_DATABASE} missing required tables: {missing}")


@contextmanager
def isolated_app(args: argparse.Namespace) -> Iterator[Any]:
    project_root = Path(args.project_root).resolve()
    original_cwd = Path.cwd()
    original_path = list(sys.path)
    env_names = ("CHROMA_INDEX_DIR", "VECTOR_INDEX_DIR", "EMBEDDING_BACKEND", "CUDA_VISIBLE_DEVICES")
    old_env = {name: os.environ.get(name) for name in env_names}
    with tempfile.TemporaryDirectory(prefix="sqlsense_full_small_lab_", ignore_cleanup_errors=True) as temp_dir:
        temp = Path(temp_dir)
        os.environ["CHROMA_INDEX_DIR"] = str(temp / "chroma")
        os.environ["VECTOR_INDEX_DIR"] = str(temp / "vector")
        os.environ["EMBEDDING_BACKEND"] = "deterministic"
        os.environ["CUDA_VISIBLE_DEVICES"] = ""
        sys.path.insert(0, str(project_root))
        os.chdir(temp)
        app = None
        try:
            from core.app_service import AppService

            app = AppService()
            ok, message, report = app.connect_database_and_prepare(
                db_type="mysql",
                host=args.host,
                port=args.port,
                username=args.user,
                password=args.password,
                database=args.database,
                use_ai_enrichment=False,
            )
            if not ok or not app.is_database_ready():
                raise RuntimeError(f"SQLSense prepare failed: {message}; report={report}")
            yield app
        finally:
            if app is not None:
                engine = app.database_service.get_engine()
                if engine is not None:
                    engine.dispose()
            os.chdir(original_cwd)
            sys.path[:] = original_path
            for name, value in old_env.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value


def normalize_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return ("decimal", format(value.normalize(), "f"))
    return value


def normalize_rows(rows: list[Any], *, ordered: bool) -> Any:
    normalized = []
    for row in rows:
        values = list(row.values()) if isinstance(row, dict) else list(row)
        normalized.append(tuple(normalize_value(value) for value in values))
    return normalized if ordered else Counter(normalized)


def is_ordered(sql: str) -> bool:
    return " ORDER BY " in f" {' '.join(str(sql).upper().split())} "


def run_oracle(engine: Engine, sql: str) -> Any:
    with engine.connect() as connection:
        return normalize_rows(connection.execute(text(sql)).all(), ordered=is_ordered(sql))


def route_from(payload: dict[str, Any], context: dict[str, Any]) -> str:
    return str(
        payload.get("route")
        or payload.get("route_used")
        or context.get("route_recommendation")
        or context.get("route")
        or ""
    )


def failure_note(result: CaseResult, *, message: str = "") -> str:
    if result.expected_type == "fail_closed" and result.generated_sql:
        return "fail-closed case produced SQL"
    if (
        result.expected_type == "fail_closed"
        and result.route not in SAFE_REJECTION_ROUTES
        and result.query_shape not in SAFE_REJECTION_ROUTES
    ):
        return f"unexpected fail-closed route/shape: {result.route or '-'} / {result.query_shape or '-'}"
    if not result.generated_sql:
        return f"no SQL generated: {message}"
    if not result.executed:
        return f"generated SQL was not executed: {message}"
    return "generated result set differs from oracle result set"


def run_case(app: Any, oracle_engine: Engine, case: dict[str, Any]) -> CaseResult:
    expected_execute = bool(case.get("expect_execute"))
    result = CaseResult(
        case_id=str(case.get("id")),
        question=str(case.get("question")),
        expected_type="executable" if expected_execute else "fail_closed",
        oracle_sql=str(case.get("oracle_sql") or "").strip(),
    )
    app.reset_conversation()
    payload = app.process_question(result.question)
    context = payload.get("query_context") or {}
    result.route = route_from(payload, context)
    result.query_shape = str(context.get("query_shape") or "")
    result.generated_sql = str(payload.get("generated_sql") or payload.get("sql") or "")

    if not expected_execute:
        result.passed = (
            not result.generated_sql
            and (result.route in SAFE_REJECTION_ROUTES or result.query_shape in SAFE_REJECTION_ROUTES)
        )
        result.note = "safe rejection" if result.passed else failure_note(result)
        return result

    if not result.generated_sql:
        result.note = failure_note(result, message=str(payload.get("error") or payload.get("message") or ""))
        return result

    ok, message, rows = app.execute_sql(result.generated_sql, revalidate=True)
    result.executed = bool(ok)
    if not ok:
        result.note = failure_note(result, message=message)
        return result

    actual = normalize_rows(list(rows or []), ordered=is_ordered(result.generated_sql))
    expected = run_oracle(oracle_engine, result.oracle_sql)
    result.passed = actual == expected
    result.note = "result match" if result.passed else failure_note(result)
    return result


def print_results(results: list[CaseResult], *, verbose: bool) -> None:
    print("SQLSense full-small-lab verification")
    print("=" * 140)
    print(f"{'ID':<4} {'OK':<4} {'TYPE':<12} {'EXEC':<5} {'ROUTE':<28} {'SHAPE':<24} QUESTION")
    print("-" * 140)
    for item in results:
        print(
            f"{item.case_id:<4} {'PASS' if item.passed else 'FAIL':<4} {item.expected_type:<12} "
            f"{'yes' if item.executed else 'no':<5} {item.route:<28} {item.query_shape:<24} {item.question}"
        )

    failures = [item for item in results if not item.passed]
    if failures:
        print("\nFailures")
        print("=" * 140)
    for item in failures:
        print(f"\n[{item.case_id}] {item.question}")
        print(f"Expected: {item.expected_type}")
        print(f"Route/query_shape: {item.route or '-'} / {item.query_shape or '-'}")
        print(f"Executed: {'yes' if item.executed else 'no'}")
        print(f"Root-cause note: {item.note}")
        print(f"Generated SQL: {item.generated_sql or '-'}")
        if item.oracle_sql:
            print(f"Oracle SQL: {item.oracle_sql}")
        if verbose:
            print()

    print("\nSummary")
    print("=" * 140)
    print(f"Total: {len(results)}")
    print(f"Passed: {sum(item.passed for item in results)}")
    print(f"Failed: {len(failures)}")


def main() -> int:
    args = parse_args()
    cases = load_cases(Path(args.case_file))
    oracle_engine = create_engine(db_url(args))
    try:
        assert_existing_lab(oracle_engine)
        with isolated_app(args) as app:
            results = [run_case(app, oracle_engine, case) for case in cases]
        print_results(results, verbose=bool(args.verbose))
        if args.json_output:
            Path(args.json_output).write_text(
                json.dumps([item.__dict__ for item in results], indent=2),
                encoding="utf-8",
            )
        return 0 if all(item.passed for item in results) else 1
    finally:
        oracle_engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
