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
from kb_pipeline.vector.embedding_service import EmbeddingService

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
        self._last_search_info: Dict[str, Any] = {}
    
    def add_documents(self, documents: List[Dict[str, Any]]) -> None:
        """
        Add documents to the retriever index.
        
        Args:
            documents: List of document dicts with text, metadata, and embedding
        """
        self.documents.extend(documents)
        self._index_built = True
        logger.info(f"Added {len(documents)} documents to vector index (total: {len(self.documents)})")

    def _result_entry(self, result: Dict[str, Any]) -> Dict[str, Any]:
        metadata = dict(result.get("metadata", {}))
        return {
            **metadata,
            "score": result.get("score", 0.0),
            "text": result.get("text", ""),
        }
    
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
            self._last_search_info = {
                "query": query,
                "doc_type": doc_type,
                "result_count": 0,
                "backend": self.embedding_service.get_backend_name(),
                "model": self.embedding_service.get_model_name(),
                "fallback_used": self.embedding_service.is_fallback_mode(),
            }
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
        
        self._last_search_info = {
            "query": query,
            "doc_type": doc_type or "all",
            "result_count": len(results),
            "backend": self.embedding_service.get_backend_name(),
            "model": self.embedding_service.get_model_name(),
            "fallback_used": use_fallback,
            "min_score": min_score,
            "top_k": top_k,
        }
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
        results = (
            self.search(query, top_k=max(top_k, 6), doc_type="table", min_score=0.2)
            + self.search(query, top_k=max(top_k, 6), doc_type="glossary", min_score=0.2)
            + self.search(query, top_k=max(top_k, 4), doc_type="column", min_score=0.2)
            + self.search(query, top_k=max(top_k, 4), doc_type="relationship", min_score=0.2)
        )
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
            metadata = dict(result.get("metadata", {}))
            table_name = metadata.get("table_name")
            column_name = metadata.get("column_name")
            
            if table_name and column_name and (table_name, column_name) not in seen:
                columns.append(self._result_entry(result))
                seen.add((table_name, column_name))
        
        return columns

    def get_relevant_metrics(self, query: str, top_k: int = 8) -> List[Dict[str, Any]]:
        results = self.search(query, top_k=top_k, doc_type="measure", min_score=0.2)
        metrics = []
        seen = set()

        for result in results:
            metadata = self._result_entry(result)
            key = (metadata.get("table_name"), metadata.get("column_name"))
            if not all(key) or key in seen:
                continue
            seen.add(key)
            metrics.append(metadata)
        return metrics

    def get_relevant_dimensions(self, query: str, top_k: int = 8) -> List[Dict[str, Any]]:
        results = self.search(query, top_k=top_k, doc_type="dimension", min_score=0.2)
        dimensions = []
        seen = set()

        for result in results:
            metadata = self._result_entry(result)
            key = (metadata.get("table_name"), metadata.get("column_name"))
            if not all(key) or key in seen:
                continue
            seen.add(key)
            dimensions.append(metadata)
        return dimensions

    def get_relevant_dates(self, query: str, top_k: int = 8) -> List[Dict[str, Any]]:
        results = self.search(query, top_k=top_k, doc_type="date", min_score=0.2)
        dates = []
        seen = set()

        for result in results:
            metadata = self._result_entry(result)
            key = (metadata.get("table_name"), metadata.get("column_name"))
            if not all(key) or key in seen:
                continue
            seen.add(key)
            dates.append(metadata)
        return dates

    def get_relevant_table_details(self, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
        """Return table documents with scores and descriptive metadata."""
        results = self.search(query, top_k=top_k, doc_type="table", min_score=0.2)
        tables = []
        seen = set()

        for result in results:
            metadata = self._result_entry(result)
            table_name = metadata.get("table_name")
            if not table_name or table_name in seen:
                continue
            seen.add(table_name)
            tables.append(metadata)

        return tables
    
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
            metadata = self._result_entry(result)
            term = metadata.get("term")
            
            if term and term not in seen:
                terms.append(metadata)
                seen.add(term)
        
        return terms

    def get_relevant_relationships(self, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
        """
        Get relevant relationships for a query.
        
        Args:
            query: Search query
            top_k: Number of relationships to return
        
        Returns:
            List of relationship metadata dicts sorted by relevance
        """
        results = self.search(query, top_k=top_k, doc_type="relationship", min_score=0.2)
        relationships = []
        seen = set()

        for result in results:
            metadata = self._result_entry(result)
            signature = (
                metadata.get("from_table"),
                metadata.get("from_column"),
                metadata.get("to_table"),
                metadata.get("to_column"),
            )
            if signature in seen:
                continue
            seen.add(signature)
            relationships.append(metadata)

        return relationships

    def get_relevant_semantic_descriptions(self, query: str, top_k: int = 8) -> List[Dict[str, Any]]:
        """Return descriptive semantic snippets from table, column, and glossary documents."""
        results = self.search(query, top_k=top_k, min_score=0.2)
        descriptions = []

        for result in results:
            metadata = result.get("metadata", {})
            description = (
                metadata.get("description")
                or metadata.get("business_purpose")
                or result.get("text", "")
            )
            if not description:
                continue
            descriptions.append(
                {
                    "type": metadata.get("type"),
                    "table_name": metadata.get("table_name"),
                    "column_name": metadata.get("column_name"),
                    "term": metadata.get("term"),
                    "description": description,
                    "score": result.get("score", 0.0),
                }
            )

        return descriptions

    def get_relevant_profiling_hints(self, query: str, top_k: int = 8) -> List[Dict[str, Any]]:
        """Return profiling-oriented hints such as sample values, row counts, and nullability."""
        results = self.search(query, top_k=top_k, min_score=0.2)
        hints = []

        for result in results:
            metadata = result.get("metadata", {})
            hint = {
                "type": metadata.get("type"),
                "table_name": metadata.get("table_name"),
                "column_name": metadata.get("column_name"),
                "row_count": metadata.get("row_count"),
                "sample_values": metadata.get("sample_values", []),
                "nullable": metadata.get("nullable"),
                "column_type": metadata.get("column_type"),
                "score": result.get("score", 0.0),
            }
            if not any(
                hint.get(key) not in (None, "", [], {})
                for key in ("row_count", "sample_values", "nullable", "column_type")
            ):
                continue
            hints.append(hint)

        return hints

    def get_normalized_evidence_package(self, query: str, top_k: int = 8) -> Dict[str, Any]:
        candidate_tables = self.get_relevant_table_details(query, top_k=max(top_k, 6))
        candidate_columns = self.get_relevant_columns(query, top_k=max(top_k + 2, 10))
        candidate_metrics = self.get_relevant_metrics(query, top_k=max(top_k, 6))
        candidate_dimensions = self.get_relevant_dimensions(query, top_k=max(top_k, 6))
        candidate_dates = self.get_relevant_dates(query, top_k=max(top_k, 4))
        relationships = self.get_relevant_relationships(query, top_k=max(top_k, 6))
        glossary_matches = self.get_relevant_glossary_terms(query, top_k=max(top_k, 6))

        score_groups = {
            "tables": [float(entry.get("score") or 0.0) for entry in candidate_tables],
            "columns": [float(entry.get("score") or 0.0) for entry in candidate_columns],
            "metrics": [float(entry.get("score") or 0.0) for entry in candidate_metrics],
            "dimensions": [float(entry.get("score") or 0.0) for entry in candidate_dimensions],
            "dates": [float(entry.get("score") or 0.0) for entry in candidate_dates],
            "relationships": [float(entry.get("score") or 0.0) for entry in relationships],
            "glossary": [float(entry.get("score") or 0.0) for entry in glossary_matches],
        }
        evidence_scores = {
            key: round(max(values), 4) if values else 0.0
            for key, values in score_groups.items()
        }
        evidence_scores["overall"] = round(max(evidence_scores.values()) if evidence_scores else 0.0, 4)

        def _close_candidates(entries: List[Dict[str, Any]], keys: tuple[str, ...]) -> List[Dict[str, Any]]:
            if len(entries) < 2:
                return []
            top_score = float(entries[0].get("score") or 0.0)
            candidates = []
            for entry in entries:
                score = float(entry.get("score") or 0.0)
                if top_score - score > 0.08:
                    continue
                candidates.append({key: entry.get(key) for key in keys if entry.get(key)})
            return candidates[1:] if len(candidates) > 1 else []

        return {
            "candidate_tables": candidate_tables,
            "candidate_columns": candidate_columns,
            "candidate_metrics": candidate_metrics,
            "candidate_dimensions": candidate_dimensions,
            "candidate_dates": candidate_dates,
            "relationships": relationships,
            "glossary_matches": glossary_matches,
            "evidence_scores": evidence_scores,
            "source_metadata": {
                **self.get_status(),
                "query": query,
            },
            "ambiguity_candidates": {
                "tables": _close_candidates(candidate_tables, ("table_name",)),
                "metrics": _close_candidates(candidate_metrics, ("table_name", "column_name")),
                "dimensions": _close_candidates(candidate_dimensions, ("table_name", "column_name")),
            },
            "missing_evidence_indicators": {
                "tables_missing": not candidate_tables,
                "columns_missing": not candidate_columns,
                "metrics_missing": not candidate_metrics,
                "dimensions_missing": not candidate_dimensions,
                "dates_missing": not candidate_dates,
                "relationships_missing": not relationships,
                "glossary_missing": not glossary_matches,
            },
            "retrieval_sources": ["vector"] if any(
                [
                    candidate_tables,
                    candidate_columns,
                    candidate_metrics,
                    candidate_dimensions,
                    candidate_dates,
                    relationships,
                    glossary_matches,
                ]
            ) else [],
        }

    def get_status(self) -> Dict[str, Any]:
        """Return retriever status for CLI/debug reporting."""
        return {
            "index_built": self._index_built,
            "document_count": len(self.documents),
            "embedding": self.embedding_service.get_status(),
            "last_search": dict(self._last_search_info),
        }

    def clear(self) -> None:
        """Clear all documents from the index."""
        self.documents = []
        self._index_built = False
        self._last_search_info = {}
        logger.info("Vector index cleared")
