"""Central production architecture contract registry.

This module records stable public boundaries described by the architecture
documents. It is intentionally small: runtime stages may import constants from
here, but this file must not perform planning, retrieval, SQL generation, or IO.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping


ARCHITECTURE_CONTRACT_VERSION = "sd1-boundaries-v1"


@dataclass(frozen=True)
class PipelineContractVersions:
    retrieval: str
    planner_input: str
    pipeline_result: str


PIPELINE_CONTRACTS = PipelineContractVersions(
    retrieval="phase3c-retrieval-v1",
    planner_input="phase3c-planner-input-v1",
    pipeline_result="phase3c-pipeline-result-v1",
)


ACTIVE_BOUNDARIES: Mapping[str, str] = MappingProxyType(
    {
        "application_orchestration": "core.app_service",
        "database_foundation": "kb_pipeline.database_service",
        "question_pipeline": "query_pipeline.query_pipeline",
        "query_planner": "query_pipeline.query_planner",
        "sql_question_service": "sql_pipeline.question_service",
        "sql_validator": "sql_pipeline.sql_validator",
        "sql_executor": "sql_pipeline.query_executor",
        "api_gateway": "api_gateway.app",
        "observability": "infrastructure.observability",
    }
)


PUBLIC_ENTRY_POINTS: Mapping[str, str] = MappingProxyType(
    {
        "application": "core.app_service:AppService",
        "database": "kb_pipeline.database_service:DatabaseService",
        "planning": "query_pipeline.query_pipeline:QueryPipeline",
        "planning_result": "query_pipeline.query_pipeline:QueryPipelineResult",
        "retrieval": "query_pipeline.query_pipeline:retrieve_context",
        "planner": "query_pipeline.query_planner:build_query_context",
        "sql_generation": "sql_pipeline.question_service:QuestionService.generate_from_pipeline",
        "sql_validation": "sql_pipeline.sql_validator:validate_sql",
        "sql_execution": "sql_pipeline.query_executor:execute_query",
        "gateway": "api_gateway.app:gateway",
    }
)


COMPATIBILITY_MODULES: Mapping[str, str] = MappingProxyType(
    {
        "core.context_retriever": "query_pipeline.context_retriever",
        "core.database_service": "kb_pipeline.database_service",
        "core.query_planner": "query_pipeline.query_planner",
        "core.question_service": "sql_pipeline.question_service",
        "db.connection": "kb_pipeline.connection",
        "db.data_profiler": "kb_pipeline.data_profiler",
        "db.query_executor": "sql_pipeline.query_executor",
        "db.schema_reader": "kb_pipeline.schema_reader",
        "semantic.ai_semantic_enricher": "kb_pipeline.ai_semantic_enricher",
        "semantic.business_glossary": "kb_pipeline.business_glossary",
        "semantic.erp_metadata": "kb_pipeline.schema_facts",
        "semantic.relationship_graph": "kb_pipeline.relationship_graph",
        "semantic.semantic_mapper": "kb_pipeline.semantic_mapper",
        "utils.question_normalizer": "query_pipeline.question_normalizer",
        "utils.sql_validator": "sql_pipeline.sql_validator",
        "vector_store.persistence": "kb_pipeline.vector.persistence",
    }
)


PACKAGE_DEPENDENCIES: Mapping[str, frozenset[str]] = MappingProxyType(
    {
        "infrastructure": frozenset({"infrastructure"}),
        "kb_pipeline": frozenset({"core", "infrastructure", "kb_pipeline", "utils"}),
        "query_pipeline": frozenset(
            {"core", "infrastructure", "kb_pipeline", "query_pipeline", "utils"}
        ),
        "sql_pipeline": frozenset(
            {"core", "infrastructure", "kb_pipeline", "query_pipeline", "sql_pipeline", "utils"}
        ),
        "core": frozenset(
            {"core", "infrastructure", "kb_pipeline", "query_pipeline", "sql_pipeline", "utils"}
        ),
        "api_gateway": frozenset({"api_gateway", "core", "infrastructure"}),
    }
)


def active_module_for(module_name: str) -> str:
    """Return the active implementation for a compatibility import path."""
    return COMPATIBILITY_MODULES.get(module_name, module_name)
