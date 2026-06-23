"""
core/insight_service.py
=======================
Runtime insight service boundary.

AI is allowed only during KB semantic enrichment. Runtime insight generation
stays disabled so the CLI remains deterministic after KB build.
"""

from typing import Optional, List, Dict, Any

from utils.logger import get_logger

logger = get_logger()


class InsightService:
    """Service for runtime insight handling."""
    
    def __init__(self):
        self.last_insights: Optional[List[str]] = None
        self.last_insights_skipped: bool = False
    
    def generate_insights(
        self,
        user_question: str,
        sql: str,
        rows: List[Dict[str, Any]],
        knowledge_base: Optional[Dict[str, Any]] = None,
        ai_backend: str = "local",
    ) -> tuple[bool, str, Optional[List[str]]]:
        """
        Runtime insight generation is disabled.
        
        Args:
            user_question: Original user question
            sql: SQL that was executed
            rows: Query results
            knowledge_base: Knowledge base for context
            ai_backend: Kept for backward compatibility; not used.
        
        Returns:
            (success, message, insights)
        """
        if not rows:
            return False, "No data to analyze", None

        self.last_insights = None
        self.last_insights_skipped = True
        logger.info("Runtime insight generation skipped because AI is restricted to KB enrichment.")
        return (
            False,
            "Runtime insight generation is disabled. AI is used only during KB semantic enrichment.",
            None,
        )
    
    def get_last_insights(self) -> Optional[List[str]]:
        """Get last generated insights."""
        return self.last_insights
    
    def get_last_insights_skipped(self) -> bool:
        """Get whether last insights were skipped."""
        return self.last_insights_skipped
    
    def set_insights_skipped(self, skipped: bool) -> None:
        """Set whether insights were skipped."""
        self.last_insights_skipped = skipped
    
    def reset(self) -> None:
        """Reset insight state."""
        self.last_insights = None
        self.last_insights_skipped = False
