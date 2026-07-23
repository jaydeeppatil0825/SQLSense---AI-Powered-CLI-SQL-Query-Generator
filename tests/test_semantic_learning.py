import ast
from pathlib import Path

from core.app_service import AppService
from kb_pipeline.business_glossary import generate_business_glossary
from kb_pipeline.vector.embedding_service import EmbeddingService
from kb_pipeline.vector.index_builder import VectorIndexBuilder
from semantic_learning.candidate_store import FileCandidateStore
from semantic_learning.candidate_validator import validate_candidate
from semantic_learning.learned_aliases import LearnedAliasCandidate
from semantic_learning.learning_recorder import LearningRecorder
from semantic_learning.promotion_policy import PromotionPolicy, apply_promotion_policy


def _kb():
    return {
        "orders": {
            "columns": [
                {"name": "order_id", "type": "INTEGER", "semantic_type": "id"},
                {"name": "total_amount", "type": "DECIMAL(12,2)", "semantic_type": "money"},
                {"name": "order_status", "type": "VARCHAR(30)", "semantic_type": "status", "profile_facts": {"sample_values": ["paid"]}},
                {"name": "customer_name", "type": "VARCHAR(100)", "semantic_type": "name", "planner_roles": {"dimension_candidate": True}},
            ],
            "relationships": [],
        },
        "payments": {
            "columns": [
                {"name": "payment_amount", "type": "DECIMAL(12,2)", "semantic_type": "money"},
            ],
            "relationships": [],
        },
    }


def _candidate(**overrides):
    data = {
        "database_identity_hash": "db-1",
        "schema_fingerprint": "schema-1",
        "phrase": "revenue",
        "target_type": "metric",
        "table": "orders",
        "column": "total_amount",
        "semantic_role": "metric",
    }
    data.update(overrides)
    return LearnedAliasCandidate(**data)


def test_candidate_contract_creation_redacts_and_normalizes():
    candidate = _candidate(phrase="Revenue 123456789")

    assert candidate.contract_version == "learned-alias-v1"
    assert candidate.normalized_phrase == "revenue redacted phone"
    assert candidate.support_count == 1
    assert "password" not in _candidate(phrase="password=secret").normalized_phrase


def test_recording_requires_deterministic_plan_and_execution_success_context(tmp_path):
    store = FileCandidateStore(tmp_path / "aliases.json")
    recorder = LearningRecorder(store)
    context = {
        "query_shape": "single_table_aggregate",
        "deterministic_query_plan": {"query_shape": "single_table_aggregate"},
        "plan": {"question": "show revenue"},
        "selected_tables": [{"table": "orders"}],
        "selected_metric": {"table": "orders", "column": "total_amount"},
    }

    recorded = recorder.record_successful_execution(
        query_context=context,
        knowledge_base=_kb(),
        database_identity_hash="db-1",
        schema_fingerprint="schema-1",
    )
    skipped = recorder.record_successful_execution(
        query_context={"selected_metric": {"table": "orders", "column": "total_amount"}},
        knowledge_base=_kb(),
        database_identity_hash="db-1",
        schema_fingerprint="schema-1",
    )

    assert len(recorded) == 2
    assert skipped == []
    assert store.load()[0].evidence_examples_hashes


def test_recorder_failure_does_not_affect_execution_result():
    class DB:
        knowledge_base_metadata = {"schema_fingerprint": "schema-1"}

        def get_engine(self):
            return object()

        def get_knowledge_base(self):
            return _kb()

        def _connected_database_identity(self):
            return {"database_name": "demo"}

    class Results:
        def validate_planned_query_artifact(self, sql):
            return True, "", {
                "query_context": {
                    "deterministic_query_plan": {"query_shape": "single_table_aggregate"},
                    "selected_metric": {"table": "orders", "column": "total_amount"},
                }
            }

        def execute_sql(self, **kwargs):
            return True, "ok", [{"total_amount": 10}]

    class FailingRecorder:
        def record_successful_execution(self, **kwargs):
            raise RuntimeError("store unavailable")

    service = AppService()
    service.database_service = DB()
    service.result_service = Results()
    service.learning_recorder = FailingRecorder()

    assert service.execute_sql("SELECT SUM(total_amount) FROM orders") == (True, "ok", [{"total_amount": 10}])


def test_candidate_validation_table_metric_dimension_value_and_rejections():
    assert validate_candidate(_candidate(target_type="table", column="", semantic_role="table_entity"), _kb(), schema_fingerprint="schema-1").status == "pending"
    assert validate_candidate(_candidate(), _kb(), schema_fingerprint="schema-1").status == "pending"
    assert validate_candidate(_candidate(phrase="customer", target_type="dimension", column="customer_name", semantic_role="dimension"), _kb(), schema_fingerprint="schema-1").status == "pending"
    assert validate_candidate(_candidate(phrase="paid", target_type="value", column="order_status", value="paid", semantic_role="filter_value"), _kb(), schema_fingerprint="schema-1").status == "pending"
    assert validate_candidate(_candidate(column="missing"), _kb(), schema_fingerprint="schema-1").rejection_reason == "target_column_missing"
    assert validate_candidate(_candidate(), _kb(), schema_fingerprint="schema-2").rejection_reason == "schema_fingerprint_mismatch"


def test_conflict_stays_pending_and_threshold_promotion_works():
    approved = _candidate(status="approved", support_count=5)
    conflict = _candidate(table="payments", column="payment_amount", support_count=3)
    promoted = _candidate(support_count=3)
    rejected = _candidate(phrase="bad", column="missing", support_count=10)

    conflict_result = validate_candidate(conflict, _kb(), schema_fingerprint="schema-1", approved_aliases=[approved])
    policy_result = apply_promotion_policy(
        [promoted, rejected],
        _kb(),
        schema_fingerprint="schema-1",
        policy=PromotionPolicy(support_threshold=3),
    )

    assert conflict_result.status == "pending"
    assert conflict_result.rejection_reason == "conflicts_with_approved_alias"
    assert policy_result[0].status == "approved"
    assert policy_result[1].status == "rejected"


def test_approved_alias_exports_to_glossary_and_vector_as_non_authoritative():
    alias = _candidate(status="approved", support_count=3, confidence_score=0.8)
    glossary = generate_business_glossary(_kb(), learned_aliases=[alias])
    doc_builder = VectorIndexBuilder(EmbeddingService())
    docs = doc_builder.build_from_glossary(glossary)

    assert "revenue" in glossary
    mapping = glossary["revenue"]["mapped_columns"][0]
    assert mapping["source"] == "learned_alias"
    assert mapping["authority"] == "non_authoritative_evidence"
    assert mapping["safe_for_join_authorization"] is False
    learned_docs = [doc for doc in docs if doc["metadata"].get("term") == "revenue"]
    assert learned_docs[0]["metadata"]["evidence_source"] == "learned_alias"
    assert learned_docs[0]["metadata"]["safe_for_join_authorization"] is False


def test_learning_boundaries_do_not_import_runtime_authority_modules():
    semantic_imports = set()
    for path in Path("semantic_learning").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        semantic_imports |= {
            node.module or ""
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        }
    planner_imports = set()
    for path in Path("query_pipeline").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        planner_imports |= {
            node.module or ""
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        }
    relationship_graph_imports = {
        node.module or ""
        for node in ast.walk(ast.parse(Path("kb_pipeline/relationship_graph.py").read_text(encoding="utf-8")))
        if isinstance(node, ast.ImportFrom)
    }
    generator_imports = {
        node.module or ""
        for node in ast.walk(ast.parse(Path("sql_pipeline/deterministic_sql_generator.py").read_text(encoding="utf-8")))
        if isinstance(node, ast.ImportFrom)
    }
    executor_imports = {
        node.module or ""
        for node in ast.walk(ast.parse(Path("sql_pipeline/query_executor.py").read_text(encoding="utf-8")))
        if isinstance(node, ast.ImportFrom)
    }

    assert not any(name.startswith("sql_pipeline.sql_validator") for name in semantic_imports)
    assert not any(name.startswith("sql_pipeline.query_executor") for name in semantic_imports)
    assert not any(name.startswith("sql_pipeline.deterministic_sql_generator") for name in semantic_imports)
    assert "semantic_learning.candidate_store" not in planner_imports
    assert "semantic_learning" not in relationship_graph_imports
    assert "semantic_learning" not in generator_imports
    assert "semantic_learning" not in executor_imports
