"""
conversation/conversation_memory.py
===================================
Manages conversation sessions and turn history for follow-up question support.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from utils.file_utils import save_json
from utils.logger import get_logger


class ConversationMemory:
    """
    Manages a conversation session with turn history.
    
    Stores:
    - session_id
    - started_at
    - ended_at
    - status
    - turns (list of conversation turns)
    - last_user_question
    - last_rewritten_question
    - last_generated_sql
    - last_rows
    - last_chart_path
    - last_insights
    - last_insights_skipped
    - last_tables_used
    - last_business_terms_used
    - last_filters_used
    """
    
    def __init__(self, session_id: str | None = None):
        """
        Initialize a new conversation session.
        
        Args:
            session_id: Optional session ID. If None, generates one from timestamp.
        """
        self.logger = get_logger()
        
        if session_id is None:
            session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        self.session_id = session_id
        self.started_at = datetime.now().isoformat()
        self.ended_at: str | None = None
        self.status = "active"  # active, ended
        self.turns: list[dict[str, Any]] = []
        
        # Last context for follow-up rewriting
        self.last_user_question: str | None = None
        self.last_rewritten_question: str | None = None
        self.last_generated_sql: str | None = None
        self.last_rows: list[dict[str, Any]] | None = None
        self.last_chart_path: str | None = None
        self.last_insights: list[str] | None = None
        self.last_insights_skipped: bool = False
        self.last_tables_used: list[str] | None = None
        self.last_business_terms_used: list[str] | None = None
        self.last_filters_used: list[str] | None = None
        
        self.logger.info(f"Conversation session started: {self.session_id}")
    
    def add_turn(
        self,
        user_question: str,
        is_follow_up: bool,
        rewritten_question: str,
        generated_sql: str,
        row_count: int = 0,
        method: str | None = None,
        chart_path: str | None = None,
        insights: list[str] | None = None,
    ) -> None:
        """
        Add a turn to the conversation history.
        
        Args:
            user_question: The original user question
            is_follow_up: Whether this was a follow-up question
            rewritten_question: The rewritten standalone question
            generated_sql: The SQL that was generated
            row_count: Number of rows returned
            method: SQL generation method (simple or ai)
            chart_path: Path to generated chart (if any)
            insights: List of insights (if any)
        """
        turn_id = len(self.turns) + 1
        timestamp = datetime.now().isoformat()
        
        turn = {
            "turn_id": turn_id,
            "timestamp": timestamp,
            "user_question": user_question,
            "is_follow_up": is_follow_up,
            "rewritten_question": rewritten_question,
            "generated_sql": generated_sql,
            "row_count": row_count,
            "method": method,
            "chart_path": chart_path,
            "insights": insights or [],
        }
        
        self.turns.append(turn)
        
        # Update last context
        self.last_user_question = user_question
        self.last_rewritten_question = rewritten_question
        self.last_generated_sql = generated_sql
        self.last_chart_path = chart_path
        self.last_insights = insights
        
        self.logger.debug(f"Added turn {turn_id} to conversation session {self.session_id}")
    
    def get_last_context(self) -> dict[str, Any]:
        """
        Get the last context for follow-up question rewriting.
        
        Returns:
            Dictionary with last context information
        """
        return {
            "last_user_question": self.last_user_question,
            "last_rewritten_question": self.last_rewritten_question,
            "last_generated_sql": self.last_generated_sql,
            "last_rows": self.last_rows,
            "last_chart_path": self.last_chart_path,
            "last_insights": self.last_insights,
            "last_insights_skipped": self.last_insights_skipped,
            "last_tables_used": self.last_tables_used,
            "last_business_terms_used": self.last_business_terms_used,
            "last_filters_used": self.last_filters_used,
            "turn_count": len(self.turns),
        }
    
    def clear_context(self) -> None:
        """Clear the last context (for new chat)."""
        self.last_user_question = None
        self.last_rewritten_question = None
        self.last_generated_sql = None
        self.last_rows = None
        self.last_chart_path = None
        self.last_insights = None
        self.last_insights_skipped = False
        self.last_tables_used = None
        self.last_business_terms_used = None
        self.last_filters_used = None
        
        self.logger.debug(f"Cleared context for conversation session {self.session_id}")
    
    def update_execution_results(
        self,
        rows: list[dict[str, Any]],
        chart_path: str | None = None,
        insights: list[str] | None = None,
        insights_skipped: bool = False,
    ) -> None:
        """
        Update the latest turn with execution results.
        
        Args:
            rows: Query result rows
            chart_path: Path to generated chart (if any)
            insights: Generated insights (if any)
            insights_skipped: Whether user chose to skip insight generation
        """
        if self.turns:
            self.turns[-1]["row_count"] = len(rows)
            self.turns[-1]["chart_path"] = chart_path
            self.turns[-1]["insights"] = insights or []
            self.turns[-1]["insights_skipped"] = insights_skipped
        
        self.last_rows = rows
        self.last_chart_path = chart_path
        self.last_insights = insights
        self.last_insights_skipped = insights_skipped
        
        self.logger.debug(f"Updated execution results for conversation session {self.session_id}")
    
    def end_session(self) -> None:
        """End the conversation session."""
        self.ended_at = datetime.now().isoformat()
        self.status = "ended"
        self.logger.info(f"Conversation session ended: {self.session_id}")
    
    def save_session(self, output_dir: str = "output/conversations") -> str:
        """
        Save the conversation session to a JSON file.
        
        Args:
            output_dir: Directory to save the session file
        
        Returns:
            Path to the saved session file
        """
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
        
        filename = f"session_{self.session_id}.json"
        file_path = output_path / filename
        
        session_data = {
            "session_id": self.session_id,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "status": self.status,
            "turns": self.turns,
            "last_user_question": self.last_user_question,
            "last_rewritten_question": self.last_rewritten_question,
            "last_generated_sql": self.last_generated_sql,
            "last_chart_path": self.last_chart_path,
            "last_insights": self.last_insights,
            "last_insights_skipped": self.last_insights_skipped,
            "last_tables_used": self.last_tables_used,
            "last_business_terms_used": self.last_business_terms_used,
            "last_filters_used": self.last_filters_used,
        }
        
        save_json(session_data, str(file_path))
        self.logger.info(f"Conversation session saved to {file_path}")
        
        return str(file_path)
    
    def get_recent_turns(self, count: int = 5) -> list[dict[str, Any]]:
        """
        Get the most recent conversation turns.
        
        Args:
            count: Number of recent turns to return
        
        Returns:
            List of recent turns
        """
        return self.turns[-count:] if self.turns else []
    
    def to_dict(self) -> dict[str, Any]:
        """Convert the conversation memory to a dictionary."""
        return {
            "session_id": self.session_id,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "status": self.status,
            "turns": self.turns,
            "last_user_question": self.last_user_question,
            "last_rewritten_question": self.last_rewritten_question,
            "last_generated_sql": self.last_generated_sql,
            "last_chart_path": self.last_chart_path,
            "last_insights": self.last_insights,
            "last_tables_used": self.last_tables_used,
            "last_business_terms_used": self.last_business_terms_used,
            "last_filters_used": self.last_filters_used,
        }
