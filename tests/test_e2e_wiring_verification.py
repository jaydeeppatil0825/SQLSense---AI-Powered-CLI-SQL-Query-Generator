import json
from pathlib import Path

from scripts import run_e2e_wiring_verification as e2e


def test_parse_question_file_separates_supported_and_fail_closed(tmp_path):
    path = tmp_path / "questions.txt"
    path.write_text(
        """
Supported current scope
1. Show active customers
2) Count orders by status

Fail-closed safety tests
41. Delete all orders
42: Count distinct orders by city
""",
        encoding="utf-8",
    )

    cases = e2e.parse_question_file(path)

    assert [(case.test_id, case.expected_category) for case in cases] == [
        ("1", "supported"),
        ("2", "supported"),
        ("41", "fail_closed"),
        ("42", "fail_closed"),
    ]
    assert cases[0].question == "Show active customers"


def test_redact_hides_nested_secrets():
    value = {
        "password": "secret",
        "nested": {"authorization_header": "Bearer abc"},
        "connection_url": "mysql://root:secret@localhost/db",
    }

    redacted = e2e.redact(value)

    assert redacted["password"] == "<redacted>"
    assert redacted["nested"]["authorization_header"] == "<redacted>"
    assert "secret" not in json.dumps(redacted)


def test_write_reports_contains_required_fields(tmp_path):
    report = {
        "run_timestamp": "2026-07-23T00:00:00Z",
        "git": {"branch": "main", "commit": "abc123"},
        "database": {"name": "lab", "table_count": 1, "row_counts": {"customers": 2}},
        "kb_build": {"status": "passed", "ai_enrichment_status": "fallback"},
        "questions": [
            {
                "test_id": "1",
                "question": "Show customers",
                "expected_category": "supported",
                "actual_route": "deterministic_sql_required",
                "query_shape": "single_table_list",
                "validation_status": "passed",
                "execution_status": "passed",
                "row_count": 2,
                "generated_sql": "SELECT * FROM customers LIMIT 50;",
                "passed": True,
            }
        ],
        "password": "secret",
    }

    md_path, json_path = e2e.write_reports(report, tmp_path)

    assert md_path.exists()
    assert json_path.exists()
    assert "SQLSense E2E Wiring Report" in md_path.read_text(encoding="utf-8")
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["password"] == "<redacted>"
    assert payload["questions"][0]["test_id"] == "1"


def test_report_only_mode_exits_zero(monkeypatch, tmp_path):
    def fake_build_report(args):
        return {"database": {"name": "lab"}, "kb_build": {}, "questions": [], "setup_error": "missing"}, 1

    monkeypatch.setattr(e2e, "build_report", fake_build_report)
    monkeypatch.setattr(
        e2e,
        "parse_args",
        lambda: type("Args", (), {"report_dir": str(tmp_path), "report_only": True})(),
    )

    assert e2e.main() == 0


def test_normal_mode_returns_nonzero_on_failure(monkeypatch, tmp_path):
    def fake_build_report(args):
        return {"database": {"name": "lab"}, "kb_build": {}, "questions": [], "setup_error": "missing"}, 1

    monkeypatch.setattr(e2e, "build_report", fake_build_report)
    monkeypatch.setattr(
        e2e,
        "parse_args",
        lambda: type("Args", (), {"report_dir": str(tmp_path), "report_only": False})(),
    )

    assert e2e.main() == 1
