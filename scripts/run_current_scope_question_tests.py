"""Run the current-scope SQLSense business lab with expected answers."""

from __future__ import annotations

import argparse
from collections import Counter
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
import json
import os
from pathlib import Path
import platform
import re
import sys
import tempfile
import time
from typing import Any, Iterator

from sqlalchemy import create_engine, inspect, text

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_e2e_wiring_verification import git_info, mysql_url, path_length, redact, route_from, split_sql_statements


DEFAULT_SQL_FILE = ROOT / "tests" / "fixtures" / "sqlsense_current_scope_business_lab.sql"
DEFAULT_QUESTIONS_FILE = ROOT / "tests" / "fixtures" / "sqlsense_current_scope_business_test_cases_with_answers.txt"
DEFAULT_REPORT_DIR = ROOT / "reports" / "e2e_current_scope"
DEFAULT_DB_NAME = "sqlsense_current_scope_business_lab"
REPORT_MD = "current_scope_question_report.md"
REPORT_JSON = "current_scope_question_report.json"
SAFE_REJECTION_ROUTES = {"cannot_plan_safely", "blocked_unsafe", "clarification_required"}
DB_NAME_RE = re.compile(r"^[A-Za-z0-9_]+$")
CASE_RE = re.compile(r"^TEST\s+(\d+)\s*$", re.IGNORECASE)


@dataclass
class ExpectedCase:
    test_id: str
    question: str
    expected_category: str
    expected_behavior: str = ""
    expected_sql: str = ""
    expected_reason: str = ""


@dataclass
class QuestionRunResult:
    test_id: str
    question: str
    expected_category: str
    actual_route: str = ""
    query_shape: str = ""
    executable: bool = False
    generated_sql: str = ""
    expected_sql: str = ""
    sql_validation_status: str = ""
    execution_status: str = ""
    expected_result_row_count: int = 0
    actual_result_row_count: int = 0
    result_comparison_status: str = ""
    selected_join_path_length: int = 0
    grain_decision: dict[str, Any] = field(default_factory=dict)
    reason_code: str = ""
    failed_stage: str = ""
    error_summary: str = ""
    duration_ms: int = 0
    passed: bool = False


def parse_answer_file(path: Path) -> list[ExpectedCase]:
    lines = path.read_text(encoding="utf-8").splitlines()
    cases: list[ExpectedCase] = []
    current: dict[str, Any] | None = None
    collecting_sql = False
    sql_lines: list[str] = []
    section_category = "supported"

    def flush() -> None:
        nonlocal current, collecting_sql, sql_lines
        if not current:
            return
        expected_sql = "\n".join(sql_lines).strip()
        behavior = str(current.get("expected_behavior") or "")
        category = str(current.get("expected_category") or section_category)
        if "fail" in behavior.lower() or expected_sql.lower() in {"none", "no sql"}:
            category = "fail_closed"
        cases.append(
            ExpectedCase(
                test_id=str(current["test_id"]).zfill(2),
                question=str(current.get("question") or "").strip(),
                expected_category=category,
                expected_behavior=behavior,
                expected_sql="" if category == "fail_closed" else expected_sql,
                expected_reason=str(current.get("expected_reason") or ""),
            )
        )
        current = None
        collecting_sql = False
        sql_lines = []

    for raw_line in lines:
        line = raw_line.rstrip()
        stripped = line.strip()
        heading = stripped.lower().strip("#:- ")
        if current is None and ("fail-closed" in heading or "fail closed" in heading):
            flush()
            section_category = "fail_closed"
            continue
        if current is None and "supported current-scope" in heading:
            flush()
            section_category = "supported"
            continue
        match = CASE_RE.match(stripped)
        if match:
            flush()
            current = {"test_id": match.group(1), "expected_category": section_category}
            continue
        if current is None:
            continue
        if stripped.startswith("-") and set(stripped) <= {"-"}:
            flush()
            continue
        if stripped.lower().startswith("question:"):
            current["question"] = stripped.split(":", 1)[1].strip()
            collecting_sql = False
        elif stripped.lower().startswith("expected behavior:"):
            current["expected_behavior"] = stripped.split(":", 1)[1].strip()
            collecting_sql = False
        elif stripped.lower().startswith("expected reason:"):
            current["expected_reason"] = stripped.split(":", 1)[1].strip()
            collecting_sql = False
        elif stripped.lower().startswith("expected sql:"):
            collecting_sql = True
            tail = stripped.split(":", 1)[1].strip()
            if tail:
                sql_lines.append(tail)
        elif stripped.lower().startswith(("should execute:", "expected note:")):
            collecting_sql = False
        elif collecting_sql:
            sql_lines.append(line)
    flush()
    return [case for case in cases if case.question]


def normalize_test_id(value: str) -> str:
    match = re.search(r"(\d+)", value or "")
    return match.group(1).zfill(2) if match else value


def rewrite_fixture_database(sql_text: str, database: str) -> str:
    if database == DEFAULT_DB_NAME:
        return sql_text
    return re.sub(rf"(?i)\b{re.escape(DEFAULT_DB_NAME)}\b", database, sql_text)


def create_database_from_fixture(sql_file: Path, *, host: str, port: int, user: str, password: str, database: str) -> dict[str, Any]:
    if not sql_file.exists():
        raise RuntimeError(f"SQL fixture not found: {sql_file}")
    if not DB_NAME_RE.fullmatch(database):
        raise RuntimeError("Database name must contain only letters, digits, and underscores.")

    sql_text = rewrite_fixture_database(sql_file.read_text(encoding="utf-8"), database)
    statements = [statement.strip().rstrip(";").strip() for statement in split_sql_statements(sql_text)]
    admin_engine = create_engine(mysql_url(host, port, user, password))
    try:
        with admin_engine.begin() as connection:
            connection.execute(text(f"DROP DATABASE IF EXISTS `{database}`"))
            for statement in statements:
                if statement and not statement.startswith("--"):
                    connection.execute(text(statement))
    finally:
        admin_engine.dispose()

    engine = create_engine(mysql_url(host, port, user, password, database))
    try:
        return inspect_database_state(engine)
    finally:
        engine.dispose()


def inspect_database_state(engine) -> dict[str, Any]:
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
    old_env = {name: os.environ.get(name) for name in ("CHROMA_INDEX_DIR", "VECTOR_INDEX_DIR")}
    with tempfile.TemporaryDirectory(prefix="sqlsense_current_scope_", ignore_cleanup_errors=True) as temp_dir:
        temp = Path(temp_dir)
        os.environ["CHROMA_INDEX_DIR"] = str(temp / "chroma")
        os.environ["VECTOR_INDEX_DIR"] = str(temp / "vector")
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


def scalar_value(value: Any) -> str:
    if isinstance(value, Decimal):
        return format(value.normalize(), "f")
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return "" if value is None else str(value)


def row_tuple(row: Any) -> tuple[str, ...]:
    if isinstance(row, dict):
        values = row.values()
    elif hasattr(row, "_mapping"):
        values = row._mapping.values()
    else:
        values = row
    return tuple(scalar_value(value) for value in values)


def unordered_row_tuple(row: Any) -> tuple[str, ...]:
    return tuple(sorted(row_tuple(row)))


def compare_rows(actual: list[Any], expected: list[Any], ordered: bool) -> str:
    if len(actual) != len(expected):
        return "row_count_mismatch"
    actual_rows = [row_tuple(row) for row in actual]
    expected_rows = [row_tuple(row) for row in expected]
    if ordered and actual_rows == expected_rows:
        return "match"
    if Counter(actual_rows) == Counter(expected_rows):
        return "match"
    if Counter(unordered_row_tuple(row) for row in actual) == Counter(unordered_row_tuple(row) for row in expected):
        return "match_unordered_columns"
    return "row_value_mismatch"


def expected_sql_is_ordered(sql: str) -> bool:
    return bool(re.search(r"\border\s+by\b", sql or "", re.IGNORECASE))


def execute_sql_rows(engine, sql: str) -> list[Any]:
    with engine.connect() as connection:
        result = connection.execute(text(sql))
        return list(result.fetchall())


def validation_status(result: dict[str, Any]) -> str:
    validation = result.get("validation_result") or {}
    if validation.get("is_valid") is True or validation.get("valid") is True:
        return "passed"
    if validation:
        return "rejected"
    return ""


def run_question(app: Any, expected_engine, case: ExpectedCase) -> QuestionRunResult:
    started = time.perf_counter()
    output = QuestionRunResult(
        test_id=case.test_id,
        question=case.question,
        expected_category=case.expected_category,
        expected_sql=case.expected_sql,
    )
    try:
        result = app.process_question(case.question)
        context = result.get("query_context") or app.get_last_query_context() or {}
        sql = str(result.get("sql") or result.get("generated_sql") or "")
        route = route_from(result, context)
        output.actual_route = route
        output.query_shape = str(context.get("query_shape") or result.get("query_shape") or "")
        output.generated_sql = sql
        output.executable = bool(sql and result.get("success"))
        output.sql_validation_status = validation_status(result)
        output.selected_join_path_length = path_length(context)
        output.grain_decision = dict(context.get("phase8a_grain_analysis") or context.get("grain_decision") or {})
        output.reason_code = str(context.get("reason_code") or context.get("route_reason") or result.get("message") or "")

        if case.expected_category == "fail_closed":
            output.execution_status = "not_executed" if not sql else "unexpected_sql"
            output.result_comparison_status = "blocked" if not sql else "unexpected_sql"
            output.passed = not sql and (not route or route in SAFE_REJECTION_ROUTES)
            output.failed_stage = "" if output.passed else "planner"
            return output

        if not sql:
            output.execution_status = "not_executed"
            output.result_comparison_status = "no_actual_sql"
            output.error_summary = str(result.get("error") or result.get("message") or "no SQL generated")
            output.failed_stage = "generator"
            return output

        success, message, actual_rows = app.execute_sql(sql, revalidate=True)
        output.execution_status = "passed" if success else "failed"
        output.actual_result_row_count = len(actual_rows or [])
        if not success:
            output.error_summary = str(message)
            output.result_comparison_status = "actual_execution_failed"
            output.failed_stage = "executor"
            return output

        actual_compare_rows = execute_sql_rows(expected_engine, sql)
        expected_rows = execute_sql_rows(expected_engine, case.expected_sql)
        output.expected_result_row_count = len(expected_rows)
        output.result_comparison_status = compare_rows(actual_compare_rows, expected_rows, expected_sql_is_ordered(case.expected_sql))
        output.passed = output.result_comparison_status in {"match", "match_unordered_columns"}
        output.failed_stage = "" if output.passed else "comparison"
        return output
    except Exception as exc:
        output.error_summary = str(exc)
        output.failed_stage = output.failed_stage or "runtime"
        return output
    finally:
        output.duration_ms = int((time.perf_counter() - started) * 1000)


def build_kb(app: Any, args: argparse.Namespace) -> dict[str, Any]:
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
    status = "passed" if ok else "failed"
    return {
        "status": status,
        "message": message,
        "prepare": redact(prepare),
        "schema_extraction": "completed" if ok else "failed",
        "profiling": "completed" if ok else "failed",
        "schema_facts": "generated" if ok else "failed",
        "business_glossary": "generated" if ok else "failed",
        "relationship_graph": "generated" if ok else "failed",
        "vector_artifacts": "generated" if ok else "failed",
        "artifact_fingerprints": {
            "schema_fingerprint": metadata.get("schema_fingerprint") or metadata.get("schema_hash") or "",
            "kb_fingerprint": metadata.get("kb_fingerprint") or metadata.get("knowledge_base_hash") or "",
            "graph_fingerprint": metadata.get("graph_fingerprint") or "",
            "glossary_fingerprint": metadata.get("glossary_fingerprint") or "",
            "vector_fingerprint": metadata.get("vector_fingerprint") or "",
        },
        "ai_provider_attempts": prepare.get("ai_provider_attempts") or prepare.get("provider_attempts") or [],
        "ai_enrichment_status": prepare.get("ai_enrichment_status") or prepare.get("semantic_enrichment_status") or "",
        "ai_fallback_used": bool(prepare.get("fallback_used") or prepare.get("ai_fallback_used")),
    }


def summarize(report: dict[str, Any]) -> dict[str, int]:
    questions = report.get("questions") or []
    supported = [item for item in questions if item.get("expected_category") == "supported"]
    fail_closed = [item for item in questions if item.get("expected_category") == "fail_closed"]
    return {
        "total": len(questions),
        "supported_total": len(supported),
        "fail_closed_total": len(fail_closed),
        "supported_passed": sum(1 for item in supported if item.get("passed")),
        "supported_failed": sum(1 for item in supported if not item.get("passed")),
        "fail_closed_passed": sum(1 for item in fail_closed if item.get("passed")),
        "fail_closed_failed": sum(1 for item in fail_closed if not item.get("passed")),
        "total_passed": sum(1 for item in questions if item.get("passed")),
        "total_failed": sum(1 for item in questions if not item.get("passed")),
    }


def write_reports(report: dict[str, Any], report_dir: Path) -> tuple[Path, Path]:
    report_dir.mkdir(parents=True, exist_ok=True)
    clean = redact(report)
    summary = summarize(clean)
    clean["summary"] = summary
    clean["final_verdict"] = "passed" if summary["total_failed"] == 0 and not clean.get("setup_error") else "failed"

    json_path = report_dir / REPORT_JSON
    md_path = report_dir / REPORT_MD
    json_path.write_text(json.dumps(clean, indent=2, sort_keys=True, default=str), encoding="utf-8")

    lines = [
        "# SQLSense Current-Scope Question Report",
        "",
        f"- Run timestamp: {clean.get('run_timestamp', '')}",
        f"- Git: {clean.get('git', {}).get('branch', '')} {clean.get('git', {}).get('commit', '')}",
        f"- Python: {clean.get('python_version', '')}",
        f"- MySQL: {clean.get('database', {}).get('mysql_version', '')}",
        f"- Database: {clean.get('database', {}).get('name', '')}",
        f"- Table count: {clean.get('database', {}).get('table_count', 0)}",
        f"- KB build: {clean.get('kb_build', {}).get('status', '')}",
        f"- AI semantic enrichment: {clean.get('kb_build', {}).get('ai_enrichment_status', '')}",
        f"- AI fallback used: {clean.get('kb_build', {}).get('ai_fallback_used', False)}",
        f"- Total tests: {summary['total']}",
        f"- Supported: {summary['supported_passed']} passed / {summary['supported_failed']} failed",
        f"- Fail-closed: {summary['fail_closed_passed']} passed / {summary['fail_closed_failed']} failed",
        f"- Final verdict: {clean['final_verdict']}",
        "",
        "## Artifact Fingerprints",
    ]
    for key, value in sorted((clean.get("kb_build", {}).get("artifact_fingerprints") or {}).items()):
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Row Counts"])
    for table_name, count in sorted((clean.get("database", {}).get("row_counts") or {}).items()):
        lines.append(f"- `{table_name}`: {count}")
    lines.extend(["", "## Questions", ""])
    for item in clean.get("questions") or []:
        status = "PASS" if item.get("passed") else "FAIL"
        lines.append(f"### TEST {item.get('test_id')} - {status}")
        lines.append(f"- Question: {item.get('question')}")
        lines.append(f"- Expected category: {item.get('expected_category')}")
        lines.append(f"- Route/shape: {item.get('actual_route')} / {item.get('query_shape')}")
        lines.append(f"- Executable: {item.get('executable')}")
        lines.append(f"- Validation/execution: {item.get('sql_validation_status')} / {item.get('execution_status')}")
        lines.append(f"- Expected/actual rows: {item.get('expected_result_row_count')} / {item.get('actual_result_row_count')}")
        lines.append(f"- Result comparison: {item.get('result_comparison_status')}")
        lines.append(f"- Join path length: {item.get('selected_join_path_length')}")
        if item.get("reason_code"):
            lines.append(f"- Reason code: {item.get('reason_code')}")
        if item.get("failed_stage"):
            lines.append(f"- Failed stage: {item.get('failed_stage')}")
        if item.get("error_summary"):
            lines.append(f"- Error summary: {item.get('error_summary')}")
        if item.get("generated_sql"):
            lines.append("")
            lines.append("Generated SQL:")
            lines.append("```sql")
            lines.append(str(item.get("generated_sql")))
            lines.append("```")
        if item.get("expected_sql"):
            lines.append("")
            lines.append("Expected SQL:")
            lines.append("```sql")
            lines.append(str(item.get("expected_sql")))
            lines.append("```")
        lines.append("")
    if clean.get("setup_error"):
        lines.extend(["## Setup Error", "", str(clean["setup_error"])])
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return md_path, json_path


def selected_cases(cases: list[ExpectedCase], args: argparse.Namespace) -> list[ExpectedCase]:
    output = cases
    if args.test_id:
        wanted = normalize_test_id(args.test_id)
        output = [case for case in output if case.test_id == wanted]
    if args.limit:
        output = output[: args.limit]
    return output


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
        cases = selected_cases(parse_answer_file(Path(args.questions_file)), args)
        if not cases and not args.setup_only:
            raise RuntimeError(f"No tests parsed from {args.questions_file}")

        if not args.questions_only:
            report["database"].update(
                create_database_from_fixture(
                    Path(args.sql_file),
                    host=args.host,
                    port=args.port,
                    user=args.user,
                    password=args.password,
                    database=args.database,
                )
            )
        else:
            engine = create_engine(mysql_url(args.host, args.port, args.user, args.password, args.database))
            try:
                report["database"].update(inspect_database_state(engine))
            finally:
                engine.dispose()

        with isolated_app() as app:
            report["kb_build"] = build_kb(app, args)
            if report["kb_build"]["status"] != "passed":
                raise RuntimeError(report["kb_build"].get("message") or "KB build failed")
            if args.setup_only:
                return report, 0

            expected_engine = create_engine(mysql_url(args.host, args.port, args.user, args.password, args.database))
            try:
                for case in cases:
                    item = asdict(run_question(app, expected_engine, case))
                    report["questions"].append(item)
                    if args.verbose:
                        status = "PASS" if item["passed"] else "FAIL"
                        print(f"TEST {case.test_id}: {status} {item['failed_stage'] or item['result_comparison_status']}")
            finally:
                expected_engine.dispose()
    except Exception as exc:
        report["setup_error"] = str(exc)
        return report, 1

    summary = summarize(report)
    failed = summary["supported_failed"] + summary["fail_closed_failed"]
    return report, 1 if failed else 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sql-file", default=os.getenv("SQLSENSE_CURRENT_SCOPE_SQL_FILE", str(DEFAULT_SQL_FILE)))
    parser.add_argument("--questions-file", default=os.getenv("SQLSENSE_CURRENT_SCOPE_QUESTIONS_FILE", str(DEFAULT_QUESTIONS_FILE)))
    parser.add_argument("--report-dir", default=str(DEFAULT_REPORT_DIR))
    parser.add_argument("--host", default=os.getenv("DB_HOST", os.getenv("SQLSENSE_TEST_DB_HOST", "localhost")))
    parser.add_argument("--port", type=int, default=int(os.getenv("DB_PORT", os.getenv("SQLSENSE_TEST_DB_PORT", "3306"))))
    parser.add_argument("--user", default=os.getenv("DB_USER", os.getenv("SQLSENSE_TEST_DB_USER", "root")))
    parser.add_argument("--password", default=os.getenv("DB_PASSWORD", os.getenv("SQLSENSE_TEST_DB_PASSWORD", "")))
    parser.add_argument("--database", default=os.getenv("SQLSENSE_TEST_DB", os.getenv("SQLSENSE_TEST_DB_NAME", DEFAULT_DB_NAME)))
    parser.add_argument("--disable-ai-enrichment", action="store_true")
    parser.add_argument("--report-only", action="store_true")
    parser.add_argument("--setup-only", action="store_true")
    parser.add_argument("--questions-only", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--test-id", default="")
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report, exit_code = build_report(args)
    md_path, json_path = write_reports(report, Path(args.report_dir))
    print(f"Markdown report: {md_path}")
    print(f"JSON report: {json_path}")
    if report.get("setup_error"):
        print(f"Setup error: {redact(report['setup_error'])}")
    return 0 if args.report_only else exit_code


if __name__ == "__main__":
    raise SystemExit(main())
