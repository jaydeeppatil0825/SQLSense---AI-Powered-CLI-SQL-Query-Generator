"""Core flow tests for ask-question and execute-last-SQL behavior."""
import importlib
from pathlib import Path

import pytest
from sqlalchemy import Column, Integer, MetaData, Table, create_engine

from core.app_service import AppService


KB = {
    "orders": {
        "columns": [
            {"name": "order_id", "type": "INTEGER", "nullable": False},
            {"name": "final_amount", "type": "INTEGER", "nullable": True},
        ],
        "primary_keys": ["order_id"],
        "foreign_keys": [],
    }
}


def _service_with_orders(monkeypatch, tmp_path):
    monkeypatch.setenv("VECTOR_INDEX_DIR", str(tmp_path / "vector_index"))
    engine = create_engine("sqlite:///:memory:")
    metadata = MetaData()
    orders = Table(
        "orders",
        metadata,
        Column("order_id", Integer, primary_key=True),
        Column("final_amount", Integer),
    )
    metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(orders.insert(), [{"order_id": 1, "final_amount": 100}])

    service = AppService()
    service.database_service.engine = engine
    service.database_service.knowledge_base = KB
    service.database_ready = True
    return service


def test_process_question_saves_sql_and_execute_last_sql_uses_same_sql(monkeypatch, tmp_path):
    service = _service_with_orders(monkeypatch, tmp_path)
    expected_sql = "SELECT SUM(final_amount) AS total_sales FROM orders;"
    monkeypatch.setattr(
        "core.question_service.generate_simple_sql",
        lambda *args, **kwargs: expected_sql,
    )
    monkeypatch.setattr(
        "core.question_service.generate_sql",
        lambda user_question, knowledge_base, backend=None, query_plan=None, selected_tables=None: expected_sql,
    )
    monkeypatch.setattr(
        "core.question_service.generate_sql_with_retry",
        lambda user_question, knowledge_base, backend, first_attempt_sql, validation_reason, query_plan=None, selected_tables=None: expected_sql,
    )

    result = service.process_question("show total sales", ai_backend="local")

    assert result["success"] is True
    assert result["error"] is None
    assert result["sql"] == expected_sql
    assert service.get_last_sql() == expected_sql

    exec_success, exec_message, rows = service.execute_sql(service.get_last_sql(), revalidate=True)

    assert exec_success is True
    assert rows == [{"total_sales": 100}]
    assert service.get_last_sql() == expected_sql


def test_destructive_natural_language_question_is_blocked(monkeypatch, tmp_path):
    service = _service_with_orders(monkeypatch, tmp_path)

    result = service.process_question("delete all customers", ai_backend="local")

    assert result["success"] is False
    assert result["message"] == "Unsafe request blocked. Only SELECT questions are allowed."
    assert result["sql"] is None
    assert service.get_last_sql() is None


def test_process_question_loads_active_glossary_without_glossary_menu(monkeypatch, tmp_path):
    service = _service_with_orders(monkeypatch, tmp_path)
    active_glossary = {
        "sales": {
            "description": "Sales amount",
            "mapped_columns": [{"table": "orders", "column": "final_amount", "confidence": "high"}],
            "example_questions": ["show total sales"],
        }
    }
    captured = {}

    def fake_load_business_glossary(glossary_path="semantic/business_glossary.json"):
        service.database_service.business_glossary = active_glossary
        service.database_service.refresh_vector_index()
        return True, "Business glossary loaded successfully", active_glossary

    def fake_process_question(
        question,
        knowledge_base,
        business_glossary=None,
        vector_retriever=None,
        ai_backend="local",
        pipeline_context=None,
    ):
        captured["business_glossary"] = business_glossary
        captured["vector_retriever"] = vector_retriever
        captured["pipeline_context"] = pipeline_context
        return True, "ok", "SELECT * FROM orders LIMIT 50;", None

    service.database_service.business_glossary = None
    monkeypatch.setattr(service.database_service, "load_business_glossary", fake_load_business_glossary)
    monkeypatch.setattr(service.question_service, "process_question", fake_process_question)

    result = service.process_question("show orders", ai_backend="local")

    assert result["success"] is True
    assert result["sql"] == "SELECT * FROM orders LIMIT 50;"
    assert captured["business_glossary"] == active_glossary
    assert captured["vector_retriever"] is not None
    assert captured["pipeline_context"] is not None


def test_process_question_uses_persisted_vector_index_after_reload(monkeypatch, tmp_path):
    vector_dir = tmp_path / "vector_index"
    monkeypatch.setenv("EMBEDDING_BACKEND", "unsupported")
    monkeypatch.setenv("VECTOR_INDEX_DIR", str(vector_dir))

    first_service = AppService()
    first_service.database_service.knowledge_base = KB
    first_service.database_service.knowledge_base_origin = "built"
    first_service.database_service.business_glossary = {
        "sales": {
            "description": "Total amount",
            "mapped_columns": [{"table": "orders", "column": "final_amount", "confidence": "high"}],
            "example_questions": ["show total sales"],
        }
    }
    first_service.database_ready = True
    first_service.database_service.refresh_vector_index()

    second_service = AppService()
    second_service.database_service.knowledge_base = KB
    second_service.database_service.knowledge_base_origin = "loaded"
    second_service.database_service.business_glossary = first_service.database_service.business_glossary
    second_service.database_ready = True
    second_service.database_service.refresh_vector_index()

    captured = {}

    def fake_process_question(
        question,
        knowledge_base,
        business_glossary=None,
        vector_retriever=None,
        ai_backend="local",
        pipeline_context=None,
    ):
        captured["vector_retriever"] = vector_retriever
        captured["vector_status"] = second_service.database_service.get_vector_status()
        captured["pipeline_context"] = pipeline_context
        return True, "ok", "SELECT * FROM orders LIMIT 50;", None

    monkeypatch.setattr(second_service.question_service, "process_question", fake_process_question)

    result = second_service.process_question("show orders", ai_backend="local")

    assert result["success"] is True
    assert captured["vector_retriever"] is not None
    assert captured["pipeline_context"] is not None
    assert captured["vector_status"]["persistence"]["loaded_from_disk"] is True
    assert captured["vector_status"]["persistence"]["source"] == "disk"


def test_persisted_vector_status_includes_database_identity_and_schema_hash(monkeypatch, tmp_path):
    vector_dir = tmp_path / "vector_index"
    monkeypatch.setenv("EMBEDDING_BACKEND", "unsupported")
    monkeypatch.setenv("VECTOR_INDEX_DIR", str(vector_dir))

    service = AppService()
    service.database_service.knowledge_base = KB
    service.database_service.knowledge_base_origin = "built"
    service.database_service.business_glossary = {
        "order amount": {
            "description": "Order amount",
            "mapped_columns": [{"table": "orders", "column": "final_amount", "confidence": "high"}],
            "example_questions": ["show order amount"],
        }
    }
    service.database_service.db_config = {
        "db_type": "mysql",
        "host": "localhost",
        "port": 3306,
        "database": "runtime_dynamic_db",
    }

    service.database_service.refresh_vector_index()

    vector_status = service.database_service.get_vector_status()
    persistence = vector_status["persistence"]
    retriever = service.database_service.get_vector_retriever()
    table_details = retriever.get_relevant_table_details("show orders", top_k=3)
    column_details = retriever.get_relevant_columns("order amount", top_k=3)

    assert persistence["db_engine"] == "mysql"
    assert persistence["db_host"] == "localhost"
    assert persistence["db_port"] == "3306"
    assert persistence["db_name"] == "runtime_dynamic_db"
    assert persistence["schema_hash"]
    assert persistence["vector_index_version"] == 2
    assert table_details[0]["db_name"] == "runtime_dynamic_db"
    assert table_details[0]["schema_hash"] == persistence["schema_hash"]
    assert table_details[0]["source_type"] == "table"
    assert table_details[0]["evidence_source"] == "knowledge_base_table"
    assert column_details[0]["db_name"] == "runtime_dynamic_db"
    assert column_details[0]["source_type"] == "column"
    assert column_details[0]["evidence_source"] == "knowledge_base_column"


def test_invalid_generated_sql_never_becomes_last_executable_sql(monkeypatch, tmp_path):
    service = _service_with_orders(monkeypatch, tmp_path)
    monkeypatch.setattr("core.question_service.generate_simple_sql", lambda *args, **kwargs: None)

    result = service.process_question("show total sales", ai_backend="local")

    assert result["success"] is False
    assert result["sql"] is None
    assert service.get_last_sql() is None
    assert "no sql" in (result.get("message") or "").lower() or "no sql" in (result.get("error") or "").lower()


def test_cli_menu_labels_remain_unchanged():
    source = Path("main.py").read_text(encoding="utf-8")

    assert 'print("  1) Connect Database / Auto Build KB")' in source
    assert 'print("  2) Ask a Question / Ask Business Question")' in source
    assert 'print("  3) Execute Last SQL")' in source
    assert 'print("  4) Semantic AI Settings")' in source
    assert 'print("  5) Search Business Glossary")' in source
    assert 'print("  6) Rebuild / Refresh Knowledge Base")' in source
    assert 'print("  7) Exit")' in source


def test_connect_database_and_prepare_triggers_kb_build(monkeypatch):
    service = AppService()
    build_calls = []

    monkeypatch.setattr(
        service,
        "connect_database",
        lambda **kwargs: (True, "connected", object()),
    )

    def fake_build_knowledge_base(**kwargs):
        build_calls.append(kwargs)
        service.database_service.knowledge_base = KB
        service.database_service.business_glossary = {"orders": {"mapped_columns": []}}
        return True, "Knowledge base built successfully", KB

    monkeypatch.setattr(service, "build_knowledge_base", fake_build_knowledge_base)
    monkeypatch.setattr(
        service,
        "get_vector_status",
        lambda: {"index_status": "ready", "persistence": {"source": "rebuilt"}},
    )

    success, message, report = service.connect_database_and_prepare(
        db_type="mysql",
        host="localhost",
        port=3306,
        username="root",
        password="secret",
        database="dynamic_db",
    )

    assert success is True
    assert report["connected"] is True
    assert report["kb_built"] is True
    assert report["database_ready"] is True
    assert service.is_database_ready() is True
    assert build_calls == [{"use_ai_enrichment": True, "ai_backend": service.get_active_backend(), "force_rebuild": True}]


def test_connect_database_failure_does_not_build_kb(monkeypatch):
    service = AppService()
    build_called = False

    monkeypatch.setattr(
        service,
        "connect_database",
        lambda **kwargs: (False, "Connection failed", None),
    )

    def fake_build_knowledge_base(**kwargs):
        nonlocal build_called
        build_called = True
        return True, "Knowledge base built successfully", KB

    monkeypatch.setattr(service, "build_knowledge_base", fake_build_knowledge_base)

    success, message, report = service.connect_database_and_prepare(database="missing_db")

    assert success is False
    assert build_called is False
    assert report["connected"] is False
    assert report["database_ready"] is False
    assert service.is_database_ready() is False


def test_connect_database_and_prepare_leaves_database_not_ready_when_kb_build_fails(monkeypatch):
    service = AppService()

    monkeypatch.setattr(
        service,
        "connect_database",
        lambda **kwargs: (True, "connected", object()),
    )
    monkeypatch.setattr(
        service,
        "build_knowledge_base",
        lambda **kwargs: (False, "Knowledge base build failed", None),
    )

    success, message, report = service.connect_database_and_prepare(database="dynamic_db")

    assert success is False
    assert report["connected"] is True
    assert report["kb_built"] is False
    assert report["database_ready"] is False
    assert service.is_database_ready() is False


def test_connect_database_and_prepare_succeeds_when_vector_build_is_degraded(monkeypatch):
    service = AppService()

    monkeypatch.setattr(
        service,
        "connect_database",
        lambda **kwargs: (True, "connected", object()),
    )

    def fake_build_knowledge_base(**kwargs):
        service.database_service.knowledge_base = KB
        service.database_service.business_glossary = {"orders": {"mapped_columns": []}}
        return True, "Knowledge base built successfully", KB

    monkeypatch.setattr(service, "build_knowledge_base", fake_build_knowledge_base)
    monkeypatch.setattr(
        service,
        "get_vector_status",
        lambda: {
            "index_status": "degraded",
            "persistence": {"source": "rebuild_failed", "persistence_error": "vector rebuild failed"},
        },
    )

    success, message, report = service.connect_database_and_prepare(database="dynamic_db")

    assert success is True
    assert report["database_ready"] is True
    assert report["vector_status"] == "degraded"
    assert "vector rebuild failed" in report["vector_warning"]


def test_process_question_is_blocked_before_database_ready():
    service = AppService()
    service.database_service.knowledge_base = KB
    service.database_ready = False

    result = service.process_question("show orders", ai_backend="local")

    assert result["success"] is False
    assert result["sql"] is None
    assert "not ready" in (result.get("error") or "").lower()


def test_process_question_normalizes_tuple_payload(monkeypatch, tmp_path):
    service = _service_with_orders(monkeypatch, tmp_path)
    service.question_service.last_query_context = {"route_used": "simple_rule_based"}
    monkeypatch.setattr(
        service.query_pipeline,
        "run",
        lambda **kwargs: (True, "ok", "SELECT order_id FROM orders;", None),
    )

    result = service.process_question("show all orders", ai_backend="local")

    assert result["success"] is True
    assert result["sql"] == "SELECT order_id FROM orders;"
    assert result["generated_sql"] == "SELECT order_id FROM orders;"
    assert result["route"] == "rule-based"
    assert result["route_used"] == "rule-based"


def test_process_question_normalizes_none_payload(monkeypatch, tmp_path):
    service = _service_with_orders(monkeypatch, tmp_path)
    monkeypatch.setattr(service.query_pipeline, "run", lambda **kwargs: None)

    result = service.process_question("show all orders", ai_backend="local")

    assert result["success"] is False
    assert result["sql"] is None
    assert "no result" in (result["error"] or "").lower()


def test_process_question_normalizes_dict_payload(monkeypatch, tmp_path):
    service = _service_with_orders(monkeypatch, tmp_path)
    monkeypatch.setattr(
        service.query_pipeline,
        "run",
        lambda **kwargs: {
            "success": True,
            "message": "ok",
            "sql": "SELECT order_id FROM orders;",
            "route": "simple-rule-based",
            "validation_result": {"is_valid": True, "reason": "ok"},
            "query_context": {"route_used": "simple-rule-based"},
            "error": None,
        },
    )

    result = service.process_question("show all orders", ai_backend="local")

    assert result["success"] is True
    assert result["route"] == "rule-based"
    assert result["route_used"] == "rule-based"
    assert result["validation_result"]["is_valid"] is True


def test_rebuild_or_refresh_knowledge_base_forces_rebuild(monkeypatch):
    service = AppService()
    captured = {}

    def fake_build_knowledge_base(**kwargs):
        captured.update(kwargs)
        service.database_service.knowledge_base = KB
        return True, "Knowledge base built successfully", KB

    monkeypatch.setattr(service, "build_knowledge_base", fake_build_knowledge_base)

    success, message, knowledge_base = service.rebuild_or_refresh_knowledge_base()

    assert success is True
    assert knowledge_base == KB
    assert captured["force_rebuild"] is True


def test_pipeline_architecture_reflects_current_folder_structure():
    source = Path("PIPELINE_ARCHITECTURE.md").read_text(encoding="utf-8")

    assert "kb_pipeline/" in source
    assert "query_pipeline/" in source
    assert "sql_pipeline/" in source


def test_main_imports_working_services_through_compatibility_paths():
    main_module = importlib.import_module("main")
    app_service_module = importlib.import_module("core.app_service")
    db_connection_module = importlib.import_module("db.connection")

    assert main_module.AppService is app_service_module.AppService
    assert main_module.SUPPORTED_DB_TYPES == db_connection_module.SUPPORTED_DB_TYPES


def test_handle_ask_question_guards_none_result(monkeypatch, capsys):
    main_module = importlib.import_module("main")
    state = main_module.SessionState()

    monkeypatch.setattr(state.app_service, "is_database_connected", lambda: True)
    monkeypatch.setattr(state.app_service, "is_database_ready", lambda: True)
    monkeypatch.setattr(state.app_service, "load_knowledge_base", lambda: (True, "ok", KB))
    monkeypatch.setattr(state.app_service, "detect_action", lambda question: None)
    monkeypatch.setattr(state.app_service, "get_active_backend", lambda: "local")
    monkeypatch.setattr(state.app_service, "process_question", lambda question, ai_backend=None: None)
    monkeypatch.setattr(main_module, "_input", lambda prompt: "show all partner")

    main_module.handle_ask_question(state)

    output = capsys.readouterr().out
    assert "Internal error: question processing returned no result." in output


@pytest.mark.parametrize(
    ("question", "sql"),
    [
        ("show all partner", "SELECT partner_id, partner_name FROM partners;"),
        ("count partner", "SELECT COUNT(*) AS total_partners FROM partners;"),
        ("show all bill", "SELECT bill_id, bill_number FROM bills;"),
        ("count bill", "SELECT COUNT(*) AS total_bills FROM bills;"),
    ],
)
def test_handle_ask_question_accepts_simple_rule_based_result(monkeypatch, capsys, question, sql):
    main_module = importlib.import_module("main")
    state = main_module.SessionState()

    monkeypatch.setattr(state.app_service, "is_database_connected", lambda: True)
    monkeypatch.setattr(state.app_service, "is_database_ready", lambda: True)
    monkeypatch.setattr(state.app_service, "load_knowledge_base", lambda: (True, "ok", KB))
    monkeypatch.setattr(state.app_service, "detect_action", lambda asked_question: None)
    monkeypatch.setattr(state.app_service, "get_active_backend", lambda: "local")
    monkeypatch.setattr(
        state.app_service,
        "get_vector_status",
        lambda: {"persistence": {}, "embedding": {}, "retriever": {}},
    )
    monkeypatch.setattr(
        state.app_service,
        "process_question",
        lambda asked_question, ai_backend=None: {
            "success": True,
            "question": asked_question,
            "message": "ok",
            "generated_sql": sql,
            "sql": sql,
            "route": "rule-based",
            "route_used": "simple_rule_based",
            "validation_result": {"is_valid": True, "reason": "ok"},
            "query_context": {
                "plan": {"intent": "count" if "count" in asked_question else "list"},
                "selected_tables": [
                    {
                        "table": "partners" if "partner" in asked_question else "bills",
                        "confidence": 0.95,
                        "selected_columns": [],
                    }
                ],
                "route_used": "simple_rule_based",
                "route_reason": "simple single-table question",
            },
            "error": None,
        },
    )
    monkeypatch.setattr(main_module, "_input", lambda prompt: question)

    main_module.handle_ask_question(state)

    output = capsys.readouterr().out
    assert "Unexpected error" not in output
    assert "Route: rule-based" in output
    assert sql in output


def test_ai_backend_status_refresh_updates_displayed_status(monkeypatch, capsys):
    main_module = importlib.import_module("main")
    state = main_module.SessionState()
    state.app_service.set_local_backend("llama3", "http://localhost:11434")

    responses = iter(
        [
            (True, "Ollama is running. Configured model 'llama3' matched available model 'llama3:latest'."),
        ]
    )
    monkeypatch.setattr(state.app_service, "test_backend_connection", lambda: next(responses))
    answers = iter(["4", "5"])
    monkeypatch.setattr(main_module, "_input", lambda prompt: next(answers))

    main_module.handle_ai_backend_settings(state)

    output = capsys.readouterr().out
    assert "Backend status: not tested" in output
    assert "Backend status: connected" in output
    assert "llama3:latest" in output
