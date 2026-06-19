"""
vector_store/__init__.py
========================
Vector database module for semantic search and retrieval.

This module provides vector indexing, retrieval, and persistence
for tables, columns, relationships, and business glossary terms.
"""

from kb_pipeline.vector.index_builder import VectorIndexBuilder
from kb_pipeline.vector.retriever import VectorRetriever
from kb_pipeline.vector.embedding_service import EmbeddingService
from kb_pipeline.vector.persistence import VectorIndexPersistence

__all__ = ["VectorIndexBuilder", "VectorRetriever", "EmbeddingService", "VectorIndexPersistence"]
