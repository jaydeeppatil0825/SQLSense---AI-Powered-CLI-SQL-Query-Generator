"""Tests for database service knowledge-base workflow."""

from pathlib import Path

from sqlalchemy import Column, Integer, MetaData, Table, create_engine

from core.database_service import DatabaseService


def test_build_knowledge_base_falls_back_when_ollama_is_not_running(monkeypatch, tmp_path):
    monkeypatch.setenv("VECTOR_INDEX_DIR", str(tmp_path / "vector_index"))
    engine = create_engine("sqlite:///:memory:")
    metadata = MetaData()
    Table(
        "orders",
        metadata,
        Column("order_id", Integer, primary_key=True),
        Column("final_amount", Integer),
    )
    metadata.create_all(engine)

    service = DatabaseService()
    service.engine = engine

    monkeypatch.setattr("core.database_service.save_json", lambda data, path: None)
    monkeypatch.setattr("core.database_service.save_business_glossary", lambda glossary, path: None)
    monkeypatch.setattr("core.database_service.check_ollama_status", lambda: (False, "Ollama is not running."))

    def fail_if_called(*args, **kwargs):
        raise AssertionError("AI enrichment should not run when Ollama preflight fails")

    monkeypatch.setattr("core.database_service.enrich_knowledge_base_with_ai", fail_if_called)

    success, message, knowledge_base = service.build_knowledge_base(
        use_ai_enrichment=True,
        ai_backend="local",
    )

    assert success is True
    assert message == "Knowledge base built successfully"
    assert "orders" in knowledge_base
    assert service.get_last_ai_enrichment_result() == (
        "fallback",
        "Ollama is not running. Using rule-based enrichment.",
    )


def test_build_knowledge_base_invalid_ai_json_falls_back_once_and_stays_ready(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("EMBEDDING_BACKEND", "unsupported")
    monkeypatch.setenv("VECTOR_INDEX_DIR", str(tmp_path / "vector_index"))
    monkeypatch.setenv("CHROMA_INDEX_DIR", str(tmp_path / "chroma"))
    engine = create_engine("sqlite:///:memory:")
    metadata = MetaData()
    Table(
        "orders",
        metadata,
        Column("order_id", Integer, primary_key=True),
        Column("final_amount", Integer),
    )
    metadata.create_all(engine)

    service = DatabaseService()
    service.engine = engine
    service.db_config = {"db_type": "sqlite", "sqlite_path": ":memory:"}
    monkeypatch.setattr("core.database_service.save_json", lambda data, path: None)
    monkeypatch.setattr("core.database_service.save_business_glossary", lambda glossary, path: None)
    monkeypatch.setattr("core.database_service.check_ollama_status", lambda: (True, "ready"))
    monkeypatch.setattr(
        "kb_pipeline.ai_semantic_enricher._call_ai_backend",
        lambda messages, backend, response_format=None: "prefix {not valid json}",
    )

    success, message, knowledge_base = service.build_knowledge_base(
        use_ai_enrichment=True,
        ai_backend="local",
    )

    assert success is True
    assert message == "Knowledge base built successfully"
    assert "orders" in knowledge_base
    assert service.get_last_ai_enrichment_result() == (
        "fallback",
        "Local AI returned invalid JSON. Using rule-based fallback.",
    )
    assert "Using rule-based fallback" not in capsys.readouterr().out
    assert service.get_vector_status()["index_status"] == "ready"


def test_build_knowledge_base_skips_nvidia_ai_enrichment_when_backend_not_connected(monkeypatch, tmp_path):
    monkeypatch.setenv("VECTOR_INDEX_DIR", str(tmp_path / "vector_index"))
    engine = create_engine("sqlite:///:memory:")
    metadata = MetaData()
    Table(
        "orders",
        metadata,
        Column("order_id", Integer, primary_key=True),
        Column("final_amount", Integer),
    )
    metadata.create_all(engine)

    service = DatabaseService()
    service.engine = engine

    monkeypatch.setattr("core.database_service.save_json", lambda data, path: None)
    monkeypatch.setattr("core.database_service.save_business_glossary", lambda glossary, path: None)

    class FakeBackendService:
        def test_backend_connection(self, backend=None):
            return False, "NVIDIA backend returned an empty response."

    monkeypatch.setattr("core.database_service.get_ai_backend_service", lambda: FakeBackendService())

    def fail_if_called(*args, **kwargs):
        raise AssertionError("AI enrichment should not run when NVIDIA preflight fails")

    monkeypatch.setattr("core.database_service.enrich_knowledge_base_with_ai", fail_if_called)

    success, message, knowledge_base = service.build_knowledge_base(
        use_ai_enrichment=True,
        ai_backend="nvidia",
    )

    assert success is True
    assert message == "Knowledge base built successfully"
    assert "orders" in knowledge_base
    assert service.get_last_ai_enrichment_result() == (
        "fallback",
        "NVIDIA backend returned an empty response. Using rule-based enrichment.",
    )


def test_connect_database_fails_cleanly_for_missing_database_and_clears_stale_state(monkeypatch, tmp_path):
    monkeypatch.setenv("VECTOR_INDEX_DIR", str(tmp_path / "vector_index"))
    service = DatabaseService()
    service.engine = object()
    service.db_config = {"db_type": "mysql", "database": "old_db"}
    service.knowledge_base = {"orders": {"columns": []}}
    service.business_glossary = {"sales": {"mapped_columns": []}}
    service.knowledge_base_origin = "loaded"
    service.vector_index_status = "ready"

    class MissingDatabaseError(Exception):
        pass

    monkeypatch.setattr(
        "core.database_service.connect_engine",
        lambda **kwargs: (_ for _ in ()).throw(MissingDatabaseError('(1049, "Unknown database \'missing_db\'")')),
    )
    monkeypatch.setattr(
        "core.database_service.list_accessible_databases",
        lambda **kwargs: ["alpha_db", "beta_db"],
    )

    success, message, engine = service.connect_database(
        db_type="mysql",
        host="localhost",
        port=3306,
        username="root",
        password="secret",
        database="missing_db",
    )

    assert success is False
    assert engine is None
    assert "missing_db" in message
    assert "Available databases: alpha_db, beta_db" in message
    assert "enter the correct database name or create/import the database first" in message
    assert service.engine is None
    assert service.get_db_config() == {}
    assert service.get_knowledge_base() is None
    assert service.get_business_glossary() is None
    assert service.get_vector_status()["index_status"] == "not_built"


def test_connect_database_accepts_arbitrary_database_name_without_runtime_hardcoding(monkeypatch, tmp_path):
    monkeypatch.setenv("VECTOR_INDEX_DIR", str(tmp_path / "vector_index"))
    service = DatabaseService()
    fake_engine = object()
    captured: dict = {}

    def fake_connect_engine(**kwargs):
        captured.update(kwargs)
        return fake_engine

    monkeypatch.setattr("core.database_service.connect_engine", fake_connect_engine)

    success, message, engine = service.connect_database(
        db_type="mysql",
        host="localhost",
        port=3306,
        username="root",
        password="secret",
        database="user_selected_db_42",
    )

    assert success is True
    assert engine is fake_engine
    assert captured["database"] == "user_selected_db_42"
    assert service.get_db_config()["database"] == "user_selected_db_42"
    assert "user_selected_db_42" in message


def test_build_knowledge_base_requires_active_database_connection(monkeypatch, tmp_path):
    monkeypatch.setenv("VECTOR_INDEX_DIR", str(tmp_path / "vector_index"))
    service = DatabaseService()

    success, message, knowledge_base = service.build_knowledge_base()

    assert success is False
    assert knowledge_base is None
    assert message == "No database connection. Connect a database first."


def test_build_knowledge_base_reports_partial_ai_enrichment(monkeypatch, tmp_path):
    monkeypatch.setenv("VECTOR_INDEX_DIR", str(tmp_path / "vector_index"))
    engine = create_engine("sqlite:///:memory:")
    metadata = MetaData()
    Table(
        "orders",
        metadata,
        Column("order_id", Integer, primary_key=True),
        Column("final_amount", Integer),
    )
    Table(
        "customers",
        metadata,
        Column("customer_id", Integer, primary_key=True),
        Column("customer_name", Integer),
    )
    metadata.create_all(engine)

    service = DatabaseService()
    service.engine = engine

    monkeypatch.setattr("core.database_service.save_json", lambda data, path: None)
    monkeypatch.setattr("core.database_service.save_business_glossary", lambda glossary, path: None)
    monkeypatch.setattr("core.database_service.check_ollama_status", lambda: (True, "Ollama is running."))

    def fake_enrich(kb, backend="local"):
        result = {
            table_name: {
                **table_data,
                **({"business_description": f"{table_name} enriched"} if table_name == "orders" else {}),
            }
            for table_name, table_data in kb.items()
        }
        monkeypatch.setattr(
            "core.database_service.get_last_enrichment_report",
            lambda: (["orders"], {"customers": "Local AI timed out"}),
        )
        monkeypatch.setattr(
            "core.database_service.get_last_enrichment_reason",
            lambda: "Partial AI enrichment fallback",
        )
        return result

    monkeypatch.setattr("core.database_service.enrich_knowledge_base_with_ai", fake_enrich)

    success, message, knowledge_base = service.build_knowledge_base(
        use_ai_enrichment=True,
        ai_backend="local",
    )

    assert success is True
    assert knowledge_base["orders"]["business_description"] == "orders enriched"
    assert service.get_last_ai_enrichment_result() == (
        "partial",
        "AI enrichment completed for 1 table(s); fallback used for 1 table(s).",
    )


def test_build_knowledge_base_keeps_generated_glossary_active_and_builds_vector_index(monkeypatch, tmp_path):
    monkeypatch.setenv("VECTOR_INDEX_DIR", str(tmp_path / "vector_index"))
    engine = create_engine("sqlite:///:memory:")
    metadata = MetaData()
    Table(
        "orders",
        metadata,
        Column("order_id", Integer, primary_key=True),
        Column("final_amount", Integer),
    )
    metadata.create_all(engine)

    active_glossary = {
        "sales": {
            "description": "Sales amount",
            "mapped_columns": [{"table": "orders", "column": "final_amount", "confidence": "high"}],
            "example_questions": ["show total sales"],
        }
    }

    service = DatabaseService()
    service.engine = engine
    service.db_config = {"db_type": "mysql", "database": "dynamic_runtime_db"}

    monkeypatch.setattr("core.database_service.save_json", lambda data, path: None)
    monkeypatch.setattr("core.database_service.save_business_glossary", lambda glossary, path: None)
    monkeypatch.setattr("core.database_service.generate_business_glossary", lambda kb, use_ai_enrichment=False: active_glossary)

    success, message, knowledge_base = service.build_knowledge_base(use_ai_enrichment=False)

    assert success is True
    assert "orders" in knowledge_base
    assert service.get_business_glossary() == active_glossary
    retriever = service.get_vector_retriever()
    assert retriever is not None
    assert "orders" in retriever.get_relevant_tables("show order sales", top_k=5)
    embedding_status = service.get_embedding_status()
    vector_status = service.get_vector_status()
    assert embedding_status["configured_backend"] == "local"
    assert "backend" in embedding_status
    assert vector_status["index_status"] == "ready"
    assert vector_status["retriever"]["index_built"] is True
    assert vector_status["retriever"]["document_count"] >= 3
    assert vector_status["persistence"]["rebuilt"] is True
    assert vector_status["persistence"]["persisted"] is True
    assert vector_status["persistence"]["database_name"] == "dynamic_runtime_db"


def test_fresh_knowledge_base_build_persists_database_identity_and_schema_hash(monkeypatch, tmp_path):
    monkeypatch.setenv("VECTOR_INDEX_DIR", str(tmp_path / "vector_index"))
    engine = create_engine("sqlite:///:memory:")
    metadata = MetaData()
    Table(
        "runtime_records",
        metadata,
        Column("record_id", Integer, primary_key=True),
        Column("record_value", Integer, nullable=False),
    )
    metadata.create_all(engine)

    service = DatabaseService()
    service.engine = engine
    service.db_config = {
        "db_type": "sqlite",
        "sqlite_path": "dynamic_runtime.sqlite",
    }
    persisted = {}

    def capture_json(data, path):
        persisted[path] = data

    monkeypatch.setattr("core.database_service.save_json", capture_json)
    monkeypatch.setattr("core.database_service.save_business_glossary", lambda glossary, path: None)
    monkeypatch.setattr(service, "refresh_vector_index", lambda: None)

    success, _, knowledge_base = service.build_knowledge_base(use_ai_enrichment=False)

    assert success is True
    assert "runtime_records" in knowledge_base
    metadata_payload = persisted["semantic/knowledge_base.meta.json"]
    assert metadata_payload["db_engine"] == "sqlite"
    assert metadata_payload["db_name"] == "dynamic_runtime.sqlite"
    assert metadata_payload["database_name"] == "dynamic_runtime.sqlite"
    assert len(metadata_payload["schema_hash"]) == 64
    assert metadata_payload["schema_fingerprint"] == metadata_payload["schema_hash"]


def test_build_knowledge_base_exposes_module_summary_as_counts(monkeypatch, tmp_path):
    monkeypatch.setenv("VECTOR_INDEX_DIR", str(tmp_path / "vector_index"))
    engine = create_engine("sqlite:///:memory:")
    metadata = MetaData()
    Table(
        "customer_records",
        metadata,
        Column("record_id", Integer, primary_key=True),
        Column("record_name", Integer),
    )
    metadata.create_all(engine)

    service = DatabaseService()
    service.engine = engine
    service.db_config = {"db_type": "sqlite", "sqlite_path": ":memory:"}

    monkeypatch.setattr("core.database_service.save_json", lambda data, path: None)
    monkeypatch.setattr("core.database_service.save_business_glossary", lambda glossary, path: None)

    success, message, knowledge_base = service.build_knowledge_base(use_ai_enrichment=False)

    assert success is True
    summary = service.get_last_build_summary()
    assert isinstance(summary["modules_detected"], dict)
    assert summary["modules_detected"]
    assert sum(summary["modules_detected"].values()) == len(knowledge_base)


def test_database_service_loads_persisted_index_when_fresh(monkeypatch, tmp_path):
    monkeypatch.setenv("EMBEDDING_BACKEND", "unsupported")
    monkeypatch.setenv("VECTOR_INDEX_DIR", str(tmp_path / "vector_index"))
    knowledge_base = {
        "orders": {
            "module": "transaction",
            "business_purpose": "Stores sales orders",
            "columns": [
                {"name": "order_id", "type": "int", "semantic_type": "id"},
                {"name": "final_amount", "type": "decimal", "semantic_type": "money"},
            ],
            "relationships": [],
        }
    }
    glossary = {
        "sales": {
            "description": "Sales amount",
            "mapped_columns": [{"table": "orders", "column": "final_amount", "confidence": "high"}],
            "example_questions": ["show total sales"],
        }
    }

    first_service = DatabaseService()
    first_service.knowledge_base = knowledge_base
    first_service.business_glossary = glossary
    first_service.refresh_vector_index()

    first_status = first_service.get_vector_status()
    assert first_status["persistence"]["source"] == "rebuilt"
    assert first_status["persistence"]["persisted"] is True

    second_service = DatabaseService()
    second_service.knowledge_base = knowledge_base
    second_service.business_glossary = glossary

    def fail_if_rebuilt(*args, **kwargs):
        raise AssertionError("Fresh persisted vector index should load from disk instead of rebuilding")

    monkeypatch.setattr(second_service.vector_index_builder, "build_from_knowledge_base", fail_if_rebuilt)
    monkeypatch.setattr(second_service.vector_index_builder, "build_from_glossary", fail_if_rebuilt)

    second_service.refresh_vector_index()

    second_status = second_service.get_vector_status()
    assert second_status["index_status"] == "ready"
    assert second_status["persistence"]["loaded_from_disk"] is True
    assert second_status["persistence"]["source"] == "disk"
    assert "orders" in second_service.get_vector_retriever().get_relevant_tables("show sales orders", top_k=5)


def test_database_service_rebuilds_persisted_index_when_glossary_changes(monkeypatch, tmp_path):
    monkeypatch.setenv("EMBEDDING_BACKEND", "unsupported")
    monkeypatch.setenv("VECTOR_INDEX_DIR", str(tmp_path / "vector_index"))
    knowledge_base = {
        "stock_positions": {
            "module": "snapshot",
            "business_purpose": "Tracks stock levels",
            "columns": [
                {"name": "warehouse_code", "type": "varchar", "semantic_type": "code"},
                {"name": "quantity_on_hand", "type": "int", "semantic_type": "quantity"},
            ],
            "relationships": [],
        }
    }
    first_glossary = {
        "stock": {
            "description": "Current stock",
            "mapped_columns": [{"table": "stock_positions", "column": "quantity_on_hand", "confidence": "high"}],
            "example_questions": ["show stock"],
        }
    }
    second_glossary = {
        "inventory": {
            "description": "Inventory balance",
            "mapped_columns": [{"table": "stock_positions", "column": "quantity_on_hand", "confidence": "high"}],
            "example_questions": ["show inventory by warehouse"],
        }
    }

    first_service = DatabaseService()
    first_service.knowledge_base = knowledge_base
    first_service.business_glossary = first_glossary
    first_service.refresh_vector_index()

    second_service = DatabaseService()
    second_service.knowledge_base = knowledge_base
    second_service.business_glossary = second_glossary
    second_service.refresh_vector_index()

    second_status = second_service.get_vector_status()
    assert second_status["index_status"] == "ready"
    assert second_status["persistence"]["source"] == "rebuilt"
    assert second_status["persistence"]["rebuilt"] is True
    assert "glossary hash changed" in second_status["persistence"]["stale_reason"]


def test_database_service_rebuilds_persisted_index_when_schema_changes(monkeypatch, tmp_path):
    monkeypatch.setenv("EMBEDDING_BACKEND", "unsupported")
    monkeypatch.setenv("VECTOR_INDEX_DIR", str(tmp_path / "vector_index"))
    first_kb = {
        "alpha_records": {
            "module": "reference",
            "business_purpose": "Stores alpha records",
            "columns": [
                {"name": "record_id", "type": "int", "semantic_type": "id"},
                {"name": "record_name", "type": "varchar", "semantic_type": "name"},
            ],
            "relationships": [],
        }
    }
    second_kb = {
        "alpha_records": {
            "module": "reference",
            "business_purpose": "Stores alpha records",
            "columns": [
                {"name": "record_id", "type": "int", "semantic_type": "id"},
                {"name": "record_name", "type": "varchar", "semantic_type": "name"},
                {"name": "created_on", "type": "date", "semantic_type": "date"},
            ],
            "relationships": [],
        }
    }

    first_service = DatabaseService()
    first_service.knowledge_base = first_kb
    first_service.knowledge_base_origin = "built"
    first_service.business_glossary = {}
    first_service.refresh_vector_index()

    second_service = DatabaseService()
    second_service.knowledge_base = second_kb
    second_service.knowledge_base_origin = "built"
    second_service.business_glossary = {}
    second_service.refresh_vector_index()

    second_status = second_service.get_vector_status()
    assert second_status["index_status"] == "ready"
    assert second_status["persistence"]["source"] == "rebuilt"
    assert "schema hash changed" in second_status["persistence"]["stale_reason"]


def test_database_service_marks_loaded_index_stale_when_connected_database_changes(monkeypatch, tmp_path):
    monkeypatch.setenv("EMBEDDING_BACKEND", "unsupported")
    monkeypatch.setenv("VECTOR_INDEX_DIR", str(tmp_path / "vector_index"))
    knowledge_base = {
        "orders": {
            "module": "transaction",
            "business_purpose": "Stores order rows",
            "columns": [
                {"name": "order_id", "type": "int", "semantic_type": "id"},
                {"name": "amount_value", "type": "decimal", "semantic_type": "money"},
            ],
            "relationships": [],
        }
    }

    first_service = DatabaseService()
    first_service.knowledge_base = knowledge_base
    first_service.knowledge_base_origin = "built"
    first_service.business_glossary = {}
    first_service.db_config = {"db_type": "mysql", "database": "alpha_db"}
    first_service.refresh_vector_index()

    second_service = DatabaseService()
    second_service.knowledge_base = knowledge_base
    second_service.knowledge_base_origin = "loaded"
    second_service.business_glossary = {}
    second_service.db_config = {"db_type": "mysql", "database": "beta_db"}
    second_service.engine = object()
    second_service.refresh_vector_index()

    second_status = second_service.get_vector_status()
    assert second_status["index_status"] == "stale"
    assert second_status["persistence"]["source"] == "stale"
    assert "database name changed" in second_status["persistence"]["stale_reason"]


def test_load_knowledge_base_rejects_cached_kb_for_different_connected_database(monkeypatch, tmp_path):
    monkeypatch.setenv("VECTOR_INDEX_DIR", str(tmp_path / "vector_index"))
    service = DatabaseService()
    service.engine = object()
    service.db_config = {"db_type": "mysql", "database": "connected_db"}
    service.knowledge_base = {"stale": {"columns": []}}
    service.business_glossary = {"stale": {"mapped_columns": []}}

    monkeypatch.setattr(
        "core.database_service.load_json",
        lambda path: {"orders": {"columns": [], "primary_keys": [], "foreign_keys": [], "relationships": []}},
    )
    monkeypatch.setattr(
        service,
        "_load_knowledge_base_metadata_file",
        lambda: {"database_type": "mysql", "database_name": "other_db"},
    )

    success, message, knowledge_base = service.load_knowledge_base()

    assert success is False
    assert knowledge_base is None
    assert "Connected database differs from the cached knowledge base" in message
    assert service.get_knowledge_base() is None
    assert service.get_business_glossary() is None
    assert service.get_vector_status()["index_status"] == "stale"


def test_runtime_code_has_no_hardcoded_demo_database_dependencies():
    repo_root = Path(__file__).resolve().parents[1]
    runtime_files = [
        repo_root / "main.py",
        repo_root / "core" / "app_service.py",
        repo_root / "core" / "database_service.py",
        repo_root / "db" / "connection.py",
        repo_root / "vector_store" / "persistence.py",
    ]
    forbidden_names = [
        "ai_sales_demo",
        "vector_erp_test",
        "pcsoft_erp_test",
        "company_db",
    ]

    for path in runtime_files:
        text = path.read_text(encoding="utf-8")
        for forbidden in forbidden_names:
            assert forbidden not in text, f"Runtime file {path.name} contains hardcoded database name '{forbidden}'"
