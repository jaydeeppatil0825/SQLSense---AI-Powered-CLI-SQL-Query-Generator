"""Focused regression coverage for Phase 1E filtered aggregate safety."""

from core.query_planner import build_intent, build_query_context


def test_filtered_generic_aggregate_metric_remains_ambiguous():
    question = "show sum amount from bills where bill status is paid"
    intent = build_intent(question)
    billed_metric = {
        "table": "bills",
        "column": "billed_value",
        "semantic_type": "numeric_candidate",
        "is_measure": True,
        "score": 0.95,
        "source": "vector",
    }
    paid_metric = {
        "table": "bills",
        "column": "paid_value",
        "semantic_type": "numeric_candidate",
        "is_measure": True,
        "score": 0.7,
        "source": "vector",
    }
    status_filter = {
        "table": "bills",
        "column": "bill_status",
        "semantic_type": "text_candidate",
        "score": 0.96,
        "matched_terms": ["bill status"],
        "source": "vector",
    }
    evidence = {
        "query_terms": ["amount", "bills", "bill status"],
        "matched_tables": [{"table": "bills", "score": 0.96, "source": "vector"}],
        "matched_columns": [billed_metric, paid_metric, status_filter],
        "matched_glossary_terms": [],
        "matched_relationships": [],
        "possible_join_paths": [],
        "measure_candidates": [billed_metric, paid_metric],
        "dimension_candidates": [],
        "filter_candidates": [status_filter],
        "date_candidates": [],
        "retrieval_sources": ["normalized_vector_evidence"],
        "evidence_scores": {"overall": 0.9},
        "source_metadata": {"backend": "chroma"},
        "ambiguity_candidates": {
            "metrics": [{"table_name": "bills", "column_name": "paid_value"}],
        },
        "missing_evidence_indicators": {},
        "normalized_package_used": True,
        "confidence": 0.9,
    }

    context = build_query_context(question, {}, intent=intent, retrieved_context=evidence)

    assert intent["metric_is_generic"] is True
    assert context["query_shape"] == "filtered_query"
    assert context["route_recommendation"] == "cannot_plan_safely"
    assert context["selected_metric"] is None
    assert context["route_reason"] == "metric evidence is ambiguous"
    metric_ambiguity = next(
        entry for entry in context["ambiguity_details"] if entry["type"] == "metric_selection"
    )
    choices = {choice.get("column") or choice.get("column_name") for choice in metric_ambiguity["choices"]}
    assert choices >= {"billed_value", "paid_value"}
