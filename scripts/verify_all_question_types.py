"""End-to-end verification for every supported deterministic SQLSense shape.

Run from the project root after setting DB_USER and DB_PASSWORD:

    python scripts/verify_all_question_types.py --setup

The direct SQL statements in this file are test oracles only. Natural-language
questions still travel through the normal AppService, QueryPipeline,
QuestionService, validator, and executor flow.
"""

from __future__ import annotations

import argparse
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
import os
from pathlib import Path
import re
import sys
import tempfile
from typing import Any, Iterator, Sequence

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine, URL


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATABASE = "sqlsense_all_types_lab"
DATABASE_NAME_RE = re.compile(r"^[A-Za-z0-9_]+$")
EXPECTED_ROUTE = "deterministic_sql_required"


CUSTOMERS = [
    (1, "John Carter", "Pune"),
    (2, "Jane Patel", "Mumbai"),
    (3, "Acme Labs", "Pune"),
    (4, "Ravi Shah", "Delhi"),
    (5, "Mina Roy", "Bengaluru"),
]


INVOICES = [
    (1, "SI-001", 1, "John Carter", "paid", "12000.00", "12000.00", "2026-01-01"),
    (2, "SI-002", 2, "Jane Patel", "paid", "10500.00", "10000.00", "2026-01-06"),
    (3, "SI-003", 3, "Acme Labs", "paid", "9000.00", "8500.00", "2026-01-11"),
    (4, "SI-004", 4, "Ravi Shah", "paid", "8500.00", "8000.00", "2026-01-16"),
    (5, "SI-005", 5, "Mina Roy", "paid", "7000.00", "6500.00", "2026-01-21"),
    (6, "SI-006", 1, "John Carter", "paid", "6500.00", "6000.00", "2026-01-26"),
    (7, "SI-007", 2, "Jane Patel", "paid", "5500.00", "5000.00", "2026-01-31"),
    (8, "SI-008", 3, "Acme Labs", "pending", "5000.00", "0.00", "2026-02-05"),
    (9, "SI-009", 4, "Ravi Shah", "pending", "4500.00", "0.00", "2026-02-10"),
    (10, "SI-010", 5, "Mina Roy", "pending", "4000.00", None, "2026-02-15"),
    (11, "SI-011", 1, "John Carter", "pending", "3500.00", "0.00", "2026-02-20"),
    (12, "SI-012", 2, "Jane Patel", "pending", "3000.00", None, "2026-02-25"),
    (13, "SI-013", 3, "Acme Labs", "pending", "2500.00", "0.00", "2026-03-02"),
    (14, "SI-014", 4, "Ravi Shah", "partial", "2000.00", "1200.00", "2026-03-07"),
    (15, "SI-015", 5, "Mina Roy", "partial", "1500.00", "800.00", "2026-03-12"),
    (16, "SI-016", 1, "John Carter", "partial", "1000.00", "500.00", "2026-03-17"),
    (17, "SI-017", 2, "Jane Patel", "partial", "750.00", "300.00", "2026-03-22"),
    (18, "SI-018", 3, "Acme Labs", "partial", "500.00", None, "2026-03-27"),
]


@dataclass(frozen=True)
class VerificationCase:
    case_id: str
    category: str
    question: str
    expected_route: str
    expected_shape: str = ""
    oracle_sql: str = ""
    ordered: bool = False
    required_sql: tuple[str, ...] = ()
    forbidden_sql: tuple[str, ...] = ()
    join_case: bool = False


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
    intent: dict[str, Any] = field(default_factory=dict)
    planner: dict[str, Any] = field(default_factory=dict)


def safe(
    case_id: str,
    category: str,
    question: str,
    shape: str,
    oracle_sql: str,
    *required_sql: str,
    ordered: bool = False,
    forbidden_sql: Sequence[str] = (),
    join_case: bool = False,
) -> VerificationCase:
    return VerificationCase(
        case_id=case_id,
        category=category,
        question=question,
        expected_route=EXPECTED_ROUTE,
        expected_shape=shape,
        oracle_sql=oracle_sql,
        ordered=ordered,
        required_sql=tuple(required_sql),
        forbidden_sql=tuple(forbidden_sql),
        join_case=join_case,
    )


def blocked(case_id: str, question: str, route: str = "cannot_plan_safely") -> VerificationCase:
    return VerificationCase(case_id, "H", question, route)


CASES: tuple[VerificationCase, ...] = (
    # A. Single-table basics
    safe("A01", "A", "show all service invoices", "single_table_list", "SELECT * FROM service_invoices", "FROM service_invoices"),
    safe("A02", "A", "count service invoices", "single_table_count", "SELECT COUNT(*) AS count_rows FROM service_invoices", "COUNT(*)", "FROM service_invoices"),
    safe("A03", "A", "show sum gross amount from service invoices", "single_table_aggregate", "SELECT SUM(gross_amount) AS value FROM service_invoices", "SUM(gross_amount)"),
    safe("A04", "A", "show average gross amount from service invoices", "single_table_aggregate", "SELECT AVG(gross_amount) AS value FROM service_invoices", "AVG(gross_amount)"),
    safe("A05", "A", "show highest received amount from service invoices", "single_table_aggregate", "SELECT MAX(received_amount) AS value FROM service_invoices", "MAX(received_amount)"),
    safe("A06", "A", "show lowest gross amount from service invoices", "single_table_aggregate", "SELECT MIN(gross_amount) AS value FROM service_invoices", "MIN(gross_amount)"),

    # B. Filtered queries
    safe("B01", "B", "show service invoices where invoice status is pending", "filtered_query", "SELECT * FROM service_invoices WHERE invoice_status = 'pending'", "WHERE invoice_status = 'pending'"),
    safe("B02", "B", "show service invoices where gross amount greater than 5000", "filtered_query", "SELECT * FROM service_invoices WHERE gross_amount > 5000", "WHERE gross_amount > 5000"),
    safe("B03", "B", "show service invoices where invoice status is pending and received amount equals 0", "filtered_query", "SELECT * FROM service_invoices WHERE invoice_status = 'pending' AND received_amount = 0", "WHERE invoice_status = 'pending' AND received_amount = 0"),
    safe("B04", "B", "show service invoices where invoice status is paid or invoice status is partial", "filtered_query", "SELECT * FROM service_invoices WHERE invoice_status = 'paid' OR invoice_status = 'partial'", "WHERE invoice_status = 'paid' OR invoice_status = 'partial'"),
    safe("B05", "B", "show service invoices where gross amount between 1000 and 5000", "filtered_query", "SELECT * FROM service_invoices WHERE gross_amount BETWEEN 1000 AND 5000", "WHERE gross_amount BETWEEN 1000 AND 5000"),
    safe("B06", "B", "show service invoices where customer name contains John", "filtered_query", "SELECT * FROM service_invoices WHERE customer_name LIKE '%John%'", "WHERE customer_name LIKE '%John%'"),
    safe("B07", "B", "show service invoices where invoice status is not paid", "filtered_query", "SELECT * FROM service_invoices WHERE invoice_status <> 'paid'", "WHERE invoice_status <> 'paid'"),
    safe("B08", "B", "show service invoices where invoice date is after 2026-02-01", "filtered_query", "SELECT * FROM service_invoices WHERE invoice_date > '2026-02-01'", "WHERE invoice_date > '2026-02-01'"),
    safe("B09", "B", "show service invoices where received amount is null", "filtered_query", "SELECT * FROM service_invoices WHERE received_amount IS NULL", "WHERE received_amount IS NULL"),
    safe("B10", "B", "show service invoices where received amount is not null", "filtered_query", "SELECT * FROM service_invoices WHERE received_amount IS NOT NULL", "WHERE received_amount IS NOT NULL"),

    # C. Filtered aggregates
    safe("C01", "C", "show sum gross amount from service invoices where invoice status is paid", "filtered_query", "SELECT SUM(gross_amount) AS value FROM service_invoices WHERE invoice_status = 'paid'", "SUM(gross_amount)", "WHERE invoice_status = 'paid'"),
    safe("C02", "C", "show sum received amount from service invoices where invoice status is paid", "filtered_query", "SELECT SUM(received_amount) AS value FROM service_invoices WHERE invoice_status = 'paid'", "SUM(received_amount)", "WHERE invoice_status = 'paid'"),
    safe("C03", "C", "average gross amount from service invoices where invoice status is pending", "filtered_query", "SELECT AVG(gross_amount) AS value FROM service_invoices WHERE invoice_status = 'pending'", "AVG(gross_amount)", "WHERE invoice_status = 'pending'"),

    # D. GROUP BY
    safe("D01", "D", "show sum gross amount by invoice status from service invoices", "grouped_aggregate", "SELECT invoice_status, SUM(gross_amount) AS value FROM service_invoices GROUP BY invoice_status", "SUM(gross_amount)", "GROUP BY invoice_status"),
    safe("D02", "D", "show average gross amount by invoice status from service invoices", "grouped_aggregate", "SELECT invoice_status, AVG(gross_amount) AS value FROM service_invoices GROUP BY invoice_status", "AVG(gross_amount)", "GROUP BY invoice_status"),
    safe("D03", "D", "show count service invoices by invoice status", "grouped_aggregate", "SELECT invoice_status, COUNT(*) AS value FROM service_invoices GROUP BY invoice_status", "COUNT(*)", "GROUP BY invoice_status"),
    safe("D04", "D", "show highest received amount by invoice status from service invoices", "grouped_aggregate", "SELECT invoice_status, MAX(received_amount) AS value FROM service_invoices GROUP BY invoice_status", "MAX(received_amount)", "GROUP BY invoice_status"),
    safe("D05", "D", "show lowest gross amount by invoice status from service invoices", "grouped_aggregate", "SELECT invoice_status, MIN(gross_amount) AS value FROM service_invoices GROUP BY invoice_status", "MIN(gross_amount)", "GROUP BY invoice_status"),

    # E. HAVING
    safe("E01", "E", "show invoice status where sum gross amount is greater than 10000 from service invoices", "grouped_aggregate", "SELECT invoice_status, SUM(gross_amount) AS value FROM service_invoices GROUP BY invoice_status HAVING SUM(gross_amount) > 10000", "GROUP BY invoice_status", "HAVING SUM(gross_amount) > 10000"),
    safe("E02", "E", "show invoice status where count is greater than 5 from service invoices", "grouped_aggregate", "SELECT invoice_status, COUNT(*) AS value FROM service_invoices GROUP BY invoice_status HAVING COUNT(*) > 5", "GROUP BY invoice_status", "HAVING COUNT(*) > 5"),
    safe("E03", "E", "show sum gross amount from service invoices where invoice status is paid group by customer name having sum gross amount greater than 10000", "grouped_aggregate", "SELECT customer_name, SUM(gross_amount) AS value FROM service_invoices WHERE invoice_status = 'paid' GROUP BY customer_name HAVING SUM(gross_amount) > 10000", "WHERE invoice_status = 'paid'", "GROUP BY customer_name", "HAVING SUM(gross_amount) > 10000"),

    # F. ORDER BY / LIMIT / ranking
    safe("F01", "F", "top 5 service invoices by gross amount", "ranking_query", "SELECT * FROM service_invoices ORDER BY gross_amount DESC LIMIT 5", "ORDER BY gross_amount DESC", "LIMIT 5", ordered=True),
    safe("F02", "F", "highest 10 service invoices by received amount", "ranking_query", "SELECT * FROM service_invoices ORDER BY received_amount DESC LIMIT 10", "ORDER BY received_amount DESC", "LIMIT 10", ordered=True),
    safe("F03", "F", "lowest 5 service invoices by gross amount", "ranking_query", "SELECT * FROM service_invoices ORDER BY gross_amount ASC LIMIT 5", "ORDER BY gross_amount ASC", "LIMIT 5", ordered=True),
    safe("F04", "F", "show service invoices ordered by gross amount", "ranking_query", "SELECT * FROM service_invoices ORDER BY gross_amount ASC", "ORDER BY gross_amount ASC", ordered=True),
    safe("F05", "F", "show service invoices order by invoice date descending", "ranking_query", "SELECT * FROM service_invoices ORDER BY invoice_date DESC", "ORDER BY invoice_date DESC", ordered=True),
    safe("F06", "F", "show service invoices where invoice status is paid order by gross amount descending", "ranking_query", "SELECT * FROM service_invoices WHERE invoice_status = 'paid' ORDER BY gross_amount DESC", "WHERE invoice_status = 'paid'", "ORDER BY gross_amount DESC", ordered=True),
    safe("F07", "F", "show sum gross amount by invoice status order by sum gross amount descending", "ranking_query", "SELECT invoice_status, SUM(gross_amount) AS value FROM service_invoices GROUP BY invoice_status ORDER BY SUM(gross_amount) DESC", "GROUP BY invoice_status", "ORDER BY SUM(gross_amount) DESC", ordered=True),
    safe("F08", "F", "top 3 customer name by sum received amount", "ranking_query", "SELECT customer_name, SUM(received_amount) AS value FROM service_invoices GROUP BY customer_name ORDER BY SUM(received_amount) DESC LIMIT 3", "GROUP BY customer_name", "ORDER BY SUM(received_amount) DESC", "LIMIT 3", ordered=True),
    safe("F09", "F", "lowest invoice status by average gross amount", "ranking_query", "SELECT invoice_status, AVG(gross_amount) AS value FROM service_invoices GROUP BY invoice_status ORDER BY AVG(gross_amount) ASC LIMIT 50", "GROUP BY invoice_status", "ORDER BY AVG(gross_amount) ASC", "LIMIT 50", ordered=True),

    # G. Relationship-Graph-authorized joins
    safe("G01", "G", "show service invoices with customer details", "joined_lookup", "SELECT service_invoices.invoice_id, service_invoices.invoice_code, service_invoices.customer_id, service_invoices.customer_name, service_invoices.invoice_status, service_invoices.gross_amount, service_invoices.received_amount, service_invoices.invoice_date, customers.customer_id, customers.name, customers.city FROM service_invoices INNER JOIN customers ON service_invoices.customer_id = customers.customer_id", "INNER JOIN customers", "ON service_invoices.customer_id = customers.customer_id", "LIMIT 50", forbidden_sql=("SELECT *",), join_case=True),
    safe("G02", "G", "show service invoices with customer name", "joined_lookup", "SELECT service_invoices.invoice_id, service_invoices.invoice_code, service_invoices.customer_id, service_invoices.customer_name, service_invoices.invoice_status, service_invoices.gross_amount, service_invoices.received_amount, service_invoices.invoice_date, customers.name FROM service_invoices INNER JOIN customers ON service_invoices.customer_id = customers.customer_id", "INNER JOIN customers", "customers.name AS customers__name", "LIMIT 50", forbidden_sql=("SELECT *",), join_case=True),
    safe("G03", "G", "show customer name and invoice status", "joined_lookup", "SELECT customers.name, service_invoices.invoice_status FROM service_invoices INNER JOIN customers ON service_invoices.customer_id = customers.customer_id", "INNER JOIN customers", "customers.name AS customers__name", "service_invoices.invoice_status AS service_invoices__invoice_status", "LIMIT 50", forbidden_sql=("SELECT *",), join_case=True),
    safe("G04", "G", "show service invoices where customer city is Pune", "joined_lookup", "SELECT service_invoices.invoice_id, service_invoices.invoice_code, service_invoices.customer_id, service_invoices.customer_name, service_invoices.invoice_status, service_invoices.gross_amount, service_invoices.received_amount, service_invoices.invoice_date FROM service_invoices INNER JOIN customers ON service_invoices.customer_id = customers.customer_id WHERE customers.city = 'Pune'", "INNER JOIN customers", "WHERE customers.city = 'Pune'", "LIMIT 50", forbidden_sql=("SELECT *",), join_case=True),
    safe("G05", "G", "show customers with their service invoices", "joined_lookup", "SELECT customers.customer_id, customers.name, customers.city, service_invoices.invoice_id, service_invoices.invoice_code, service_invoices.customer_id, service_invoices.customer_name, service_invoices.invoice_status, service_invoices.gross_amount, service_invoices.received_amount, service_invoices.invoice_date FROM customers INNER JOIN service_invoices ON service_invoices.customer_id = customers.customer_id", "INNER JOIN service_invoices", "LIMIT 50", forbidden_sql=("SELECT *",), join_case=True),

    # H. Fail closed / safety
    blocked("H01", "show sum amount from service invoices"),
    blocked("H02", "show sum value from service invoices"),
    blocked("H03", "show service invoices where amount is paid"),
    blocked("H04", "show service invoices where unknown field is pending"),
    blocked("H05", "top 5 service invoices by amount"),
    blocked("H06", "show service invoices ordered by unknown field"),
    blocked("H07", "show sum gross amount and received amount by invoice status from service invoices"),
    blocked("H08", "show service invoices with unknown details"),
    blocked("H09", "show service invoices with customer details and product details"),
    blocked("H10", "show sum gross amount by customer city from service invoices"),
    blocked("H11", "delete service invoices", "blocked_unsafe"),
    blocked("H12", "update service invoices set gross amount = 0", "blocked_unsafe"),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--setup", action="store_true", help="Drop and recreate the deterministic test database.")
    parser.add_argument("--host", default=os.getenv("DB_HOST", "localhost"))
    parser.add_argument("--port", type=int, default=int(os.getenv("DB_PORT", "3306")))
    parser.add_argument("--user", default=os.getenv("DB_USER", ""))
    parser.add_argument("--database", default=os.getenv("SQLSENSE_TEST_DB", DEFAULT_DATABASE))
    return parser.parse_args()


def database_url(args: argparse.Namespace, *, include_database: bool) -> URL:
    password = os.getenv("DB_PASSWORD", "")
    if not args.user or not password:
        raise RuntimeError("DB_USER and DB_PASSWORD must be set in the environment.")
    if not DATABASE_NAME_RE.fullmatch(args.database):
        raise RuntimeError("SQLSENSE_TEST_DB must contain only letters, digits, and underscores.")
    return URL.create(
        "mysql+pymysql",
        username=args.user,
        password=password,
        host=args.host,
        port=args.port,
        database=args.database if include_database else None,
    )


def setup_database(args: argparse.Namespace) -> None:
    server_engine = create_engine(database_url(args, include_database=False), isolation_level="AUTOCOMMIT")
    try:
        with server_engine.connect() as connection:
            connection.execute(text(f"DROP DATABASE IF EXISTS `{args.database}`"))
            connection.execute(text(f"CREATE DATABASE `{args.database}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"))
    finally:
        server_engine.dispose()

    engine = create_engine(database_url(args, include_database=True))
    try:
        with engine.begin() as connection:
            connection.execute(text(
                "CREATE TABLE customers ("
                "customer_id INTEGER NOT NULL PRIMARY KEY, "
                "name VARCHAR(100) NOT NULL, "
                "city VARCHAR(100) NOT NULL"
                ") ENGINE=InnoDB"
            ))
            connection.execute(text(
                "CREATE TABLE service_invoices ("
                "invoice_id INTEGER NOT NULL PRIMARY KEY, "
                "invoice_code VARCHAR(20) NOT NULL UNIQUE, "
                "customer_id INTEGER NOT NULL, "
                "customer_name VARCHAR(100) NOT NULL, "
                "invoice_status VARCHAR(30) NOT NULL, "
                "gross_amount DECIMAL(12,2) NOT NULL, "
                "received_amount DECIMAL(12,2) NULL, "
                "invoice_date DATE NOT NULL, "
                "CONSTRAINT fk_service_invoices_customer FOREIGN KEY (customer_id) "
                "REFERENCES customers(customer_id)"
                ") ENGINE=InnoDB"
            ))
            connection.execute(
                text("INSERT INTO customers (customer_id, name, city) VALUES (:customer_id, :name, :city)"),
                [{"customer_id": row[0], "name": row[1], "city": row[2]} for row in CUSTOMERS],
            )
            connection.execute(
                text(
                    "INSERT INTO service_invoices "
                    "(invoice_id, invoice_code, customer_id, customer_name, invoice_status, "
                    "gross_amount, received_amount, invoice_date) VALUES "
                    "(:invoice_id, :invoice_code, :customer_id, :customer_name, :invoice_status, "
                    ":gross_amount, :received_amount, :invoice_date)"
                ),
                [
                    {
                        "invoice_id": row[0],
                        "invoice_code": row[1],
                        "customer_id": row[2],
                        "customer_name": row[3],
                        "invoice_status": row[4],
                        "gross_amount": Decimal(row[5]),
                        "received_amount": Decimal(row[6]) if row[6] is not None else None,
                        "invoice_date": date.fromisoformat(row[7]),
                    }
                    for row in INVOICES
                ],
            )
    finally:
        engine.dispose()


def assert_fixture(engine: Engine) -> None:
    schema = inspect(engine)
    if set(schema.get_table_names()) != {"customers", "service_invoices"}:
        raise RuntimeError("Test database schema does not match the controlled fixture; rerun with --setup.")
    with engine.connect() as connection:
        customers = connection.execute(text("SELECT customer_id, name, city FROM customers ORDER BY customer_id")).all()
        invoices = connection.execute(text(
            "SELECT invoice_id, invoice_code, customer_id, customer_name, invoice_status, "
            "gross_amount, received_amount, invoice_date FROM service_invoices ORDER BY invoice_id"
        )).all()
    normalized_customers = [(row[0], row[1], row[2]) for row in customers]
    normalized_invoices = [
        (
            row[0], row[1], row[2], row[3], row[4], f"{row[5]:.2f}",
            None if row[6] is None else f"{row[6]:.2f}", row[7].isoformat(),
        )
        for row in invoices
    ]
    if normalized_customers != CUSTOMERS or normalized_invoices != INVOICES:
        raise RuntimeError("Test database data does not match the controlled fixture; rerun with --setup.")


@contextmanager
def isolated_app(args: argparse.Namespace) -> Iterator[Any]:
    original_cwd = Path.cwd()
    isolated_env_names = (
        "CHROMA_INDEX_DIR",
        "VECTOR_INDEX_DIR",
        "CUDA_VISIBLE_DEVICES",
        "EMBEDDING_BACKEND",
    )
    old_env = {name: os.environ.get(name) for name in isolated_env_names}
    with tempfile.TemporaryDirectory(prefix="sqlsense_verify_", ignore_cleanup_errors=True) as temp_dir:
        temp_path = Path(temp_dir)
        os.environ["ANONYMIZED_TELEMETRY"] = "False"
        os.environ["CHROMA_ANONYMIZED_TELEMETRY"] = "False"
        os.environ["CHROMA_INDEX_DIR"] = str(temp_path / "chroma")
        os.environ["VECTOR_INDEX_DIR"] = str(temp_path / "vector")
        os.environ["CUDA_VISIBLE_DEVICES"] = ""
        os.environ["EMBEDDING_BACKEND"] = "deterministic"
        if str(PROJECT_ROOT) not in sys.path:
            sys.path.insert(0, str(PROJECT_ROOT))
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
                use_ai_enrichment=False,
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
            for name, value in old_env.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value


def normalize_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return ("decimal", format(value.quantize(Decimal("0.01")), "f"))
    if isinstance(value, date):
        return ("date", value.isoformat())
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def normalize_rows(rows: Sequence[Any], *, ordered: bool) -> Any:
    normalized = []
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


def normalize_sql(sql: str) -> str:
    return " ".join(str(sql or "").strip().rstrip(";").split())


def validate_join_contract(context: dict[str, Any], sql: str) -> str:
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
    if " AS " not in compact.upper():
        return "joined projection is not explicitly aliased"
    return ""


def run_case(app: Any, oracle_engine: Engine, case: VerificationCase) -> CaseResult:
    result = CaseResult(case=case)
    app.reset_conversation()
    payload = app.process_question(case.question)
    context = payload.get("query_context") or {}
    result.planner = context
    result.intent = context.get("intent") or {}
    result.actual_route = str(
        payload.get("route") or payload.get("route_used")
        or context.get("route_recommendation") or context.get("route") or ""
    )
    result.actual_shape = str(context.get("query_shape") or "")
    result.sql = payload.get("generated_sql") or payload.get("sql")
    result.validation = payload.get("validation_result") or {}
    result.error = str(payload.get("error") or "")

    if result.actual_route != case.expected_route:
        result.reason = f"expected route {case.expected_route}, got {result.actual_route}"
        return result

    if case.expected_route != EXPECTED_ROUTE:
        if result.sql:
            result.reason = "fail-closed question returned SQL"
            return result
        if result.executed:
            result.reason = "fail-closed question executed"
            return result
        result.passed = True
        return result

    if result.actual_shape != case.expected_shape:
        result.reason = f"expected query_shape {case.expected_shape}, got {result.actual_shape}"
        return result
    if not result.sql:
        result.reason = f"safe question returned no SQL: {result.error or payload.get('message')}"
        return result
    if result.validation.get("is_valid") is not True:
        result.reason = f"generated SQL did not validate: {result.validation.get('reason')}"
        return result

    normalized_sql = normalize_sql(result.sql)
    for required in case.required_sql:
        if required.lower() not in normalized_sql.lower():
            result.reason = f"generated SQL is missing required pattern: {required}"
            return result
    for forbidden in case.forbidden_sql:
        if forbidden.lower() in normalized_sql.lower():
            result.reason = f"generated SQL contains forbidden pattern: {forbidden}"
            return result
    if case.join_case:
        join_error = validate_join_contract(context, result.sql)
        if join_error:
            result.reason = join_error
            return result

    result.expected_result = run_oracle(oracle_engine, case)
    executed, message, rows = app.execute_sql(result.sql, revalidate=True)
    result.executed = bool(executed)
    if not executed:
        result.reason = f"validated SQL did not execute: {message}"
        return result
    result.actual_result = normalize_rows(list(rows or []), ordered=case.ordered)
    if result.actual_result != result.expected_result:
        result.reason = "execution result differs from direct SQL oracle"
        return result
    result.passed = True
    return result


class NoConnectEngine:
    def __init__(self) -> None:
        self.connect_calls = 0

    def connect(self) -> Any:
        self.connect_calls += 1
        raise AssertionError("invalid SQL reached engine.connect()")


def run_executor_guards(knowledge_base: dict[str, Any]) -> list[tuple[str, bool, str]]:
    from sql_pipeline.query_executor import execute_query

    guards = [
        ("unsafe_write", "DELETE FROM service_invoices"),
        (
            "invalid_grouped_projection",
            "SELECT invoice_status, gross_amount FROM service_invoices GROUP BY invoice_status;",
        ),
        (
            "non_graph_join",
            "SELECT service_invoices.invoice_id, customers.name FROM service_invoices "
            "INNER JOIN customers ON service_invoices.invoice_id = customers.customer_id;",
        ),
    ]
    results = []
    for guard_name, sql in guards:
        engine = NoConnectEngine()
        rejected = False
        reason = ""
        try:
            execute_query(sql, engine, knowledge_base=knowledge_base)
        except (ValueError, RuntimeError) as exc:
            rejected = True
            reason = str(exc)
        except AssertionError as exc:
            reason = str(exc)
        passed = rejected and engine.connect_calls == 0
        if not reason:
            reason = "invalid SQL was not rejected"
        results.append((guard_name, passed, reason))
    return results


def print_results(results: Sequence[CaseResult], guards: Sequence[tuple[str, bool, str]]) -> None:
    print("\nSQLSense Complete Deterministic Runtime Verification")
    print("=" * 132)
    print(f"{'ID':<5} {'RESULT':<7} {'EXPECTED ROUTE':<28} {'ACTUAL ROUTE':<28} {'EXEC':<5} QUESTION")
    print("-" * 132)
    for result in results:
        status = "PASS" if result.passed else "FAIL"
        executed = "yes" if result.executed else "no"
        print(
            f"{result.case.case_id:<5} {status:<7} {result.case.expected_route:<28} "
            f"{result.actual_route:<28} {executed:<5} {result.case.question}"
        )
    for name, passed, _ in guards:
        print(f"X-{name:<20} {'PASS' if passed else 'FAIL':<7} {'pre-connection rejection':<28}")

    failures = [result for result in results if not result.passed]
    guard_failures = [guard for guard in guards if not guard[1]]
    if failures or guard_failures:
        print("\nFailure diagnostics")
        print("=" * 132)
        print("Failed question IDs: " + ", ".join(result.case.case_id for result in failures))

    def clipped(value: Any, limit: int = 1600) -> str:
        rendered = repr(value)
        return rendered if len(rendered) <= limit else rendered[:limit] + "...<truncated>"

    for result in failures:
        print(f"\n[{result.case.case_id}] {result.case.question}")
        print(f"Reason: {result.reason}")
        print(f"Expected route/shape: {result.case.expected_route} / {result.case.expected_shape or '-'}")
        print(f"Actual route/shape: {result.actual_route} / {result.actual_shape or '-'}")
        print(f"Expected SQL patterns: {list(result.case.required_sql)}")
        print(f"Actual SQL: {result.sql}")
        print(f"Expected result: {clipped(result.expected_result)}")
        print(f"Actual result: {clipped(result.actual_result)}")
        print(f"Executed: {result.executed}")
        print(f"Validation: {result.validation}")
        print(f"Error: {result.error}")
        print(f"Intent: {clipped(result.intent)}")
        print(f"Selected evidence: {clipped(result.planner.get('selected_evidence'))}")
        print(f"Clause plan: {clipped(result.planner.get('clause_plan'))}")
    for name, _, reason in guard_failures:
        print(f"\n[X-{name}] {reason}")

    question_passed = sum(result.passed for result in results)
    total_checks = len(results) + len(guards)
    passed_checks = question_passed + sum(guard[1] for guard in guards)
    print("\nSummary")
    print("=" * 132)
    print(f"Question cases: {len(results)}")
    print(f"Executor guards: {len(guards)}")
    print(f"Total checks: {total_checks}")
    print(f"Passed: {passed_checks}")
    print(f"Failed: {total_checks - passed_checks}")
    print("Skipped: 0")


def main() -> int:
    args = parse_args()
    if len(CASES) != 53:
        print(f"Internal verifier error: expected 53 question cases, found {len(CASES)}")
        return 1
    oracle_engine = None
    try:
        if args.setup:
            setup_database(args)
            print(f"Fresh deterministic database created: {args.database}")
        oracle_engine = create_engine(database_url(args, include_database=True))
        assert_fixture(oracle_engine)
        with isolated_app(args) as app:
            results = [run_case(app, oracle_engine, case) for case in CASES]
            knowledge_base = app.database_service.get_knowledge_base() or {}
            guards = run_executor_guards(knowledge_base)
        print_results(results, guards)
        return 0 if all(result.passed for result in results) and all(item[1] for item in guards) else 1
    except Exception as exc:
        print("\nSQLSense verification could not start.")
        print(f"Reason: {exc}")
        print(f"Question cases: {len(CASES)}")
        print("Passed: 0")
        print(f"Failed: {len(CASES)}")
        print("Skipped: 0")
        return 1
    finally:
        if oracle_engine is not None:
            oracle_engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
