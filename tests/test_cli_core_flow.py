"""Core flow tests for ask-question and execute-last-SQL behavior."""
import importlib
from pathlib import Path

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
    return service


def test_process_question_saves_sql_and_execute_last_sql_uses_same_sql(monkeypatch, tmp_path):
    service = _service_with_orders(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "core.question_service.generate_sql",
        lambda user_question, knowledge_base, backend=None, query_plan=None, selected_tables=None: "SELECT SUM(final_amount) AS total_sales FROM orders;",
    )
    monkeypatch.setattr(
        "core.question_service.generate_sql_with_retry",
        lambda user_question, knowledge_base, backend, first_attempt_sql, validation_reason, query_plan=None, selected_tables=None: "SELECT SUM(final_amount) AS total_sales FROM orders;",
    )

    success, message, sql, error = service.process_question("show total sales", ai_backend="local")

    assert success is True
    assert error is None
    assert sql == "SELECT SUM(final_amount) AS total_sales FROM orders;"
    assert service.get_last_sql() == sql

    exec_success, exec_message, rows = service.execute_sql(service.get_last_sql(), revalidate=True)

    assert exec_success is True
    assert rows == [{"total_sales": 100}]
    assert service.get_last_sql() == sql


def test_destructive_natural_language_question_is_blocked(monkeypatch, tmp_path):
    service = _service_with_orders(monkeypatch, tmp_path)

    success, message, sql, error = service.process_question("delete all customers", ai_backend="local")

    assert success is False
    assert message == "Unsafe request blocked. Only SELECT questions are allowed."
    assert sql is None
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

    success, message, sql, error = service.process_question("show orders", ai_backend="local")

    assert success is True
    assert sql == "SELECT * FROM orders LIMIT 50;"
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
    first_service.database_service.refresh_vector_index()

    second_service = AppService()
    second_service.database_service.knowledge_base = KB
    second_service.database_service.knowledge_base_origin = "loaded"
    second_service.database_service.business_glossary = first_service.database_service.business_glossary
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

    success, message, sql, error = second_service.process_question("show orders", ai_backend="local")

    assert success is True
    assert captured["vector_retriever"] is not None
    assert captured["pipeline_context"] is not None
    assert captured["vector_status"]["persistence"]["loaded_from_disk"] is True
    assert captured["vector_status"]["persistence"]["source"] == "disk"


def test_invalid_generated_sql_never_becomes_last_executable_sql(monkeypatch, tmp_path):
    service = _service_with_orders(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "core.question_service.generate_sql",
        lambda user_question, knowledge_base, backend=None, query_plan=None, selected_tables=None: "SELECT final_amount FROM LIMIT 50",
    )
    monkeypatch.setattr(
        "core.question_service.generate_sql_with_retry",
        lambda user_question, knowledge_base, backend, first_attempt_sql, validation_reason, query_plan=None, selected_tables=None: "SELECT final_amount FROM LIMIT 50",
    )
    monkeypatch.setattr("core.question_service.generate_simple_sql", lambda *args, **kwargs: None)

    success, message, sql, error = service.process_question("show total sales", ai_backend="local")

    assert success is False
    assert sql is None
    assert service.get_last_sql() is None
    assert "Could not generate a valid SQL query." in error


def test_cli_menu_labels_remain_unchanged():
    source = Path("main.py").read_text(encoding="utf-8")

    assert 'print("  1) Connect Database")' in source
    assert 'print("  2) Build Knowledge Base")' in source
    assert 'print("  3) Ask a Question / Ask Business Question")' in source
    assert 'print("  4) Execute Last SQL")' in source
    assert 'print("  5) AI Backend Settings")' in source
    assert 'print("  6) Search Business Glossary")' in source
    assert 'print("  7) Exit")' in source


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
