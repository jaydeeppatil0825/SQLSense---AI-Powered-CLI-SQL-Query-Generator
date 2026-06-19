from core.query_pipeline import QueryPipeline
from core.question_service import QuestionService


KNOWLEDGE_BASE = {
    "accounts": {
        "columns": [
            {"name": "account_id", "type": "INTEGER", "semantic_type": "id"},
            {"name": "account_label", "type": "VARCHAR(100)", "semantic_type": "name"},
        ],
        "primary_keys": ["account_id"],
        "foreign_keys": [],
        "relationships": [],
    }
}


def test_query_pipeline_returns_structured_debug_fields(monkeypatch):
    question_service = QuestionService()
    pipeline = QueryPipeline(question_service)
    built_intent = {
        "user_goal": "show accounts",
        "intent_type": "list",
        "business_operation": "browse",
        "requested_metrics": [],
        "requested_dimensions": ["accounts"],
        "requested_filters": [],
        "requested_sort": {},
        "limit": None,
        "needs_grouping": False,
        "needs_aggregation": False,
        "needs_join": False,
        "raw_business_terms": ["accounts"],
        "confidence": 0.71,
        "source": "fallback",
    }

    preview_context = {
        "plan": {
            "question": "show all accounts",
            "intent": "list",
            "question_terms": ["show", "all", "accounts"],
        },
        "selected_table_names": ["accounts"],
        "selected_columns": [
            {"table": "accounts", "column": "account_label", "confidence": 0.91},
        ],
        "selected_tables": [
            {"table": "accounts", "confidence": 0.91},
        ],
        "selected_knowledge_base": KNOWLEDGE_BASE,
        "join_paths": [],
        "vector_results": {
            "tables": [{"table_name": "accounts"}],
            "columns": [{"table_name": "accounts", "column_name": "account_label"}],
            "relationships": [],
            "glossary_terms": [],
        },
        "route_used": "rule-based",
    }

    monkeypatch.setattr("core.query_pipeline.build_intent", lambda *args, **kwargs: built_intent)
    monkeypatch.setattr("core.query_pipeline.build_query_context", lambda *args, **kwargs: preview_context)

    def fake_process_question(**kwargs):
        question_service.last_query_context = preview_context
        return True, "SQL generated successfully (rule-based)", "SELECT account_id, account_label FROM accounts LIMIT 50;", None

    monkeypatch.setattr(question_service, "process_question", fake_process_question)

    result = pipeline.run(
        question="  show   all accounts ",
        knowledge_base=KNOWLEDGE_BASE,
        business_glossary={},
        vector_retriever=None,
        ai_backend="local",
    )

    assert result.success is True
    assert result.normalized_question == "show all accounts"
    assert result.intent == built_intent
    assert result.retrieved_context["selected_table_names"] == ["accounts"]
    assert result.plan["intent"] == "list"
    assert result.generated_sql == "SELECT account_id, account_label FROM accounts LIMIT 50;"
    assert result.validation_result == {"is_valid": True, "reason": "SQL is valid"}
    assert result.route == "rule-based"


def test_query_pipeline_reports_validation_failure_when_sql_generation_fails(monkeypatch):
    question_service = QuestionService()
    pipeline = QueryPipeline(question_service)
    built_intent = {
        "user_goal": "show accounts",
        "intent_type": "list",
        "business_operation": "browse",
        "requested_metrics": [],
        "requested_dimensions": ["accounts"],
        "requested_filters": [],
        "requested_sort": {},
        "limit": None,
        "needs_grouping": False,
        "needs_aggregation": False,
        "needs_join": False,
        "raw_business_terms": ["accounts"],
        "confidence": 0.55,
        "source": "fallback",
    }

    failed_context = {
        "plan": {
            "question": "show all accounts",
            "intent": "list",
            "question_terms": ["show", "all", "accounts"],
        },
        "selected_table_names": ["accounts"],
        "selected_columns": [],
        "selected_tables": [{"table": "accounts", "confidence": 0.52}],
        "join_paths": [],
        "vector_results": {},
        "route_used": "fallback-failed",
    }

    monkeypatch.setattr("core.query_pipeline.build_intent", lambda *args, **kwargs: built_intent)
    monkeypatch.setattr("core.query_pipeline.build_query_context", lambda *args, **kwargs: failed_context)

    def fake_process_question(**kwargs):
        question_service.last_query_context = failed_context
        return False, "Could not generate a valid SQL query for this question.", None, "validation failed"

    monkeypatch.setattr(question_service, "process_question", fake_process_question)

    result = pipeline.run(
        question="show all accounts",
        knowledge_base=KNOWLEDGE_BASE,
        business_glossary={},
        vector_retriever=None,
        ai_backend="local",
    )

    assert result.success is False
    assert result.generated_sql is None
    assert result.validation_result == {"is_valid": False, "reason": "validation failed"}
    assert result.route == "fallback-failed"
