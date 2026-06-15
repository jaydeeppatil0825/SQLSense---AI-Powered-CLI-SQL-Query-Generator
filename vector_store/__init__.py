"""
vector_store/__init__.py
========================
Vector database module for semantic search and retrieval.

This module provides vector indexing, retrieval, and persistence
for tables, columns, relationships, and business glossary terms.
"""

from vector_store.index_builder import VectorIndexBuilder
from vector_store.retriever import VectorRetriever
from vector_store.embedding_service import EmbeddingService
from vector_store.persistence import VectorIndexPersistence

__all__ = ["VectorIndexBuilder", "VectorRetriever", "EmbeddingService", "VectorIndexPersistence"]
