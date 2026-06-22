import importlib
from pathlib import Path

from core.query_pipeline import QueryPipeline
from core.question_service import QuestionService
from query_pipeline.query_planner import build_query_context


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
    retrieved_context = {
        "matched_tables": [{"table": "accounts", "score": 1.0, "source": "kb_identifier"}],
        "matched_columns": [{"table": "accounts", "column": "account_label", "score": 0.9, "source": "kb_identifier"}],
        "matched_glossary_terms": [],
        "matched_relationships": [],
        "possible_join_paths": [],
        "measure_candidates": [],
        "dimension_candidates": [{"table": "accounts", "column": "account_label", "score": 0.9, "source": "kb_identifier"}],
        "filter_candidates": [],
        "retrieval_sources": ["kb_identifier"],
        "confidence": 0.95,
    }

    preview_context = {
        "plan": {
            "question": "show all accounts",
            "intent": "list",
            "question_terms": ["show", "all", "accounts"],
        },
        "route_recommendation": "simple_rule_ok",
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
    monkeypatch.setattr("core.query_pipeline.retrieve_context", lambda *args, **kwargs: retrieved_context)
    monkeypatch.setattr("core.query_pipeline.build_query_context", lambda *args, **kwargs: preview_context)
    captured = {}

    def fake_process_question(**kwargs):
        captured["pipeline_context"] = kwargs.get("pipeline_context")
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
    assert result.retrieved_context == retrieved_context
    assert result.plan["intent"] == "list"
    assert result.generated_sql == "SELECT account_id, account_label FROM accounts LIMIT 50;"
    assert result.validation_result == {"is_valid": True, "reason": "SQL is valid"}
    assert result.route == "rule-based"
    debug_payload = result.to_dict()
    assert debug_payload["formula_evidence"] == []
    assert debug_payload["evidence_sources"] == ["kb_identifier"]
    assert "rows" not in debug_payload
    assert "executed_sql" not in debug_payload
    assert "executed_rows" not in debug_payload
    assert captured["pipeline_context"]["intent"] == built_intent
    assert captured["pipeline_context"]["retrieved_context"] == retrieved_context
    assert captured["pipeline_context"]["plan"]["intent"] == "list"
    assert captured["pipeline_context"]["route_recommendation"] == "simple_rule_ok"
    assert captured["pipeline_context"]["formula_evidence"] == []
    assert captured["pipeline_context"]["evidence_sources"] == ["kb_identifier"]


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
    retrieved_context = {
        "matched_tables": [{"table": "accounts", "score": 0.52, "source": "kb_identifier"}],
        "matched_columns": [],
        "matched_glossary_terms": [],
        "matched_relationships": [],
        "possible_join_paths": [],
        "measure_candidates": [],
        "dimension_candidates": [],
        "filter_candidates": [],
        "retrieval_sources": ["kb_identifier"],
        "confidence": 0.52,
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
    monkeypatch.setattr("core.query_pipeline.retrieve_context", lambda *args, **kwargs: retrieved_context)
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


def test_pipeline_architecture_document_exists():
    assert Path("PIPELINE_ARCHITECTURE.md").exists()


def test_primary_pipeline_modules_import_from_new_paths():
    kb_database_service = importlib.import_module("kb_pipeline.database_service")
    kb_relationship_graph = importlib.import_module("kb_pipeline.relationship_graph")
    kb_embedding_service = importlib.import_module("kb_pipeline.vector.embedding_service")
    query_pipeline_module = importlib.import_module("query_pipeline.query_pipeline")
    query_planner_module = importlib.import_module("query_pipeline.query_planner")
    conversation_memory_module = importlib.import_module("query_pipeline.conversation.conversation_memory")
    sql_question_service = importlib.import_module("sql_pipeline.question_service")
    sql_generator_module = importlib.import_module("sql_pipeline.sql_generator")
    sql_validator_module = importlib.import_module("sql_pipeline.sql_validator")

    assert hasattr(kb_database_service, "DatabaseService")
    assert hasattr(kb_relationship_graph, "build_relationship_graph")
    assert hasattr(kb_embedding_service, "EmbeddingService")
    assert hasattr(query_pipeline_module, "QueryPipeline")
    assert hasattr(query_planner_module, "build_query_context")
    assert hasattr(conversation_memory_module, "ConversationMemory")
    assert hasattr(sql_question_service, "QuestionService")
    assert hasattr(sql_generator_module, "generate_sql")
    assert hasattr(sql_validator_module, "validate_sql")


def test_query_planner_does_not_build_vector_index_when_retriever_is_missing():
    knowledge_base = {
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

    context = build_query_context(
        "show all accounts",
        knowledge_base,
        business_glossary={},
        use_vector_retrieval=True,
        vector_retriever=None,
    )

    assert context["vector_used"] is False
    assert context["vector_results"]["used_vector"] is False
    assert context["vector_results"]["error"] == "vector retriever unavailable"


def test_sql_generation_modules_do_not_build_vector_index_directly():
    sql_runtime_files = [
        Path("sql_pipeline/sql_generator.py"),
        Path("sql_pipeline/simple_query_generator.py"),
        Path("sql_pipeline/prompt_builder.py"),
        Path("sql_pipeline/query_executor.py"),
    ]
    forbidden_tokens = [
        "VectorIndexBuilder",
        "VectorIndexPersistence",
        "EmbeddingService(",
    ]

    for path in sql_runtime_files:
        text = path.read_text(encoding="utf-8")
        assert "kb_pipeline.vector" not in text, f"{path} should not import KB vector internals directly"
        for token in forbidden_tokens:
            assert token not in text, f"{path} should not construct vector infrastructure via {token}"
