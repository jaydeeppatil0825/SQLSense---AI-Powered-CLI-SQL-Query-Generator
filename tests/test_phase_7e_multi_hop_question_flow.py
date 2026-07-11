from sql_pipeline.deterministic_sql_generator import DeterministicSqlResult
from sql_pipeline.question_service import QuestionService

from tests.test_phase_7b_planner_multi_hop import _context, _kb


def _pipeline_context(question, context):
    return {
        "normalized_question": question,
        "query_context": context,
        "plan": context["plan"],
        "retrieved_context": context["retrieved_context"],
        "route_recommendation": context["route_recommendation"],
        "clause_plan": context["clause_plan"],
        "complex_sql_plan": context["complex_sql_plan"],
        "formula_evidence": [],
        "evidence_sources": context["evidence_sources"],
    }


def test_resolved_two_edge_lookup_returns_validated_sql_and_memory(monkeypatch):
    question = "show orders with region details"
    kb = _kb()
    context = _context(question, kb)
    seen_paths = []

    import sql_pipeline.question_service as question_service

    real_validate = question_service.validate_sql_structure

    def validate_spy(sql, knowledge_base, selected_join_path=None):
        seen_paths.append(selected_join_path)
        return real_validate(sql, knowledge_base, selected_join_path=selected_join_path)

    monkeypatch.setattr(question_service, "validate_sql_structure", validate_spy)

    success, message, sql, error = QuestionService().process_question(
        question,
        kb,
        pipeline_context=_pipeline_context(question, context),
    )

    assert success is True
    assert error is None
    assert message == "SQL generated successfully (deterministic)"
    assert context["route_recommendation"] == "deterministic_sql_required"
    assert "INNER JOIN customers ON orders.customer_id = customers.customer_id" in sql
    assert "INNER JOIN regions ON customers.region_id = regions.region_id" in sql
    assert seen_paths == [context["selected_join_path"]]

    service = QuestionService()
    success, _, sql, _ = service.process_question(
        question,
        kb,
        pipeline_context=_pipeline_context(question, context),
    )
    assert success is True
    assert service.get_conversation_memory().get_last_context()["last_generated_sql"] == sql


def test_invalid_generated_multi_hop_sql_fails_validation(monkeypatch):
    question = "show orders with region details"
    kb = _kb()
    context = _context(question, kb)

    import sql_pipeline.question_service as question_service

    monkeypatch.setattr(
        question_service,
        "generate_deterministic_sql",
        lambda **_: DeterministicSqlResult(
            status="generated",
            sql=(
                "SELECT orders.order_id AS orders__order_id FROM orders "
                "INNER JOIN customers ON orders.order_id = customers.customer_id "
                "INNER JOIN regions ON customers.region_id = regions.region_id LIMIT 50;"
            ),
            reason="test invalid sql",
        ),
    )

    success, message, sql, error = QuestionService().process_question(
        question,
        kb,
        pipeline_context=_pipeline_context(question, context),
    )

    assert success is False
    assert "SQL validation failed" in message
    assert sql is None
    assert error is None


def test_ambiguous_and_missing_paths_fail_closed():
    ambiguous = _context("show orders with region details", _kb(alternate_path=True))
    missing_kb = _kb()
    missing_kb["customers"]["foreign_keys"] = []
    missing = _context("show orders with region details", missing_kb)

    assert ambiguous["route_recommendation"] == "cannot_plan_safely"
    assert ambiguous["selected_join_path"] is None
    assert missing["route_recommendation"] == "cannot_plan_safely"
    assert missing["selected_join_path"] is None


def test_direct_lookup_and_multi_hop_aggregate_behavior_are_preserved():
    direct = _context("show orders with region details", _kb(direct_region=True))
    aggregate = _context("show total order amount by region name", _kb())

    assert direct["route_recommendation"] == "deterministic_sql_required"
    assert len(direct["selected_join_path"]["edges"]) == 1
    assert aggregate["route_recommendation"] == "cannot_plan_safely"
    assert aggregate.get("selected_join_path") is None
