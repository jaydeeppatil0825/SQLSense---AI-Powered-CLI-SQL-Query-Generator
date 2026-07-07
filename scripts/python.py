r"""Verify SQLSense join phases on an already-created MySQL database.

Database expected to already exist:
    sqlsense_join_phase_simple_lab

This script DOES NOT create, drop, or modify the database. It only:
1. connects SQLSense to the existing DB,
2. asks the natural-language questions through AppService,
3. compares route/query_shape/SQL patterns,
4. executes generated safe SQL through SQLSense,
5. compares aggregate answers with direct SQL oracle queries.

Run from anywhere, passing the SQLSense project root:

PowerShell example:
    $env:DB_USER="root"
    $env:DB_PASSWORD="your_password"
    python verify_sqlsense_join_phase_simple_lab.py --project-root "C:\Users\JAYDEEP PATIL\OneDrive\Desktop\SQL-Sense"

Optional:
    python verify_sqlsense_join_phase_simple_lab.py --project-root "..." --verbose
    python verify_sqlsense_join_phase_simple_lab.py --project-root "..." --use-ai-enrichment
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


DEFAULT_DATABASE = "sqlsense_join_phase_simple_lab"
DATABASE_NAME_RE = re.compile(r"^[A-Za-z0-9_]+$")
EXPECTED_ROUTE = "deterministic_sql_required"


@dataclass(frozen=True)
class VerificationCase:
    case_id: str
    section: str
    question: str
    expected_route: str
    expected_shape: str = ""
    oracle_sql: str = ""
    required_sql: tuple[str, ...] = ()
    forbidden_sql: tuple[str, ...] = ()
    ordered: bool = False
    compare_result: bool = True
    join_case: bool = False
    expected_note: str = ""


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


def safe(
    case_id: str,
    section: str,
    question: str,
    shape: str,
    oracle_sql: str,
    *required_sql: str,
    ordered: bool = False,
    forbidden_sql: Sequence[str] = (),
    compare_result: bool = True,
    join_case: bool = False,
    note: str = "",
) -> VerificationCase:
    return VerificationCase(
        case_id=case_id,
        section=section,
        question=question,
        expected_route=EXPECTED_ROUTE,
        expected_shape=shape,
        oracle_sql=oracle_sql,
        required_sql=tuple(required_sql),
        forbidden_sql=tuple(forbidden_sql),
        ordered=ordered,
        compare_result=compare_result,
        join_case=join_case,
        expected_note=note,
    )


def blocked(case_id: str, section: str, question: str, note: str, route: str = "cannot_plan_safely") -> VerificationCase:
    return VerificationCase(
        case_id=case_id,
        section=section,
        question=question,
        expected_route=route,
        expected_note=note,
        compare_result=False,
    )


CASES: tuple[VerificationCase, ...] = (
    # A. Basic single-table checks
    safe(
        "A01", "A", "show all customers", "single_table_list",
        "SELECT customer_id, customer_name, city, segment, customer_status FROM customers ORDER BY customer_id",
        "FROM customers",
        forbidden_sql=("JOIN",),
        compare_result=False,
        note="single-table list from customers",
    ),
    safe(
        "A02", "A", "count customers", "single_table_count",
        "SELECT COUNT(*) AS count__customers__rows FROM customers",
        "COUNT(*)", "FROM customers",
        forbidden_sql=("JOIN",),
        note="expected answer: 6",
    ),
    safe(
        "A03", "A", "show delivered orders", "filtered_query",
        "SELECT order_id FROM orders WHERE order_status = 'Delivered' ORDER BY order_id",
        "FROM orders", "order_status", "Delivered",
        forbidden_sql=("JOIN",),
        compare_result=False,
        note="expected delivered order IDs: 101, 102, 104, 106, 107, 108",
    ),
    safe(
        "A04", "A", "show total order amount", "single_table_aggregate",
        "SELECT SUM(order_amount) AS sum__orders__order_amount FROM orders",
        "SUM", "order_amount", "FROM orders",
        forbidden_sql=("JOIN",),
        note="expected answer: 12700.00",
    ),

    # B. Phase 5 direct joined lookup
    safe(
        "B05", "B", "show orders with customer details", "joined_lookup",
        "SELECT orders.order_id, customers.customer_name FROM orders INNER JOIN customers ON orders.customer_id = customers.customer_id ORDER BY orders.order_id",
        "INNER JOIN customers", "orders.customer_id = customers.customer_id",
        forbidden_sql=("SELECT *",),
        compare_result=False,
        join_case=True,
        note="Phase 5: one direct edge orders -> customers",
    ),
    safe(
        "B06", "B", "show order items with product details", "joined_lookup",
        "SELECT order_items.item_id, products.product_name FROM order_items INNER JOIN products ON order_items.product_id = products.product_id ORDER BY order_items.item_id",
        "INNER JOIN products", "order_items.product_id = products.product_id",
        forbidden_sql=("SELECT *",),
        compare_result=False,
        join_case=True,
        note="Phase 5: one direct edge order_items -> products",
    ),

    # C. Phase 6 direct joined aggregate
    safe(
        "C07", "C", "show total order amount by customer city", "joined_aggregate",
        """
        SELECT customers.city AS customers__city, SUM(orders.order_amount) AS sum__orders__order_amount
        FROM orders INNER JOIN customers ON orders.customer_id = customers.customer_id
        GROUP BY customers.city
        """,
        "SUM", "orders.order_amount", "customers.city", "INNER JOIN customers", "GROUP BY customers.city",
        forbidden_sql=("SELECT *", "LEFT JOIN", "RIGHT JOIN", "FULL JOIN", "CROSS JOIN", "NATURAL JOIN"),
        join_case=True,
        note="expected: Bengaluru=3000.00, Mumbai=2300.00, Nashik=1200.00, Pune=6200.00",
    ),
    safe(
        "C08", "C", "count orders by customer segment", "joined_aggregate",
        """
        SELECT customers.segment AS customers__segment, COUNT(*) AS count__orders__rows
        FROM orders INNER JOIN customers ON orders.customer_id = customers.customer_id
        GROUP BY customers.segment
        """,
        "COUNT(*)", "customers.segment", "INNER JOIN customers", "GROUP BY customers.segment",
        forbidden_sql=("SELECT *", "LEFT JOIN", "RIGHT JOIN"),
        join_case=True,
        note="expected: Enterprise=3, Retail=3, Wholesale=2",
    ),
    safe(
        "C09", "C", "average shipping cost by customer city", "joined_aggregate",
        """
        SELECT customers.city AS customers__city, AVG(orders.shipping_cost) AS avg__orders__shipping_cost
        FROM orders INNER JOIN customers ON orders.customer_id = customers.customer_id
        GROUP BY customers.city
        """,
        "AVG", "orders.shipping_cost", "customers.city", "INNER JOIN customers", "GROUP BY customers.city",
        forbidden_sql=("SELECT *",),
        join_case=True,
        note="expected: Bengaluru=90.00, Mumbai=55.00, Nashik=60.00, Pune=62.50",
    ),
    safe(
        "C10", "C", "minimum order amount by customer segment", "joined_aggregate",
        """
        SELECT customers.segment AS customers__segment, MIN(orders.order_amount) AS min__orders__order_amount
        FROM orders INNER JOIN customers ON orders.customer_id = customers.customer_id
        GROUP BY customers.segment
        """,
        "MIN", "orders.order_amount", "customers.segment", "INNER JOIN customers", "GROUP BY customers.segment",
        forbidden_sql=("SELECT *",),
        join_case=True,
        note="expected: Enterprise=2000.00, Retail=700.00, Wholesale=800.00",
    ),
    safe(
        "C11", "C", "maximum order amount by customer city", "joined_aggregate",
        """
        SELECT customers.city AS customers__city, MAX(orders.order_amount) AS max__orders__order_amount
        FROM orders INNER JOIN customers ON orders.customer_id = customers.customer_id
        GROUP BY customers.city
        """,
        "MAX", "orders.order_amount", "customers.city", "INNER JOIN customers", "GROUP BY customers.city",
        forbidden_sql=("SELECT *",),
        join_case=True,
        note="expected: Bengaluru=3000.00, Mumbai=1500.00, Nashik=1200.00, Pune=2500.00",
    ),

    # D. Phase 6 direct joined aggregate with filters
    safe(
        "D12", "D", "show total delivered order amount by customer city", "joined_aggregate",
        """
        SELECT customers.city AS customers__city, SUM(orders.order_amount) AS sum__orders__order_amount
        FROM orders INNER JOIN customers ON orders.customer_id = customers.customer_id
        WHERE orders.order_status = 'Delivered'
        GROUP BY customers.city
        """,
        "SUM", "orders.order_amount", "customers.city", "orders.order_status", "Delivered", "GROUP BY customers.city",
        forbidden_sql=("SELECT *",),
        join_case=True,
        note="expected: Bengaluru=3000.00, Mumbai=1500.00, Nashik=1200.00, Pune=4200.00",
    ),
    safe(
        "D13", "D", "show total order amount by customer city for active customers", "joined_aggregate",
        """
        SELECT customers.city AS customers__city, SUM(orders.order_amount) AS sum__orders__order_amount
        FROM orders INNER JOIN customers ON orders.customer_id = customers.customer_id
        WHERE customers.customer_status = 'Active'
        GROUP BY customers.city
        """,
        "SUM", "orders.order_amount", "customers.city", "customers.customer_status", "Active", "GROUP BY customers.city",
        forbidden_sql=("SELECT *",),
        join_case=True,
        note="expected: Bengaluru=3000.00, Mumbai=2300.00, Pune=6200.00",
    ),
    safe(
        "D14", "D", "show total item sales by product category", "joined_aggregate",
        """
        SELECT products.category AS products__category, SUM(order_items.line_total) AS sum__order_items__line_total
        FROM order_items INNER JOIN products ON order_items.product_id = products.product_id
        GROUP BY products.category
        """,
        "SUM", "order_items.line_total", "products.category", "INNER JOIN products", "GROUP BY products.category",
        forbidden_sql=("SELECT *",),
        join_case=True,
        note="expected: Accessories=2900.00, Furniture=9800.00",
    ),
    safe(
        "D15", "D", "total quantity by product category for active products", "joined_aggregate",
        """
        SELECT products.category AS products__category, SUM(order_items.quantity) AS sum__order_items__quantity
        FROM order_items INNER JOIN products ON order_items.product_id = products.product_id
        WHERE products.product_status = 'Active'
        GROUP BY products.category
        """,
        "SUM", "order_items.quantity", "products.category", "products.product_status", "Active", "GROUP BY products.category",
        forbidden_sql=("SELECT *",),
        join_case=True,
        note="expected: Accessories=14, Furniture=10",
    ),

    # E. Phase 6 ranking / top N
    safe(
        "E16", "E", "top 3 customers by total order amount", "joined_aggregate",
        """
        SELECT customers.customer_name AS customers__customer_name, SUM(orders.order_amount) AS sum__orders__order_amount
        FROM orders INNER JOIN customers ON orders.customer_id = customers.customer_id
        GROUP BY customers.customer_name
        ORDER BY sum__orders__order_amount DESC
        LIMIT 3
        """,
        "SUM", "orders.order_amount", "customers.customer_name", "ORDER BY", "DESC", "LIMIT 3",
        ordered=True,
        forbidden_sql=("SELECT *",),
        join_case=True,
        note="expected top 3: Crown Manufacturing=4500.00, Future Systems=3000.00, Bright Stores=1500.00",
    ),
    safe(
        "E17", "E", "bottom 2 customer cities by average shipping cost", "joined_aggregate",
        """
        SELECT customers.city AS customers__city, AVG(orders.shipping_cost) AS avg__orders__shipping_cost
        FROM orders INNER JOIN customers ON orders.customer_id = customers.customer_id
        GROUP BY customers.city
        ORDER BY avg__orders__shipping_cost ASC
        LIMIT 2
        """,
        "AVG", "orders.shipping_cost", "customers.city", "ORDER BY", "ASC", "LIMIT 2",
        ordered=True,
        forbidden_sql=("SELECT *",),
        join_case=True,
        note="expected bottom 2: Mumbai=55.00, Nashik=60.00",
    ),
    safe(
        "E18", "E", "top 2 products by total line total", "joined_aggregate",
        """
        SELECT products.product_name AS products__product_name, SUM(order_items.line_total) AS sum__order_items__line_total
        FROM order_items INNER JOIN products ON order_items.product_id = products.product_id
        GROUP BY products.product_name
        ORDER BY sum__order_items__line_total DESC
        LIMIT 2
        """,
        "SUM", "order_items.line_total", "products.product_name", "ORDER BY", "DESC", "LIMIT 2",
        ordered=True,
        forbidden_sql=("SELECT *",),
        join_case=True,
        note="expected top 2: Office Chair=6100.00, Steel Desk=3700.00",
    ),

    # F. Should fail closed until BFS / multi-hop phase
    blocked(
        "F19", "F", "show total item sales by customer city",
        "metric order_items.line_total to dimension customers.city requires order_items -> orders -> customers, two edges",
    ),
    blocked(
        "F20", "F", "show total quantity by customer segment",
        "metric order_items.quantity to dimension customers.segment requires order_items -> orders -> customers, two edges",
    ),
    blocked(
        "F21", "F", "show total order amount by product category",
        "metric orders.order_amount to dimension products.category requires orders -> order_items -> products, two edges",
    ),

    # G. Unsafe / unsupported wording should fail closed
    blocked(
        "G22", "G", "left join orders with customers",
        "explicit LEFT JOIN wording is unsupported; only graph-authorized INNER JOIN is allowed",
    ),
    blocked(
        "G23", "G", "show total order amount and average shipping cost by customer city",
        "multiple metrics should fail closed",
    ),
    blocked(
        "G24", "G", "show total order amount by customer city and customer segment",
        "multiple dimensions should fail closed",
    ),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", required=True, help="Path to SQLSense project root, e.g. C:\\...\\SQL-Sense")
    parser.add_argument("--host", default=os.getenv("DB_HOST", "localhost"))
    parser.add_argument("--port", type=int, default=int(os.getenv("DB_PORT", "3306")))
    parser.add_argument("--user", default=os.getenv("DB_USER", ""))
    parser.add_argument("--database", default=os.getenv("SQLSENSE_TEST_DB", DEFAULT_DATABASE))
    parser.add_argument("--use-ai-enrichment", action="store_true", help="Optional; default is off for deterministic diagnosis.")
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args()


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
    expected_tables = {"customers", "products", "orders", "order_items"}
    actual_tables = set(schema.get_table_names())
    if not expected_tables.issubset(actual_tables):
        raise RuntimeError(
            f"Existing DB does not look like sqlsense_join_phase_simple_lab. "
            f"Expected tables {sorted(expected_tables)}, found {sorted(actual_tables)}. "
            "This script does not create the DB; load the SQL fixture first."
        )
    with engine.connect() as connection:
        counts = {
            name: connection.execute(text(f"SELECT COUNT(*) FROM `{name}`")).scalar_one()
            for name in sorted(expected_tables)
        }
    expected_counts = {"customers": 6, "products": 4, "orders": 8, "order_items": 12}
    if counts != expected_counts:
        raise RuntimeError(
            f"Fixture row counts do not match expected {expected_counts}; got {counts}. "
            "This script will not modify the DB. Reload the fixture if needed."
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

    with tempfile.TemporaryDirectory(prefix="sqlsense_join_phase_verify_", ignore_cleanup_errors=True) as temp_dir:
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
                engine = app.database_service.get_engine()
                if engine is not None:
                    engine.dispose()
            os.chdir(original_cwd)
            sys.path[:] = old_path
            for name, value in old_env.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value


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


def run_oracle(engine: Engine, case: VerificationCase) -> Any:
    with engine.connect() as connection:
        rows = connection.execute(text(case.oracle_sql)).all()
    return normalize_rows(rows, ordered=case.ordered)


def normalize_sql(sql: str | None) -> str:
    return " ".join(str(sql or "").strip().rstrip(";").split())


def _contains_normalized(sql: str, pattern: str) -> bool:
    return normalize_sql(pattern).lower() in sql.lower()


def validate_join_contract(context: dict[str, Any], sql: str, *, aggregate: bool) -> str:
    selected_path = context.get("selected_join_path")
    if not isinstance(selected_path, dict):
        return "selected_join_path is missing"
    if selected_path.get("path_source") != "relationship_graph":
        return "selected_join_path is not Relationship Graph-backed"
    edges = [edge for edge in selected_path.get("edges", []) if isinstance(edge, dict)]
    if len(edges) != 1:
        return f"expected one selected join edge, got {len(edges)}"
    edge = edges[0]
    left = f"{edge.get('from_table')}.{edge.get('from_column')}"
    right = f"{edge.get('to_table')}.{edge.get('to_column')}"
    compact = normalize_sql(sql)
    if not (f"{left} = {right}" in compact or f"{right} = {left}" in compact):
        return "JOIN ON condition does not match selected_join_path"
    if compact.upper().count(" INNER JOIN ") != 1:
        return "expected exactly one INNER JOIN"
    forbidden_join_words = (" LEFT JOIN ", " RIGHT JOIN ", " FULL JOIN ", " CROSS JOIN ", " NATURAL JOIN ", " USING ")
    if any(word in compact.upper() for word in forbidden_join_words):
        return "non-INNER or unsupported join syntax was generated"
    if aggregate and " GROUP BY " not in compact.upper():
        return "joined aggregate SQL is missing GROUP BY"
    return ""


def _payload_route(payload: dict[str, Any], context: dict[str, Any]) -> str:
    return str(
        payload.get("route")
        or payload.get("route_used")
        or context.get("route_recommendation")
        or context.get("route")
        or ""
    )


def run_case(app: Any, oracle_engine: Engine, case: VerificationCase) -> CaseResult:
    result = CaseResult(case=case)
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

    if case.expected_route != EXPECTED_ROUTE:
        if result.actual_route != case.expected_route:
            result.reason = f"expected fail-closed route {case.expected_route}, got {result.actual_route}"
            return result
        if result.sql:
            result.reason = "fail-closed case returned SQL"
            return result
        result.passed = True
        return result

    if result.actual_route != EXPECTED_ROUTE:
        result.reason = f"expected route {EXPECTED_ROUTE}, got {result.actual_route}; message={result.message or result.error}"
        return result
    if result.actual_shape != case.expected_shape:
        result.reason = f"expected query_shape {case.expected_shape}, got {result.actual_shape}"
        return result
    if not result.sql:
        result.reason = f"safe case returned no SQL: {result.error or result.message}"
        return result
    if result.validation and result.validation.get("is_valid") is not True:
        result.reason = f"generated SQL did not validate: {result.validation.get('reason')}"
        return result

    normalized_sql = normalize_sql(result.sql)
    for required in case.required_sql:
        if not _contains_normalized(normalized_sql, required):
            result.reason = f"generated SQL is missing required pattern: {required}"
            return result
    for forbidden in case.forbidden_sql:
        if _contains_normalized(normalized_sql, forbidden):
            result.reason = f"generated SQL contains forbidden pattern: {forbidden}"
            return result

    if case.join_case:
        join_error = validate_join_contract(context, result.sql, aggregate=case.expected_shape == "joined_aggregate")
        if join_error:
            result.reason = join_error
            return result

    executed, message, rows = app.execute_sql(result.sql, revalidate=True)
    result.executed = bool(executed)
    if not executed:
        result.reason = f"validated SQL did not execute: {message}"
        return result
    result.actual_result = normalize_rows(list(rows or []), ordered=case.ordered)

    if case.compare_result:
        result.expected_result = run_oracle(oracle_engine, case)
        if result.actual_result != result.expected_result:
            result.reason = "execution result differs from direct SQL oracle"
            return result

    result.passed = True
    return result


def clipped(value: Any, limit: int = 1800) -> str:
    rendered = repr(value)
    return rendered if len(rendered) <= limit else rendered[:limit] + "...<truncated>"


def print_results(results: Sequence[CaseResult], *, verbose: bool) -> None:
    print("\nSQLSense join-phase verification on existing DB")
    print("=" * 140)
    print(f"{'ID':<5} {'OK':<4} {'EXPECTED SHAPE':<22} {'ACTUAL SHAPE':<22} {'EXEC':<5} QUESTION")
    print("-" * 140)
    for result in results:
        print(
            f"{result.case.case_id:<5} {'PASS' if result.passed else 'FAIL':<4} "
            f"{result.case.expected_shape or result.case.expected_route:<22} "
            f"{result.actual_shape or result.actual_route:<22} "
            f"{'yes' if result.executed else 'no':<5} {result.case.question}"
        )

    failures = [result for result in results if not result.passed]
    if failures:
        print("\nFailure diagnostics")
        print("=" * 140)
        print("Failed question IDs: " + ", ".join(result.case.case_id for result in failures))

    for result in failures:
        print(f"\n[{result.case.case_id}] {result.case.question}")
        print(f"Expected note: {result.case.expected_note}")
        print(f"Reason: {result.reason}")
        print(f"Expected route/shape: {result.case.expected_route} / {result.case.expected_shape or '-'}")
        print(f"Actual route/shape: {result.actual_route or '-'} / {result.actual_shape or '-'}")
        print(f"Required SQL patterns: {list(result.case.required_sql)}")
        print(f"Forbidden SQL patterns: {list(result.case.forbidden_sql)}")
        print(f"Generated SQL: {result.sql}")
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
    print("=" * 140)
    print(f"Database: {DEFAULT_DATABASE}")
    print(f"Question cases: {len(results)}")
    print(f"Passed: {passed}")
    print(f"Failed: {len(results) - passed}")
    print("Skipped: 0")


def main() -> int:
    args = parse_args()
    oracle_engine: Engine | None = None
    try:
        oracle_engine = create_engine(database_url(args, include_database=True))
        assert_existing_fixture(oracle_engine)
        with isolated_app(args) as app:
            results = [run_case(app, oracle_engine, case) for case in CASES]
        print_results(results, verbose=bool(args.verbose))
        return 0 if all(result.passed for result in results) else 1
    except Exception as exc:
        print("\nSQLSense join-phase verification could not start.")
        print(f"Reason: {exc}")
        print("This script does not create the DB. Load sqlsense_join_phase_simple_lab first, then rerun.")
        return 1
    finally:
        if oracle_engine is not None:
            oracle_engine.dispose()


if __name__ == "__main__":
    sys.exit(main())