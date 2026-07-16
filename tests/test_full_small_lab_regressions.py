import json
from pathlib import Path

import pytest

from core.cache_service import CacheConfig, MemoryCacheStore
from query_pipeline.query_pipeline import QueryPipeline
from sql_pipeline.question_service import QuestionService


ROOT = Path(__file__).resolve().parents[1]
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


@pytest.fixture(scope="module")
def lab_context():
    kb = json.loads((ROOT / "semantic" / "knowledge_base.json").read_text(encoding="utf-8"))
    glossary = json.loads((ROOT / "semantic" / "business_glossary.json").read_text(encoding="utf-8"))
    return kb, glossary


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
    kb, glossary = lab_context
    sql, context = _ask(question, kb, glossary)

    assert context["query_shape"] == shape
    for fragment in fragments:
        assert fragment in sql


def test_full_small_lab_regressions_cache_parity(lab_context):
    kb, glossary = lab_context
    store = MemoryCacheStore(CacheConfig(memory_max_entries=100, ttl_seconds=300))

    disabled = [_ask(question, kb, glossary)[0] for question, _, _ in QUESTIONS]
    first = [_ask(question, kb, glossary, store)[0] for question, _, _ in QUESTIONS]
    second = [_ask(question, kb, glossary, store)[0] for question, _, _ in QUESTIONS]

    assert first == disabled
    assert second == disabled
    stats = store.stats()["artifacts"]
    assert stats["retrieval_evidence"]["hit"] >= len(QUESTIONS)
    assert stats["planner_evidence"]["hit"] >= len(QUESTIONS)
