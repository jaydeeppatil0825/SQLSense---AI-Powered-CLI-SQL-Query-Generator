"""Tests for database service knowledge-base workflow."""

from sqlalchemy import Column, Integer, MetaData, Table, create_engine

from core.database_service import DatabaseService


def test_build_knowledge_base_falls_back_when_ollama_is_not_running(monkeypatch):
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


def test_build_knowledge_base_reports_partial_ai_enrichment(monkeypatch):
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
