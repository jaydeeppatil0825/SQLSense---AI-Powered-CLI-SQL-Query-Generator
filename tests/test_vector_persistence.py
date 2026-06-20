"""Tests for persistent vector index storage."""

from datetime import date, datetime, timezone
from decimal import Decimal

from vector_store import EmbeddingService, VectorIndexBuilder, VectorIndexPersistence


def _sample_knowledge_base():
    return {
        "stock_positions": {
            "module": "snapshot",
            "business_purpose": "Tracks stock levels by warehouse",
            "columns": [
                {"name": "warehouse_code", "type": "varchar", "semantic_type": "code"},
                {"name": "quantity_on_hand", "type": "int", "semantic_type": "quantity"},
            ],
            "relationships": [
                {
                    "from_table": "stock_positions",
                    "from_column": "warehouse_code",
                    "to_table": "warehouse_directory",
                    "to_column": "warehouse_code",
                    "direction": "many-to-one",
                    "confidence": 0.98,
                    "reason": "warehouse stock reference",
                }
            ],
        },
        "warehouse_directory": {
            "module": "reference",
            "business_purpose": "Stores warehouse details",
            "columns": [
                {"name": "warehouse_code", "type": "varchar", "semantic_type": "code"},
                {"name": "warehouse_name", "type": "varchar", "semantic_type": "name"},
            ],
            "relationships": [],
        },
    }


def _sample_glossary(term="stock"):
    return {
        term: {
            "description": "Current stock on hand",
            "mapped_columns": [
                {"table": "stock_positions", "column": "quantity_on_hand", "confidence": "high"}
            ],
            "business_terms": ["inventory"],
            "example_questions": ["show current stock by warehouse"],
        }
    }


def _source_context(database_name="analytics_demo", schema_fingerprint="schema-v1"):
    return {
        "database_name": database_name,
        "database_type": "mysql",
        "schema_fingerprint": schema_fingerprint,
    }


def test_vector_index_persistence_save_and_load(monkeypatch, tmp_path):
    monkeypatch.setenv("EMBEDDING_BACKEND", "unsupported")
    service = EmbeddingService()
    builder = VectorIndexBuilder(service)
    persistence = VectorIndexPersistence(tmp_path / "vector_index")

    knowledge_base = _sample_knowledge_base()
    glossary = _sample_glossary()
    documents = builder.build_from_knowledge_base(knowledge_base) + builder.build_from_glossary(glossary)

    saved, message, save_details = persistence.save_documents(
        documents,
        knowledge_base,
        glossary,
        service,
        source_context=_source_context(),
    )
    assert saved is True
    assert "Saved" in message
    assert save_details["persisted"] is True

    loaded, load_message, loaded_documents, load_details = persistence.load_documents(
        knowledge_base,
        glossary,
        service,
        source_context=_source_context(),
    )
    assert loaded is True
    assert "Loaded" in load_message
    assert len(loaded_documents) == len(documents)
    assert load_details["loaded_from_disk"] is True
    assert load_details["source"] == "disk"
    assert load_details["fresh"] is True
    manifest = persistence._read_json(persistence.manifest_path)
    assert manifest["database_name"] == "analytics_demo"
    assert manifest["schema_fingerprint"] == "schema-v1"
    assert manifest["created_at"]
    assert manifest["document_count"] == len(documents)


def test_vector_index_persistence_detects_stale_hash_change(monkeypatch, tmp_path):
    monkeypatch.setenv("EMBEDDING_BACKEND", "unsupported")
    service = EmbeddingService()
    builder = VectorIndexBuilder(service)
    persistence = VectorIndexPersistence(tmp_path / "vector_index")

    knowledge_base = _sample_knowledge_base()
    first_glossary = _sample_glossary("stock")
    second_glossary = _sample_glossary("inventory")
    documents = builder.build_from_knowledge_base(knowledge_base) + builder.build_from_glossary(first_glossary)

    saved, _, _ = persistence.save_documents(
        documents,
        knowledge_base,
        first_glossary,
        service,
        source_context=_source_context(),
    )
    assert saved is True

    inspection = persistence.inspect_index(
        knowledge_base,
        second_glossary,
        service,
        source_context=_source_context(),
    )
    assert inspection["fresh"] is False
    assert "glossary hash changed" in inspection["stale_reason"]


def test_vector_index_persistence_rejects_corrupted_documents(monkeypatch, tmp_path):
    monkeypatch.setenv("EMBEDDING_BACKEND", "unsupported")
    service = EmbeddingService()
    builder = VectorIndexBuilder(service)
    persistence = VectorIndexPersistence(tmp_path / "vector_index")

    knowledge_base = _sample_knowledge_base()
    glossary = _sample_glossary()
    documents = builder.build_from_knowledge_base(knowledge_base) + builder.build_from_glossary(glossary)

    saved, _, _ = persistence.save_documents(
        documents,
        knowledge_base,
        glossary,
        service,
        source_context=_source_context(),
    )
    assert saved is True

    persistence.documents_path.write_text("{not-json", encoding="utf-8")

    loaded, message, loaded_documents, details = persistence.load_documents(
        knowledge_base,
        glossary,
        service,
        source_context=_source_context(),
    )
    assert loaded is False
    assert loaded_documents == []
    assert "persisted index unreadable" in message
    assert details["source"] == "rebuild_required"


def test_vector_index_persistence_detects_embedding_dimension_change(monkeypatch, tmp_path):
    monkeypatch.setenv("EMBEDDING_BACKEND", "unsupported")
    first_service = EmbeddingService()
    builder = VectorIndexBuilder(first_service)
    persistence = VectorIndexPersistence(tmp_path / "vector_index")

    knowledge_base = _sample_knowledge_base()
    glossary = _sample_glossary()
    documents = builder.build_from_knowledge_base(knowledge_base) + builder.build_from_glossary(glossary)

    saved, _, _ = persistence.save_documents(
        documents,
        knowledge_base,
        glossary,
        first_service,
        source_context=_source_context(),
    )
    assert saved is True

    second_service = EmbeddingService()
    second_service._dimension = 8

    inspection = persistence.inspect_index(
        knowledge_base,
        glossary,
        second_service,
        source_context=_source_context(),
    )
    assert inspection["fresh"] is False
    assert "embedding dimension changed" in inspection["stale_reason"]


def test_vector_index_persistence_detects_database_name_change(monkeypatch, tmp_path):
    monkeypatch.setenv("EMBEDDING_BACKEND", "unsupported")
    service = EmbeddingService()
    builder = VectorIndexBuilder(service)
    persistence = VectorIndexPersistence(tmp_path / "vector_index")

    knowledge_base = _sample_knowledge_base()
    glossary = _sample_glossary()
    documents = builder.build_from_knowledge_base(knowledge_base) + builder.build_from_glossary(glossary)

    saved, _, _ = persistence.save_documents(
        documents,
        knowledge_base,
        glossary,
        service,
        source_context=_source_context(database_name="alpha_db"),
    )
    assert saved is True

    inspection = persistence.inspect_index(
        knowledge_base,
        glossary,
        service,
        source_context=_source_context(database_name="beta_db"),
    )
    assert inspection["fresh"] is False
    assert inspection["stale_reason"] == "database name changed"


def test_vector_index_persistence_detects_schema_fingerprint_change(monkeypatch, tmp_path):
    monkeypatch.setenv("EMBEDDING_BACKEND", "unsupported")
    service = EmbeddingService()
    builder = VectorIndexBuilder(service)
    persistence = VectorIndexPersistence(tmp_path / "vector_index")

    knowledge_base = _sample_knowledge_base()
    glossary = _sample_glossary()
    documents = builder.build_from_knowledge_base(knowledge_base) + builder.build_from_glossary(glossary)

    saved, _, _ = persistence.save_documents(
        documents,
        knowledge_base,
        glossary,
        service,
        source_context=_source_context(schema_fingerprint="schema-v1"),
    )
    assert saved is True

    inspection = persistence.inspect_index(
        knowledge_base,
        glossary,
        service,
        source_context=_source_context(schema_fingerprint="schema-v2"),
    )
    assert inspection["fresh"] is False
    assert inspection["stale_reason"] == "schema hash changed"


def test_vector_index_persistence_sanitizes_runtime_metadata(monkeypatch, tmp_path):
    monkeypatch.setenv("EMBEDDING_BACKEND", "unsupported")
    service = EmbeddingService()
    persistence = VectorIndexPersistence(tmp_path / "vector_index")

    knowledge_base = _sample_knowledge_base()
    glossary = _sample_glossary()
    documents = [
        {
            "text": "Runtime metadata document",
            "metadata": {
                "table_name": "stock_positions",
                "sample_date": date(2026, 6, 16),
                "sample_datetime": datetime(2026, 6, 16, 12, 30, 45, tzinfo=timezone.utc),
                "sample_decimal": Decimal("123.45"),
                "sample_bytes": b"warehouse-a",
                "nested": {
                    "values": [Decimal("9.99"), date(2026, 6, 1)],
                    "tuple_values": ("alpha", datetime(2026, 6, 16, 8, 0, 0)),
                    "set_values": {"north", "south"},
                },
            },
            "embedding": [0.1] * int(service.get_dimension() or 0),
            "tokenized_text": ["runtime", "metadata"],
        }
    ]

    saved, message, save_details = persistence.save_documents(
        documents,
        knowledge_base,
        glossary,
        service,
        source_context=_source_context(),
    )
    assert saved is True
    assert "Saved" in message
    assert save_details["persisted"] is True

    raw_documents = persistence._read_json(persistence.documents_path)
    assert raw_documents[0]["metadata"]["sample_date"] == "2026-06-16"
    assert raw_documents[0]["metadata"]["sample_datetime"] == "2026-06-16T12:30:45+00:00"
    assert raw_documents[0]["metadata"]["sample_decimal"] == "123.45"
    assert raw_documents[0]["metadata"]["sample_bytes"] == "warehouse-a"
    assert raw_documents[0]["metadata"]["nested"]["values"] == ["9.99", "2026-06-01"]
    assert raw_documents[0]["metadata"]["nested"]["tuple_values"] == ["alpha", "2026-06-16T08:00:00"]
    assert raw_documents[0]["metadata"]["nested"]["set_values"] == ["north", "south"]

    loaded, load_message, loaded_documents, load_details = persistence.load_documents(
        knowledge_base,
        glossary,
        service,
        source_context=_source_context(),
    )
    assert loaded is True
    assert "Loaded" in load_message
    assert load_details["loaded_from_disk"] is True
    assert load_details["source"] == "disk"
    assert loaded_documents[0]["metadata"]["sample_date"] == "2026-06-16"
    assert loaded_documents[0]["metadata"]["sample_datetime"] == "2026-06-16T12:30:45+00:00"
    assert loaded_documents[0]["metadata"]["sample_decimal"] == "123.45"
    assert loaded_documents[0]["metadata"]["nested"]["values"] == ["9.99", "2026-06-01"]
