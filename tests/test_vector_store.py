"""
Tests for vector store functionality.
"""

import sys
import types

from vector_store import VectorIndexBuilder, VectorRetriever, EmbeddingService


def test_embedding_service_initialization():
    service = EmbeddingService()
    assert service is not None
    assert service.get_dimension() == 384


def test_embedding_service_embed():
    service = EmbeddingService()
    embedding = service.embed("test text")
    assert isinstance(embedding, list)
    assert len(embedding) == 384
    assert all(isinstance(x, float) for x in embedding)


def test_embedding_service_embed_batch():
    service = EmbeddingService()
    texts = ["test one", "test two", "test three"]
    embeddings = service.embed_batch(texts)
    assert len(embeddings) == 3
    assert all(len(emb) == 384 for emb in embeddings)


def test_embedding_service_forced_fallback_status(monkeypatch):
    monkeypatch.setenv("EMBEDDING_BACKEND", "unsupported")
    service = EmbeddingService()

    status = service.get_status()

    assert status["configured_backend"] == "unsupported"
    assert status["backend"] == "fallback"
    assert status["model"] == "deterministic-hash"
    assert status["fallback_used"] is True
    assert "Unsupported embedding backend" in status["init_error"]


def test_embedding_service_uses_local_sentence_transformer_when_available(monkeypatch):
    class FakeSentenceTransformer:
        def __init__(self, model_name):
            self.model_name = model_name

        def get_sentence_embedding_dimension(self):
            return 8

        def encode(self, texts, convert_to_numpy=False, normalize_embeddings=True):
            if isinstance(texts, str):
                return [0.5] * 8
            return [[0.25] * 8 for _ in texts]

    fake_module = types.SimpleNamespace(SentenceTransformer=FakeSentenceTransformer)

    monkeypatch.setenv("EMBEDDING_BACKEND", "local")
    monkeypatch.setenv("EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_module)

    service = EmbeddingService()
    status = service.get_status()

    assert status["configured_backend"] == "local"
    assert status["configured_model"] == "sentence-transformers/all-MiniLM-L6-v2"
    assert status["backend"] == "local"
    assert status["model"] == "sentence-transformers/all-MiniLM-L6-v2"
    assert status["fallback_used"] is False
    assert service.get_dimension() == 8
    assert len(service.embed("warehouse stock")) == 8
    assert all(len(embedding) == 8 for embedding in service.embed_batch(["stock", "warehouse"]))


def test_index_builder_build_from_knowledge_base():
    service = EmbeddingService()
    builder = VectorIndexBuilder(service)

    knowledge_base = {
        "client_directory": {
            "module": "reference",
            "business_purpose": "Stores client records",
            "columns": [
                {"name": "client_id", "type": "int", "semantic_type": "id"},
                {"name": "client_name", "type": "varchar", "semantic_type": "name"},
            ],
            "relationships": [
                {
                    "from_table": "client_directory",
                    "from_column": "client_id",
                    "to_table": "invoice_headers",
                    "to_column": "client_id",
                    "direction": "one-to-many",
                    "confidence": 0.95,
                    "reason": "client invoices",
                }
            ],
        },
        "invoice_headers": {
            "module": "transaction",
            "business_purpose": "Stores invoice records",
            "columns": [
                {"name": "invoice_id", "type": "int", "semantic_type": "id"},
                {"name": "client_id", "type": "int", "semantic_type": "id"},
                {"name": "total_due", "type": "decimal", "semantic_type": "money"},
            ],
            "relationships": [],
        },
    }

    documents = builder.build_from_knowledge_base(knowledge_base)
    table_docs = [d for d in documents if d["metadata"]["type"] == "table"]
    column_docs = [d for d in documents if d["metadata"]["type"] == "column"]
    relationship_docs = [d for d in documents if d["metadata"]["type"] == "relationship"]

    assert len(table_docs) == 2
    assert len(column_docs) == 5
    assert len(relationship_docs) == 1
    assert all(document.get("embedding") for document in documents)
    assert all(isinstance(document.get("tokenized_text"), list) for document in documents)


def test_index_builder_build_from_glossary():
    service = EmbeddingService()
    builder = VectorIndexBuilder(service)

    glossary = {
        "payables": {
            "description": "Open amount due",
            "mapped_columns": [{"table": "invoice_headers", "column": "total_due", "confidence": "high"}],
            "business_terms": ["amount due"],
            "example_questions": ["show current payables"],
        },
    }

    documents = builder.build_from_glossary(glossary)
    assert len(documents) == 1
    assert documents[0]["metadata"]["type"] == "glossary"
    assert documents[0]["metadata"]["term"] == "payables"


def test_retriever_search_finds_relevant_stock_documents():
    service = EmbeddingService()
    builder = VectorIndexBuilder(service)
    retriever = VectorRetriever(service)

    knowledge_base = {
        "stock_positions": {
            "module": "snapshot",
            "business_purpose": "Tracks stock levels",
            "columns": [
                {"name": "product_code", "type": "varchar", "semantic_type": "code"},
                {"name": "quantity_on_hand", "type": "int", "semantic_type": "quantity"},
                {"name": "warehouse_code", "type": "varchar", "semantic_type": "code"},
            ],
            "relationships": [],
        },
        "warehouse_directory": {
            "module": "reference",
            "business_purpose": "Stores warehouse information",
            "columns": [
                {"name": "warehouse_code", "type": "varchar", "semantic_type": "code"},
                {"name": "warehouse_name", "type": "varchar", "semantic_type": "name"},
            ],
            "relationships": [],
        },
    }

    retriever.add_documents(builder.build_from_knowledge_base(knowledge_base))
    results = retriever.search("current stock by warehouse", top_k=5)

    assert results
    assert any("stock" in result["text"].lower() or "warehouse" in result["text"].lower() for result in results)


def test_retriever_get_relevant_tables():
    service = EmbeddingService()
    builder = VectorIndexBuilder(service)
    retriever = VectorRetriever(service)

    knowledge_base = {
        "supplier_directory": {
            "module": "reference",
            "business_purpose": "Stores supplier information",
            "columns": [
                {"name": "supplier_code", "type": "varchar", "semantic_type": "code"},
                {"name": "supplier_name", "type": "varchar", "semantic_type": "name"},
            ],
            "relationships": [],
        },
        "purchase_invoices": {
            "module": "transaction",
            "business_purpose": "Stores purchase invoices",
            "columns": [
                {"name": "invoice_id", "type": "int", "semantic_type": "id"},
                {"name": "amount_due", "type": "decimal", "semantic_type": "money"},
            ],
            "relationships": [],
        },
    }

    retriever.add_documents(builder.build_from_knowledge_base(knowledge_base))
    table_names = retriever.get_relevant_tables("supplier information", top_k=5)
    assert table_names
    assert "supplier_directory" in table_names


def test_retriever_returns_relationships_glossary_and_search_status():
    service = EmbeddingService()
    builder = VectorIndexBuilder(service)
    retriever = VectorRetriever(service)

    knowledge_base = {
        "stock_positions": {
            "module": "snapshot",
            "business_purpose": "Tracks stock levels by warehouse",
            "columns": [
                {"name": "warehouse_id", "type": "int", "semantic_type": "id"},
                {"name": "quantity_on_hand", "type": "int", "semantic_type": "quantity"},
            ],
            "relationships": [
                {
                    "from_table": "stock_positions",
                    "from_column": "warehouse_id",
                    "to_table": "warehouse_directory",
                    "to_column": "warehouse_id",
                    "direction": "many-to-one",
                    "confidence": 0.97,
                    "reason": "warehouse stock lookup",
                }
            ],
        },
        "warehouse_directory": {
            "module": "reference",
            "business_purpose": "Stores warehouse details",
            "columns": [
                {"name": "warehouse_id", "type": "int", "semantic_type": "id"},
                {"name": "warehouse_name", "type": "varchar", "semantic_type": "name"},
            ],
            "relationships": [],
        },
    }
    glossary = {
        "stock": {
            "description": "Current stock on hand",
            "mapped_columns": [
                {"table": "stock_positions", "column": "quantity_on_hand", "confidence": "high"}
            ],
            "business_terms": ["inventory"],
            "example_questions": ["show current stock by warehouse"],
        }
    }

    retriever.add_documents(builder.build_from_knowledge_base(knowledge_base))
    retriever.add_documents(builder.build_from_glossary(glossary))

    relationships = retriever.get_relevant_relationships("current stock by warehouse", top_k=3)
    glossary_terms = retriever.get_relevant_glossary_terms("current stock by warehouse", top_k=3)
    columns = retriever.get_relevant_columns("current stock by warehouse", top_k=5)
    status = retriever.get_status()

    assert relationships
    assert relationships[0]["from_table"] == "stock_positions"
    assert glossary_terms
    assert glossary_terms[0]["term"] == "stock"
    assert any(column["table_name"] == "stock_positions" for column in columns)
    assert status["index_built"] is True
    assert status["document_count"] >= 6
    assert status["last_search"]["query"] == "current stock by warehouse"
    assert status["last_search"]["result_count"] >= 1
    assert "fallback_used" in status["last_search"]


def test_retriever_filter_by_type():
    service = EmbeddingService()
    builder = VectorIndexBuilder(service)
    retriever = VectorRetriever(service)

    knowledge_base = {
        "client_directory": {
            "module": "reference",
            "business_purpose": "Stores client records",
            "columns": [{"name": "client_name", "type": "varchar", "semantic_type": "name"}],
            "relationships": [],
        },
    }

    retriever.add_documents(builder.build_from_knowledge_base(knowledge_base))

    table_results = retriever.search("client", top_k=10, doc_type="table")
    column_results = retriever.search("client", top_k=10, doc_type="column")

    assert all(result["metadata"]["type"] == "table" for result in table_results)
    assert all(result["metadata"]["type"] == "column" for result in column_results)


def test_retriever_min_score_threshold():
    service = EmbeddingService()
    builder = VectorIndexBuilder(service)
    retriever = VectorRetriever(service)

    knowledge_base = {
        "item_catalog": {
            "module": "reference",
            "business_purpose": "Stores item information",
            "columns": [{"name": "item_name", "type": "varchar", "semantic_type": "name"}],
            "relationships": [],
        },
    }

    retriever.add_documents(builder.build_from_knowledge_base(knowledge_base))
    high_threshold_results = retriever.search("xyz unrelated query", top_k=10, min_score=0.8)
    assert len(high_threshold_results) == 0 or all(result["score"] >= 0.8 for result in high_threshold_results)


def test_vector_retrieval_fallback_without_documents():
    service = EmbeddingService()
    retriever = VectorRetriever(service)
    assert retriever.search("test query", top_k=5) == []


def test_fallback_embeddings_still_produce_usable_deterministic_retrieval():
    service = EmbeddingService()
    builder = VectorIndexBuilder(service)
    retriever = VectorRetriever(service)

    knowledge_base = {
        "payable_ledger": {
            "module": "transaction",
            "business_purpose": "Stores payable balances",
            "columns": [{"name": "amount_due", "type": "decimal", "semantic_type": "money"}],
            "relationships": [],
        },
        "event_log": {
            "module": "event",
            "business_purpose": "Stores application events",
            "columns": [{"name": "message_text", "type": "varchar", "semantic_type": "text"}],
            "relationships": [],
        },
    }

    retriever.add_documents(builder.build_from_knowledge_base(knowledge_base))
    first = retriever.get_relevant_tables("current payables", top_k=3)
    second = retriever.get_relevant_tables("current payables", top_k=3)

    assert first == second
    assert "payable_ledger" in first
