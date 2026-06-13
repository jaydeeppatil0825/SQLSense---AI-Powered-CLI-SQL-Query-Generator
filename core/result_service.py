"""
core/result_service.py
======================
Result service for SQL execution.

This service handles CLI SQL execution and result processing.
"""

from typing import Optional, List, Dict, Any
from sqlalchemy.engine import Engine

from db.query_executor import execute_query
from utils.sql_validator import validate_sql
from utils.logger import get_logger

logger = get_logger()


class ResultService:
    """Service for SQL execution and result processing."""
    
    def __init__(self):
        self.last_sql: Optional[str] = None
        self.last_rows: Optional[List[Dict[str, Any]]] = None
        self.last_row_count: Optional[int] = None
        self.last_columns: Optional[List[str]] = None
        self.last_question: Optional[str] = None
    
    def execute_sql(
        self,
        sql: str,
        engine: Engine,
        knowledge_base: Optional[Dict[str, Any]] = None,
        revalidate: bool = True,
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
            rows = execute_query(sql, engine, knowledge_base=knowledge_base)
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
