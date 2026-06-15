"""
vector_store/index_builder.py
=============================
Build and maintain vector index from knowledge base and glossary.
"""

from typing import Dict, List, Any
from utils.logger import get_logger
from vector_store.embedding_service import EmbeddingService

logger = get_logger()


class VectorIndexBuilder:
    """Build vector index from knowledge base and business glossary."""
    
    def __init__(self, embedding_service: EmbeddingService = None):
        self.embedding_service = embedding_service or EmbeddingService()
    
    def build_from_knowledge_base(self, knowledge_base: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Build vector documents from knowledge base.
        
        Args:
            knowledge_base: Knowledge base dict from semantic/knowledge_base.json
            
        Returns:
            List of document dicts with text, metadata, and embedding
        """
        documents = []
        
        for table_name, table_data in knowledge_base.items():
            # Add table document
            table_doc = self._create_table_document(table_name, table_data)
            documents.append(table_doc)
            
            # Add column documents
            for column in table_data.get("columns", []):
                col_doc = self._create_column_document(table_name, column)
                documents.append(col_doc)
            
            # Add relationship documents
            for relationship in table_data.get("relationships", []):
                rel_doc = self._create_relationship_document(table_name, relationship)
                documents.append(rel_doc)

        self._attach_embeddings(documents)
        
        logger.info(f"Built {len(documents)} vector documents from knowledge base")
        return documents
    
    def build_from_glossary(self, glossary: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Build vector documents from business glossary.
        
        Args:
            glossary: Business glossary dict from semantic/business_glossary.json
            
        Returns:
            List of document dicts with text, metadata, and embedding
        """
        documents = []
        
        for term, term_data in glossary.items():
            glossary_doc = self._create_glossary_document(term, term_data)
            documents.append(glossary_doc)

        self._attach_embeddings(documents)
        
        logger.info(f"Built {len(documents)} vector documents from glossary")
        return documents

    def _attach_embeddings(self, documents: List[Dict[str, Any]]) -> None:
        """Embed a batch of vector documents in one pass when possible."""
        if not documents:
            return

        texts = [str(document.get("text", "")) for document in documents]
        embeddings = self.embedding_service.embed_batch(texts)
        for document, embedding in zip(documents, embeddings):
            document["embedding"] = embedding
            document["tokenized_text"] = self.embedding_service.tokenize(document.get("text", ""))
    
    def _create_table_document(self, table_name: str, table_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create a vector document for a table."""
        module = table_data.get("module", "unknown")
        purpose = table_data.get("business_purpose", "")
        description = table_data.get("business_description", "")
        
        # Build searchable text
        text_parts = [
            f"Table: {table_name}",
            f"Module: {module}",
            f"Purpose: {purpose}",
            f"Description: {description}",
        ]
        
        # Add column names for context
        column_names = [col.get("name", "") for col in table_data.get("columns", [])]
        if column_names:
            text_parts.append(f"Columns: {', '.join(column_names[:10])}")
        
        text = " ".join(text_parts)
        
        metadata = {
            "type": "table",
            "table_name": table_name,
            "module": module,
            "semantic_type": "table",
        }
        
        document = {
            "text": text,
            "metadata": metadata,
        }
        return document
    
    def _create_column_document(self, table_name: str, column: Dict[str, Any]) -> Dict[str, Any]:
        """Create a vector document for a column."""
        column_name = column.get("name", "")
        column_type = column.get("type", "")
        semantic_type = column.get("semantic_type", "general")
        description = column.get("business_description", "")
        business_terms = column.get("business_terms", [])
        
        # Build searchable text
        text_parts = [
            f"Column: {column_name}",
            f"Table: {table_name}",
            f"Type: {column_type}",
            f"Semantic type: {semantic_type}",
        ]
        
        if description:
            text_parts.append(f"Description: {description}")
        
        if business_terms:
            text_parts.append(f"Business terms: {', '.join(business_terms)}")
        
        # Add sample values for context
        sample_values = column.get("sample_values", [])
        if sample_values:
            text_parts.append(f"Sample values: {', '.join(str(v) for v in sample_values[:5])}")
        
        text = " ".join(text_parts)
        
        metadata = {
            "type": "column",
            "table_name": table_name,
            "column_name": column_name,
            "semantic_type": semantic_type,
            "column_type": column_type,
        }
        
        document = {
            "text": text,
            "metadata": metadata,
        }
        return document
    
    def _create_relationship_document(self, table_name: str, relationship: Dict[str, Any]) -> Dict[str, Any]:
        """Create a vector document for a relationship."""
        direction = relationship.get("direction", "unknown")
        from_table = relationship.get("from_table", "")
        to_table = relationship.get("to_table", "")
        from_column = relationship.get("from_column", "")
        to_column = relationship.get("to_column", "")
        confidence = relationship.get("confidence", 0.0)
        reason = relationship.get("reason", "")
        
        # Build searchable text
        text_parts = [
            f"Relationship: {from_table}.{from_column} to {to_table}.{to_column}",
            f"Direction: {direction}",
            f"Confidence: {confidence}",
        ]
        
        if reason:
            text_parts.append(f"Reason: {reason}")
        
        text = " ".join(text_parts)
        
        metadata = {
            "type": "relationship",
            "from_table": from_table,
            "to_table": to_table,
            "from_column": from_column,
            "to_column": to_column,
            "direction": direction,
            "confidence": confidence,
        }
        
        document = {
            "text": text,
            "metadata": metadata,
        }
        return document
    
    def _create_glossary_document(self, term: str, term_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create a vector document for a glossary term."""
        description = term_data.get("description", "")
        mapped_columns = term_data.get("mapped_columns", [])
        example_questions = term_data.get("example_questions", [])
        business_terms = term_data.get("business_terms", [])
        
        # Build searchable text
        text_parts = [
            f"Business term: {term}",
            f"Description: {description}",
        ]
        
        if business_terms:
            text_parts.append(f"Also known as: {', '.join(business_terms)}")
        
        if mapped_columns:
            column_mappings = [f"{m.get('table', '')}.{m.get('column', '')}" for m in mapped_columns]
            text_parts.append(f"Maps to columns: {', '.join(column_mappings)}")
        
        if example_questions:
            text_parts.append(f"Example questions: {', '.join(example_questions[:3])}")
        
        text = " ".join(text_parts)
        
        # Extract table names from mapped columns
        table_names = list(set(m.get("table", "") for m in mapped_columns))
        
        metadata = {
            "type": "glossary",
            "term": term,
            "description": description,
            "table_names": table_names,
            "business_terms": business_terms,
        }
        
        document = {
            "text": text,
            "metadata": metadata,
        }
        return document
