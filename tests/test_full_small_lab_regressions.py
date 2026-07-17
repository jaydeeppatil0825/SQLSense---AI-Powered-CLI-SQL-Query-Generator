import hashlib
import json
from pathlib import Path

import pytest

from core.cache_service import CacheConfig, MemoryCacheStore
from query_pipeline.query_pipeline import QueryPipeline
from semantic.business_glossary import generate_business_glossary
from sql_pipeline.question_service import QuestionService
from vector_store import EmbeddingService, VectorIndexBuilder


ROOT = Path(__file__).resolve().parents[1]
FULL_SMALL_LAB_IDENTITY = {
    "database_type": "mysql",
    "database_name": "sqlsense_full_small_lab",
    "db_engine": "mysql",
    "db_host": "localhost",
    "db_port": "3306",
    "db_name": "sqlsense_full_small_lab",
}
QUESTIONS = [
    (
        "count delivered orders",
        "filtered_query",
        [
            "SELECT COUNT(*) AS count_rows FROM orders",
            "WHERE order_status = 'delivered'",
        ],
    ),
    (
        "show customers with their orders",
        "joined_lookup",
        [
            "FROM customers INNER JOIN orders ON orders.customer_id = customers.customer_id",
        ],
    ),
    (
        "show delivered orders with customer details",
        "joined_lookup",
        [
            "FROM orders INNER JOIN customers ON orders.customer_id = customers.customer_id",
            "WHERE orders.order_status = 'delivered'",
        ],
    ),
    (
        "top cities by total order amount",
        "joined_aggregate",
        [
            "SUM(orders.total_amount)",
            "GROUP BY customers.city",
            "ORDER BY sum__orders__total_amount DESC",
        ],
    ),
    (
        "completed payment amount by customer city",
        "joined_aggregate",
        [
            "SUM(payments.payment_amount)",
            "WHERE payments.payment_status = 'completed'",
            "GROUP BY customers.city",
        ],
    ),
]


def _fingerprint(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def _column(
    name,
    data_type,
    semantic_type,
    *,
    measure=False,
    dimension=False,
    date=False,
    join=False,
    samples=None,
    business_terms=None,
):
    roles = {
        "measure_candidate": bool(measure),
        "dimension_candidate": bool(dimension),
        "filter_candidate": bool(dimension or date),
        "date_candidate": bool(date),
        "sort_candidate": bool(measure or dimension or date),
        "join_candidate": bool(join),
    }
    column = {
        "name": name,
        "type": data_type,
        "nullable": True,
        "semantic_type": semantic_type,
        "is_measure": bool(measure),
        "is_dimension": bool(dimension),
        "is_date": bool(date),
        "planner_roles": roles,
        "business_terms": list(business_terms or []),
    }
    if samples:
        column["profile_facts"] = {
            "sample_values": list(samples),
            "unique_count": len(set(samples)),
        }
    return column


def _fk(column, referenced_table, referenced_column):
    return {
        "column": column,
        "referenced_table": referenced_table,
        "referenced_column": referenced_column,
    }


def _full_small_lab_knowledge_base():
    return {
        "customers": {
            "business_description": "Customer records.",
            "business_terms": ["customer", "customers"],
            "primary_keys": ["customer_id"],
            "foreign_keys": [],
            "relationships": [],
            "columns": [
                _column("customer_id", "INT", "id", join=True),
                _column("customer_name", "VARCHAR(100)", "name", dimension=True, business_terms=["customer name"]),
                _column("city", "VARCHAR(100)", "text", dimension=True, samples=["Mumbai", "Pune", "Delhi"], business_terms=["city", "cities", "customer city"]),
                _column("customer_status", "VARCHAR(30)", "status", dimension=True, samples=["active", "inactive"]),
                _column("customer_segment", "VARCHAR(30)", "category_candidate", dimension=True, samples=["retail", "enterprise"]),
                _column("credit_limit", "DECIMAL(12,2)", "money", measure=True, business_terms=["credit limit"]),
            ],
        },
        "orders": {
            "business_description": "Order records.",
            "business_terms": ["order", "orders"],
            "primary_keys": ["order_id"],
            "foreign_keys": [_fk("customer_id", "customers", "customer_id")],
            "relationships": [],
            "columns": [
                _column("order_id", "INT", "id", join=True),
                _column("customer_id", "INT", "id", join=True),
                _column("order_status", "VARCHAR(30)", "status", dimension=True, samples=["delivered", "pending", "cancelled"]),
                _column("payment_status", "VARCHAR(30)", "status", dimension=True, samples=["paid", "partial", "unpaid"]),
                _column("total_amount", "DECIMAL(12,2)", "money", measure=True, business_terms=["order amount", "order total", "order total amount"]),
            ],
        },
        "payments": {
            "business_description": "Payment records.",
            "business_terms": ["payment", "payments"],
            "primary_keys": ["payment_id"],
            "foreign_keys": [_fk("order_id", "orders", "order_id")],
            "relationships": [],
            "columns": [
                _column("payment_id", "INT", "id", join=True),
                _column("order_id", "INT", "id", join=True),
                _column("payment_amount", "DECIMAL(12,2)", "money", measure=True, business_terms=["payment amount"]),
                _column("payment_status", "VARCHAR(30)", "status", dimension=True, samples=["completed", "pending"]),
                _column("payment_method", "VARCHAR(30)", "category_candidate", dimension=True, samples=["bank", "card", "cash"]),
            ],
        },
        "suppliers": {
            "business_description": "Supplier records.",
            "business_terms": ["supplier", "suppliers"],
            "primary_keys": ["supplier_id"],
            "foreign_keys": [],
            "relationships": [],
            "columns": [
                _column("supplier_id", "INT", "id", join=True),
                _column("supplier_name", "VARCHAR(100)", "name", dimension=True),
                _column("supplier_status", "VARCHAR(30)", "status", dimension=True, samples=["active", "inactive"]),
            ],
        },
        "products": {
            "business_description": "Product records.",
            "business_terms": ["product", "products"],
            "primary_keys": ["product_id"],
            "foreign_keys": [_fk("supplier_id", "suppliers", "supplier_id")],
            "relationships": [],
            "columns": [
                _column("product_id", "INT", "id", join=True),
                _column("supplier_id", "INT", "id", join=True),
                _column("product_name", "VARCHAR(100)", "name", dimension=True),
                _column("product_category", "VARCHAR(50)", "category_candidate", dimension=True, samples=["electronics", "grocery"]),
                _column("product_status", "VARCHAR(30)", "status", dimension=True, samples=["active", "inactive"]),
                _column("unit_price", "DECIMAL(12,2)", "money", measure=True, business_terms=["product price", "unit price"]),
            ],
        },
        "order_items": {
            "business_description": "Order line item records.",
            "business_terms": ["order item", "order items", "line item"],
            "primary_keys": ["order_item_id"],
            "foreign_keys": [
                _fk("order_id", "orders", "order_id"),
                _fk("product_id", "products", "product_id"),
            ],
            "relationships": [],
            "columns": [
                _column("order_item_id", "INT", "id", join=True),
                _column("order_id", "INT", "id", join=True),
                _column("product_id", "INT", "id", join=True),
                _column("quantity", "INT", "quantity", measure=True, business_terms=["quantity"]),
                _column("line_total", "DECIMAL(12,2)", "money", measure=True, business_terms=["line total"]),
            ],
        },
    }


def _full_small_lab_artifact_set():
    kb = _full_small_lab_knowledge_base()
    glossary = generate_business_glossary(kb, use_ai_enrichment=False)
    schema_fingerprint = _fingerprint(
        {
            table: {
                "columns": [
                    {
                        "name": column.get("name"),
                        "type": column.get("type"),
                        "semantic_type": column.get("semantic_type"),
                    }
                    for column in data.get("columns", [])
                ],
                "primary_keys": data.get("primary_keys", []),
                "foreign_keys": data.get("foreign_keys", []),
            }
            for table, data in kb.items()
        }
    )
    manifest = {
        **FULL_SMALL_LAB_IDENTITY,
        "artifact_contract_version": "full-small-lab-test-v1",
        "builder_policy_version": "deterministic-no-ai-v1",
        "ai_enrichment_enabled": False,
        "schema_fingerprint": schema_fingerprint,
        "kb_fingerprint": _fingerprint(kb),
        "glossary_fingerprint": _fingerprint(glossary),
        "relationship_graph_fingerprint": _fingerprint(
            {table: data.get("foreign_keys", []) for table, data in kb.items()}
        ),
    }
    return kb, glossary, manifest


@pytest.fixture(scope="module")
def lab_context():
    return _full_small_lab_artifact_set()


def _ask(question, kb, glossary, cache_store=None):
    pipeline = QueryPipeline().run(
        question,
        kb,
        glossary,
        cache_store=cache_store,
        cache_database_identity={
            "db_engine": "lab",
            "db_host": "local",
            "db_port": "0",
            "database": "full_small_lab",
        },
    )
    service = QuestionService()
    ok, message, sql, error = service.process_question(
        question,
        kb,
        glossary,
        pipeline_context=pipeline.to_pipeline_context(),
    )
    assert ok, message or error
    return sql, service.get_last_query_context()


@pytest.mark.parametrize(("question", "shape", "fragments"), QUESTIONS)
def test_full_small_lab_regressions_without_cache(lab_context, question, shape, fragments):
    kb, glossary, _ = lab_context
    sql, context = _ask(question, kb, glossary)

    assert context["query_shape"] == shape
    for fragment in fragments:
        assert fragment in sql


def test_full_small_lab_regressions_cache_parity(lab_context):
    kb, glossary, _ = lab_context
    store = MemoryCacheStore(CacheConfig(memory_max_entries=100, ttl_seconds=300))

    disabled = [_ask(question, kb, glossary)[0] for question, _, _ in QUESTIONS]
    first = [_ask(question, kb, glossary, store)[0] for question, _, _ in QUESTIONS]
    second = [_ask(question, kb, glossary, store)[0] for question, _, _ in QUESTIONS]

    assert first == disabled
    assert second == disabled
    stats = store.stats()["artifacts"]
    assert stats["retrieval_evidence"]["hit"] >= len(QUESTIONS)
    assert stats["planner_evidence"]["hit"] >= len(QUESTIONS)


def test_full_small_lab_artifacts_have_matching_identity(lab_context):
    kb, glossary, manifest = lab_context
    committed_meta = json.loads((ROOT / "semantic" / "knowledge_base.meta.json").read_text(encoding="utf-8"))

    assert committed_meta["database_name"] == "sqlsense_ai_semantic_lab"
    assert manifest["database_name"] == "sqlsense_full_small_lab"
    assert manifest["ai_enrichment_enabled"] is False
    assert set(kb) == {"customers", "orders", "payments", "products", "suppliers", "order_items"}
    assert manifest["schema_fingerprint"]
    assert manifest["kb_fingerprint"] == _fingerprint(kb)
    assert manifest["glossary_fingerprint"] == _fingerprint(glossary)


def test_full_small_lab_glossary_preserves_multiple_owners(lab_context):
    _, glossary, _ = lab_context

    generic_payment_status = {
        (mapping["table"], mapping["column"])
        for mapping in glossary["payment status"]["mapped_columns"]
    }
    assert generic_payment_status == {
        ("orders", "payment_status"),
        ("payments", "payment_status"),
    }
    assert glossary["orders payment status"]["mapped_columns"][0]["table"] == "orders"
    assert glossary["payments payment status"]["mapped_columns"][0]["table"] == "payments"
    order_total_mapping = glossary["order total amount"]["mapped_columns"][0]
    assert order_total_mapping["table"] == "orders"
    assert order_total_mapping["column"] == "total_amount"
    assert glossary["customer city"]["mapped_columns"][0]["table"] == "customers"


def test_full_small_lab_vector_documents_share_artifact_identity(monkeypatch, lab_context):
    monkeypatch.setenv("EMBEDDING_BACKEND", "unsupported")
    kb, glossary, manifest = lab_context
    source_context = {
        **FULL_SMALL_LAB_IDENTITY,
        "schema_fingerprint": manifest["schema_fingerprint"],
        "schema_hash": manifest["schema_fingerprint"],
    }

    builder = VectorIndexBuilder(EmbeddingService())
    documents = builder.build_from_knowledge_base(kb, source_context=source_context)
    documents.extend(builder.build_from_glossary(glossary, source_context=source_context))

    assert documents
    for document in documents:
        metadata = document["metadata"]
        assert metadata["database_name"] == "sqlsense_full_small_lab"
        assert metadata["schema_fingerprint"] == manifest["schema_fingerprint"]

    relationship_docs = [doc for doc in documents if doc["metadata"].get("type") == "relationship"]
    assert relationship_docs == []
