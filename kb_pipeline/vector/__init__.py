"""
KB Pipeline vector subpackage.

This package contains the derived vector search layer built from the runtime
knowledge base and glossary.
"""

from importlib import import_module

__all__ = [
    "VectorIndexBuilder",
    "VectorRetriever",
    "EmbeddingService",
    "VectorIndexPersistence",
    "ChromaStore",
    "HybridVectorRetriever",
]

_MODULE_MAP = {
    "EmbeddingService": "kb_pipeline.vector.embedding_service",
    "VectorIndexBuilder": "kb_pipeline.vector.index_builder",
    "VectorRetriever": "kb_pipeline.vector.retriever",
    "VectorIndexPersistence": "kb_pipeline.vector.persistence",
    "ChromaStore": "kb_pipeline.vector.chroma_store",
    "HybridVectorRetriever": "kb_pipeline.vector.chroma_store",
}


def __getattr__(name):
    module_name = _MODULE_MAP.get(name)
    if not module_name:
        raise AttributeError(name)
    return getattr(import_module(module_name), name)
