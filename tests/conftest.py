from __future__ import annotations

from pathlib import Path


REGRESSION_FILES = {
    "test_agp_role_scoring.py",
    "test_filter_resolution_regressions.py",
    "test_full_small_lab_regressions.py",
    "test_phase_1_to_8_live_regressions.py",
    "test_phase_1e_metric_ambiguity.py",
    "test_phase_3_grouped_having.py",
    "test_phase_4_ranking.py",
    "test_phase_5_join_lookup.py",
    "test_phase_6_joined_aggregate.py",
    "test_phase_6g_interval_logic.py",
    "test_phase_7a_bfs_join_resolver.py",
    "test_phase_7b_planner_multi_hop.py",
    "test_phase_7c_multi_hop_generator.py",
    "test_phase_7d_multi_hop_validator.py",
    "test_phase_7e_multi_hop_question_flow.py",
    "test_phase_7f_multi_hop_execution.py",
    "test_phase_7g_join_lookup_nlp.py",
    "test_phase_8a_grain_analyzer.py",
    "test_phase_8b_joined_aggregate_planner.py",
    "test_phase_8c_joined_aggregate_generator.py",
    "test_phase_8d_joined_aggregate_validator.py",
    "test_phase_8e_question_service_handoff.py",
    "test_phase_8f_joined_aggregate_execution.py",
    "test_phase_9a_cache_service.py",
    "test_phase_9b_path_grain_cache.py",
    "test_phase_9c_retrieval_planner_cache.py",
    "test_smoke_regression_hardening.py",
}

INTEGRATION_FILES = {
    "test_api_gateway_contract.py",
    "test_chroma_store.py",
    "test_cli_core_flow.py",
    "test_database_service.py",
    "test_full_small_lab_regressions.py",
    "test_query_pipeline.py",
    "test_vector_persistence.py",
    "test_vector_store.py",
}

LIVE_FILES = {
    "test_phase_1_to_8_live_regressions.py",
}

MYSQL_FILES = {
    "test_connection.py",
    "test_database_service.py",
    "test_full_small_lab_regressions.py",
    "test_phase_1_to_8_live_regressions.py",
}

FRONTEND_CONTRACT_FILES = {
    "test_api_gateway_contract.py",
}


def pytest_collection_modifyitems(config, items):
    del config
    for item in items:
        filename = Path(str(item.fspath)).name
        item.add_marker("unit")
        if filename in REGRESSION_FILES:
            item.add_marker("regression")
        if filename in INTEGRATION_FILES:
            item.add_marker("integration")
        if filename in LIVE_FILES:
            item.add_marker("live")
        if filename in MYSQL_FILES:
            item.add_marker("mysql")
        if filename in FRONTEND_CONTRACT_FILES:
            item.add_marker("frontend_contract")
