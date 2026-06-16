"""Generic hybrid routing tests for rule-based vs AI SQL generation."""

from core.query_planner import build_query_context
from core.question_service import QuestionService


GENERIC_KB = {
    "alpha_records": {
        "columns": [
            {"name": "record_id", "type": "INTEGER", "nullable": False, "semantic_type": "id"},
            {"name": "record_name", "type": "VARCHAR(100)", "nullable": False, "semantic_type": "name"},
            {"name": "owner_id", "type": "INTEGER", "nullable": True, "semantic_type": "id"},
            {"name": "amount_total", "type": "DECIMAL(12,2)", "nullable": True, "semantic_type": "money"},
            {"name": "created_on", "type": "DATE", "nullable": True, "semantic_type": "date"},
        ],
        "primary_keys": ["record_id"],
        "foreign_keys": [],
    },
    "beta_events": {
        "columns": [
            {"name": "event_id", "type": "INTEGER", "nullable": False, "semantic_type": "id"},
            {"name": "owner_id", "type": "INTEGER", "nullable": True, "semantic_type": "id"},
            {"name": "event_total", "type": "DECIMAL(12,2)", "nullable": True, "semantic_type": "money"},
        ],
        "primary_keys": ["event_id"],
        "foreign_keys": [],
    },
}


def _context(
    selected_table_names,
    *,
    intent="list",
    confidence=0.9,
    metric=None,
    dimension=None,
    grouping=None,
    filters=None,
    date_range=None,
):
    selected_tables = []
    selected_columns = []
    for table_name in selected_table_names:
        column_entries = [
            {
                "column": column["name"],
                "semantic_type": column.get("semantic_type", "general"),
                "confidence": confidence,
                "reason": "selected from schema metadata",
            }
            for column in GENERIC_KB[table_name]["columns"][:3]
        ]
        selected_tables.append(
            {
                "table": table_name,
                "confidence": confidence,
                "reason": "selected dynamically",
                "selected_columns": column_entries,
            }
        )
        for column_entry in column_entries:
            selected_columns.append({"table": table_name, **column_entry})

    plan = {
        "question": "generic question",
        "intent": intent,
        "metric": metric,
        "dimension": dimension,
        "filters": filters or [],
        "date_range": date_range,
        "grouping": grouping or [],
        "sorting": None,
        "limit": 50,
        "question_terms": [],
        "semantic_hints": {metric} if metric else set(),
        "matched_glossary_terms": [],
    }
    selected_kb = {table_name: GENERIC_KB[table_name] for table_name in selected_table_names}
    return {
        "plan": plan,
        "selected_tables": selected_tables,
        "selected_columns": selected_columns,
        "selected_table_names": list(selected_table_names),
        "selected_knowledge_base": selected_kb,
        "warnings": [],
        "confidence": confidence,
        "knowledge_base": GENERIC_KB,
        "vector_results": {"table_names": list(selected_table_names), "columns": [], "glossary_terms": [], "relationships": []},
        "vector_used": False,
    }


def test_simple_table_listing_uses_rule_based(monkeypatch):
    service = QuestionService()
    monkeypatch.setattr("core.question_service.build_query_context", lambda *args, **kwargs: _context(["alpha_records"], intent="list"))
    monkeypatch.setattr("core.question_service.generate_simple_sql", lambda *args, **kwargs: "SELECT * FROM alpha_records LIMIT 50;")

    def fail_ai(*args, **kwargs):
        raise AssertionError("AI should not be called for simple list questions")

    monkeypatch.setattr("core.question_service.generate_sql", fail_ai)

    success, message, sql, error = service.process_question("show records", GENERIC_KB, ai_backend="local")

    assert success is True
    assert sql == "SELECT * FROM alpha_records LIMIT 50;"
    assert service.get_last_query_context()["route_used"] == "rule-based"


def test_count_query_uses_rule_based(monkeypatch):
    service = QuestionService()
    monkeypatch.setattr("core.question_service.build_query_context", lambda *args, **kwargs: _context(["alpha_records"], intent="count"))
    monkeypatch.setattr(
        "core.question_service.generate_simple_sql",
        lambda *args, **kwargs: "SELECT COUNT(*) AS total_alpha_records FROM alpha_records;",
    )
    monkeypatch.setattr("core.question_service.generate_sql", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("AI should not be called")))

    success, message, sql, error = service.process_question("count records", GENERIC_KB, ai_backend="local")

    assert success is True
    assert "COUNT(*)" in sql
    assert service.get_last_query_context()["route_used"] == "rule-based"


def test_simple_limit_query_uses_rule_based(monkeypatch):
    service = QuestionService()
    context = _context(["alpha_records"], intent="list")
    context["plan"]["limit"] = 10
    monkeypatch.setattr("core.question_service.build_query_context", lambda *args, **kwargs: context)
    monkeypatch.setattr("core.question_service.generate_simple_sql", lambda *args, **kwargs: "SELECT * FROM alpha_records LIMIT 10;")
    monkeypatch.setattr("core.question_service.generate_sql", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("AI should not be called")))

    success, message, sql, error = service.process_question("show 10 records", GENERIC_KB, ai_backend="local")

    assert success is True
    assert sql == "SELECT * FROM alpha_records LIMIT 10;"
    assert service.get_last_query_context()["route_used"] == "rule-based"


def test_simple_aggregate_query_uses_rule_based(monkeypatch):
    service = QuestionService()
    monkeypatch.setattr(
        "core.question_service.build_query_context",
        lambda *args, **kwargs: _context(
            ["alpha_records"],
            intent="total",
            metric="money",
            date_range={"start": "2026-06-01", "end_exclusive": "2026-07-01"},
        ),
    )
    monkeypatch.setattr(
        "core.question_service.generate_simple_sql",
        lambda *args, **kwargs: (
            "SELECT SUM(amount_total) AS total_amount_total "
            "FROM alpha_records WHERE created_on >= '2026-06-01' AND created_on < '2026-07-01';"
        ),
    )
    monkeypatch.setattr("core.question_service.generate_sql", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("AI should not be called")))

    success, message, sql, error = service.process_question("show total this month", GENERIC_KB, ai_backend="local")

    assert success is True
    assert "SUM(amount_total)" in sql
    assert service.get_last_query_context()["route_used"] == "rule-based"


def test_low_confidence_simple_looking_query_goes_to_ai(monkeypatch):
    service = QuestionService()
    ai_called = {"value": False}
    monkeypatch.setattr("core.question_service.build_query_context", lambda *args, **kwargs: _context(["alpha_records"], intent="list", confidence=0.35))
    monkeypatch.setattr("core.question_service.generate_simple_sql", lambda *args, **kwargs: "SELECT * FROM alpha_records LIMIT 50;")

    def fake_generate_sql(*args, **kwargs):
        ai_called["value"] = True
        return "SELECT record_id, record_name FROM alpha_records LIMIT 50;"

    monkeypatch.setattr("core.question_service.generate_sql", fake_generate_sql)

    success, message, sql, error = service.process_question("show entries", GENERIC_KB, ai_backend="local")

    assert success is True
    assert ai_called["value"] is True
    assert service.get_last_query_context()["route_used"] == "ai"


def test_complex_join_query_goes_to_ai(monkeypatch):
    service = QuestionService()
    ai_called = {"value": False}
    monkeypatch.setattr(
        "core.question_service.build_query_context",
        lambda *args, **kwargs: _context(
            ["alpha_records", "beta_events"],
            intent="comparison",
            metric="money",
            dimension="owner",
            grouping=["owner"],
        ),
    )
    monkeypatch.setattr("core.question_service.generate_simple_sql", lambda *args, **kwargs: "SELECT * FROM alpha_records LIMIT 50;")

    def fake_generate_sql(*args, **kwargs):
        ai_called["value"] = True
        return (
            "SELECT a.record_name, SUM(b.event_total) AS total_event_total "
            "FROM alpha_records a JOIN beta_events b ON a.owner_id = b.owner_id "
            "GROUP BY a.record_name LIMIT 50;"
        )

    monkeypatch.setattr("core.question_service.generate_sql", fake_generate_sql)

    success, message, sql, error = service.process_question("show totals by owner", GENERIC_KB, ai_backend="local")

    assert success is True
    assert ai_called["value"] is True
    assert "JOIN beta_events" in sql
    assert service.get_last_query_context()["route_used"] == "ai"


def test_generated_rule_based_sql_is_validated_before_return(monkeypatch):
    service = QuestionService()
    monkeypatch.setattr("core.question_service.build_query_context", lambda *args, **kwargs: _context(["alpha_records"], intent="list"))
    monkeypatch.setattr("core.question_service.generate_simple_sql", lambda *args, **kwargs: "SELECT record_name FROM LIMIT 50")
    monkeypatch.setattr(
        "core.question_service.generate_sql",
        lambda *args, **kwargs: "SELECT record_id, record_name FROM alpha_records LIMIT 50;",
    )

    success, message, sql, error = service.process_question("show records", GENERIC_KB, ai_backend="local")

    assert success is True
    assert sql == "SELECT record_id, record_name FROM alpha_records LIMIT 50;"
    assert "FROM LIMIT" not in sql
    assert service.get_last_query_context()["route_used"] == "ai"


def test_query_planner_does_not_assign_business_specific_intents():
    knowledge_base = {
        "alpha_records": {
            "columns": [
                {"name": "record_id", "type": "INTEGER", "nullable": False, "semantic_type": "id"},
                {"name": "status_flag", "type": "VARCHAR(30)", "nullable": True, "semantic_type": "status", "sample_values": ["Pending", "Closed"]},
                {"name": "amount_total", "type": "DECIMAL(12,2)", "nullable": True, "semantic_type": "money"},
            ],
            "primary_keys": ["record_id"],
            "foreign_keys": [],
            "relationships": [],
        },
        "beta_locations": {
            "columns": [
                {"name": "location_name", "type": "VARCHAR(50)", "nullable": False, "semantic_type": "name"},
            ],
            "primary_keys": ["location_name"],
            "foreign_keys": [],
            "relationships": [],
        },
    }

    first = build_query_context("show pending records", knowledge_base, use_vector_retrieval=False)
    second = build_query_context("show current stock by warehouse", knowledge_base, use_vector_retrieval=False)

    assert first["plan"]["intent"] == "list"
    assert first["plan"]["filters"]
    assert second["plan"]["intent"] == "list"
    assert second["plan"]["dimension"] == "warehouse"
