"""
vector_store/retriever.py
==========================
Vector retrieval for semantic search.

Provides similarity search over indexed documents
using cosine similarity.
"""

from typing import List, Dict, Any, Tuple
import math
from utils.logger import get_logger
from vector_store.embedding_service import EmbeddingService

logger = get_logger()


def _token_overlap_score(query_tokens: list[str], doc_tokens: list[str]) -> float:
    """Compute a light lexical similarity score for deterministic fallback mode."""
    if not query_tokens or not doc_tokens:
        return 0.0

    query_set = set(query_tokens)
    doc_set = set(doc_tokens)
    overlap = len(query_set & doc_set)
    if overlap == 0:
        return 0.0

    return overlap / math.sqrt(len(query_set) * len(doc_set))


class VectorRetriever:
    """Retrieve relevant documents using vector similarity."""
    
    def __init__(self, embedding_service: EmbeddingService = None):
        self.embedding_service = embedding_service or EmbeddingService()
        self.documents: List[Dict[str, Any]] = []
        self._index_built = False
    
    def add_documents(self, documents: List[Dict[str, Any]]) -> None:
        """
        Add documents to the retriever index.
        
        Args:
            documents: List of document dicts with text, metadata, and embedding
        """
        self.documents.extend(documents)
        self._index_built = True
        logger.info(f"Added {len(documents)} documents to vector index (total: {len(self.documents)})")
    
    def search(
        self,
        query: str,
        top_k: int = 10,
        doc_type: str = None,
        min_score: float = 0.3,
    ) -> List[Dict[str, Any]]:
        """
        Search for relevant documents using vector similarity.
        
        Args:
            query: Search query text
            top_k: Number of top results to return
            doc_type: Optional filter by document type (table, column, relationship, glossary)
            min_score: Minimum similarity score threshold
            
        Returns:
            List of (document, score) tuples sorted by score descending
        """
        if not self._index_built:
            logger.warning("Vector index not built, returning empty results")
            return []
        
        query_embedding = self.embedding_service.embed(query)
        query_tokens = self.embedding_service.tokenize(query)
        use_fallback = self.embedding_service.is_fallback_mode()
        
        # Calculate similarity scores
        scored_docs = []
        for doc in self.documents:
            # Filter by document type if specified
            if doc_type and doc.get("metadata", {}).get("type") != doc_type:
                continue
            
            doc_embedding = doc.get("embedding", [])
            score = self._cosine_similarity(query_embedding, doc_embedding)
            if use_fallback:
                lexical_score = _token_overlap_score(
                    query_tokens,
                    doc.get("tokenized_text") or self.embedding_service.tokenize(doc.get("text", "")),
                )
                score = max(score, lexical_score)
            
            if score >= min_score:
                scored_docs.append((doc, score))
        
        # Sort by score descending
        scored_docs.sort(key=lambda x: x[1], reverse=True)
        
        # Return top_k results
        results = []
        for doc, score in scored_docs[:top_k]:
            results.append({
                "document": doc,
                "score": round(score, 4),
                "metadata": doc.get("metadata", {}),
                "text": doc.get("text", ""),
            })
        
        logger.info(f"Vector search for '{query}' returned {len(results)} results")
        return results
    
    def _cosine_similarity(self, vec1: List[float], vec2: List[float]) -> float:
        """
        Calculate cosine similarity between two vectors.
        
        Args:
            vec1: First vector
            vec2: Second vector
            
        Returns:
            Cosine similarity score between 0 and 1
        """
        if not vec1 or not vec2 or len(vec1) != len(vec2):
            return 0.0
        
        dot_product = sum(a * b for a, b in zip(vec1, vec2))
        norm1 = math.sqrt(sum(a * a for a in vec1))
        norm2 = math.sqrt(sum(b * b for b in vec2))
        
        if norm1 == 0 or norm2 == 0:
            return 0.0
        
        return dot_product / (norm1 * norm2)
    
    def get_relevant_tables(self, query: str, top_k: int = 5) -> List[str]:
        """
        Get relevant table names for a query.
        
        Args:
            query: Search query
            top_k: Number of tables to return
            
        Returns:
            List of table names sorted by relevance
        """
        results = self.search(query, top_k=max(top_k, 8), min_score=0.2)
        ranked_tables: list[tuple[str, float]] = []
        seen: set[str] = set()

        for result in results:
            metadata = result.get("metadata", {})
            score = float(result.get("score", 0.0))
            doc_type = metadata.get("type")

            candidate_tables: list[str] = []
            if doc_type == "table" and metadata.get("table_name"):
                candidate_tables.append(metadata["table_name"])
            elif doc_type == "column" and metadata.get("table_name"):
                candidate_tables.append(metadata["table_name"])
            elif doc_type == "glossary":
                candidate_tables.extend([table for table in metadata.get("table_names", []) if table])
            elif doc_type == "relationship":
                for key in ("from_table", "to_table"):
                    table_name = metadata.get(key)
                    if table_name:
                        candidate_tables.append(table_name)

            for table_name in candidate_tables:
                if table_name in seen:
                    continue
                ranked_tables.append((table_name, score))
                seen.add(table_name)
                if len(ranked_tables) >= top_k:
                    return [name for name, _ in ranked_tables]

        return [name for name, _ in ranked_tables]
    
    def get_relevant_columns(self, query: str, top_k: int = 10) -> List[Dict[str, Any]]:
        """
        Get relevant columns for a query.
        
        Args:
            query: Search query
            top_k: Number of columns to return
            
        Returns:
            List of column metadata dicts sorted by relevance
        """
        results = self.search(query, top_k=top_k, doc_type="column")
        columns = []
        seen = set()
        
        for result in results:
            metadata = result.get("metadata", {})
            table_name = metadata.get("table_name")
            column_name = metadata.get("column_name")
            
            if table_name and column_name and (table_name, column_name) not in seen:
                columns.append(metadata)
                seen.add((table_name, column_name))
        
        return columns
    
    def get_relevant_glossary_terms(self, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
        """
        Get relevant glossary terms for a query.
        
        Args:
            query: Search query
            top_k: Number of terms to return
            
        Returns:
            List of glossary term metadata dicts sorted by relevance
        """
        results = self.search(query, top_k=top_k, doc_type="glossary")
        terms = []
        seen = set()
        
        for result in results:
            metadata = result.get("metadata", {})
            term = metadata.get("term")
            
            if term and term not in seen:
                terms.append(metadata)
                seen.add(term)
        
        return terms
    
    def clear(self) -> None:
        """Clear all documents from the index."""
        self.documents = []
        self._index_built = False
        logger.info("Vector index cleared")
