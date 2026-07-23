"""
core/result_service.py
======================
Result service for SQL execution.

This service handles CLI SQL execution and result processing.
"""

from datetime import datetime, timedelta, timezone
import hashlib
import json
from typing import Optional, List, Dict, Any
from uuid import uuid4
from sqlalchemy.engine import Engine

from sql_pipeline.query_executor import execute_query
from sql_pipeline.sql_validator import validate_sql
from utils.logger import get_logger

logger = get_logger()

PLANNED_QUERY_ARTIFACT_VERSION = "planned-query-artifact-v1"


def _stable_hash(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class ResultService:
    """Service for SQL execution and result processing."""
    
    def __init__(self):
        self.last_sql: Optional[str] = None
        self.last_rows: Optional[List[Dict[str, Any]]] = None
        self.last_row_count: Optional[int] = None
        self.last_columns: Optional[List[str]] = None
        self.last_question: Optional[str] = None
        self.last_selected_join_path: Optional[Dict[str, Any]] = None
        self.last_query_context: Optional[Dict[str, Any]] = None
        self.last_planned_query_artifact: Optional[Dict[str, Any]] = None

    def create_planned_query_artifact(
        self,
        *,
        sql: str,
        database_identity: Dict[str, Any],
        schema_fingerprint: str = "",
        kb_fingerprint: str = "",
        query_context: Optional[Dict[str, Any]] = None,
        ttl_seconds: int = 3600,
    ) -> Dict[str, Any]:
        """Store the immutable SQL artifact required for execution."""
        now = datetime.now(timezone.utc)
        artifact = {
            "artifact_type": "planned_query",
            "artifact_version": PLANNED_QUERY_ARTIFACT_VERSION,
            "query_id": uuid4().hex,
            "sql": sql,
            "sql_hash": _stable_hash(sql),
            "database_identity_hash": _stable_hash(database_identity or {}),
            "schema_fingerprint": str(schema_fingerprint or ""),
            "kb_fingerprint": str(kb_fingerprint or ""),
            "planner_contract_version": str((query_context or {}).get("planner_contract_version") or ""),
            "query_context": dict(query_context or {}),
            "created_at": now.isoformat(),
            "expires_at": (now + timedelta(seconds=max(1, int(ttl_seconds or 3600)))).isoformat(),
        }
        self.last_planned_query_artifact = artifact
        return artifact

    def validate_planned_query_artifact(
        self,
        sql: str,
    ) -> tuple[bool, str, Optional[Dict[str, Any]]]:
        """Return the stored artifact only when the requested SQL is exactly approved."""
        artifact = self.last_planned_query_artifact
        if not artifact:
            return False, "planned query artifact is missing", None
        if artifact.get("artifact_type") != "planned_query":
            return False, "planned query artifact type is invalid", None
        if artifact.get("artifact_version") != PLANNED_QUERY_ARTIFACT_VERSION:
            return False, "planned query artifact version is invalid", None
        if artifact.get("sql") != sql or artifact.get("sql_hash") != _stable_hash(sql):
            return False, "SQL does not match the planned query artifact", None
        try:
            expires_at = datetime.fromisoformat(str(artifact.get("expires_at") or ""))
        except ValueError:
            return False, "planned query artifact expiry is invalid", None
        if expires_at <= datetime.now(timezone.utc):
            return False, "planned query artifact has expired", None
        context = artifact.get("query_context")
        if not isinstance(context, dict):
            return False, "planned query artifact context is invalid", None
        return True, "planned query artifact accepted", artifact
    
    def execute_sql(
        self,
        sql: str,
        engine: Engine,
        knowledge_base: Optional[Dict[str, Any]] = None,
        revalidate: bool = True,
        selected_join_path: Optional[Dict[str, Any]] = None,
        query_context: Optional[Dict[str, Any]] = None,
    ) -> tuple[bool, str, Optional[List[Dict[str, Any]]]]:
        """
        Execute SQL query.
        
        Args:
            sql: SQL to execute
            engine: Database engine
            knowledge_base: Knowledge base for validation
            revalidate: Whether to revalidate SQL before execution
        
        Returns:
            (success, message, rows)
        """
        # Revalidate SQL if requested
        if revalidate:
            is_valid, reason = validate_sql(sql)
            if not is_valid:
                logger.error(f"SQL failed re-validation: {reason}")
                return False, f"SQL failed re-validation: {reason}", None
        
        # Execute query
        try:
            rows = execute_query(
                sql,
                engine,
                knowledge_base=knowledge_base,
                selected_join_path=selected_join_path,
                query_context=query_context,
            )
            logger.info(f"Query executed successfully, {len(rows)} rows returned")
            
            # Store results
            self.last_sql = sql
            self.last_rows = rows
            self.last_row_count = len(rows) if rows else 0
            self.last_columns = list(rows[0].keys()) if rows else []
            
            return True, "Query executed successfully", rows
        except Exception as e:
            logger.error(f"Query execution failed: {e}")
            return False, f"Execution failed: {e}", None
    
    def get_last_sql(self) -> Optional[str]:
        """Get last executed SQL."""
        return self.last_sql

    def get_last_selected_join_path(self) -> Optional[Dict[str, Any]]:
        """Get planner join path stored with the last generated SQL."""
        return self.last_selected_join_path

    def get_last_query_context(self) -> Optional[Dict[str, Any]]:
        """Get planner context stored with the last generated SQL."""
        return self.last_query_context

    def get_last_planned_query_artifact(self) -> Optional[Dict[str, Any]]:
        """Get execution artifact stored with the last generated SQL."""
        return self.last_planned_query_artifact
    
    def get_last_rows(self) -> Optional[List[Dict[str, Any]]]:
        """Get last query results."""
        return self.last_rows
    
    def get_last_row_count(self) -> Optional[int]:
        """Get last query row count."""
        return self.last_row_count
    
    def get_last_columns(self) -> Optional[List[str]]:
        """Get last query columns."""
        return self.last_columns
    
    def get_last_question(self) -> Optional[str]:
        """Get last question."""
        return self.last_question
    
    def set_last_question(self, question: str) -> None:
        """Set last question."""
        self.last_question = question
    
    def reset(self) -> None:
        """Reset result state."""
        self.last_sql = None
        self.last_rows = None
        self.last_row_count = None
        self.last_columns = None
        self.last_question = None
        self.last_selected_join_path = None
        self.last_query_context = None
        self.last_planned_query_artifact = None
