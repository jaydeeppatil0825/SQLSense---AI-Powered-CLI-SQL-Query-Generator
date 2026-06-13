"""
core/insight_service.py
=======================
Insight service for insights generation.

This service handles CLI insight generation from query results.
"""

from typing import Optional, List, Dict, Any

from insights.insight_generator import generate_insights
from utils.logger import get_logger

logger = get_logger()


class InsightService:
    """Service for insights generation."""
    
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
        Generate insights from query results.
        
        Args:
            user_question: Original user question
            sql: SQL that was executed
            rows: Query results
            knowledge_base: Knowledge base for context
            ai_backend: AI backend to use
        
        Returns:
            (success, message, insights)
        """
        if not rows:
            return False, "No data to analyze", None
        
        try:
            insights = generate_insights(
                user_question=user_question,
                sql=sql,
                rows=rows,
                knowledge_base=knowledge_base,
                backend=ai_backend,
            )
            logger.info(f"Generated {len(insights)} insights")
            
            # Store insights
            self.last_insights = insights
            self.last_insights_skipped = False
            
            return True, "Insights generated successfully", insights
        except Exception as e:
            logger.error(f"Insight generation failed: {e}")
            return False, f"Insight generation failed: {e}", None
    
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
