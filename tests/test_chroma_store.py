"""Tests for ChromaDB-backed semantic retrieval with fallback safety."""

from __future__ import annotations

import math
from types import SimpleNamespace

from kb_pipeline.vector.chroma_store import ChromaStore
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
                {"name": "item_label", "type": "varchar", "semantic_type": "text_candidate"},
                {"name": "units_available", "type": "int", "semantic_type": "numeric_candidate"},
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
