"""
core/database_service.py
========================
Database service for database operations.

This service handles database connection, knowledge base building,
and business glossary loading for the CLI.
"""

from typing import Optional, Dict, Any
from sqlalchemy.engine import Engine

from db.connection import connect_engine, get_engine, SUPPORTED_DB_TYPES
from semantic.knowledge_base_builder import build_knowledge_base
from semantic.erp_metadata import enrich_knowledge_base_for_erp, summarize_knowledge_base
from semantic.business_glossary import load_business_glossary, generate_business_glossary, save_business_glossary
from ai.sql_generator import check_ollama_status
from semantic.ai_semantic_enricher import (
    enrich_knowledge_base_with_ai,
    _describe_ai_enrichment_failure,
    get_last_enrichment_reason,
    get_last_enrichment_report,
)
from utils.file_utils import load_json, save_json
from utils.logger import get_logger
from vector_store import EmbeddingService, VectorIndexBuilder, VectorRetriever

logger = get_logger()

KNOWLEDGE_BASE_PATH = "semantic/knowledge_base.json"


class DatabaseService:
    """Service for database operations."""
    
    def __init__(self):
        self.engine: Optional[Engine] = None
        self.knowledge_base: Optional[Dict[str, Any]] = None
        self.business_glossary: Optional[Dict[str, Any]] = None
        self.db_config: Dict[str, Any] = {}
        self.last_ai_enrichment_status: str = "skipped"
        self.last_ai_enrichment_message: str = "AI enrichment skipped."
        self.last_build_summary: Dict[str, Any] = {}
        self.embedding_service = EmbeddingService()
        self.vector_index_builder = VectorIndexBuilder(self.embedding_service)
        self.vector_retriever: Optional[VectorRetriever] = None
        self.vector_index_status: str = "not_built"

    def _align_glossary_with_active_knowledge_base(self, glossary: Dict[str, Any]) -> Dict[str, Any]:
        """Ensure loaded glossary mappings belong to the active knowledge base."""
        if not glossary or not self.knowledge_base:
            return glossary

        kb_tables = set(self.knowledge_base.keys())
        if not kb_tables:
            return glossary

        table_columns = {
            table_name: {str(column.get("name", "")) for column in table_data.get("columns", [])}
            for table_name, table_data in self.knowledge_base.items()
        }

        aligned: Dict[str, Any] = {}
        mapped_terms = 0
        for term, term_data in glossary.items():
            mappings = []
            for mapping in term_data.get("mapped_columns", []):
                table_name = mapping.get("table")
                column_name = mapping.get("column")
                if table_name not in kb_tables:
                    continue
                if column_name and column_name not in table_columns.get(table_name, set()):
                    continue
                mappings.append(mapping)

            if mappings:
                mapped_terms += 1

            if mappings or not term_data.get("mapped_columns"):
                aligned[term] = {
                    **term_data,
                    "mapped_columns": mappings,
                }

        if mapped_terms == 0:
            logger.info("Loaded glossary does not match the active KB; regenerating glossary from the current KB.")
            return generate_business_glossary(self.knowledge_base, use_ai_enrichment=False)

        return aligned
    
    def connect_database(
        self,
        db_type: str = "mysql",
        host: str = "localhost",
        port: Optional[int] = None,
        username: str = "",
        password: str = "",
        database: str = "",
        sqlite_path: str = "",
    ) -> tuple[bool, str, Optional[Engine]]:
        """
        Connect to database.
        
        Returns:
            (success, message, engine)
        """
        try:
            if db_type == "sqlite":
                if not sqlite_path:
                    return False, "SQLite file path cannot be empty.", None
                engine = connect_engine(db_type="sqlite", sqlite_path=sqlite_path)
                self.engine = engine
                self.db_config = {"db_type": "sqlite", "sqlite_path": sqlite_path}
                logger.info(f"Connected to SQLite: {sqlite_path}")
                return True, f"Connected to SQLite: {sqlite_path}", engine
            else:
                if db_type not in SUPPORTED_DB_TYPES:
                    return False, f"Unsupported database type: {db_type}", None
                
                if not username:
                    return False, "Username cannot be empty.", None
                
                if not database:
                    return False, "Database name cannot be empty.", None
                
                engine = connect_engine(
                    db_type=db_type,
                    host=host,
                    port=port,
                    username=username,
                    password=password,
                    database=database,
                )
                self.engine = engine
                self.db_config = {
                    "db_type": db_type,
                    "host": host,
                    "port": port,
                    "username": username,
                    "database": database,
                }
                logger.info(f"Connected to {db_type}://{username}@{host}:{port}/{database}")
                return True, f"Connected to {db_type}://{username}@{host}:{port}/{database}", engine
        except Exception as e:
            logger.error(f"Database connection failed: {e}")
            return False, f"Connection failed: {e}", None
    
    def connect_from_env(self) -> tuple[bool, str, Optional[Engine]]:
        """
        Connect to database using environment variables.
        
        Returns:
            (success, message, engine)
        """
        try:
            engine = get_engine()
            self.engine = engine
            return True, "Connected using environment variables", engine
        except Exception as e:
            logger.error(f"Database connection from env failed: {e}")
            return False, f"Connection failed: {e}", None
    
    def build_knowledge_base(
        self,
        use_ai_enrichment: bool = False,
        ai_backend: str = "local",
    ) -> tuple[bool, str, Optional[Dict[str, Any]]]:
        """
        Build knowledge base from connected database.
        
        Args:
            use_ai_enrichment: Whether to use AI for semantic enrichment
            ai_backend: AI backend to use for enrichment
        
        Returns:
            (success, message, knowledge_base)
        """
        if not self.engine:
            return False, "No database connection", None
        
        try:
            knowledge_base = build_knowledge_base(self.engine)
            logger.info("Knowledge base built successfully")
        except Exception as e:
            logger.error(f"Knowledge base build failed: {e}")
            return False, f"Knowledge base build failed: {e}", None
        
        # Save knowledge base
        try:
            knowledge_base = enrich_knowledge_base_for_erp(knowledge_base)
            save_json(knowledge_base, KNOWLEDGE_BASE_PATH)
            logger.info(f"Knowledge base saved to {KNOWLEDGE_BASE_PATH}")
        except Exception as e:
            logger.error(f"Failed to save knowledge base: {e}")
            return False, f"Failed to save knowledge base: {e}", None
        
        final_knowledge_base = knowledge_base
        self.last_ai_enrichment_status = "skipped"
        self.last_ai_enrichment_message = "AI enrichment skipped."
        
        # AI semantic enrichment — failure must NEVER stop KB generation
        if use_ai_enrichment:
            try:
                ai_backend = "local"
                ollama_ok, _ = check_ollama_status()
                if not ollama_ok:
                    raise ConnectionError("Ollama is not running")
                enriched_kb = enrich_knowledge_base_with_ai(knowledge_base, backend=ai_backend)
                
                if enriched_kb is not knowledge_base:
                    final_knowledge_base = enrich_knowledge_base_for_erp(enriched_kb)
                    enriched_tables, fallback_tables = get_last_enrichment_report()
                    if fallback_tables:
                        self.last_ai_enrichment_status = "partial"
                        self.last_ai_enrichment_message = (
                            f"AI enrichment completed for {len(enriched_tables)} table(s); "
                            f"fallback used for {len(fallback_tables)} table(s)."
                        )
                    else:
                        self.last_ai_enrichment_status = "completed"
                        self.last_ai_enrichment_message = "AI enrichment completed successfully."
                    try:
                        save_json(final_knowledge_base, KNOWLEDGE_BASE_PATH)
                        logger.info(f"Enriched knowledge base saved to {KNOWLEDGE_BASE_PATH}")
                    except Exception as e:
                        logger.error(f"Failed to save enriched knowledge base: {e}")
                        # Fall back silently — rule-based KB is already saved
                        final_knowledge_base = knowledge_base
                        self.last_ai_enrichment_status = "fallback"
                        self.last_ai_enrichment_message = "Could not save enriched knowledge base. Using rule-based enrichment."
                else:
                    # enrich_knowledge_base_with_ai returned the original dict — enrichment failed
                    reason = get_last_enrichment_reason() or "AI enrichment returned no changes"
                    self.last_ai_enrichment_status = "fallback"
                    if "timed out" in reason.lower():
                        self.last_ai_enrichment_message = "Local AI timed out. Using rule-based fallback."
                    elif reason == "Ollama is not running":
                        self.last_ai_enrichment_message = "Ollama is not running. Using rule-based enrichment."
                    else:
                        self.last_ai_enrichment_message = f"{reason}. Using rule-based fallback."
                    logger.info(self.last_ai_enrichment_message)
                    print(f"  {self.last_ai_enrichment_message}")
            except Exception as e:
                # Catch everything so a timeout or network error never stops KB generation
                reason = _describe_ai_enrichment_failure(e, ai_backend)
                self.last_ai_enrichment_status = "fallback"
                if reason == "Ollama is not running":
                    self.last_ai_enrichment_message = "Ollama is not running. Using rule-based enrichment."
                elif "timed out" in reason.lower():
                    self.last_ai_enrichment_message = "Local AI timed out. Using rule-based fallback."
                else:
                    self.last_ai_enrichment_message = f"{reason}. Using rule-based fallback."
                logger.info(self.last_ai_enrichment_message)
                logger.debug("AI enrichment technical details", exc_info=True)
                print(f"  {self.last_ai_enrichment_message}")
        
        # Generate business glossary
        try:
            glossary = generate_business_glossary(final_knowledge_base, use_ai_enrichment=use_ai_enrichment)
            save_business_glossary(glossary, "semantic/business_glossary.json")
            self.business_glossary = glossary
            logger.info("Business glossary saved")
        except Exception as e:
            logger.error(f"Failed to generate business glossary: {e}")
        
        self.knowledge_base = final_knowledge_base
        self.last_build_summary = summarize_knowledge_base(final_knowledge_base)
        self.refresh_vector_index()
        return True, "Knowledge base built successfully", final_knowledge_base
    
    def load_knowledge_base(self) -> tuple[bool, str, Optional[Dict[str, Any]]]:
        """
        Load knowledge base from file.
        
        Returns:
            (success, message, knowledge_base)
        """
        try:
            knowledge_base = load_json(KNOWLEDGE_BASE_PATH)
            self.knowledge_base = knowledge_base
            if self.business_glossary is None:
                self.load_business_glossary()
            else:
                self.refresh_vector_index()
            return True, "Knowledge base loaded successfully", knowledge_base
        except FileNotFoundError:
            return False, "Knowledge base not found", None
        except Exception as e:
            logger.error(f"Failed to load knowledge base: {e}")
            return False, f"Failed to load knowledge base: {e}", None
    
    def load_business_glossary(self, glossary_path: str = "semantic/business_glossary.json") -> tuple[bool, str, Optional[Dict[str, Any]]]:
        """
        Load business glossary from file.
        
        Returns:
            (success, message, glossary)
        """
        try:
            if self.knowledge_base:
                has_ai_terms = any(
                    column.get("business_terms")
                    for table_data in self.knowledge_base.values()
                    for column in table_data.get("columns", [])
                )
                glossary = generate_business_glossary(
                    self.knowledge_base,
                    use_ai_enrichment=bool(has_ai_terms),
                )
            else:
                glossary = load_business_glossary(glossary_path)
            self.business_glossary = glossary
            self.refresh_vector_index()
            return True, "Business glossary loaded successfully", glossary
        except Exception as e:
            logger.error(f"Failed to load business glossary: {e}")
            return False, f"Failed to load business glossary: {e}", None
    
    def search_glossary(self, search_term: str) -> tuple[bool, str, Optional[Dict[str, Any]]]:
        """
        Search business glossary for a term.
        
        Returns:
            (success, message, matches)
        """
        if not self.business_glossary:
            success, _, self.business_glossary = self.load_business_glossary()
            if not success:
                return False, "Business glossary not available", None
        
        try:
            from semantic.business_glossary import search_business_glossary
            matches = search_business_glossary(search_term, self.business_glossary)
            return True, "Search completed", matches
        except Exception as e:
            logger.error(f"Glossary search failed: {e}")
            return False, f"Search failed: {e}", None
    
    def is_connected(self) -> bool:
        """Check if database is connected."""
        return self.engine is not None
    
    def get_engine(self) -> Optional[Engine]:
        """Get the database engine."""
        return self.engine
    
    def get_knowledge_base(self) -> Optional[Dict[str, Any]]:
        """Get the knowledge base."""
        return self.knowledge_base
    
    def get_business_glossary(self) -> Optional[Dict[str, Any]]:
        """Get the business glossary."""
        return self.business_glossary

    def refresh_vector_index(self) -> None:
        """
        Rebuild the in-memory vector index from the active KB and glossary.

        Persistence is intentionally left for a later phase; this method keeps
        the current CLI session fast and deterministic without changing storage.
        """
        if not self.knowledge_base:
            self.vector_retriever = None
            self.vector_index_status = "not_built"
            return

        retriever = VectorRetriever(self.embedding_service)
        kb_docs = self.vector_index_builder.build_from_knowledge_base(self.knowledge_base)
        retriever.add_documents(kb_docs)

        if self.business_glossary:
            glossary_docs = self.vector_index_builder.build_from_glossary(self.business_glossary)
            retriever.add_documents(glossary_docs)

        self.vector_retriever = retriever
        self.vector_index_status = "ready"

    def get_vector_retriever(self) -> Optional[VectorRetriever]:
        """Return the active in-memory vector retriever for the current session."""
        if self.vector_retriever is None and self.knowledge_base:
            self.refresh_vector_index()
        return self.vector_retriever

    def get_last_ai_enrichment_result(self) -> tuple[str, str]:
        """Return the last AI enrichment status and clean CLI message."""
        return self.last_ai_enrichment_status, self.last_ai_enrichment_message

    def get_last_ai_enrichment_report(self) -> tuple[list[str], dict[str, str]]:
        """Return enriched and fallback tables from the last AI enrichment run."""
        return get_last_enrichment_report()
    
    def get_db_config(self) -> Dict[str, Any]:
        """Get the database configuration."""
        return self.db_config

    def get_last_build_summary(self) -> Dict[str, Any]:
        """Return the latest knowledge-base build summary."""
        return dict(self.last_build_summary)
