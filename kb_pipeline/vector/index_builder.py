"""
Build and maintain vector index documents from dynamic KB evidence.
"""

from typing import Dict, List, Any
from utils.logger import get_logger
from kb_pipeline.schema_facts import (
    column_ai_metadata,
    column_is_date,
    column_is_dimension,
    column_is_measure,
    column_profile_facts,
    resolved_semantic_type,
)
from kb_pipeline.vector.embedding_service import EmbeddingService

logger = get_logger()


class VectorIndexBuilder:
    """Build vector index from knowledge base and business glossary."""
    
    def __init__(self, embedding_service: EmbeddingService = None):
        self.embedding_service = embedding_service or EmbeddingService()
    
    def build_from_knowledge_base(
        self,
        knowledge_base: Dict[str, Any],
        source_context: Dict[str, Any] | None = None,
    ) -> List[Dict[str, Any]]:
        """
        Build vector documents from knowledge base.
        
        Args:
            knowledge_base: Knowledge base dict from semantic/knowledge_base.json
            
        Returns:
            List of document dicts with text, metadata, and embedding
        """
        documents = []
        source_context = source_context or {}
        
        for table_name, table_data in knowledge_base.items():
            # Add table document
            table_doc = self._create_table_document(table_name, table_data, source_context=source_context)
            documents.append(table_doc)
            
            # Add column documents
            for column in table_data.get("columns", []):
                col_doc = self._create_column_document(table_name, column, source_context=source_context)
                documents.append(col_doc)
                documents.extend(
                    self._create_specialized_column_documents(
                        table_name,
                        column,
                        source_context=source_context,
                    )
                )
            
            # Add relationship documents
            for relationship in table_data.get("relationships", []):
                rel_doc = self._create_relationship_document(table_name, relationship, source_context=source_context)
                documents.append(rel_doc)

        self._attach_embeddings(documents)
        
        logger.info(f"Built {len(documents)} vector documents from knowledge base")
        return documents
    
    def build_from_glossary(
        self,
        glossary: Dict[str, Any],
        source_context: Dict[str, Any] | None = None,
    ) -> List[Dict[str, Any]]:
        """
        Build vector documents from business glossary.
        
        Args:
            glossary: Business glossary dict from semantic/business_glossary.json
            
        Returns:
            List of document dicts with text, metadata, and embedding
        """
        documents = []
        source_context = source_context or {}
        
        for term, term_data in glossary.items():
            glossary_doc = self._create_glossary_document(term, term_data, source_context=source_context)
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
    
    def _shared_metadata(
        self,
        *,
        source_type: str,
        evidence_source: str,
        source_context: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        source_context = source_context or {}
        db_engine = str(source_context.get("db_engine", source_context.get("database_type", "")) or "")
        db_name = str(source_context.get("db_name", source_context.get("database_name", "")) or "")
        schema_hash = str(source_context.get("schema_hash", source_context.get("schema_fingerprint", "")) or "")
        return {
            "type": source_type,
            "source_type": source_type,
            "evidence_source": evidence_source,
            "db_engine": db_engine,
            "db_host": str(source_context.get("db_host", "") or ""),
            "db_port": str(source_context.get("db_port", "") or ""),
            "db_name": db_name,
            "schema_hash": schema_hash,
            "database_type": db_engine,
            "database_name": db_name,
            "schema_fingerprint": schema_hash,
        }

    def _create_table_document(
        self,
        table_name: str,
        table_data: Dict[str, Any],
        *,
        source_context: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        """Create a vector document for a table."""
        purpose = table_data.get("business_purpose", "")
        description = table_data.get("business_description", "")
        
        # Build searchable text
        text_parts = [
            f"Table: {table_name}",
            f"Purpose: {purpose}",
            f"Description: {description}",
        ]
        
        # Add column names for context
        column_names = [col.get("name", "") for col in table_data.get("columns", [])]
        if column_names:
            text_parts.append(f"Columns: {', '.join(column_names[:10])}")
        
        text = " ".join(text_parts)
        
        metadata = {
            **self._shared_metadata(
                source_type="table",
                evidence_source="knowledge_base_table",
                source_context=source_context,
            ),
            "table_name": table_name,
            "semantic_type": "table",
            "description": description,
            "business_purpose": purpose,
            "row_count": table_data.get("row_count"),
            "column_names": column_names[:20],
        }
        
        document = {
            "text": text,
            "metadata": metadata,
        }
        return document

    def _create_specialized_column_documents(
        self,
        table_name: str,
        column: Dict[str, Any],
        *,
        source_context: Dict[str, Any] | None = None,
    ) -> List[Dict[str, Any]]:
        column_name = str(column.get("name", "") or "")
        if not column_name:
            return []

        documents: List[Dict[str, Any]] = []
        semantic_type = resolved_semantic_type(column)
        core_semantic_type = str(column.get("semantic_type", "unknown")).strip().lower() or "unknown"
        ai_metadata = column_ai_metadata(column)
        planner_roles = dict(column.get("planner_roles", {}) or {})
        business_terms = ai_metadata.get("business_terms", [])
        description = ai_metadata.get("business_description", "")

        def _specialized_document(doc_type: str, *, evidence_source: str, text: str, extra_metadata: Dict[str, Any] | None = None) -> Dict[str, Any]:
            metadata = {
                **self._shared_metadata(
                    source_type=doc_type,
                    evidence_source=evidence_source,
                    source_context=source_context,
                ),
                "table_name": table_name,
                "column_name": column_name,
                "semantic_type": semantic_type,
                "core_semantic_type": core_semantic_type,
                "column_type": column.get("type", ""),
                "description": description,
                "business_terms": list(business_terms),
                "planner_roles": planner_roles,
            }
            if extra_metadata:
                metadata.update(extra_metadata)
            return {"text": text, "metadata": metadata}

        if column_is_measure(column):
            documents.append(
                _specialized_document(
                    "measure",
                    evidence_source="measure_candidate",
                    text=f"Measure evidence: {table_name}.{column_name} metric {semantic_type}.",
                    extra_metadata={"is_measure": True},
                )
            )
        if column_is_dimension(column):
            documents.append(
                _specialized_document(
                    "dimension",
                    evidence_source="dimension_candidate",
                    text=f"Dimension evidence: {table_name}.{column_name} dimension {semantic_type}.",
                    extra_metadata={"is_dimension": True},
                )
            )
        if column_is_date(column):
            documents.append(
                _specialized_document(
                    "date",
                    evidence_source="date_candidate",
                    text=f"Date evidence: {table_name}.{column_name} date field.",
                    extra_metadata={"is_date": True},
                )
            )

        documents.append(
            _specialized_document(
                "semantic_metadata",
                evidence_source="semantic_metadata",
                text=(
                    f"Semantic metadata: {table_name}.{column_name} semantic type {semantic_type}; "
                    f"core semantic type {core_semantic_type}; planner roles {', '.join(sorted(name for name, enabled in planner_roles.items() if enabled)) or 'none'}."
                ),
            )
        )

        if ai_metadata.get("ai_semantic_type") or business_terms or description:
            documents.append(
                _specialized_document(
                    "ai_metadata",
                    evidence_source="ai_semantic_metadata",
                    text=(
                        f"AI metadata: {table_name}.{column_name} ai semantic type {ai_metadata.get('ai_semantic_type', '')}. "
                        f"Business terms: {', '.join(business_terms)}. Description: {description}"
                    ).strip(),
                    extra_metadata={
                        "ai_semantic_type": ai_metadata.get("ai_semantic_type", ""),
                        "ai_confidence": ai_metadata.get("confidence", 0.0),
                    },
                )
            )

        return documents
    
    def _create_column_document(
        self,
        table_name: str,
        column: Dict[str, Any],
        *,
        source_context: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        """Create a vector document for a column."""
        column_name = column.get("name", "")
        column_type = column.get("type", "")
        semantic_type = resolved_semantic_type(column)
        core_semantic_type = str(column.get("semantic_type", "unknown")).strip().lower() or "unknown"
        ai_metadata = column_ai_metadata(column)
        profile_facts = column_profile_facts(column)
        description = ai_metadata.get("business_description", "")
        business_terms = ai_metadata.get("business_terms", [])
        unique_count = profile_facts.get("unique_count")
        null_count = profile_facts.get("null_count")
        min_value = profile_facts.get("min")
        max_value = profile_facts.get("max")
        
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
        sample_values = profile_facts.get("sample_values", [])
        if sample_values:
            text_parts.append(f"Sample values: {', '.join(str(v) for v in sample_values[:5])}")
        if unique_count not in (None, ""):
            text_parts.append(f"Unique count: {unique_count}")
        if null_count not in (None, ""):
            text_parts.append(f"Null count: {null_count}")
        if min_value not in (None, "") or max_value not in (None, ""):
            text_parts.append(f"Range: {min_value} to {max_value}")
        
        text = " ".join(text_parts)
        
        metadata = {
            **self._shared_metadata(
                source_type="column",
                evidence_source="knowledge_base_column",
                source_context=source_context,
            ),
            "table_name": table_name,
            "column_name": column_name,
            "semantic_type": semantic_type,
            "core_semantic_type": core_semantic_type,
            "column_type": column_type,
            "description": description,
            "business_terms": business_terms,
            "sample_values": sample_values[:5],
            "nullable": column.get("nullable"),
            "unique_count": unique_count,
            "null_count": null_count,
            "min": min_value,
            "max": max_value,
            "is_measure": column_is_measure(column),
            "is_dimension": column_is_dimension(column),
            "is_date": column_is_date(column),
        }
        
        document = {
            "text": text,
            "metadata": metadata,
        }
        return document
    
    def _create_relationship_document(
        self,
        table_name: str,
        relationship: Dict[str, Any],
        *,
        source_context: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
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
            **self._shared_metadata(
                source_type="relationship",
                evidence_source=str(relationship.get("source", "relationship_context") or "relationship_context"),
                source_context=source_context,
            ),
            "from_table": from_table,
            "to_table": to_table,
            "from_column": from_column,
            "to_column": to_column,
            "direction": direction,
            "confidence": confidence,
            "description": reason,
            "join_condition": relationship.get("join_condition"),
            "table_name": table_name,
        }
        
        document = {
            "text": text,
            "metadata": metadata,
        }
        return document
    
    def _create_glossary_document(
        self,
        term: str,
        term_data: Dict[str, Any],
        *,
        source_context: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        """Create a vector document for a glossary term."""
        description = term_data.get("description", "")
        mapped_columns = term_data.get("mapped_columns", [])
        example_questions = term_data.get("example_questions", [])
        primary_terms = term_data.get("primary_terms", []) or term_data.get("business_terms", [])
        related_terms = term_data.get("related_terms", [])
        target_type = str(term_data.get("target_type", "") or "column")
        usage_scope = str(term_data.get("usage_scope", "") or "primary_match")
        confidence = float(term_data.get("confidence", 0.0) or 0.0)
        sample_values = []
        profile_facts = []
        for mapping in mapped_columns:
            sample_values.extend(str(value) for value in (mapping.get("sample_values") or []) if str(value))
            mapping_profile = mapping.get("profile_facts")
            if isinstance(mapping_profile, dict):
                profile_facts.append(mapping_profile)
        sample_values = list(dict.fromkeys(sample_values))[:8]
        
        # Build searchable text
        text_parts = [
            f"Business term: {term}",
            f"Description: {description}",
            f"Target type: {target_type}",
            f"Usage scope: {usage_scope}",
        ]
        
        if primary_terms:
            text_parts.append(f"Primary terms: {', '.join(primary_terms)}")
        if related_terms:
            text_parts.append(f"Relationship context: {', '.join(related_terms)}")
        if sample_values:
            text_parts.append(f"Sample value evidence: {', '.join(sample_values)}")
        
        if mapped_columns:
            column_mappings = [f"{m.get('table', '')}.{m.get('column', '')}" for m in mapped_columns]
            text_parts.append(f"Maps to columns: {', '.join(column_mappings)}")
        
        if example_questions:
            text_parts.append(f"Example questions: {', '.join(example_questions[:3])}")
        
        text = " ".join(text_parts)
        
        # Extract table names from mapped columns
        table_names = list(set(m.get("table", "") for m in mapped_columns))
        
        metadata = {
            **self._shared_metadata(
                source_type="glossary",
                evidence_source=str(term_data.get("source", "business_glossary") or "business_glossary"),
                source_context=source_context,
            ),
            "term": term,
            "description": description,
            "table_names": table_names,
            "business_terms": list(primary_terms),
            "primary_terms": list(primary_terms),
            "related_terms": list(related_terms),
            "target_type": target_type,
            "usage_scope": usage_scope,
            "confidence": confidence,
            "mapped_columns": mapped_columns,
            "sample_values": sample_values,
            "profile_facts": profile_facts,
            "sources": list(term_data.get("sources", []) or []),
            "example_questions": example_questions[:3],
        }
        
        document = {
            "text": text,
            "metadata": metadata,
        }
        return document
