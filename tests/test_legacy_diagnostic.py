import pytest


LEGACY_MODULES = {
    "test_erp_queries.py": "old ERP AI-first routing and 4-tuple QuestionService contract",
    "test_hybrid_routing.py": "old hybrid AI-first routing and wrapper-path expectations",
    "test_local_ai_backend.py": "old ai.sql_generator Ollama wrapper path",
    "test_phase11_basic_nl.py": "old Phase 11 runtime-AI/basic-NL generator",
    "test_prompt_builder.py": "old ai.prompt_builder contract",
    "test_properties.py": "mixed properties tied to removed ai.* runtime modules",
    "test_simple_query_generator.py": "old ai.simple_query_generator path",
    "test_sql_generator.py": "old ai.sql_generator retry contract",
}


@pytest.mark.legacy
@pytest.mark.parametrize(("module", "reason"), sorted(LEGACY_MODULES.items()))
def test_legacy_module_is_isolated(module, reason):
    pytest.skip(f"{module}: {reason}")
