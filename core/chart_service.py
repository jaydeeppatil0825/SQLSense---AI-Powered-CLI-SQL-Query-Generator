"""
core/chart_service.py
=====================
Chart service for chart generation.

This service handles CLI chart generation from query results.
"""

from typing import Optional, Dict, Any
from datetime import datetime

from utils.logger import get_logger

try:
    from charts.chart_generator import detect_chart_type as _detect_chart_type
    from charts.chart_generator import generate_chart as _generate_chart
except ImportError:  # pragma: no cover - optional chart package is not always present.
    _detect_chart_type = None
    _generate_chart = None

logger = get_logger()


class ChartService:
    """Service for chart generation."""
    
    def __init__(self):
        self.last_chart_path: Optional[str] = None
        self.last_chart_type: Optional[str] = None
    
    def generate_chart(
        self,
        rows: list[Dict[str, Any]],
        chart_type: Optional[str] = None,
        output_path: Optional[str] = None,
    ) -> tuple[bool, str, Optional[str], Optional[str]]:
        """
        Generate chart from query results.
        
        Args:
            rows: Query results
            chart_type: Chart type (auto, bar, line, pie)
            output_path: Output path for chart file
        
        Returns:
            (success, message, chart_path, chart_type)
        """
        if _detect_chart_type is None or _generate_chart is None:
            return False, "Chart generation is not available in this installation", None, None

        if not rows:
            return False, "No data to chart", None, None
        
        # Detect chart type if not provided
        if not chart_type or chart_type == "auto":
            chart_type = _detect_chart_type(rows)
            if not chart_type:
                return False, "Chart not suitable for this result", None, None
        
        # Generate output path if not provided
        if not output_path:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_path = f"output/charts/chart_{timestamp}.png"
        
        try:
            chart_path = _generate_chart(rows, chart_type, output_path)
            logger.info(f"Chart saved successfully: {chart_path}")
            
            # Store chart info
            self.last_chart_path = chart_path
            self.last_chart_type = chart_type
            
            return True, "Chart generated successfully", chart_path, chart_type
        except ValueError as e:
            logger.warning(f"Chart generation skipped: {e}")
            return False, str(e), None, None
        except Exception as e:
            logger.error(f"Chart generation failed: {e}")
            return False, f"Chart generation failed: {e}", None, None
    
    def detect_chart_type(self, rows: list[Dict[str, Any]]) -> Optional[str]:
        """
        Detect suitable chart type for data.
        
        Args:
            rows: Query results
        
        Returns:
            Chart type or None
        """
        if not rows or _detect_chart_type is None:
            return None
        return _detect_chart_type(rows)
    
    def get_last_chart_path(self) -> Optional[str]:
        """Get last generated chart path."""
        return self.last_chart_path
    
    def get_last_chart_type(self) -> Optional[str]:
        """Get last generated chart type."""
        return self.last_chart_type
    
    def reset(self) -> None:
        """Reset chart state."""
        self.last_chart_path = None
        self.last_chart_type = None
