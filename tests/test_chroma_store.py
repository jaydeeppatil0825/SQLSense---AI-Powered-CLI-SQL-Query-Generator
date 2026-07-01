"""Tests for ChromaDB-backed semantic retrieval with fallback safety."""

from __future__ import annotations

import math
from types import SimpleNamespace

from kb_pipeline.vector.chroma_store import ChromaStore
from kb_pipeline.schema_facts import enrich_knowledge_base_schema_facts
from vector_store import EmbeddingService, VectorIndexBuilder
from core.database_service import DatabaseService


class _FakeChromaCollection:
    def __init__(self, bucket: dict):
        self._bucket = bucket

    def upsert(self, ids, documents, metadatas, embeddings):
        for doc_id, document, metadata, embedding in zip(ids, documents, metadatas, embeddings):
            self._bucket["items"][doc_id] = {
                "document": document,
                "metadata": metadata,
                "embedding": embedding,
            }

    def query(self, query_embeddings, n_results, where=None):
        query_embedding = list(query_embeddings[0])
        matches = []
        for item in self._bucket["items"].values():
            metadata = item["metadata"]
            if where and metadata.get("doc_type") != where.get("doc_type"):
                continue
            score = _cosine(query_embedding, list(item["embedding"]))
            matches.append((score, item))
        matches.sort(key=lambda entry: entry[0], reverse=True)
        top_matches = matches[:n_results]
        return {
            "documents": [[entry[1]["document"] for entry in top_matches]],
            "metadatas": [[entry[1]["metadata"] for entry in top_matches]],
            "distances": [[1.0 - entry[0] for entry in top_matches]],
        }

    def count(self):
        return len(self._bucket["items"])


class _FakePersistentClient:
    _stores: dict[str, dict[str, dict]] = {}

    def __init__(self, path: str):
        self.path = str(path)
        self._stores.setdefault(self.path, {})

    def list_collections(self):
        return [SimpleNamespace(name=name) for name in self._stores[self.path].keys()]

    def delete_collection(self, name: str):
        self._stores[self.path].pop(name, None)

    def get_or_create_collection(self, name: str, metadata=None):
        bucket = self._stores[self.path].setdefault(name, {"metadata": metadata or {}, "items": {}})
        return _FakeChromaCollection(bucket)

    def get_collection(self, name: str):
        if name not in self._stores[self.path]:
            raise KeyError(name)
        return _FakeChromaCollection(self._stores[self.path][name])


def _fake_chromadb_module():
    return SimpleNamespace(PersistentClient=_FakePersistentClient)


def test_chroma_client_disables_anonymized_telemetry_and_still_builds(monkeypatch, tmp_path):
    captured = {}

    class FakeSettings:
        def __init__(self, **kwargs):
            captured["settings_kwargs"] = kwargs

    class SettingsAwareClient(_FakePersistentClient):
        def __init__(self, path: str, settings=None):
            captured["client_settings"] = settings
            super().__init__(path)

    def fake_import(name):
        if name == "chromadb":
            return SimpleNamespace(PersistentClient=SettingsAwareClient)
        if name == "chromadb.config":
            return SimpleNamespace(Settings=FakeSettings)
        raise ImportError(name)

    monkeypatch.setenv("EMBEDDING_BACKEND", "unsupported")
    monkeypatch.setattr("kb_pipeline.vector.chroma_store.importlib.import_module", fake_import)
    embedding_service = EmbeddingService()
    builder = VectorIndexBuilder(embedding_service)
    store = ChromaStore(embedding_service, persist_dir=tmp_path / "chroma")
    documents = builder.build_from_knowledge_base(_sample_kb(), source_context=_source_context())

    built, _, details = store.build_or_refresh_chroma_index(documents, _source_context())

    assert captured["settings_kwargs"] == {"anonymized_telemetry": False}
    assert captured["client_settings"] is not None
    assert built is True
    assert details["ready"] is True
    assert store.is_ready() is True


def _cosine(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if not left_norm or not right_norm:
        return 0.0
    return dot / (left_norm * right_norm)


def _source_context(db_name: str = "dynamic_ops_lab", schema_hash: str = "schema-h1") -> dict:
    return {
        "db_engine": "mysql",
        "db_host": "localhost",
        "db_port": "3306",
        "db_name": db_name,
        "schema_hash": schema_hash,
    }


def _sample_kb():
    return {
        "stock_positions": {
            "business_purpose": "Tracks stock levels by storage point",
            "columns": [
                {"name": "stock_id", "type": "int", "semantic_type": "id"},
                {
                    "name": "item_label",
                    "type": "varchar",
                    "semantic_type": "text_candidate",
                    "is_dimension": True,
                    "ai_metadata": {"business_terms": ["item label"], "business_description": "Display item label."},
                },
                {"name": "units_available", "type": "int", "semantic_type": "numeric_candidate", "is_measure": True},
                {"name": "snapshot_date", "type": "date", "semantic_type": "date", "is_date": True},
            ],
            "relationships": [
                {
                    "from_table": "stock_positions",
                    "from_column": "stock_id",
                    "to_table": "partners",
                    "to_column": "partner_id",
                    "direction": "many-to-one",
                    "confidence": 0.91,
                    "reason": "relationship evidence",
                }
            ],
        }
    }


def _sample_glossary():
    return {
        "stock positions": {
            "description": "Current stock by location",
            "primary_terms": ["stock positions", "stock position"],
            "related_terms": [],
            "business_terms": ["stock positions", "stock position"],
            "mapped_tables": ["stock_positions"],
            "mapped_columns": [],
            "related_tables": [],
            "target_type": "table",
            "usage_scope": "table_lookup",
            "confidence": 0.99,
            "example_questions": ["show stock positions"],
        }
    }


def test_chroma_unavailable_falls_back_to_existing_vector_retriever(monkeypatch, tmp_path):
    monkeypatch.setenv("EMBEDDING_BACKEND", "unsupported")
    monkeypatch.setenv("VECTOR_INDEX_DIR", str(tmp_path / "vector_index"))
    monkeypatch.setenv("CHROMA_INDEX_DIR", str(tmp_path / "chroma"))
    monkeypatch.setattr(
        "kb_pipeline.vector.chroma_store.importlib.import_module",
        lambda name: (_ for _ in ()).throw(ImportError("chromadb missing")),
    )

    service = DatabaseService()
    service.knowledge_base = _sample_kb()
    service.business_glossary = _sample_glossary()
    service.db_config = {"db_type": "mysql", "host": "localhost", "port": 3306, "database": "dynamic_ops_lab"}

    service.refresh_vector_index()

    retriever = service.get_vector_retriever()
    status = service.get_vector_status()

    assert status["index_status"] == "ready"
    assert status["retriever"]["active_backend"] == "fallback"
    assert status["chroma"]["ready"] is False
    assert "stock_positions" in retriever.get_relevant_tables("show stock positions", top_k=5)


def test_chroma_metadata_contains_db_name_and_schema_hash(monkeypatch, tmp_path):
    monkeypatch.setenv("EMBEDDING_BACKEND", "unsupported")
    monkeypatch.setattr(
        "kb_pipeline.vector.chroma_store.importlib.import_module",
        lambda name: _fake_chromadb_module(),
    )

    embedding_service = EmbeddingService()
    builder = VectorIndexBuilder(embedding_service)
    store = ChromaStore(embedding_service, persist_dir=tmp_path / "chroma")
    documents = builder.build_from_knowledge_base(_sample_kb(), source_context=_source_context())
    documents += builder.build_from_glossary(_sample_glossary(), source_context=_source_context())

    built, _, details = store.build_or_refresh_chroma_index(documents, _source_context())

    assert built is True
    client_store = _FakePersistentClient._stores[str(tmp_path / "chroma")]
    collection_bucket = client_store[details["collection_name"]]
    stored_metadata = list(collection_bucket["items"].values())[0]["metadata"]
    assert stored_metadata["db_name"] == "dynamic_ops_lab"
    assert stored_metadata["schema_hash"] == "schema-h1"
    assert stored_metadata["doc_type"] in {"table", "column", "glossary", "relationship"}


def test_chroma_table_docs_are_searchable(monkeypatch, tmp_path):
    monkeypatch.setenv("EMBEDDING_BACKEND", "unsupported")
    monkeypatch.setattr(
        "kb_pipeline.vector.chroma_store.importlib.import_module",
        lambda name: _fake_chromadb_module(),
    )

    embedding_service = EmbeddingService()
    builder = VectorIndexBuilder(embedding_service)
    store = ChromaStore(embedding_service, persist_dir=tmp_path / "chroma")
    documents = builder.build_from_knowledge_base(_sample_kb(), source_context=_source_context())
    documents += builder.build_from_glossary(_sample_glossary(), source_context=_source_context())
    store.build_or_refresh_chroma_index(documents, _source_context())

    results = store.get_relevant_table_details("show stock positions", top_k=5)

    assert results
    assert results[0]["table_name"] == "stock_positions"
    assert results[0]["db_name"] == "dynamic_ops_lab"


def test_simple_chroma_retrieval_excludes_relationship_only_matches(monkeypatch, tmp_path):
    monkeypatch.setenv("EMBEDDING_BACKEND", "unsupported")
    monkeypatch.setattr(
        "kb_pipeline.vector.chroma_store.importlib.import_module",
        lambda name: _fake_chromadb_module(),
    )

    embedding_service = EmbeddingService()
    builder = VectorIndexBuilder(embedding_service)
    store = ChromaStore(embedding_service, persist_dir=tmp_path / "chroma")
    documents = builder.build_from_knowledge_base(_sample_kb(), source_context=_source_context())
    store.build_or_refresh_chroma_index(documents, _source_context())

    simple_tables = store.search_tables("partners", top_k=5)
    relationship_hits = store.search_relationships("partners", top_k=5)

    assert "stock_positions" not in simple_tables
    assert relationship_hits
    assert relationship_hits[0]["from_table"] == "stock_positions"


def test_stale_db_or_schema_collection_is_not_reused(monkeypatch, tmp_path):
    monkeypatch.setenv("EMBEDDING_BACKEND", "unsupported")
    monkeypatch.setattr(
        "kb_pipeline.vector.chroma_store.importlib.import_module",
        lambda name: _fake_chromadb_module(),
    )

    embedding_service = EmbeddingService()
    builder = VectorIndexBuilder(embedding_service)
    store = ChromaStore(embedding_service, persist_dir=tmp_path / "chroma")
    documents = builder.build_from_knowledge_base(_sample_kb(), source_context=_source_context(db_name="alpha_db", schema_hash="schema-a"))
    documents += builder.build_from_glossary(_sample_glossary(), source_context=_source_context(db_name="alpha_db", schema_hash="schema-a"))
    store.build_or_refresh_chroma_index(documents, _source_context(db_name="alpha_db", schema_hash="schema-a"))

    loaded_same, _, _ = store.load_current_collection(_source_context(db_name="alpha_db", schema_hash="schema-a"))
    loaded_other_db, _, _ = store.load_current_collection(_source_context(db_name="beta_db", schema_hash="schema-a"))
    loaded_other_schema, _, _ = store.load_current_collection(_source_context(db_name="alpha_db", schema_hash="schema-b"))

    assert loaded_same is True
    assert loaded_other_db is False
    assert loaded_other_schema is False


def test_vector_index_builder_includes_specialized_foundation_evidence(monkeypatch):
    monkeypatch.setenv("EMBEDDING_BACKEND", "unsupported")

    embedding_service = EmbeddingService()
    builder = VectorIndexBuilder(embedding_service)
    documents = builder.build_from_knowledge_base(_sample_kb(), source_context=_source_context())

    doc_types = {document["metadata"]["type"] for document in documents}

    assert {"table", "column", "relationship", "measure", "dimension", "date", "semantic_metadata", "ai_metadata"} <= doc_types


def test_real_and_fallback_relationship_evidence_is_indexed_and_retrievable(monkeypatch, tmp_path):
    monkeypatch.setenv("EMBEDDING_BACKEND", "unsupported")
    monkeypatch.setattr(
        "kb_pipeline.vector.chroma_store.importlib.import_module",
        lambda name: _fake_chromadb_module(),
    )
    schema_data = {
        "customers": {
            "columns": [{"name": "customer_id", "type": "INTEGER", "sample_values": [1, 2]}],
            "primary_keys": ["customer_id"],
            "foreign_keys": [],
        },
        "orders": {
            "columns": [{"name": "customer_id", "type": "INTEGER", "sample_values": [1, 2]}],
            "primary_keys": [],
            "foreign_keys": [
                {
                    "column": "customer_id",
                    "referenced_table": "customers",
                    "referenced_column": "customer_id",
                }
            ],
        },
        "visits": {
            "columns": [{"name": "customer_id", "type": "INTEGER", "sample_values": [1, 2]}],
            "primary_keys": [],
            "foreign_keys": [],
        },
    }
    knowledge_base = enrich_knowledge_base_schema_facts(schema_data)
    embedding_service = EmbeddingService()
    builder = VectorIndexBuilder(embedding_service)
    documents = builder.build_from_knowledge_base(knowledge_base, source_context=_source_context())
    relationship_documents = [doc for doc in documents if doc["metadata"]["type"] == "relationship"]

    assert {doc["metadata"]["relationship_type"] for doc in relationship_documents} == {"foreign_key", "inferred"}
    real_document = next(doc for doc in relationship_documents if doc["metadata"]["relationship_type"] == "foreign_key")
    fallback_document = next(doc for doc in relationship_documents if doc["metadata"]["relationship_type"] == "inferred")
    assert real_document["metadata"]["confidence"] == 1.0
    assert real_document["metadata"]["relationship_source"] == "database_metadata"
    assert fallback_document["metadata"]["safe_for_planner"] is True
    assert "strong_sample_overlap" in fallback_document["metadata"]["evidence"]

    store = ChromaStore(embedding_service, persist_dir=tmp_path / "chroma")
    store.build_or_refresh_chroma_index(documents, _source_context())
    package = store.get_normalized_evidence_package("customer relationships", top_k=10)

    assert {item["relationship_type"] for item in package["relationships"]} == {"foreign_key", "inferred"}


def test_glossary_vector_document_indexes_profile_and_sample_evidence(monkeypatch):
    monkeypatch.setenv("EMBEDDING_BACKEND", "unsupported")
    builder = VectorIndexBuilder(EmbeddingService())
    glossary = {
        "event state": {
            "description": "Schema state column.",
            "primary_terms": ["event state"],
            "related_terms": [],
            "sources": ["schema_identifier", "profiling", "sample_values"],
            "mapped_columns": [
                {
                    "table": "event_records",
                    "column": "event_state",
                    "sample_values": ["Open", "Closed"],
                    "profile_facts": {"unique_count": 2},
                }
            ],
        }
    }

    document = builder.build_from_glossary(glossary, source_context=_source_context())[0]

    assert "Sample value evidence: Open, Closed" in document["text"]
    assert document["metadata"]["sample_values"] == ["Open", "Closed"]
    assert document["metadata"]["profile_facts"] == [{"unique_count": 2}]


def test_chroma_returns_normalized_evidence_package(monkeypatch, tmp_path):
    monkeypatch.setenv("EMBEDDING_BACKEND", "unsupported")
    monkeypatch.setattr(
        "kb_pipeline.vector.chroma_store.importlib.import_module",
        lambda name: _fake_chromadb_module(),
    )

    embedding_service = EmbeddingService()
    builder = VectorIndexBuilder(embedding_service)
    store = ChromaStore(embedding_service, persist_dir=tmp_path / "chroma")
    documents = builder.build_from_knowledge_base(_sample_kb(), source_context=_source_context())
    documents += builder.build_from_glossary(_sample_glossary(), source_context=_source_context())
    store.build_or_refresh_chroma_index(documents, _source_context())

    package = store.get_normalized_evidence_package("show stock positions by item label", top_k=5)

    assert package["candidate_tables"]
    assert package["candidate_columns"]
    assert package["candidate_metrics"]
    assert package["candidate_dimensions"]
    assert package["candidate_dates"]
    assert package["relationships"]
    assert package["glossary_matches"]
    assert package["source_metadata"]["backend"] == "chroma"
    assert package["retrieval_sources"] == ["vector"]
