import json

from scripts import run_current_scope_question_tests as runner


def test_parse_answer_file_extracts_supported_and_fail_closed_cases(tmp_path):
    path = tmp_path / "cases.txt"
    path.write_text(
        """
Supported current-scope tests
-----------------------------

TEST 01
Question: show active customers
Expected behavior: executable SELECT
Expected SQL:
SELECT *
FROM customers
WHERE customer_status = 'Active'
LIMIT 50;
------------------------------------------------------------------------------

Fail-closed safety tests
------------------------

TEST 51
Question: delete cancelled orders
Expected behavior: fail closed
Expected reason: DELETE request is outside current SELECT-only scope.
Expected SQL: none
Should execute: no
------------------------------------------------------------------------------
""",
        encoding="utf-8",
    )

    cases = runner.parse_answer_file(path)

    assert [(case.test_id, case.expected_category) for case in cases] == [("01", "supported"), ("51", "fail_closed")]
    assert cases[0].expected_sql.startswith("SELECT *")
    assert cases[1].expected_sql == ""
    assert "DELETE" in cases[1].expected_reason


def test_compare_rows_accepts_unordered_columns():
    actual = [{"city": "Pune", "count": 2}]
    expected = [{"count": 2, "city": "Pune"}]

    assert runner.compare_rows(actual, expected, ordered=False) == "match_unordered_columns"


def test_compare_sql_semantics_rejects_wrong_grouping_table():
    actual_sql = "SELECT delivery_city, SUM(shipping_cost) FROM shipments GROUP BY delivery_city"
    expected_sql = (
        "SELECT c.city, SUM(s.shipping_cost) "
        "FROM shipments s "
        "INNER JOIN orders o ON s.order_id = o.order_id "
        "INNER JOIN customers c ON o.customer_id = c.customer_id "
        "GROUP BY c.city"
    )

    assert runner.compare_sql_semantics(actual_sql, expected_sql) == "sql_table_mismatch"


def test_compare_sql_semantics_rejects_wrong_group_by_column():
    actual_sql = "SELECT delivery_city, SUM(shipping_cost) FROM shipments GROUP BY delivery_city"
    expected_sql = "SELECT city, SUM(shipping_cost) FROM shipments GROUP BY city"

    assert runner.compare_sql_semantics(actual_sql, expected_sql) == "sql_group_by_mismatch"


def test_rewrite_fixture_database_only_changes_default_lab_name():
    sql = "DROP DATABASE IF EXISTS sqlsense_current_scope_business_lab; USE sqlsense_current_scope_business_lab;"

    rewritten = runner.rewrite_fixture_database(sql, "tmp_lab")

    assert "tmp_lab" in rewritten
    assert "sqlsense_current_scope_business_lab" not in rewritten


def test_write_reports_contains_current_scope_fields(tmp_path):
    report = {
        "run_timestamp": "2026-07-23T00:00:00Z",
        "git": {"branch": "main", "commit": "abc123"},
        "python_version": "3.12",
        "database": {"name": "lab", "mysql_version": "8.0", "table_count": 1, "row_counts": {"customers": 2}},
        "kb_build": {"status": "passed", "ai_enrichment_status": "enriched", "ai_fallback_used": False},
        "questions": [
            {
                "test_id": "01",
                "question": "show customers",
                "expected_category": "supported",
                "actual_route": "deterministic_sql_required",
                "query_shape": "filtered_lookup",
                "executable": True,
                "generated_sql": "SELECT * FROM customers LIMIT 50;",
                "expected_sql": "SELECT * FROM customers LIMIT 50;",
                "sql_validation_status": "passed",
                "execution_status": "passed",
                "expected_result_row_count": 2,
                "actual_result_row_count": 2,
                "result_comparison_status": "match",
                "selected_join_path_length": 0,
                "passed": True,
            }
        ],
        "password": "secret",
    }

    md_path, json_path = runner.write_reports(report, tmp_path)

    assert md_path.name == runner.REPORT_MD
    assert json_path.name == runner.REPORT_JSON
    assert "SQLSense Current-Scope Question Report" in md_path.read_text(encoding="utf-8")
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["password"] == "<redacted>"
    assert payload["summary"]["total"] == 1


def test_report_only_mode_exits_zero(monkeypatch, tmp_path):
    def fake_build_report(args):
        return {"database": {"name": "lab"}, "kb_build": {}, "questions": [], "setup_error": "missing"}, 1

    monkeypatch.setattr(runner, "build_report", fake_build_report)
    monkeypatch.setattr(
        runner,
        "parse_args",
        lambda: type("Args", (), {"report_dir": str(tmp_path), "report_only": True})(),
    )

    assert runner.main() == 0
