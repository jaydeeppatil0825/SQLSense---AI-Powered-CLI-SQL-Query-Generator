import ast
import json
from pathlib import Path

import pytest

from kb_pipeline.business_glossary import generate_business_glossary
from kb_pipeline.relationship_graph import build_relationship_graph
from kb_pipeline.semantic_providers.base import ProviderAttempt, SemanticProviderResult
from kb_pipeline.semantic_providers.local_provider import AISemanticProvider
from kb_pipeline.semantic_providers.provider_chain import SemanticProviderChain, build_default_provider_chain
from kb_pipeline.semantic_providers.response_parser import (
    SemanticMappingValidationError,
    validate_enriched_knowledge_base,
)
from kb_pipeline.semantic_providers.rule_based_provider import RuleBasedSemanticProvider
from kb_pipeline.vector.embedding_service import EmbeddingService
from kb_pipeline.vector.index_builder import VectorIndexBuilder


def _kb():
    return {
        "orders": {
            "table_name": "orders",
            "primary_keys": ["order_id"],
            "foreign_keys": [],
            "columns": [
                {"name": "order_id", "type": "INTEGER", "semantic_type": "id"},
                {"name": "total_amount", "type": "DECIMAL(10,2)", "semantic_type": "numeric_candidate"},
            ],
        }
    }


def _ai_kb():
    enriched = json.loads(json.dumps(_kb()))
    enriched["orders"]["ai_metadata"] = {
        "table_description": "Sales order records",
        "business_purpose": "Stores sales order records",
        "business_terms": ["order", "sale"],
        "confidence": 0.9,
        "reason": "table and columns describe sales orders",
    }
    enriched["orders"]["business_terms"] = ["order", "sale"]
    enriched["orders"]["columns"][1]["ai_metadata"] = {
        "ai_semantic_type": "money",
        "business_description": "Order amount",
        "business_terms": ["order amount", "revenue"],
        "confidence": 0.91,
        "reason": "decimal amount column",
    }
    return enriched


class _FakeProvider:
    def __init__(self, name, provider_type, result=None, exc=None):
        self.provider_name = name
        self.provider_type = provider_type
        self.result = result
        self.exc = exc
        self.calls = 0

    def health_check(self):
        return True, "ok"

    def map_schema_semantics(self, knowledge_base):
        self.calls += 1
        if self.exc:
            raise self.exc
        return self.result


def test_ai_semantic_mapping_is_primary_and_used():
    ai = _FakeProvider(
        "ollama",
        "local",
        SemanticProviderResult(
            status="enriched",
            knowledge_base=_ai_kb(),
            provider_used="ollama",
            provider_attempts=[ProviderAttempt("ollama", "local", "success", "ok")],
        ),
    )
    fallback = RuleBasedSemanticProvider()
    fallback.map_schema_semantics = pytest.fail

    result = SemanticProviderChain([ai, fallback]).map_schema_semantics(_kb())

    assert result.status == "enriched"
    assert result.provider_used == "ollama"
    assert result.fallback_used is False


def test_ai_disabled_uses_deterministic_fallback():
    ai = _FakeProvider("ollama", "local", exc=AssertionError("AI should be skipped"))

    result = SemanticProviderChain([ai, RuleBasedSemanticProvider()], ai_enabled=False).map_schema_semantics(_kb())

    assert result.status == "fallback"
    assert result.provider_used == "rule_based"
    assert result.fallback_used is True
    assert result.provider_attempts[0].status == "skipped"


def test_local_provider_success_marks_ai_metadata():
    provider = AISemanticProvider(
        provider_name="ollama",
        provider_type="local",
        backend="local",
        health_check=lambda: (True, "ready"),
        enrich_func=lambda kb, backend: _ai_kb(),
    )

    result = provider.map_schema_semantics(_kb())

    assert result.status == "enriched"
    assert result.knowledge_base["orders"]["ai_metadata"]["source"] == "ai_enrichment"
    assert result.knowledge_base["orders"]["columns"][1]["ai_metadata"]["provider"] == "ollama"


def test_provider_failures_fall_back_after_attempts():
    local = _FakeProvider("ollama", "local", exc=TimeoutError("timed out"))
    cloud = _FakeProvider("nvidia", "cloud", exc=RuntimeError("503 unavailable"))

    result = SemanticProviderChain([local, cloud, RuleBasedSemanticProvider()]).map_schema_semantics(_kb())

    assert result.status == "fallback"
    assert result.provider_used == "rule_based"
    assert [attempt.provider_name for attempt in result.provider_attempts] == ["ollama", "nvidia", "rule_based"]
    assert result.reason == "timed out"


def test_default_chain_records_disabled_cloud_before_rule_based():
    result = build_default_provider_chain(
        provider_order="nvidia,rule_based",
        allow_cloud_provider=False,
        enrich_func=lambda kb, backend: _ai_kb(),
    ).map_schema_semantics(_kb())

    assert result.status == "fallback"
    assert result.provider_attempts[0].provider_name == "nvidia"
    assert result.provider_attempts[0].status == "skipped"


def test_semantic_mapping_validator_rejects_unsafe_ai_output():
    enriched = _ai_kb()
    enriched["invented"] = {"columns": []}
    with pytest.raises(SemanticMappingValidationError, match="invented table"):
        validate_enriched_knowledge_base(enriched, _kb())

    enriched = _ai_kb()
    enriched["orders"]["columns"].append({"name": "fake_col", "ai_metadata": {}})
    with pytest.raises(SemanticMappingValidationError, match="invented column"):
        validate_enriched_knowledge_base(enriched, _kb())

    enriched = _ai_kb()
    enriched["orders"]["ai_metadata"]["table_description"] = "SELECT * FROM users"
    with pytest.raises(SemanticMappingValidationError, match="SQL text"):
        validate_enriched_knowledge_base(enriched, _kb())

    enriched = _ai_kb()
    enriched["orders"]["columns"][1]["ai_metadata"]["business_terms"] = ["x" * 81]
    with pytest.raises(SemanticMappingValidationError, match="too long"):
        validate_enriched_knowledge_base(enriched, _kb())

    enriched = _ai_kb()
    enriched["orders"]["columns"][1]["ai_metadata"]["confidence"] = 1.5
    with pytest.raises(SemanticMappingValidationError, match="out of range"):
        validate_enriched_knowledge_base(enriched, _kb())


def test_glossary_and_vector_consume_validated_ai_mapping():
    enriched = validate_enriched_knowledge_base(_ai_kb(), _kb())

    glossary = generate_business_glossary(enriched, use_ai_enrichment=True)
    docs = VectorIndexBuilder(EmbeddingService()).build_from_knowledge_base(enriched)

    assert "revenue" in glossary
    assert glossary["revenue"]["mapped_columns"][0]["column"] == "total_amount"
    assert any(
        doc["metadata"].get("column_name") == "total_amount"
        and "revenue" in doc["metadata"].get("business_terms", [])
        for doc in docs
    )


def test_relationship_graph_ignores_ai_aliases_for_authorization():
    enriched = _ai_kb()
    enriched["orders"]["ai_metadata"]["business_terms"].append("customer")

    graph = build_relationship_graph(enriched)

    assert graph["orders"]["edges"] == []


def test_runtime_modules_do_not_import_semantic_providers():
    runtime_roots = [Path("query_pipeline"), Path("sql_pipeline")]
    offenders = []
    for root in runtime_roots:
        for path in root.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and str(node.module or "").startswith("kb_pipeline.semantic_providers"):
                    offenders.append(str(path))
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name.startswith("kb_pipeline.semantic_providers"):
                            offenders.append(str(path))

    assert offenders == []
