"""
core/app_service.py
===================
Application service as main CLI orchestrator.

This service coordinates the lower-level services behind the CLI.
"""

from typing import Optional, Dict, Any, List, Tuple
from sqlalchemy.engine import Engine

from kb_pipeline.database_service import DatabaseService
from query_pipeline.query_pipeline import QueryPipeline
from sql_pipeline.question_service import QuestionService
from sql_pipeline.result_service import ResultService
from core.chart_service import ChartService
from core.insight_service import InsightService
from core.ai_backend_service import get_ai_backend_service
from utils.logger import get_logger

logger = get_logger()

_RULE_BASED_ROUTE_ALIASES = {
    "rule-based",
    "rule_based",
    "simple_rule_based",
    "simple-rule-based",
}


def _safe_dict(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _normalize_route_alias(route: Any) -> str:
    value = str(route or "").strip()
    if value in _RULE_BASED_ROUTE_ALIASES:
        return "rule-based"
    return value


class AppService:
    """Main application service orchestrator."""
    
    def __init__(self):
        self.database_service = DatabaseService()
        self.question_service = QuestionService()
        self.query_pipeline = QueryPipeline(self.question_service)
        self.last_pipeline_result = None
        self.result_service = ResultService()
        self.chart_service = ChartService()
        self.insight_service = InsightService()
        self.ai_backend_service = get_ai_backend_service()
        self.database_ready: bool = False
        self.last_prepare_report: Dict[str, Any] = {}

    def _reset_runtime_state(self) -> None:
        """Clear question/execution state so a new database never reuses stale runtime context."""
        self.last_pipeline_result = None
        self.question_service.reset_conversation()
        self.result_service.reset()
        self.chart_service.reset()
        self.insight_service.reset()
        self.database_ready = False
        self.last_prepare_report = {}
    
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
        force_rebuild: bool = False,
    ) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        """Build knowledge base."""
        success, message, knowledge_base = self.database_service.build_knowledge_base(
            use_ai_enrichment=use_ai_enrichment,
            ai_backend=ai_backend,
            force_rebuild=force_rebuild,
        )
        self.database_ready = bool(success and knowledge_base)
        return success, message, knowledge_base

    def connect_database_and_prepare(
        self,
        db_type: str = "mysql",
        host: str = "localhost",
        port: Optional[int] = None,
        username: str = "",
        password: str = "",
        database: str = "",
        sqlite_path: str = "",
        use_ai_enrichment: bool = True,
        ai_backend: Optional[str] = None,
    ) -> Tuple[bool, str, Dict[str, Any]]:
        """
        Connect to the selected database and prepare KB/glossary/vector state in one flow.

        Returns:
            (success, message, report)
        """
        self._reset_runtime_state()
        backend = ai_backend or self.ai_backend_service.get_active_backend()

        report: Dict[str, Any] = {
            "connected": False,
            "kb_built": False,
            "glossary_generated": False,
            "vector_status": "not_built",
            "vector_warning": "",
            "ai_enrichment_status": "skipped",
            "ai_enrichment_message": "AI enrichment skipped.",
            "database_ready": False,
        }

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
            self.last_prepare_report = report
            return False, message, report

        report["connected"] = engine is not None

        success, message, knowledge_base = self.build_knowledge_base(
            use_ai_enrichment=use_ai_enrichment,
            ai_backend=backend,
            force_rebuild=True,
        )
        report["kb_built"] = bool(success and knowledge_base)
        ai_status, ai_message = self.get_last_ai_enrichment_result()
        report["ai_enrichment_status"] = ai_status
        report["ai_enrichment_message"] = ai_message

        if not success or not knowledge_base:
            self.database_ready = False
            report["database_ready"] = False
            self.last_prepare_report = report
            return False, message, report

        glossary = self.database_service.get_business_glossary()
        report["glossary_generated"] = isinstance(glossary, dict)

        vector_status = self.get_vector_status()
        report["vector_status"] = str(vector_status.get("index_status") or "not_built")
        persistence = vector_status.get("persistence", {})
        if report["vector_status"] != "ready":
            reason = str(persistence.get("persistence_error") or persistence.get("stale_reason") or "vector index unavailable")
            report["vector_warning"] = reason

        self.database_ready = True
        report["database_ready"] = True
        self.last_prepare_report = report
        return True, "Database connected and knowledge assets prepared successfully", report

    def rebuild_or_refresh_knowledge_base(
        self,
        use_ai_enrichment: bool = True,
        ai_backend: Optional[str] = None,
    ) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        """Force a fresh KB/glossary/vector rebuild for the active database."""
        backend = ai_backend or self.ai_backend_service.get_active_backend()
        self.database_ready = False
        success, message, knowledge_base = self.build_knowledge_base(
            use_ai_enrichment=use_ai_enrichment,
            ai_backend=backend,
            force_rebuild=True,
        )
        self.database_ready = bool(success and knowledge_base)
        return success, message, knowledge_base
    
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

    def get_vector_status(self) -> Dict[str, Any]:
        """Get vector/embedding status for CLI reporting."""
        return self.database_service.get_vector_status()

    def _build_question_result(
        self,
        *,
        question: str,
        success: bool,
        message: str = "",
        generated_sql: Optional[str] = None,
        error: Optional[str] = None,
        route: str = "",
        validation_result: Optional[Dict[str, Any]] = None,
        query_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        normalized_route = _normalize_route_alias(route)
        return {
            "success": bool(success),
            "question": question,
            "message": message,
            "generated_sql": generated_sql,
            "sql": generated_sql,
            "route": normalized_route,
            "route_used": normalized_route,
            "validation_result": _safe_dict(validation_result),
            "query_context": _safe_dict(query_context),
            "error": error,
        }
    
    # Question processing
    def _process_question_payload(
        self,
        question: str,
        ai_backend: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Process a question and return the standardized CLI result payload."""
        if not self.database_ready:
            return self._build_question_result(
                question=question,
                success=False,
                message="Database is not ready",
                error="Database is not ready. Connect a database and let SQLSense prepare the knowledge base first.",
                route="cannot_plan_safely",
            )
        knowledge_base = self.database_service.get_knowledge_base()
        if not knowledge_base:
            return self._build_question_result(
                question=question,
                success=False,
                message="Knowledge base not loaded",
                error="Knowledge base not loaded",
                route="cannot_plan_safely",
            )
        
        business_glossary = self.database_service.get_business_glossary()
        if business_glossary is None:
            success, _, business_glossary = self.database_service.load_business_glossary()
            if not success:
                business_glossary = None

        vector_retriever = self.database_service.get_vector_retriever()
        backend = ai_backend or self.ai_backend_service.get_active_backend()
        
        pipeline_result = self.query_pipeline.run(
            question=question,
            knowledge_base=knowledge_base,
            business_glossary=business_glossary,
            vector_retriever=vector_retriever,
            ai_backend=backend,
        )
        self.last_pipeline_result = pipeline_result

        query_context: Dict[str, Any] = {}
        route = ""
        validation_result: Dict[str, Any] = {}
        pipeline_context: Optional[Dict[str, Any]] = None
        pipeline_error: Optional[str] = None

        if pipeline_result is None:
            pipeline_error = "Internal error: query pipeline returned no result."
            route = "cannot_plan_safely"
        elif isinstance(pipeline_result, dict):
            query_context = _safe_dict(pipeline_result.get("query_context"))
            route = str(
                pipeline_result.get("route")
                or pipeline_result.get("route_used")
                or pipeline_result.get("route_recommendation")
                or ""
            )
            pipeline_context = {
                "question": question,
                "normalized_question": str(pipeline_result.get("normalized_question") or question).strip(),
                "intent": _safe_dict(pipeline_result.get("intent")),
                "retrieved_context": _safe_dict(pipeline_result.get("retrieved_context")),
                "query_context": query_context,
                "plan": _safe_dict(pipeline_result.get("plan")),
                "route_recommendation": str(pipeline_result.get("route_recommendation") or route).strip(),
                "complex_sql_plan": _safe_dict(pipeline_result.get("complex_sql_plan")),
                "formula_evidence": list(pipeline_result.get("formula_evidence") or []),
                "evidence_sources": list(pipeline_result.get("evidence_sources") or []),
            }
        elif hasattr(pipeline_result, "to_pipeline_context"):
            payload = pipeline_result.to_dict() if hasattr(pipeline_result, "to_dict") else {}
            query_context = _safe_dict(payload.get("query_context"))
            route = str(
                payload.get("route")
                or payload.get("route_used")
                or payload.get("route_recommendation")
                or ""
            )
            pipeline_context = pipeline_result.to_pipeline_context()
        elif hasattr(pipeline_result, "to_dict"):
            payload = pipeline_result.to_dict()
            if isinstance(payload, dict):
                query_context = _safe_dict(payload.get("query_context"))
                route = str(
                    payload.get("route")
                    or payload.get("route_used")
                    or payload.get("route_recommendation")
                    or ""
                )
                pipeline_context = {
                    "question": question,
                    "normalized_question": str(payload.get("normalized_question") or question).strip(),
                    "intent": _safe_dict(payload.get("intent")),
                    "retrieved_context": _safe_dict(payload.get("retrieved_context")),
                    "query_context": query_context,
                    "plan": _safe_dict(payload.get("plan")),
                    "route_recommendation": str(payload.get("route_recommendation") or route).strip(),
                    "complex_sql_plan": _safe_dict(payload.get("complex_sql_plan")),
                    "formula_evidence": list(payload.get("formula_evidence") or []),
                    "evidence_sources": list(payload.get("evidence_sources") or []),
                }
            else:
                pipeline_error = "Internal error: query pipeline returned an invalid structured result."
                route = "cannot_plan_safely"
        else:
            pipeline_error = "Internal error: query pipeline returned an unsupported result type."
            route = "cannot_plan_safely"

        if pipeline_error:
            return self._build_question_result(
                question=question,
                success=False,
                message="Question processing returned no result",
                error=pipeline_error,
                route=route,
                validation_result=validation_result,
                query_context=query_context,
            )

        success, message, generated_sql, error = self.question_service.process_question(
            question=question,
            knowledge_base=knowledge_base,
            business_glossary=business_glossary,
            vector_retriever=vector_retriever,
            ai_backend=backend,
            pipeline_context=pipeline_context,
        )
        query_context = _safe_dict(self.question_service.get_last_query_context() or query_context)
        route = route or str(query_context.get("route_used") or query_context.get("route") or "")
        if generated_sql:
            is_valid, reason = self.question_service.validate_sql(generated_sql, query_context.get("selected_knowledge_base") or knowledge_base)
            validation_result = {"is_valid": is_valid, "reason": reason}
        elif error or message:
            validation_result = {"is_valid": False, "reason": error or message}

        if success and generated_sql:
            self.result_service.last_sql = generated_sql
            self.result_service.set_last_question(question)

        return self._build_question_result(
            question=question,
            success=success,
            message=message,
            generated_sql=generated_sql,
            error=error,
            route=route,
            validation_result=validation_result,
            query_context=query_context,
        )

    def process_question(
        self,
        question: str,
        ai_backend: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Process question and return a standardized result dictionary."""
        return self._process_question_payload(question, ai_backend)
        
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

    def get_last_pipeline_result(self):
        """Get the latest structured query-pipeline result."""
        return self.last_pipeline_result

    def is_database_ready(self) -> bool:
        """Return whether the active database is connected and its KB assets are ready."""
        return bool(self.database_ready and self.database_service.get_knowledge_base())

    def get_last_prepare_report(self) -> Dict[str, Any]:
        """Return the latest database connect/prepare workflow report for CLI status messages."""
        return dict(self.last_prepare_report)
    
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
            force_rebuild=True,
        )
        if not success:
            return False, f"Knowledge base build failed: {message}"
        
        # Load business glossary
        success, message, glossary = self.load_business_glossary()
        if not success:
            logger.warning(f"Failed to load business glossary: {message}")
        
        # Reset conversation
        self.reset_conversation()
        self.database_ready = True
        
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
        question_result = self.process_question(question, ai_backend)
        if not isinstance(question_result, dict):
            result["error"] = "Internal error: question processing returned an invalid result."
            return result
        if not question_result.get("success"):
            result["error"] = question_result.get("error") or question_result.get("message")
            return result

        sql = question_result.get("sql") or question_result.get("generated_sql")
        if not sql:
            result["error"] = "Internal error: SQL generation succeeded without a SQL result."
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
