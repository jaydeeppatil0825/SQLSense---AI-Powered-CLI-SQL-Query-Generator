"""Tests for database service knowledge-base workflow."""

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
