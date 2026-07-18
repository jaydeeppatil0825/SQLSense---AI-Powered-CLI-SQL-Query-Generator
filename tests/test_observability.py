import logging

from core.cache_service import CacheConfig, CacheIdentity, MemoryCacheStore, make_cache_key
from infrastructure.observability import (
    InMemoryMetrics,
    InMemoryTracer,
    bind_context,
    current_context,
    event,
    new_context,
    redact,
    set_metrics,
    set_tracer,
    timed_stage,
)
from query_pipeline.query_pipeline import QueryPipeline


def test_redaction_removes_nested_secrets_rows_sql_and_embeddings():
    payload = {
        "password": "secret",
        "connection": "mysql://root:secret@localhost/demo",
        "nested": {
            "Authorization": "Bearer abc",
            "cookie": "session=abc",
            "sql": "SELECT * FROM customers",
            "rows": [{"id": 1}],
            "embeddings": [0.1, 0.2],
        },
    }

    safe = redact(payload)

    rendered = str(safe)
    assert "secret" not in rendered
    assert "Bearer abc" not in rendered
    assert "SELECT *" not in rendered
    assert "0.1" not in rendered
    assert "<redacted>" in rendered


def test_event_log_payload_has_context_required_fields_and_safe_metadata(caplog):
    caplog.set_level(logging.INFO, logger="sqlsense.observability")
    context = new_context(request_id="req-1", trace_id="trace-1", session_id="session-secret")

    with bind_context(context):
        event(
            "planner_failed_closed",
            component="query_pipeline",
            stage="planner.plan",
            status="blocked",
            reason_code="ambiguous_metric",
            question="raw question should not log",
            sql="SELECT should not log",
        )

    payloads = [
        record.observability_payload
        for record in caplog.records
        if hasattr(record, "observability_payload")
    ]
    payload = payloads[-1]
    assert payload["event"] == "planner_failed_closed"
    assert payload["request_id"] == "req-1"
    assert payload["trace_id"] == "trace-1"
    assert payload["session_id_hash"]
    assert payload["component"] == "query_pipeline"
    assert payload["stage"] == "planner.plan"
    assert payload["status"] == "blocked"
    assert payload["reason_code"] == "ambiguous_metric"
    assert payload["metadata"]["question"] == "<redacted>"
    assert payload["metadata"]["sql"] == "<redacted>"


def test_timed_stage_records_duration_metric_and_trace_span():
    metrics = InMemoryMetrics()
    tracer = InMemoryTracer()
    set_metrics(metrics)
    set_tracer(tracer)
    try:
        with bind_context(new_context(request_id="req-2")):
            with timed_stage("retrieval_completed", component="query_pipeline", stage="context.retrieve") as obs:
                obs["candidate_count"] = 2

        snapshot = metrics.snapshot()
        assert "sqlsense_stage_total" in str(snapshot["counters"])
        assert "sqlsense_stage_duration_ms" in str(snapshot["histograms"])
        assert tracer.spans[-1]["name"] == "query_pipeline.context.retrieve"
        assert tracer.spans[-1]["status"] == "ok"
    finally:
        set_metrics(None)
        set_tracer(None)


def test_cache_hit_and_miss_metrics_are_emitted():
    metrics = InMemoryMetrics()
    set_metrics(metrics)
    try:
        store = MemoryCacheStore(CacheConfig(backend="memory"))
        key = make_cache_key(
            identity=CacheIdentity(
                db_engine="mysql",
                db_host="localhost",
                db_port="3306",
                db_name="demo",
                schema_hash="s",
                kb_fingerprint="k",
                graph_fingerprint="g",
            ),
            artifact_type="relationship_path",
            normalized_input={"a": 1},
        )

        store.get(key, artifact_version="v1")
        store.set(key, artifact_version="v1", value={"payload": "ok"})
        store.get(key, artifact_version="v1")

        counters = str(metrics.snapshot()["counters"])
        assert "sqlsense_cache_reads_total" in counters
        assert "miss" in counters
        assert "hit" in counters
        assert "sqlsense_cache_writes_total" in counters
    finally:
        set_metrics(None)


class _BadMetrics:
    def increment(self, *args, **kwargs):
        raise RuntimeError("metrics down")

    def observe(self, *args, **kwargs):
        raise RuntimeError("metrics down")

    def gauge(self, *args, **kwargs):
        raise RuntimeError("metrics down")


class _BadTracer:
    def span(self, *args, **kwargs):
        raise RuntimeError("tracer down")


def test_observability_adapter_failure_does_not_change_pipeline_result(monkeypatch):
    set_metrics(_BadMetrics())
    set_tracer(_BadTracer())
    try:
        monkeypatch.setattr("query_pipeline.query_pipeline.build_intent", lambda *args, **kwargs: {"intent_type": "list"})
        monkeypatch.setattr(
            "query_pipeline.query_pipeline.retrieve_context",
            lambda **kwargs: {"matched_tables": [], "matched_columns": [], "retrieval_sources": []},
        )
        monkeypatch.setattr(
            "query_pipeline.query_pipeline.build_query_context",
            lambda *args, **kwargs: {
                "plan": {"question": "show accounts"},
                "route_recommendation": "cannot_plan_safely",
                "route_reason": "test fail closed",
                "query_shape": "unknown",
                "can_plan": False,
            },
        )

        result = QueryPipeline().run("show accounts", {"accounts": {"columns": []}})

        assert result.success is False
        assert result.route == "cannot_plan_safely"
        assert current_context().request_id == ""
    finally:
        set_metrics(None)
        set_tracer(None)
