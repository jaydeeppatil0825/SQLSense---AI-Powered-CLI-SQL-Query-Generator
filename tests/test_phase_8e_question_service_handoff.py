from copy import deepcopy
from unittest.mock import MagicMock

import pytest

from core.app_service import AppService
from sql_pipeline.query_executor import execute_query
from sql_pipeline.question_service import QuestionService
from sql_pipeline.sql_validator import validate_sql_structure
from tests.test_phase_8c_joined_aggregate_generator import _context, _kb, _sql


QUESTION = "total payment amount by customer city"


def _pipeline_context(question, context):
    return {
        "normalized_question": question,
        "query_context": deepcopy(context),
        "plan": {},
        "retrieved_context": {},
        "route_recommendation": context["route_recommendation"],
        "complex_sql_plan": {},
        "formula_evidence": [],
        "evidence_sources": [],
    }


def _app(context=None):
    service = AppService()
    kb = _kb()
    context = deepcopy(context or _context())
    service.database_service.knowledge_base = kb
    service.database_ready = True
    service.query_pipeline.run = lambda **_: _pipeline_context(QUESTION, context)
    return service, kb, context


def test_phase8_context_reaches_validator(monkeypatch):
    import sql_pipeline.question_service as question_service

    context = _context()
    seen = []
    real_validate = question_service.validate_sql_structure

    def validate_spy(sql, knowledge_base, selected_join_path=None, query_context=None):
        seen.append((selected_join_path, query_context))
        return real_validate(
            sql,
            knowledge_base,
            selected_join_path=selected_join_path,
            query_context=query_context,
        )

    monkeypatch.setattr(question_service, "validate_sql_structure", validate_spy)

    service = QuestionService()
    success, message, sql, error = service.process_question(
        QUESTION,
        _kb(),
        pipeline_context=_pipeline_context(QUESTION, context),
    )

    assert success is True
    assert message == "SQL generated successfully (deterministic)"
    assert error is None
    assert sql
    assert seen[0][0] == context["selected_join_path"]
    assert seen[0][1]["selected_join_path"] == context["selected_join_path"]
    assert seen[0][1]["phase8a_grain_analysis"]["grain_preserved"] is True


def test_app_service_stores_validated_sql_with_same_context():
    service, _, context = _app()

    result = service.process_question(QUESTION, ai_backend="local")

    stored_context = service.result_service.get_last_query_context()
    assert result["success"] is True
    assert result["validation_result"]["is_valid"] is True
    assert service.get_last_sql() == result["generated_sql"]
    assert service.result_service.get_last_selected_join_path() == context["selected_join_path"]
    assert stored_context["selected_join_path"] == context["selected_join_path"]
    assert stored_context["phase8a_grain_analysis"]["grain_preserved"] is True


def test_missing_phase8a_evidence_fails_closed_and_does_not_store():
    context = _context()
    context.pop("phase8a_grain_analysis")
    service, _, _ = _app(context)

    result = service.process_question(QUESTION, ai_backend="local")

    assert result["success"] is False
    assert service.get_last_sql() is None
    assert service.result_service.get_last_query_context() is None


def test_validation_failure_does_not_store_executable_sql(monkeypatch):
    import sql_pipeline.question_service as question_service

    monkeypatch.setattr(
        question_service,
        "validate_sql_structure",
        lambda *args, **kwargs: (False, "forced validation failure"),
    )
    service, _, _ = _app()

    result = service.process_question(QUESTION, ai_backend="local")

    assert result["success"] is False
    assert service.get_last_sql() is None
    assert service.result_service.get_last_query_context() is None


def test_manual_two_edge_aggregate_without_planner_context_remains_rejected():
    context = _context()
    ok, reason = validate_sql_structure(
        _sql(context),
        _kb(),
        selected_join_path=context["selected_join_path"],
    )

    assert ok is False
    assert "planner" in reason.lower() or "context" in reason.lower()


def test_executor_still_blocks_phase8_without_context_before_connect():
    context = _context()
    engine = MagicMock()

    with pytest.raises(ValueError, match="structure invalid"):
        execute_query(
            _sql(context),
            engine,
            knowledge_base=_kb(),
            selected_join_path=context["selected_join_path"],
        )

    engine.connect.assert_not_called()
