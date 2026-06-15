"""
core/app_service.py
===================
Application service as main CLI orchestrator.

This service coordinates the lower-level services behind the CLI.
"""

from typing import Optional, Dict, Any, List, Tuple
from sqlalchemy.engine import Engine

from core.database_service import DatabaseService
from core.question_service import QuestionService
from core.result_service import ResultService
from core.chart_service import ChartService
from core.insight_service import InsightService
from core.ai_backend_service import AIBackendService
from utils.logger import get_logger

logger = get_logger()


class AppService:
    """Main application service orchestrator."""
    
    def __init__(self):
        self.database_service = DatabaseService()
        self.question_service = QuestionService()
        self.result_service = ResultService()
        self.chart_service = ChartService()
        self.insight_service = InsightService()
        self.ai_backend_service = AIBackendService()
    
    # Database operations
    def connect_database(
        self,
        db_type: str = "mysql",
        host: str = "localhost",
        port: Optional[int] = None,
        username: str = "",
        password: str = "",
        database: str = "",
        sqlite_path: str = "",
    ) -> Tuple[bool, str, Optional[Engine]]:
        """Connect to database."""
        return self.database_service.connect_database(
            db_type=db_type,
            host=host,
            port=port,
            username=username,
            password=password,
            database=database,
            sqlite_path=sqlite_path,
        )
    
    def connect_from_env(self) -> Tuple[bool, str, Optional[Engine]]:
        """Connect to database using environment variables."""
        return self.database_service.connect_from_env()
    
    def build_knowledge_base(
        self,
        use_ai_enrichment: bool = False,
        ai_backend: str = "local",
    ) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        """Build knowledge base."""
        return self.database_service.build_knowledge_base(
            use_ai_enrichment=use_ai_enrichment,
            ai_backend=ai_backend,
        )
    
    def load_knowledge_base(self) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        """Load knowledge base."""
        return self.database_service.load_knowledge_base()
    
    def load_business_glossary(self) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        """Load business glossary."""
        return self.database_service.load_business_glossary()
    
    def search_glossary(self, search_term: str) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        """Search business glossary."""
        return self.database_service.search_glossary(search_term)
    
    def is_database_connected(self) -> bool:
        """Check if database is connected."""
        return self.database_service.is_connected()
    
    def get_engine(self) -> Optional[Engine]:
        """Get database engine."""
        return self.database_service.get_engine()
    
    def get_knowledge_base(self) -> Optional[Dict[str, Any]]:
        """Get knowledge base."""
        return self.database_service.get_knowledge_base()
    
    def get_business_glossary(self) -> Optional[Dict[str, Any]]:
        """Get business glossary."""
        return self.database_service.get_business_glossary()

    def get_last_ai_enrichment_result(self) -> Tuple[str, str]:
        """Get last AI enrichment status and clean CLI message."""
        return self.database_service.get_last_ai_enrichment_result()

    def get_last_ai_enrichment_report(self) -> Tuple[list[str], dict[str, str]]:
        """Get enriched and fallback tables from the last AI enrichment run."""
        return self.database_service.get_last_ai_enrichment_report()

    def get_last_build_summary(self) -> Dict[str, Any]:
        """Get the latest knowledge-base build summary."""
        return self.database_service.get_last_build_summary()
    
    # Question processing
    def process_question(
        self,
        question: str,
        ai_backend: Optional[str] = None,
    ) -> Tuple[bool, str, Optional[str], Optional[str]]:
        """Process question and generate SQL."""
        knowledge_base = self.database_service.get_knowledge_base()
        if not knowledge_base:
            return False, "Knowledge base not loaded", None, None
        
        business_glossary = self.database_service.get_business_glossary()
        if business_glossary is None:
            success, _, business_glossary = self.database_service.load_business_glossary()
            if not success:
                business_glossary = None

        vector_retriever = self.database_service.get_vector_retriever()
        backend = ai_backend or self.ai_backend_service.get_active_backend()
        
        success, message, sql, error = self.question_service.process_question(
            question=question,
            knowledge_base=knowledge_base,
            business_glossary=business_glossary,
            vector_retriever=vector_retriever,
            ai_backend=backend,
        )
        
        # ── Bug fix: store generated SQL so Execute Last SQL (option 4) can find it
        # result_service.last_sql is what get_last_sql() reads — it must be set here,
        # not only after execution, so option 4 always has the most recent SQL.
        if success and sql:
            self.result_service.last_sql = sql
            self.result_service.set_last_question(question)
        
        return success, message, sql, error
    
    def detect_action(self, question: str) -> Optional[str]:
        """Detect conversation action."""
        return self.question_service.detect_action(question)
    
    def reset_conversation(self) -> None:
        """Reset conversation."""
        self.question_service.reset_conversation()
        self.result_service.reset()
        self.chart_service.reset()
        self.insight_service.reset()
    
    def get_conversation_memory(self):
        """Get conversation memory."""
        return self.question_service.get_conversation_memory()

    def get_last_query_context(self) -> Optional[Dict[str, Any]]:
        """Get the latest query-planning context for CLI display."""
        return self.question_service.get_last_query_context()
    
    # SQL execution
    def execute_sql(
        self,
        sql: str,
        revalidate: bool = True,
    ) -> Tuple[bool, str, Optional[List[Dict[str, Any]]]]:
        """Execute SQL query."""
        engine = self.database_service.get_engine()
        if not engine:
            return False, "Database not connected", None
        
        knowledge_base = self.database_service.get_knowledge_base()
        
        success, message, rows = self.result_service.execute_sql(
            sql=sql,
            engine=engine,
            knowledge_base=knowledge_base,
            revalidate=revalidate,
        )
        
        return success, message, rows
    
    def get_last_sql(self) -> Optional[str]:
        """Get last executed SQL."""
        return self.result_service.get_last_sql()
    
    def get_last_rows(self) -> Optional[List[Dict[str, Any]]]:
        """Get last query results."""
        return self.result_service.get_last_rows()
    
    def get_last_row_count(self) -> Optional[int]:
        """Get last query row count."""
        return self.result_service.get_last_row_count()
    
    # Chart generation
    def generate_chart(
        self,
        rows: Optional[List[Dict[str, Any]]] = None,
        chart_type: Optional[str] = None,
        output_path: Optional[str] = None,
    ) -> Tuple[bool, str, Optional[str], Optional[str]]:
        """Generate chart."""
        if rows is None:
            rows = self.result_service.get_last_rows()
        
        if not rows:
            return False, "No data to chart", None, None
        
        return self.chart_service.generate_chart(
            rows=rows,
            chart_type=chart_type,
            output_path=output_path,
        )
    
    def detect_chart_type(self, rows: Optional[List[Dict[str, Any]]] = None) -> Optional[str]:
        """Detect chart type."""
        if rows is None:
            rows = self.result_service.get_last_rows()
        return self.chart_service.detect_chart_type(rows)
    
    def get_last_chart_path(self) -> Optional[str]:
        """Get last chart path."""
        return self.chart_service.get_last_chart_path()
    
    # Insights generation
    def generate_insights(
        self,
        user_question: Optional[str] = None,
        sql: Optional[str] = None,
        rows: Optional[List[Dict[str, Any]]] = None,
        ai_backend: Optional[str] = None,
    ) -> Tuple[bool, str, Optional[List[str]]]:
        """Generate insights."""
        if user_question is None:
            user_question = self.result_service.get_last_question()
        if sql is None:
            sql = self.result_service.get_last_sql()
        if rows is None:
            rows = self.result_service.get_last_rows()
        
        if not rows:
            return False, "No data to analyze", None
        
        knowledge_base = self.database_service.get_knowledge_base()
        backend = ai_backend or self.ai_backend_service.get_active_backend()
        
        return self.insight_service.generate_insights(
            user_question=user_question or "",
            sql=sql or "",
            rows=rows,
            knowledge_base=knowledge_base,
            ai_backend=backend,
        )
    
    def get_last_insights(self) -> Optional[List[str]]:
        """Get last insights."""
        return self.insight_service.get_last_insights()
    
    # AI backend management
    def set_local_backend(self, model: str, api_url: str) -> None:
        """Set local LLM backend."""
        self.ai_backend_service.set_local_backend(model, api_url)
    
    def set_nvidia_backend(self, model: str, api_key: str, base_url: str = "") -> None:
        """Set NVIDIA backend."""
        self.ai_backend_service.set_nvidia_backend(model, api_key, base_url)
    
    def set_custom_backend(
        self,
        api_url: str,
        model: str = "",
        auth_header: str = "",
        auth_token: str = "",
    ) -> None:
        """Set custom AI backend."""
        self.ai_backend_service.set_custom_backend(
            api_url=api_url,
            model=model,
            auth_header=auth_header,
            auth_token=auth_token,
        )
    
    def get_active_backend(self) -> str:
        """Get active backend."""
        return self.ai_backend_service.get_active_backend()
    
    def get_backend_config(self) -> dict:
        """Get backend configuration."""
        return self.ai_backend_service.get_backend_config()
    
    def test_backend_connection(self) -> Tuple[bool, str]:
        """Test backend connection."""
        return self.ai_backend_service.test_backend_connection()
    
    # Full workflow: connect + build KB + load glossary
    def initialize_database(
        self,
        db_type: str = "mysql",
        host: str = "localhost",
        port: Optional[int] = None,
        username: str = "",
        password: str = "",
        database: str = "",
        sqlite_path: str = "",
        use_ai_enrichment: bool = False,
    ) -> Tuple[bool, str]:
        """
        Initialize database: connect, build KB, load glossary.
        
        Returns:
            (success, message)
        """
        # Connect to database
        success, message, engine = self.connect_database(
            db_type=db_type,
            host=host,
            port=port,
            username=username,
            password=password,
            database=database,
            sqlite_path=sqlite_path,
        )
        if not success:
            return False, f"Database connection failed: {message}"
        
        # Build knowledge base
        success, message, kb = self.build_knowledge_base(
            use_ai_enrichment=use_ai_enrichment,
            ai_backend=self.get_active_backend(),
        )
        if not success:
            return False, f"Knowledge base build failed: {message}"
        
        # Load business glossary
        success, message, glossary = self.load_business_glossary()
        if not success:
            logger.warning(f"Failed to load business glossary: {message}")
        
        # Reset conversation
        self.reset_conversation()
        
        return True, "Database initialized successfully"
    
    # Full workflow: ask question + execute + optional chart/insights
    def ask_and_execute(
        self,
        question: str,
        generate_chart: bool = False,
        generate_insights: bool = False,
        ai_backend: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Ask question, execute SQL, optionally generate chart/insights.
        
        Returns:
            Dictionary with all results
        """
        result = {
            "success": False,
            "question": question,
            "sql": None,
            "rows": None,
            "row_count": None,
            "chart": None,
            "insights": None,
            "error": None,
        }
        
        # Process question
        success, message, sql, error = self.process_question(question, ai_backend)
        if not success:
            result["error"] = error or message
            return result
        
        result["sql"] = sql
        self.result_service.set_last_question(question)
        
        # Execute SQL
        success, message, rows = self.execute_sql(sql)
        if not success:
            result["error"] = message
            return result
        
        result["rows"] = rows
        result["row_count"] = len(rows) if rows else 0
        result["success"] = True
        
        # Generate chart if requested
        if generate_chart:
            success, message, chart_path, chart_type = self.generate_chart(rows)
            if success:
                result["chart"] = {
                    "chart_type": chart_type,
                    "chart_path": chart_path,
                }
            else:
                result["chart"] = {"error": message}
        
        # Generate insights if requested
        if generate_insights:
            success, message, insights = self.generate_insights(question, sql, rows, ai_backend)
            if success:
                result["insights"] = insights
            else:
                result["insights"] = [f"Insights generation failed: {message}"]
        
        return result
