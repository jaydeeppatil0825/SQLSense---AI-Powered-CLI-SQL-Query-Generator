"""
core/database_service.py
========================
KB Pipeline service for runtime database knowledge assets.

This service belongs to the Database Knowledge Foundation pipeline.
It handles runtime database connection, knowledge-base build/load,
glossary build/load, relationship/vector refresh, and cached KB metadata
for the CLI flow.

It must not depend on user-question understanding or SQL generation logic.
"""

import os
from typing import Optional, Dict, Any, List
from sqlalchemy.engine import Engine
from kb_pipeline.connection import connect_engine, get_engine, list_accessible_databases, SUPPORTED_DB_TYPES
from core.ai_backend_service import check_ollama_status, get_ai_backend_service
from kb_pipeline.knowledge_base_builder import build_knowledge_base
from kb_pipeline.schema_facts import enrich_knowledge_base_schema_facts, summarize_knowledge_base
from kb_pipeline.schema_facts import column_business_terms
from kb_pipeline.business_glossary import load_business_glossary, generate_business_glossary, save_business_glossary
from kb_pipeline.ai_semantic_enricher import (
    enrich_knowledge_base_with_ai,
    get_last_enrichment_reason,
    get_last_enrichment_report,
)
from utils.file_utils import load_json, save_json
from utils.logger import get_logger
from kb_pipeline.vector.embedding_service import EmbeddingService
from kb_pipeline.vector.chroma_store import ChromaStore, HybridVectorRetriever
from kb_pipeline.vector.index_builder import VectorIndexBuilder
from kb_pipeline.vector.persistence import VectorIndexPersistence
from kb_pipeline.vector.retriever import VectorRetriever
from semantic_learning.candidate_store import FileCandidateStore
from semantic_learning.promotion_policy import apply_promotion_policy, approved_aliases_for_schema
from kb_pipeline.semantic_providers.provider_chain import build_default_provider_chain

logger = get_logger()

KNOWLEDGE_BASE_PATH = "semantic/knowledge_base.json"
KNOWLEDGE_BASE_META_PATH = "semantic/knowledge_base.meta.json"


def _generate_business_glossary(
    knowledge_base: Dict[str, Any],
    *,
    use_ai_enrichment: bool = False,
    learned_aliases: Optional[List[Any]] = None,
) -> Dict[str, Any]:
    """Call the glossary builder while preserving older test doubles."""
    try:
        return generate_business_glossary(
            knowledge_base,
            use_ai_enrichment=use_ai_enrichment,
            learned_aliases=learned_aliases,
        )
    except TypeError as exc:
        if "learned_aliases" not in str(exc):
            raise
        return generate_business_glossary(
            knowledge_base,
            use_ai_enrichment=use_ai_enrichment,
        )


def _semantic_fallback_message(reason: str) -> str:
    normalized = str(reason or "").strip()
    lowered = normalized.lower()
    if "ollama" in lowered and ("not running" in lowered or "connection" in lowered or "unreachable" in lowered):
        return "Ollama is not running. Using rule-based enrichment."
    if "nvidia" in lowered and "timeout" in lowered:
        return "NVIDIA backend timed out. Using rule-based enrichment."
    if "nvidia" in lowered and "empty response" in lowered:
        return "NVIDIA backend returned an empty response. Using rule-based enrichment."
    if "nvidia" in lowered and ("disabled" in lowered or "connected" in lowered or "unreachable" in lowered):
        return "NVIDIA backend is not connected. Using rule-based enrichment."
    if "timed out" in lowered or "timeout" in lowered:
        return "Local AI timed out. Using rule-based fallback."
    if normalized:
        return f"{normalized}. Using rule-based fallback."
    return "AI enrichment unavailable. Using rule-based fallback."


class DatabaseService:
    """Service for database operations."""
    
    def __init__(self):
        self.engine: Optional[Engine] = None
        self.knowledge_base: Optional[Dict[str, Any]] = None
        self.business_glossary: Optional[Dict[str, Any]] = None
        self.knowledge_base_metadata: Dict[str, Any] = {}
        self.knowledge_base_origin: str = "none"
        self.db_config: Dict[str, Any] = {}
        self.last_ai_enrichment_status: str = "skipped"
        self.last_ai_enrichment_message: str = "AI enrichment skipped."
        self.last_build_summary: Dict[str, Any] = {}
        self.embedding_service = EmbeddingService()
        self.vector_index_builder = VectorIndexBuilder(self.embedding_service)
        self.vector_index_storage = VectorIndexPersistence()
        self.chroma_store = ChromaStore(self.embedding_service)
        self.vector_retriever: Optional[Any] = None
        self.vector_fallback_retriever: Optional[VectorRetriever] = None
        self.vector_index_status: str = "not_built"
        self.vector_index_details: Dict[str, Any] = self._make_vector_index_details(
            source="none",
            stale_reason="vector index not built",
        )
        self._warm_start_cached_assets()

    def _reset_active_database_context(
        self,
        *,
        clear_connection: bool = True,
        clear_cached_assets: bool = True,
        stale_reason: str = "",
        index_status: str = "not_built",
        source: str = "none",
    ) -> None:
        """Clear runtime database state so stale KB/vector context is never reused."""
        if clear_connection:
            self.engine = None
            self.db_config = {}

        if clear_cached_assets:
            self.knowledge_base = None
            self.business_glossary = None
            self.knowledge_base_metadata = {}
            self.knowledge_base_origin = "none"
            self.last_build_summary = {}

        self.vector_retriever = None
        self.vector_fallback_retriever = None
        self.vector_index_status = index_status
        self.vector_index_details = self._make_vector_index_details(
            source=source,
            stale_reason=stale_reason,
        )

    def _connected_database_identity(self) -> Dict[str, Any]:
        """Return the active connected database identity for KB/vector checks."""
        database_type = str(self.db_config.get("db_type", "") or "")
        database_name = str(self.db_config.get("database", "") or "")
        database_host = str(self.db_config.get("host", "") or "")
        database_port = self.db_config.get("port", "")
        if database_type == "sqlite" and not database_name:
            database_name = str(self.db_config.get("sqlite_path", "") or "")
            database_host = ""
            database_port = ""
        return {
            "db_engine": database_type,
            "db_host": database_host,
            "db_port": str(database_port or ""),
            "db_name": database_name,
            "database_type": database_type,
            "database_name": database_name,
        }

    def _load_knowledge_base_metadata_file(self) -> Dict[str, Any]:
        try:
            metadata = load_json(KNOWLEDGE_BASE_META_PATH)
            return metadata if isinstance(metadata, dict) else {}
        except FileNotFoundError:
            return {}
        except Exception as exc:
            logger.debug(f"Knowledge base metadata could not be loaded: {exc}")
            return {}

    def _save_knowledge_base_metadata_file(self, metadata: Dict[str, Any]) -> None:
        save_json(metadata, KNOWLEDGE_BASE_META_PATH)

    def _knowledge_base_matches_connected_database(self) -> bool:
        """Return True when the active KB metadata matches the connected database."""
        if not self.engine:
            return True

        expected = self._connected_database_identity()
        metadata = self.knowledge_base_metadata or {}
        metadata_type = str(metadata.get("database_type", "") or "")
        metadata_name = str(metadata.get("database_name", "") or "")
        expected_type = str(expected.get("database_type", "") or "")
        expected_name = str(expected.get("database_name", "") or "")
        expected_host = str(expected.get("db_host", "") or "")
        expected_port = str(expected.get("db_port", "") or "")
        metadata_host = str(metadata.get("db_host", "") or "")
        metadata_port = str(metadata.get("db_port", "") or "")

        if not expected_type or not expected_name:
            return True
        if not metadata_type or not metadata_name:
            return False
        if metadata_type != expected_type or metadata_name != expected_name:
            return False
        if expected_host and metadata_host and metadata_host != expected_host:
            return False
        if expected_port and metadata_port and metadata_port != expected_port:
            return False
        return True

    def _is_missing_database_error(self, error: Exception) -> bool:
        message = str(error or "").lower()
        return "unknown database" in message or "(1049" in message

    def _format_missing_database_error(
        self,
        *,
        db_type: str,
        host: str,
        port: Optional[int],
        username: str,
        password: str,
        database: str,
        error: Exception,
    ) -> str:
        """Build a clean dynamic connection failure message for a missing database."""
        base_message = (
            f"Could not connect to database '{database}' on {db_type}://{username}@{host}:{port}. "
            f"Connection error: {error}"
        )

        try:
            available_databases = list_accessible_databases(
                db_type=db_type,
                host=host,
                port=port,
                username=username,
                password=password,
            )
        except Exception as exc:
            logger.debug(f"Could not list available databases: {exc}")
            available_databases = []

        if available_databases:
            preview = ", ".join(available_databases[:10])
            if len(available_databases) > 10:
                preview += ", ..."
            return (
                f"{base_message} Available databases: {preview}. "
                "Next action: enter the correct database name or create/import the database first."
            )

        return (
            f"{base_message} Next action: enter the correct database name or create/import the database first."
        )

    def _warm_start_cached_assets(self) -> None:
        """Warm-load cached KB, glossary, and vector index for a fresh CLI session."""
        if not os.path.exists(KNOWLEDGE_BASE_PATH):
            return

        try:
            self.knowledge_base = load_json(KNOWLEDGE_BASE_PATH)
            self.knowledge_base_metadata = self._load_knowledge_base_metadata_file()
            self.knowledge_base_origin = "loaded"
        except Exception as exc:
            logger.debug(f"Warm start skipped because KB could not be loaded: {exc}")
            return

        try:
            glossary = load_business_glossary("semantic/business_glossary.json")
            self.business_glossary = self._align_glossary_with_active_knowledge_base(glossary)
        except FileNotFoundError:
            logger.debug("Business glossary file not found during warm start, will regenerate if needed")
            try:
                has_ai_terms = any(
                    column_business_terms(column)
                    for table_data in (self.knowledge_base or {}).values()
                    for column in table_data.get("columns", [])
                )
                self.business_glossary = _generate_business_glossary(
                    self.knowledge_base or {},
                    use_ai_enrichment=bool(has_ai_terms),
                )
            except Exception as exc:
                logger.warning(f"Failed to generate business glossary during warm start: {exc}")
                self.business_glossary = None
        except Exception as exc:
            logger.warning(f"Failed to load or align business glossary during warm start: {exc}")
            try:
                has_ai_terms = any(
                    column_business_terms(column)
                    for table_data in (self.knowledge_base or {}).values()
                    for column in table_data.get("columns", [])
                )
                self.business_glossary = _generate_business_glossary(
                    self.knowledge_base or {},
                    use_ai_enrichment=bool(has_ai_terms),
                )
            except Exception as exc2:
                logger.warning(f"Failed to generate fallback business glossary: {exc2}")
                self.business_glossary = None

        try:
            self._load_persisted_vector_index_only()
        except Exception as exc:
            logger.debug(f"Warm start vector refresh skipped: {exc}")

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
            return _generate_business_glossary(self.knowledge_base, use_ai_enrichment=False)

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
                    self._reset_active_database_context(
                        stale_reason="database connection failed",
                    )
                    return False, "SQLite file path cannot be empty.", None
                engine = connect_engine(db_type="sqlite", sqlite_path=sqlite_path)
                self.engine = engine
                self.db_config = {"db_type": "sqlite", "sqlite_path": sqlite_path}
                if self.knowledge_base is not None and not self._knowledge_base_matches_connected_database():
                    self._reset_active_database_context(
                        clear_connection=False,
                        clear_cached_assets=True,
                        index_status="stale",
                        source="stale",
                        stale_reason="connected database differs from the cached knowledge base; rebuild the knowledge base for this database",
                    )
                    logger.info(f"Connected to SQLite: {sqlite_path}")
                    return (
                        True,
                        f"Connected to SQLite: {sqlite_path}. Cached knowledge base does not match this database. Build the knowledge base again before asking questions.",
                        engine,
                    )
                if self.knowledge_base is not None:
                    self.refresh_vector_index()
                logger.info(f"Connected to SQLite: {sqlite_path}")
                return True, f"Connected to SQLite: {sqlite_path}", engine
            else:
                if db_type not in SUPPORTED_DB_TYPES:
                    self._reset_active_database_context(
                        stale_reason="database connection failed",
                    )
                    return False, f"Unsupported database type: {db_type}", None
                
                if not username:
                    self._reset_active_database_context(
                        stale_reason="database connection failed",
                    )
                    return False, "Username cannot be empty.", None
                
                if not database:
                    self._reset_active_database_context(
                        stale_reason="database connection failed",
                    )
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
                if self.knowledge_base is not None and not self._knowledge_base_matches_connected_database():
                    self._reset_active_database_context(
                        clear_connection=False,
                        clear_cached_assets=True,
                        index_status="stale",
                        source="stale",
                        stale_reason="connected database differs from the cached knowledge base; rebuild the knowledge base for this database",
                    )
                    logger.info(f"Connected to {db_type}://{username}@{host}:{port}/{database}")
                    return (
                        True,
                        f"Connected to {db_type}://{username}@{host}:{port}/{database}. Cached knowledge base does not match this database. Build the knowledge base again before asking questions.",
                        engine,
                    )
                if self.knowledge_base is not None:
                    self.refresh_vector_index()
                logger.info(f"Connected to {db_type}://{username}@{host}:{port}/{database}")
                return True, f"Connected to {db_type}://{username}@{host}:{port}/{database}", engine
        except Exception as e:
            self._reset_active_database_context(
                stale_reason="database connection failed",
            )
            logger.error(f"Database connection failed: {e}")
            if self._is_missing_database_error(e):
                message = self._format_missing_database_error(
                    db_type=db_type,
                    host=host,
                    port=port,
                    username=username,
                    password=password,
                    database=database,
                    error=e,
                )
                return False, message, None
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
            self.db_config = {
                "db_type": "mysql",
                "host": os.getenv("DB_HOST", ""),
                "port": int(os.getenv("DB_PORT", "3306") or 3306),
                "username": os.getenv("DB_USER", ""),
                "database": os.getenv("DB_NAME", ""),
            }
            return True, "Connected using environment variables", engine
        except Exception as e:
            self._reset_active_database_context(
                stale_reason="database connection failed",
            )
            logger.error(f"Database connection from env failed: {e}")
            return False, f"Connection failed: {e}", None
    
    def build_knowledge_base(
        self,
        use_ai_enrichment: bool = False,
        ai_backend: str = "local",
        force_rebuild: bool = False,
    ) -> tuple[bool, str, Optional[Dict[str, Any]]]:
        """
        Build knowledge base from connected database.
        
        Args:
            use_ai_enrichment: Whether to use AI for semantic enrichment
            ai_backend: AI backend to use for enrichment
            force_rebuild: Whether to discard cached KB/glossary/vector state and rebuild fresh
        
        Returns:
            (success, message, knowledge_base)
        """
        if not self.engine:
            return False, "No database connection. Connect a database first.", None

        if force_rebuild:
            self._reset_active_database_context(
                clear_connection=False,
                clear_cached_assets=True,
                stale_reason="knowledge base rebuild requested",
                index_status="not_built",
                source="rebuild_requested",
            )
        
        try:
            knowledge_base = build_knowledge_base(self.engine)
            logger.info("Knowledge base built successfully")
        except Exception as e:
            logger.error(f"Knowledge base build failed: {e}")
            return False, f"Knowledge base build failed: {e}", None
        
        # Save knowledge base
        try:
            knowledge_base = enrich_knowledge_base_schema_facts(knowledge_base)
            save_json(knowledge_base, KNOWLEDGE_BASE_PATH)
            schema_payload = {
                table_name: {
                    "columns": [
                        {
                            "name": column.get("name"),
                            "type": column.get("type"),
                            "nullable": column.get("nullable"),
                            "semantic_type": column.get("semantic_type"),
                        }
                        for column in table_data.get("columns", [])
                    ],
                    "primary_keys": list(table_data.get("primary_keys", [])),
                    "foreign_keys": list(table_data.get("foreign_keys", [])),
                    "relationships": list(table_data.get("relationships", [])),
                }
                for table_name, table_data in knowledge_base.items()
            }
            self.knowledge_base_metadata = {
                **self._connected_database_identity(),
                "schema_hash": self.vector_index_storage._content_hash(schema_payload),
                "schema_fingerprint": self.vector_index_storage._content_hash(schema_payload),
            }
            self._save_knowledge_base_metadata_file(self.knowledge_base_metadata)
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
                preferred_order = ai_backend if ai_backend == "nvidia" else None
                chain = build_default_provider_chain(
                    ai_enabled=True,
                    provider_order=preferred_order,
                    allow_cloud_provider=ai_backend == "nvidia",
                    enrich_func=enrich_knowledge_base_with_ai,
                    local_health_check=check_ollama_status,
                    nvidia_health_check=lambda: get_ai_backend_service().test_backend_connection("nvidia"),
                )
                semantic_result = chain.map_schema_semantics(knowledge_base)
                self.knowledge_base_metadata["semantic_enrichment"] = semantic_result.as_metadata()

                if semantic_result.status == "enriched":
                    final_knowledge_base = enrich_knowledge_base_schema_facts(semantic_result.knowledge_base)
                    enriched_tables, fallback_tables = get_last_enrichment_report()
                    self.last_ai_enrichment_status = "partial" if fallback_tables else "completed"
                    self.last_ai_enrichment_message = (
                        f"AI enrichment completed for {len(enriched_tables)} table(s); "
                        f"fallback used for {len(fallback_tables)} table(s)."
                        if fallback_tables
                        else "AI enrichment completed successfully."
                    )
                    try:
                        save_json(final_knowledge_base, KNOWLEDGE_BASE_PATH)
                        self._save_knowledge_base_metadata_file(self.knowledge_base_metadata)
                        logger.info(f"Enriched knowledge base saved to {KNOWLEDGE_BASE_PATH}")
                    except Exception as e:
                        logger.error(f"Failed to save enriched knowledge base: {e}")
                        final_knowledge_base = knowledge_base
                        self.last_ai_enrichment_status = "fallback"
                        self.last_ai_enrichment_message = "Could not save enriched knowledge base. Using rule-based enrichment."
                else:
                    self.last_ai_enrichment_status = "fallback"
                    self.last_ai_enrichment_message = _semantic_fallback_message(semantic_result.reason)
                    logger.info(self.last_ai_enrichment_message)
            except Exception as e:
                # Catch everything so a timeout or network error never stops KB generation
                self.last_ai_enrichment_status = "fallback"
                self.last_ai_enrichment_message = _semantic_fallback_message(str(e))
                logger.info(self.last_ai_enrichment_message)
                logger.debug("AI enrichment technical details", exc_info=True)
        
        # Generate business glossary
        try:
            schema_fingerprint = str(self.knowledge_base_metadata.get("schema_fingerprint") or "")
            learned_store = FileCandidateStore()
            learned_candidates = apply_promotion_policy(
                learned_store.load(),
                final_knowledge_base,
                schema_fingerprint=schema_fingerprint,
            )
            learned_store.save(learned_candidates)
            learned_aliases = approved_aliases_for_schema(learned_candidates, schema_fingerprint)
            glossary = _generate_business_glossary(
                final_knowledge_base,
                use_ai_enrichment=use_ai_enrichment,
                learned_aliases=learned_aliases,
            )
            save_business_glossary(glossary, "semantic/business_glossary.json")
            self.business_glossary = glossary
            logger.info("Business glossary saved")
        except Exception as e:
            logger.error(f"Failed to generate business glossary: {e}")
            self.business_glossary = {}
        
        self.knowledge_base = final_knowledge_base
        self.knowledge_base_origin = "built"
        self.knowledge_base_metadata = {
            **self._connected_database_identity(),
            "schema_hash": self.vector_index_storage._content_hash(self._schema_fingerprint_payload()),
            "schema_fingerprint": self.vector_index_storage._content_hash(self._schema_fingerprint_payload()),
        }
        try:
            self._save_knowledge_base_metadata_file(self.knowledge_base_metadata)
        except Exception as exc:
            logger.debug(f"Knowledge base metadata save skipped: {exc}")
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
            if self.knowledge_base and self._knowledge_base_matches_connected_database():
                if self.business_glossary is None:
                    self.load_business_glossary()
                else:
                    self.refresh_vector_index()
                return True, "Knowledge base loaded successfully", self.knowledge_base

            knowledge_base = load_json(KNOWLEDGE_BASE_PATH)
            metadata = self._load_knowledge_base_metadata_file()
            if self.engine:
                expected = self._connected_database_identity()
                metadata_type = str(metadata.get("database_type", "") or "")
                metadata_name = str(metadata.get("database_name", "") or "")
                if not metadata_type or not metadata_name:
                    self._reset_active_database_context(
                        clear_connection=False,
                        clear_cached_assets=True,
                        index_status="stale",
                        source="stale",
                        stale_reason="cached knowledge base has no database metadata; rebuild the knowledge base for the connected database",
                    )
                    return False, "Cached knowledge base does not match the connected database. Rebuild the knowledge base first.", None
                if (
                    metadata_type != str(expected.get("database_type", "") or "")
                    or metadata_name != str(expected.get("database_name", "") or "")
                ):
                    self._reset_active_database_context(
                        clear_connection=False,
                        clear_cached_assets=True,
                        index_status="stale",
                        source="stale",
                        stale_reason="connected database differs from the cached knowledge base; rebuild the knowledge base for this database",
                    )
                    return False, "Connected database differs from the cached knowledge base. Build the knowledge base again before asking questions.", None
            self.knowledge_base = knowledge_base
            self.knowledge_base_metadata = metadata
            self.knowledge_base_origin = "loaded"
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
                    column_business_terms(column)
                    for table_data in self.knowledge_base.values()
                    for column in table_data.get("columns", [])
                )
                glossary = _generate_business_glossary(
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
            from kb_pipeline.business_glossary import search_business_glossary
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

    def _make_vector_index_details(self, **overrides: Any) -> Dict[str, Any]:
        details = {
            "index_dir": str(self.vector_index_storage.index_dir),
            "manifest_path": str(self.vector_index_storage.manifest_path),
            "documents_path": str(self.vector_index_storage.documents_path),
            "exists": False,
            "fresh": False,
            "is_fresh": False,
            "source": "none",
            "loaded_from_disk": False,
            "rebuilt": False,
            "persisted": False,
            "document_count": 0,
            "stale_reason": "",
            "persistence_error": "",
            "knowledge_base_hash": "",
            "glossary_hash": "",
            "database_name": "",
            "database_type": "",
            "schema_fingerprint": "",
            "db_engine": "",
            "db_host": "",
            "db_port": "",
            "db_name": "",
            "schema_hash": "",
            "vector_index_version": "",
            "embedding_backend": self.embedding_service.get_backend_name(),
            "embedding_model": self.embedding_service.get_model_name(),
            "embedding_dimension": self.embedding_service.get_dimension(),
            "retrieval_backend": "fallback",
            "chroma_ready": False,
            "chroma_collection": "",
            "chroma_error": "",
        }
        details.update(overrides)
        return details

    def _schema_fingerprint_payload(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {}
        for table_name, table_data in (self.knowledge_base or {}).items():
            payload[table_name] = {
                "columns": [
                    {
                        "name": column.get("name"),
                        "type": column.get("type"),
                        "nullable": column.get("nullable"),
                        "semantic_type": column.get("semantic_type"),
                    }
                    for column in table_data.get("columns", [])
                ],
                "primary_keys": list(table_data.get("primary_keys", [])),
                "foreign_keys": list(table_data.get("foreign_keys", [])),
                "relationships": list(table_data.get("relationships", [])),
            }
        return payload

    def _vector_source_context(self) -> Dict[str, Any]:
        database_identity = self._connected_database_identity()
        if not database_identity.get("db_engine"):
            database_identity = {
                **database_identity,
                "db_engine": str(self.knowledge_base_metadata.get("db_engine", self.knowledge_base_metadata.get("database_type", "")) or ""),
                "db_host": str(self.knowledge_base_metadata.get("db_host", "") or ""),
                "db_port": str(self.knowledge_base_metadata.get("db_port", "") or ""),
                "db_name": str(self.knowledge_base_metadata.get("db_name", self.knowledge_base_metadata.get("database_name", "")) or ""),
            }

        schema_hash = self.vector_index_storage._content_hash(self._schema_fingerprint_payload())
        return {
            **database_identity,
            "database_type": database_identity.get("db_engine", ""),
            "database_name": database_identity.get("db_name", ""),
            "schema_hash": schema_hash,
            "schema_fingerprint": schema_hash,
        }

    def _build_vector_documents(self) -> list[dict[str, Any]]:
        """Build vector documents from the active KB and glossary."""
        source_context = self._vector_source_context()
        documents = self.vector_index_builder.build_from_knowledge_base(
            self.knowledge_base or {},
            source_context=source_context,
        )
        if self.business_glossary:
            documents.extend(
                self.vector_index_builder.build_from_glossary(
                    self.business_glossary,
                    source_context=source_context,
                )
            )
        return documents

    def _load_persisted_vector_index_only(self) -> None:
        """Load an existing persisted vector index without rebuilding during warm start."""
        if not self.knowledge_base:
            return

        loaded, _, documents, details = self.vector_index_storage.load_documents(
            self.knowledge_base,
            self.business_glossary,
            self.embedding_service,
            source_context=self._vector_source_context(),
        )
        if not loaded:
            self.vector_retriever = None
            self.vector_fallback_retriever = None
            self.vector_index_status = "not_built"
            self.vector_index_details = self._make_vector_index_details(**details)
            return

        fallback_retriever = VectorRetriever(self.embedding_service)
        fallback_retriever.add_documents(documents)
        self.vector_fallback_retriever = fallback_retriever

        chroma_loaded, _, chroma_details = self.chroma_store.load_current_collection(self._vector_source_context())
        if chroma_loaded:
            self.vector_retriever = HybridVectorRetriever(self.chroma_store, fallback_retriever)
        else:
            self.vector_retriever = fallback_retriever
        self.vector_index_status = "ready"
        self.vector_index_details = self._make_vector_index_details(
            **details,
            retrieval_backend="chroma" if chroma_loaded else "fallback",
            chroma_ready=bool(chroma_loaded),
            chroma_collection=str(chroma_details.get("collection_name", "") or ""),
            chroma_error=str(chroma_details.get("error", "") or ""),
        )

    def refresh_vector_index(self) -> None:
        """
        Load or rebuild the vector index from the active KB and glossary.

        KB and glossary remain the source of truth. The persisted vector index
        is only a reusable search layer derived from that source content.
        """
        if not self.knowledge_base:
            self.vector_retriever = None
            self.vector_fallback_retriever = None
            self.vector_index_status = "not_built"
            self.vector_index_details = self._make_vector_index_details(
                source="none",
                stale_reason="knowledge base not loaded",
            )
            return

        inspection = self.vector_index_storage.inspect_index(
            self.knowledge_base,
            self.business_glossary,
            self.embedding_service,
            source_context=self._vector_source_context(),
        )
        rebuild_reason = inspection.get("stale_reason", "")

        if (
            self.engine is not None
            and self.knowledge_base_origin != "built"
            and rebuild_reason in {
                "database name changed",
                "database engine changed",
                "database host changed",
                "database port changed",
                "schema hash changed",
                "schema fingerprint changed",
            }
        ):
            self.vector_retriever = None
            self.vector_fallback_retriever = None
            self.vector_index_status = "stale"
            stale_details = dict(inspection)
            stale_details["source"] = "stale"
            stale_details["stale_reason"] = f"{rebuild_reason}; rebuild the knowledge base for the connected database"
            self.vector_index_details = self._make_vector_index_details(**stale_details)
            return

        if inspection.get("fresh"):
            loaded, message, documents, load_details = self.vector_index_storage.load_documents(
                self.knowledge_base,
                self.business_glossary,
                self.embedding_service,
                source_context=self._vector_source_context(),
            )
            if loaded:
                fallback_retriever = VectorRetriever(self.embedding_service)
                fallback_retriever.add_documents(documents)
                self.vector_fallback_retriever = fallback_retriever
                chroma_loaded, _, chroma_details = self.chroma_store.load_current_collection(self._vector_source_context())
                if not chroma_loaded:
                    try:
                        chroma_loaded, _, chroma_details = self.chroma_store.build_or_refresh_chroma_index(
                            documents,
                            self._vector_source_context(),
                        )
                    except Exception as exc:
                        chroma_loaded = False
                        chroma_details = {"error": str(exc)}
                if chroma_loaded:
                    self.vector_retriever = HybridVectorRetriever(self.chroma_store, fallback_retriever)
                else:
                    self.vector_retriever = fallback_retriever
                self.vector_index_status = "ready"
                self.vector_index_details = self._make_vector_index_details(
                    **load_details,
                    retrieval_backend="chroma" if chroma_loaded else "fallback",
                    chroma_ready=bool(chroma_loaded),
                    chroma_collection=str(chroma_details.get("collection_name", "") or ""),
                    chroma_error=str(chroma_details.get("error", "") or ""),
                )
                logger.info(message)
                return

            rebuild_reason = load_details.get("stale_reason", rebuild_reason)
            inspection = load_details

        try:
            documents = self._build_vector_documents()
            fallback_retriever = VectorRetriever(self.embedding_service)
            fallback_retriever.add_documents(documents)
            self.vector_fallback_retriever = fallback_retriever
            self.vector_index_status = "ready"
        except Exception as exc:
            logger.error(f"Vector index rebuild failed: {exc}")
            self.vector_retriever = None
            self.vector_fallback_retriever = None
            self.vector_index_status = "degraded"
            failure_details = dict(inspection)
            failure_details.update(
                {
                    "source": "rebuild_failed",
                    "stale_reason": rebuild_reason or f"vector rebuild failed: {exc}",
                    "persistence_error": str(exc),
                }
            )
            self.vector_index_details = self._make_vector_index_details(**failure_details)
            return

        saved, message, save_details = self.vector_index_storage.save_documents(
            documents,
            self.knowledge_base,
            self.business_glossary,
            self.embedding_service,
            source_context=self._vector_source_context(),
        )
        if not saved:
            logger.warning(message)

        chroma_ready = False
        chroma_details: Dict[str, Any] = {}
        chroma_message = ""
        try:
            chroma_ready, chroma_message, chroma_details = self.chroma_store.build_or_refresh_chroma_index(
                documents,
                self._vector_source_context(),
            )
        except Exception as exc:
            chroma_ready = False
            chroma_message = str(exc)
            chroma_details = {"error": str(exc)}
            logger.warning(f"Chroma index refresh failed; using fallback vector retriever: {exc}")

        self.vector_retriever = (
            HybridVectorRetriever(self.chroma_store, self.vector_fallback_retriever)
            if self.vector_fallback_retriever
            else (self.chroma_store if chroma_ready else None)
        )
        final_stale_reason = rebuild_reason or save_details.get("stale_reason", "")
        rebuilt_details = dict(save_details)
        rebuilt_details.update(
            {
                "source": "rebuilt",
                "rebuilt": True,
                "loaded_from_disk": False,
                "exists": bool(save_details.get("persisted")) or inspection.get("exists", False),
                "fresh": True,
                "is_fresh": True,
                "stale_reason": final_stale_reason,
                "document_count": len(documents),
                "retrieval_backend": "chroma" if chroma_ready else "fallback",
                "chroma_ready": bool(chroma_ready),
                "chroma_collection": str(chroma_details.get("collection_name", "") or ""),
                "chroma_error": "" if chroma_ready else str(chroma_details.get("error", "") or chroma_message or ""),
            }
        )
        self.vector_index_details = self._make_vector_index_details(**rebuilt_details)

    def get_vector_retriever(self) -> Optional[VectorRetriever]:
        """Return the active in-memory vector retriever for the current session."""
        if self.vector_retriever is None and self.knowledge_base:
            self.refresh_vector_index()
        return self.vector_retriever

    def get_embedding_status(self) -> Dict[str, Any]:
        """Return embedding backend status for CLI/debug reporting."""
        return self.embedding_service.get_status()

    def get_vector_status(self) -> Dict[str, Any]:
        """Return vector index and retriever status for CLI/debug reporting."""
        retriever_status = self.vector_retriever.get_status() if self.vector_retriever else {}
        return {
            "index_status": self.vector_index_status,
            "embedding": self.get_embedding_status(),
            "retriever": retriever_status,
            "persistence": dict(self.vector_index_details),
            "chroma": self.chroma_store.get_status(),
        }

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
