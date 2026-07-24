"""Run SQLSense end-to-end wiring verification against a real MySQL lab."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
import tempfile
import time
from typing import Any, Iterator

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import URL


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SQL_FILE = ROOT / "tests" / "fixtures" / "sqlsense_current_scope_business_lab.sql"
DEFAULT_QUESTIONS_FILE = ROOT / "tests" / "fixtures" / "sqlsense_current_scope_business_test_cases_no_answers.txt"
DEFAULT_REPORT_DIR = ROOT / "reports" / "e2e_wiring"
DEFAULT_DB_NAME = "sqlsense_current_scope_business_lab"
SAFE_REJECTION_ROUTES = {"cannot_plan_safely", "blocked_unsafe", "clarification_required"}
SECRET_KEYS = {"password", "token", "authorization", "cookie", "api_key", "connection_url"}
DB_NAME_RE = re.compile(r"^[A-Za-z0-9_]+$")


@dataclass
class QuestionCase:
    test_id: str
    question: str
    expected_category: str


@dataclass
class QuestionResult:
    test_id: str
    question: str
    expected_category: str
    actual_route: str = ""
    query_shape: str = ""
    executable: bool = False
    generated_sql: str = ""
    validation_status: str = ""
    execution_status: str = ""
    row_count: int = 0
    fail_closed_reason_code: str = ""
    error_message: str = ""
    duration_ms: int = 0
    selected_join_path_length: int = 0
    grain_decision: dict[str, Any] = field(default_factory=dict)
    passed: bool = False
    failed_stage: str = ""


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for key, item in value.items():
            if any(secret in str(key).lower() for secret in SECRET_KEYS):
                cleaned[key] = "<redacted>"
            else:
                cleaned[key] = redact(item)
        return cleaned
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, str):
        return re.sub(r"(://[^:/\s]+:)[^@\s]+@", r"\1<redacted>@", value)
    return value


def parse_question_file(path: Path) -> list[QuestionCase]:
    text = path.read_text(encoding="utf-8")
    cases: list[QuestionCase] = []
    category = "supported"
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        heading = line.lower().strip("#:- ")
        if any(word in heading for word in ("fail closed", "fail-closed", "unsafe", "unsupported", "no sql")):
            category = "fail_closed"
            continue
        match = re.match(r"^(?:q(?:uestion)?\s*)?(\d+)[\).:\-]\s*(.+)$", line, re.IGNORECASE)
        if not match:
            continue
        cases.append(
            QuestionCase(
                test_id=match.group(1),
                question=match.group(2).strip(),
                expected_category=category,
            )
        )
    return cases


def split_sql_statements(sql_text: str) -> list[str]:
    statements: list[str] = []
    current: list[str] = []
    quote = ""
    escape = False
    for char in sql_text:
        current.append(char)
        if quote:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == quote:
                quote = ""
            continue
        if char in {"'", '"', "`"}:
            quote = char
        elif char == ";":
            statement = "".join(current).strip()
            if statement:
                statements.append(statement)
            current = []
    tail = "".join(current).strip()
    if tail:
        statements.append(tail)
    return statements


def mysql_url(host: str, port: int, user: str, password: str, database: str | None = None) -> URL:
    return URL.create(
        "mysql+pymysql",
        username=user,
        password=password,
        host=host,
        port=port,
        database=database,
    )


def create_database_from_sql(sql_file: Path, *, host: str, port: int, user: str, password: str, database: str) -> dict[str, Any]:
    if not sql_file.exists():
        raise RuntimeError(f"SQL fixture not found: {sql_file}")
    if not DB_NAME_RE.fullmatch(database):
        raise RuntimeError("Database name must contain only letters, digits, and underscores.")
    admin_engine = create_engine(mysql_url(host, port, user, password))
    try:
        with admin_engine.begin() as connection:
            connection.execute(text(f"DROP DATABASE IF EXISTS `{database}`"))
            connection.execute(text(f"CREATE DATABASE `{database}`"))
    finally:
        admin_engine.dispose()

    engine = create_engine(mysql_url(host, port, user, password, database))
    try:
        for statement in split_sql_statements(sql_file.read_text(encoding="utf-8")):
            stripped = statement.strip().rstrip(";").strip()
            if stripped and not stripped.startswith("--"):
                with engine.begin() as connection:
                    connection.execute(text(stripped))
        return inspect_database(engine)
    finally:
        engine.dispose()


def inspect_database(engine) -> dict[str, Any]:
    inspector = inspect(engine)
    tables = sorted(inspector.get_table_names())
    row_counts: dict[str, int] = {}
    with engine.connect() as connection:
        version = str(connection.execute(text("SELECT VERSION()")).scalar() or "")
        for table_name in tables:
            row_counts[table_name] = int(connection.execute(text(f"SELECT COUNT(*) FROM `{table_name}`")).scalar() or 0)
    return {"mysql_version": version, "table_count": len(tables), "row_counts": row_counts}


@contextmanager
def isolated_app() -> Iterator[Any]:
    original_cwd = Path.cwd()
    original_path = list(sys.path)
    old_env = {name: os.environ.get(name) for name in ("CHROMA_INDEX_DIR", "VECTOR_INDEX_DIR", "EMBEDDING_BACKEND")}
    with tempfile.TemporaryDirectory(prefix="sqlsense_e2e_wiring_", ignore_cleanup_errors=True) as temp_dir:
        temp = Path(temp_dir)
        os.environ["CHROMA_INDEX_DIR"] = str(temp / "chroma")
        os.environ["VECTOR_INDEX_DIR"] = str(temp / "vector")
        os.environ.setdefault("EMBEDDING_BACKEND", "deterministic")
        sys.path.insert(0, str(ROOT))
        os.chdir(temp)
        app = None
        try:
            from core.app_service import AppService

            app = AppService()
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


def route_from(result: dict[str, Any], context: dict[str, Any]) -> str:
    return str(result.get("route") or result.get("route_used") or context.get("route_used") or context.get("route") or "")


def path_length(context: dict[str, Any]) -> int:
    path = context.get("selected_join_path")
    edges = path.get("edges") if isinstance(path, dict) else []
    return len([edge for edge in edges or [] if isinstance(edge, dict)])


def run_question(app: Any, case: QuestionCase) -> QuestionResult:
    started = time.perf_counter()
    output = QuestionResult(test_id=case.test_id, question=case.question, expected_category=case.expected_category)
    try:
        result = app.process_question(case.question)
        context = result.get("query_context") or app.get_last_query_context() or {}
        sql = str(result.get("sql") or result.get("generated_sql") or "")
        route = route_from(result, context)
        output.actual_route = route
        output.query_shape = str(context.get("query_shape") or "")
        output.generated_sql = sql
        output.executable = bool(sql and result.get("success"))
        output.validation_status = "passed" if (result.get("validation_result") or {}).get("is_valid") else "rejected"
        output.selected_join_path_length = path_length(context)
        output.grain_decision = dict(context.get("phase8a_grain_analysis") or context.get("grain_decision") or {})
        output.fail_closed_reason_code = str(context.get("reason_code") or context.get("route_reason") or result.get("message") or "")

        if case.expected_category == "fail_closed":
            output.passed = not sql and route in SAFE_REJECTION_ROUTES
            output.execution_status = "not_executed" if not sql else "unexpected_sql"
            output.failed_stage = "" if output.passed else "planner"
            return output

        if not sql:
            output.error_message = str(result.get("error") or result.get("message") or "no SQL generated")
            output.execution_status = "not_executed"
            output.failed_stage = "generator"
            return output

        success, message, rows = app.execute_sql(sql, revalidate=True)
        output.execution_status = "passed" if success else "failed"
        output.row_count = len(rows or [])
        output.error_message = "" if success else str(message)
        output.passed = bool(success)
        output.failed_stage = "" if success else "executor"
        return output
    except Exception as exc:
        output.error_message = str(exc)
        output.failed_stage = output.failed_stage or "runtime"
        return output
    finally:
        output.duration_ms = int((time.perf_counter() - started) * 1000)


def git_info() -> dict[str, str]:
    def run_git(*args: str) -> str:
        try:
            return subprocess.check_output(["git", *args], cwd=ROOT, text=True, stderr=subprocess.DEVNULL).strip()
        except Exception:
            return ""

    return {"branch": run_git("branch", "--show-current"), "commit": run_git("rev-parse", "--short", "HEAD")}


def summarize(report: dict[str, Any]) -> dict[str, int]:
    questions = report.get("questions") or []
    supported = [item for item in questions if item.get("expected_category") == "supported"]
    fail_closed = [item for item in questions if item.get("expected_category") == "fail_closed"]
    return {
        "total": len(questions),
        "supported_passed": sum(1 for item in supported if item.get("passed")),
        "supported_failed": sum(1 for item in supported if not item.get("passed")),
        "fail_closed_passed": sum(1 for item in fail_closed if item.get("passed")),
        "fail_closed_failed": sum(1 for item in fail_closed if not item.get("passed")),
    }


def write_reports(report: dict[str, Any], report_dir: Path) -> tuple[Path, Path]:
    report_dir.mkdir(parents=True, exist_ok=True)
    clean = redact(report)
    json_path = report_dir / "e2e_wiring_report.json"
    md_path = report_dir / "e2e_wiring_report.md"
    json_path.write_text(json.dumps(clean, indent=2, sort_keys=True, default=str), encoding="utf-8")
    summary = summarize(clean)
    lines = [
        "# SQLSense E2E Wiring Report",
        "",
        f"- Run timestamp: {clean.get('run_timestamp', '')}",
        f"- Git: {clean.get('git', {}).get('branch', '')} {clean.get('git', {}).get('commit', '')}",
        f"- Database: {clean.get('database', {}).get('name', '')}",
        f"- Table count: {clean.get('database', {}).get('table_count', 0)}",
        f"- KB build: {clean.get('kb_build', {}).get('status', '')}",
        f"- AI semantic enrichment: {clean.get('kb_build', {}).get('ai_enrichment_status', '')}",
        f"- Total questions: {summary['total']}",
        f"- Supported: {summary['supported_passed']} passed / {summary['supported_failed']} failed",
        f"- Fail-closed: {summary['fail_closed_passed']} passed / {summary['fail_closed_failed']} failed",
        "",
        "## Row Counts",
    ]
    for table_name, count in sorted((clean.get("database", {}).get("row_counts") or {}).items()):
        lines.append(f"- `{table_name}`: {count}")
    lines.extend(["", "## Questions", ""])
    for item in clean.get("questions") or []:
        status = "PASS" if item.get("passed") else "FAIL"
        lines.append(f"### {item.get('test_id')} - {status}")
        lines.append(f"- Question: {item.get('question')}")
        lines.append(f"- Expected: {item.get('expected_category')}")
        lines.append(f"- Route/shape: {item.get('actual_route')} / {item.get('query_shape')}")
        lines.append(f"- Validation/execution: {item.get('validation_status')} / {item.get('execution_status')}")
        lines.append(f"- Rows: {item.get('row_count', 0)}")
        if item.get("generated_sql"):
            lines.append(f"- SQL: `{item.get('generated_sql')}`")
        if item.get("error_message"):
            lines.append(f"- Error: {item.get('error_message')}")
        lines.append("")
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return md_path, json_path


def build_report(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    report: dict[str, Any] = {
        "run_timestamp": datetime.now(timezone.utc).isoformat(),
        "git": git_info(),
        "python_version": platform.python_version(),
        "database": {"name": args.database},
        "kb_build": {"status": "not_started"},
        "questions": [],
        "setup_error": "",
    }
    try:
        cases = parse_question_file(Path(args.questions_file))
        if not cases:
            raise RuntimeError(f"No questions parsed from {args.questions_file}")
        db_info = create_database_from_sql(
            Path(args.sql_file),
            host=args.host,
            port=args.port,
            user=args.user,
            password=args.password,
            database=args.database,
        )
        report["database"].update(db_info)
        with isolated_app() as app:
            ok, message, prepare = app.connect_database_and_prepare(
                db_type="mysql",
                host=args.host,
                port=args.port,
                username=args.user,
                password=args.password,
                database=args.database,
                use_ai_enrichment=not args.disable_ai_enrichment,
            )
            metadata = dict(app.database_service.knowledge_base_metadata or {})
            report["kb_build"] = {
                "status": "passed" if ok else "failed",
                "message": message,
                "prepare": redact(prepare),
                "artifact_fingerprints": {
                    "schema_fingerprint": metadata.get("schema_fingerprint") or metadata.get("schema_hash") or "",
                    "kb_fingerprint": metadata.get("kb_fingerprint") or metadata.get("knowledge_base_hash") or "",
                    "graph_fingerprint": metadata.get("graph_fingerprint") or "",
                },
                "ai_enrichment_status": prepare.get("ai_enrichment_status", ""),
                "ai_enrichment_message": prepare.get("ai_enrichment_message", ""),
            }
            if not ok:
                raise RuntimeError(message)
            report["questions"] = [asdict(run_question(app, case)) for case in cases]
    except Exception as exc:
        report["setup_error"] = str(exc)
        return report, 1

    summary = summarize(report)
    failed = summary["supported_failed"] + summary["fail_closed_failed"]
    return report, 1 if failed else 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sql-file", default=os.getenv("SQLSENSE_E2E_SQL_FILE", str(DEFAULT_SQL_FILE)))
    parser.add_argument("--questions-file", default=os.getenv("SQLSENSE_E2E_QUESTIONS_FILE", str(DEFAULT_QUESTIONS_FILE)))
    parser.add_argument("--report-dir", default=str(DEFAULT_REPORT_DIR))
    parser.add_argument("--host", default=os.getenv("SQLSENSE_TEST_DB_HOST", "localhost"))
    parser.add_argument("--port", type=int, default=int(os.getenv("SQLSENSE_TEST_DB_PORT", "3306")))
    parser.add_argument("--user", default=os.getenv("SQLSENSE_TEST_DB_USER", os.getenv("DB_USER", "root")))
    parser.add_argument("--password", default=os.getenv("SQLSENSE_TEST_DB_PASSWORD", os.getenv("DB_PASSWORD", "")))
    parser.add_argument("--database", default=os.getenv("SQLSENSE_TEST_DB_NAME", DEFAULT_DB_NAME))
    parser.add_argument("--disable-ai-enrichment", action="store_true")
    parser.add_argument("--report-only", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report, exit_code = build_report(args)
    md_path, json_path = write_reports(report, Path(args.report_dir))
    print(f"Markdown report: {md_path}")
    print(f"JSON report: {json_path}")
    if report.get("setup_error"):
        print(f"Setup error: {report['setup_error']}")
    return 0 if args.report_only else exit_code


if __name__ == "__main__":
    raise SystemExit(main())
