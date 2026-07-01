"""Deterministic routing tests for rule-based SQL and clean complex-query blocking."""

import importlib
from pathlib import Path

from core.query_planner import build_query_context
from core.question_service import QuestionService


GENERIC_KB = {
    "alpha_records": {
        "columns": [
            {"name": "record_id", "type": "INTEGER", "nullable": False, "semantic_type": "id"},
            {"name": "record_name", "type": "VARCHAR(100)", "nullable": False, "semantic_type": "name"},
            {"name": "owner_id", "type": "INTEGER", "nullable": True, "semantic_type": "id"},
            {"name": "amount_total", "type": "DECIMAL(12,2)", "nullable": True, "semantic_type": "money"},
            {"name": "created_on", "type": "DATE", "nullable": True, "semantic_type": "date"},
        ],
        "primary_keys": ["record_id"],
        "foreign_keys": [],
    },
    "beta_events": {
        "columns": [
            {"name": "event_id", "type": "INTEGER", "nullable": False, "semantic_type": "id"},
            {"name": "owner_id", "type": "INTEGER", "nullable": True, "semantic_type": "id"},
            {"name": "event_total", "type": "DECIMAL(12,2)", "nullable": True, "semantic_type": "money"},
        ],
        "primary_keys": ["event_id"],
        "foreign_keys": [],
    },
}


NOISY_KB = {
    "alpha_records": {
        "module": "reference",
        "columns": [
            {"name": "record_id", "type": "INTEGER", "nullable": False, "semantic_type": "id"},
            {"name": "record_name", "type": "VARCHAR(100)", "nullable": False, "semantic_type": "name"},
        ],
        "primary_keys": ["record_id"],
        "foreign_keys": [],
        "relationships": [
            {
                "from_table": "alpha_records",
                "from_column": "record_id",
                "to_table": "beta_events",
                "to_column": "owner_id",
                "confidence": 0.99,
            }
        ],
    },
    "beta_events": {
        "module": "transaction",
        "columns": [
            {"name": "event_id", "type": "INTEGER", "nullable": False, "semantic_type": "id"},
            {"name": "event_name", "type": "VARCHAR(100)", "nullable": False, "semantic_type": "name"},
            {"name": "amount_total", "type": "DECIMAL(12,2)", "nullable": True, "semantic_type": "money"},
            {"name": "created_on", "type": "DATE", "nullable": True, "semantic_type": "date"},
        ],
        "primary_keys": ["event_id"],
        "foreign_keys": [],
        "relationships": [],
    },
    "gamma_locations": {
        "module": "reference",
        "columns": [
            {"name": "location_id", "type": "INTEGER", "nullable": False, "semantic_type": "id"},
            {"name": "location_name", "type": "VARCHAR(100)", "nullable": False, "semantic_type": "name"},
            {"name": "state_name", "type": "VARCHAR(100)", "nullable": True, "semantic_type": "status"},
        ],
        "primary_keys": ["location_id"],
        "foreign_keys": [],
        "relationships": [],
    },
}


NOISY_GLOSSARY = {
    "alpha records": {
        "description": "Primary records",
        "mapped_columns": [{"table": "alpha_records", "column": "record_name", "confidence": "high"}],
        "business_terms": ["alpha record"],
        "example_questions": ["Show all alpha records"],
    },
    "label": {
        "description": "Display label",
        "mapped_columns": [
            {"table": "alpha_records", "column": "record_name", "confidence": "medium"},
            {"table": "beta_events", "column": "event_name", "confidence": "medium"},
            {"table": "gamma_locations", "column": "location_name", "confidence": "medium"},
        ],
        "business_terms": ["alpha records"],
        "example_questions": ["Show labels"],
    },
    "amount": {
        "description": "Measured value",
        "mapped_columns": [
            {"table": "alpha_records", "column": "record_id", "confidence": "low"},
            {"table": "beta_events", "column": "amount_total", "confidence": "high"},
        ],
        "business_terms": ["alpha records"],
        "example_questions": ["Show total amount"],
    },
    "date": {
        "description": "Date value",
        "mapped_columns": [
            {"table": "alpha_records", "column": "record_id", "confidence": "low"},
            {"table": "beta_events", "column": "created_on", "confidence": "high"},
        ],
        "business_terms": ["alpha records"],
        "example_questions": ["Show latest alpha records"],
    },
    "state": {
        "description": "State value",
        "mapped_columns": [{"table": "gamma_locations", "column": "state_name", "confidence": "high"}],
        "business_terms": ["alpha records"],
        "example_questions": ["Show states"],
    },
    "primary entries": {
        "description": "Alias for alpha records",
        "mapped_columns": [{"table": "alpha_records", "column": "record_name", "confidence": "high"}],
        "business_terms": ["primary entries"],
        "example_questions": ["Show all primary entries"],
    },
}


class _DummyVectorRetriever:
    def __init__(self, table_name: str):
        self.table_name = table_name

    def get_relevant_tables(self, query, top_k=5):
        return [self.table_name]

    def get_relevant_table_details(self, query, top_k=5):
        return [{"table_name": self.table_name, "score": 0.95}]

    def get_relevant_columns(self, query, top_k=10):
        return [{"table_name": self.table_name, "column_name": "record_name", "semantic_type": "name"}]

    def get_relevant_glossary_terms(self, query, top_k=5):
        return []

    def get_relevant_relationships(self, query, top_k=5):
        return []

    def get_relevant_semantic_descriptions(self, query, top_k=8):
        return []

    def get_relevant_profiling_hints(self, query, top_k=8):
        return []

    def get_status(self):
        return {"index_built": True, "document_count": 1}


class _NoisyAliasVectorRetriever:
    def __init__(self, ranked_tables, ranked_columns=None):
        self.ranked_tables = list(ranked_tables)
        self.ranked_columns = list(ranked_columns or [])

    def get_relevant_tables(self, query, top_k=5):
        return self.ranked_tables[:top_k]

    def get_relevant_table_details(self, query, top_k=5):
        return [
            {"table_name": table_name, "score": round(0.95 - (index * 0.03), 2)}
            for index, table_name in enumerate(self.ranked_tables[:top_k])
        ]

    def get_relevant_columns(self, query, top_k=10):
        return self.ranked_columns[:top_k]

    def get_relevant_glossary_terms(self, query, top_k=5):
        return []

    def get_relevant_relationships(self, query, top_k=5):
        return []

    def get_relevant_semantic_descriptions(self, query, top_k=8):
        return []

    def get_relevant_profiling_hints(self, query, top_k=8):
        return []

    def get_status(self):
        return {"index_built": True, "document_count": len(self.ranked_tables)}


def _context(
    selected_table_names,
    *,
    intent="list",
    confidence=0.9,
    metric=None,
    dimension=None,
    grouping=None,
    filters=None,
    date_range=None,
    route_recommendation=None,
    join_paths=None,
):
    selected_tables = []
    selected_columns = []
    for table_name in selected_table_names:
        column_entries = [
            {
                "column": column["name"],
                "semantic_type": column.get("semantic_type", "general"),
                "confidence": confidence,
                "reason": "selected from schema metadata",
            }
            for column in GENERIC_KB[table_name]["columns"][:3]
        ]
        selected_tables.append(
            {
                "table": table_name,
                "confidence": confidence,
                "reason": "selected dynamically",
                "selected_columns": column_entries,
            }
        )
        for column_entry in column_entries:
            selected_columns.append({"table": table_name, **column_entry})

    plan = {
        "question": "generic question",
        "intent": intent,
        "metric": metric,
        "dimension": dimension,
        "filters": filters or [],
        "date_range": date_range,
        "grouping": grouping or [],
        "sorting": None,
        "limit": 50,
        "question_terms": [],
        "semantic_hints": {metric} if metric else set(),
        "matched_glossary_terms": [],
    }
    selected_kb = {table_name: GENERIC_KB[table_name] for table_name in selected_table_names}
    complex_sql_plan = None
    if intent not in {"list", "count"} or metric or dimension or grouping or join_paths:
        complex_sql_plan = {
            "query_shape": "deterministic_placeholder",
            "selected_tables": list(selected_table_names),
        }
    return {
        "intent": {"intent_type": intent},
        "retrieved_context": {},
        "plan": plan,
        "selected_tables": selected_tables,
        "selected_columns": selected_columns,
        "selected_table_names": list(selected_table_names),
        "selected_knowledge_base": selected_kb,
        "warnings": [],
        "confidence": confidence,
        "route_recommendation": route_recommendation,
        "complex_sql_plan": complex_sql_plan,
        "knowledge_base": GENERIC_KB,
        "vector_results": {"table_names": list(selected_table_names), "columns": [], "glossary_terms": [], "relationships": []},
        "vector_used": False,
        "join_paths": list(join_paths or []),
        "formula_evidence": [],
        "evidence_sources": [],
    }


def _assert_complex_not_implemented(service, message, sql):
    assert sql is None
    assert "deterministic sql generation for this query shape is not implemented yet" in message.lower()
    assert service.get_last_query_context()["route_used"] == "deterministic_sql_required"


def test_simple_table_listing_uses_rule_based(monkeypatch):
    service = QuestionService()
    monkeypatch.setattr("core.question_service.build_query_context", lambda *args, **kwargs: _context(["alpha_records"], intent="list"))
    monkeypatch.setattr(
        "core.question_service.generate_simple_sql",
        lambda *args, **kwargs: "SELECT record_id, record_name FROM alpha_records LIMIT 50;",
    )

    def fail_ai(*args, **kwargs):
        raise AssertionError("AI should not be called for simple list questions")

    monkeypatch.setattr("core.question_service.generate_sql", fail_ai)

    success, message, sql, error = service.process_question("show records", GENERIC_KB, ai_backend="local")

    assert success is True
    assert sql == "SELECT record_id, record_name FROM alpha_records LIMIT 50;"
    assert service.get_last_query_context()["route_used"] == "deterministic_sql_required"


def test_pipeline_context_bypasses_duplicate_context_build(monkeypatch):
    service = QuestionService()
    pipeline_query_context = _context(["alpha_records"], intent="list")
    pipeline_context = {
        "question": "show records",
        "normalized_question": "show records",
        "intent": {"intent_type": "list"},
        "retrieved_context": {"retrieval_sources": ["kb_identifier"]},
        "query_context": pipeline_query_context,
        "plan": dict(pipeline_query_context["plan"]),
        "formula_evidence": [],
        "evidence_sources": ["kb_identifier"],
    }

    monkeypatch.setattr(
        "core.question_service.build_query_context",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("build_query_context should not run when matching pipeline context is provided")),
    )
    monkeypatch.setattr(
        "core.question_service.generate_simple_sql",
        lambda *args, **kwargs: "SELECT record_id, record_name FROM alpha_records LIMIT 50;",
    )
    monkeypatch.setattr(
        "core.question_service.generate_sql",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("AI should not be called for simple list questions")),
    )

    success, message, sql, error = service.process_question(
        "show records",
        GENERIC_KB,
        ai_backend="local",
        pipeline_context=pipeline_context,
    )

    assert success is True
    assert sql == "SELECT record_id, record_name FROM alpha_records LIMIT 50;"
    assert service.get_last_query_context()["route_used"] == "deterministic_sql_required"
    assert service.get_last_query_context()["pipeline_context"] == pipeline_context
    assert isinstance(service.get_last_query_context().get("retrieved_context"), dict)


def test_pipeline_route_cannot_plan_safely_for_ambiguous_tables_blocks_sql_generation(monkeypatch):
    service = QuestionService()
    pipeline_context = {
        "normalized_question": "show entries",
        "route_recommendation": "cannot_plan_safely",
        "query_context": {
            "route_recommendation": "cannot_plan_safely",
            "plan": {"question": "show entries", "intent": "list"},
            "selected_tables": [
                {"table": "alpha_records", "confidence": 0.61},
                {"table": "beta_events", "confidence": 0.58},
            ],
            "selected_table_names": ["alpha_records", "beta_events"],
            "selected_columns": [],
            "join_paths": [],
        },
        "plan": {"question": "show entries", "intent": "list"},
        "retrieved_context": {},
        "formula_evidence": [],
        "evidence_sources": [],
    }

    monkeypatch.setattr(
        "core.question_service.generate_sql",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("AI generation should be blocked by planner clarification route")),
    )
    monkeypatch.setattr(
        "core.question_service.generate_simple_sql",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("Rule-based generation should be blocked by planner clarification route")),
    )

    success, message, sql, error = service.process_question(
        "show entries",
        GENERIC_KB,
        ai_backend="local",
        pipeline_context=pipeline_context,
    )

    assert success is False
    assert sql is None
    assert "ambiguous" in message.lower()
    assert service.get_last_query_context()["route_used"] == "cannot_plan_safely"


def test_pipeline_route_cannot_plan_safely_blocks_sql_generation(monkeypatch):
    service = QuestionService()
    pipeline_context = {
        "normalized_question": "show total by owner",
        "route_recommendation": "cannot_plan_safely",
        "query_context": {
            "route_recommendation": "cannot_plan_safely",
            "plan": {"question": "show total by owner", "intent": "total"},
            "selected_tables": [{"table": "alpha_records", "confidence": 0.42}],
            "selected_table_names": ["alpha_records"],
            "selected_columns": [],
            "join_paths": [],
        },
        "plan": {"question": "show total by owner", "intent": "total"},
        "retrieved_context": {},
        "formula_evidence": [],
        "evidence_sources": [],
    }

    monkeypatch.setattr(
        "core.question_service.generate_sql",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("AI generation should be blocked when planner cannot plan safely")),
    )
    monkeypatch.setattr(
        "core.question_service.generate_simple_sql",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("Rule-based generation should be blocked when planner cannot plan safely")),
    )

    success, message, sql, error = service.process_question(
        "show total by owner",
        GENERIC_KB,
        ai_backend="local",
        pipeline_context=pipeline_context,
    )

    assert success is False
    assert sql is None
    assert "could not be planned safely" in message.lower()
    assert service.get_last_query_context()["route_used"] == "cannot_plan_safely"


def test_simple_generator_with_current_signature_uses_rule_based(monkeypatch):
    service = QuestionService()
    monkeypatch.setattr("core.question_service.build_query_context", lambda *args, **kwargs: _context(["alpha_records"], intent="list"))
    monkeypatch.setattr(
        "core.question_service.generate_simple_sql",
        lambda *args, **kwargs: "SELECT record_id, record_name FROM alpha_records LIMIT 50;",
    )
    monkeypatch.setattr(
        "core.question_service.generate_sql",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("AI should not be called")),
    )

    success, message, sql, error = service.process_question("show records", GENERIC_KB, ai_backend="local")

    assert success is True
    assert sql == "SELECT record_id, record_name FROM alpha_records LIMIT 50;"
    assert service.get_last_query_context()["route_used"] == "deterministic_sql_required"


def test_count_query_uses_rule_based(monkeypatch):
    service = QuestionService()
    monkeypatch.setattr("core.question_service.build_query_context", lambda *args, **kwargs: _context(["alpha_records"], intent="count"))
    monkeypatch.setattr(
        "core.question_service.generate_simple_sql",
        lambda *args, **kwargs: "SELECT COUNT(*) AS total_alpha_records FROM alpha_records;",
    )
    monkeypatch.setattr("core.question_service.generate_sql", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("AI should not be called")))

    success, message, sql, error = service.process_question("count records", GENERIC_KB, ai_backend="local")

    assert success is True
    assert "COUNT(*)" in sql
    assert service.get_last_query_context()["route_used"] == "deterministic_sql_required"


def test_show_all_clients_can_be_rule_based(monkeypatch):
    kb = {
        "clients": {
            "columns": [
                {"name": "client_id", "type": "INTEGER", "nullable": False, "semantic_type": "id"},
                {"name": "client_name", "type": "VARCHAR(100)", "nullable": False, "semantic_type": "name"},
            ],
            "primary_keys": ["client_id"],
            "foreign_keys": [],
            "relationships": [],
        }
    }
    service = QuestionService()
    monkeypatch.setattr(
        "core.question_service.generate_sql",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("AI should not be called for simple client listing")),
    )

    success, message, sql, error = service.process_question("show all clients", kb, ai_backend="local")

    assert success is True
    assert "FROM clients" in sql
    assert service.get_last_query_context()["route_used"] == "deterministic_sql_required"


def test_count_clients_can_be_rule_based(monkeypatch):
    kb = {
        "clients": {
            "columns": [
                {"name": "client_id", "type": "INTEGER", "nullable": False, "semantic_type": "id"},
                {"name": "client_name", "type": "VARCHAR(100)", "nullable": False, "semantic_type": "name"},
            ],
            "primary_keys": ["client_id"],
            "foreign_keys": [],
            "relationships": [],
        }
    }
    service = QuestionService()
    monkeypatch.setattr(
        "core.question_service.generate_sql",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("AI should not be called for simple client count")),
    )

    success, message, sql, error = service.process_question("count clients", kb, ai_backend="local")

    assert success is True
    assert "COUNT(*)" in sql
    assert service.get_last_query_context()["route_used"] == "deterministic_sql_required"


CLIENTS_RELATIONSHIP_KB = {
    "clients": {
        "columns": [
            {"name": "client_id", "type": "INTEGER", "nullable": False, "semantic_type": "id"},
            {"name": "client_name", "type": "VARCHAR(100)", "nullable": False, "semantic_type": "name"},
        ],
        "primary_keys": ["client_id"],
        "foreign_keys": [],
        "relationships": [],
    },
    "agreements": {
        "columns": [
            {"name": "agreement_id", "type": "INTEGER", "nullable": False, "semantic_type": "id"},
            {"name": "client_id", "type": "INTEGER", "nullable": False, "semantic_type": "id"},
            {"name": "deal_value", "type": "DECIMAL(12,2)", "nullable": True, "semantic_type": "money"},
        ],
        "primary_keys": ["agreement_id"],
        "foreign_keys": [
            {"column": "client_id", "referenced_table": "clients", "referenced_column": "client_id"},
        ],
        "relationships": [],
    },
    "invoices": {
        "columns": [
            {"name": "invoice_id", "type": "INTEGER", "nullable": False, "semantic_type": "id"},
            {"name": "client_id", "type": "INTEGER", "nullable": False, "semantic_type": "id"},
            {"name": "billed_value", "type": "DECIMAL(12,2)", "nullable": True, "semantic_type": "money"},
        ],
        "primary_keys": ["invoice_id"],
        "foreign_keys": [
            {"column": "client_id", "referenced_table": "clients", "referenced_column": "client_id"},
        ],
        "relationships": [],
    },
}


def test_show_all_clients_selected_table_stays_single_table_without_joins():
    context = build_query_context("show all clients", CLIENTS_RELATIONSHIP_KB, use_vector_retrieval=False)

    assert context["selected_table_names"] == ["clients"]
    assert len(context["selected_tables"]) == 1
    assert context["selected_tables"][0]["table"] == "clients"
    assert context["join_paths"] == []


def test_show_all_clients_uses_rule_based_without_join_or_ai(monkeypatch):
    service = QuestionService()
    monkeypatch.setattr(
        "core.question_service.generate_sql",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("AI should not be called for simple client browse questions")),
    )

    success, message, sql, error = service.process_question("show all clients", CLIENTS_RELATIONSHIP_KB, ai_backend="local")

    assert success is True
    assert error is None
    assert "FROM clients" in sql
    assert "JOIN" not in sql.upper()
    assert service.get_last_query_context()["selected_table_names"] == ["clients"]
    assert service.get_last_query_context()["join_paths"] == []
    assert service.get_last_query_context()["route_used"] == "deterministic_sql_required"


def test_count_clients_uses_clients_only_without_join(monkeypatch):
    service = QuestionService()
    monkeypatch.setattr(
        "core.question_service.generate_sql",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("AI should not be called for simple client count questions")),
    )

    success, message, sql, error = service.process_question("count clients", CLIENTS_RELATIONSHIP_KB, ai_backend="local")

    assert success is True
    assert error is None
    assert sql == "SELECT COUNT(*) AS total_clients FROM clients;"
    assert service.get_last_query_context()["selected_table_names"] == ["clients"]
    assert service.get_last_query_context()["join_paths"] == []
    assert service.get_last_query_context()["route_used"] == "deterministic_sql_required"


def test_top_5_clients_by_deal_value_keeps_join_path_for_deterministic_sql():
    context = build_query_context("top 5 clients by deal value", CLIENTS_RELATIONSHIP_KB, use_vector_retrieval=False)

    assert "clients" in context["selected_table_names"]
    assert "agreements" in context["selected_table_names"]
    assert context["join_paths"]
    join_conditions = [
        edge["join_condition"]
        for join_path in context["join_paths"]
        for edge in join_path["path"]
    ]
    assert (
        "agreements.client_id = clients.client_id" in join_conditions
        or "clients.client_id = agreements.client_id" in join_conditions
    )
    assert context["route_recommendation"] == "deterministic_sql_required"


def test_billed_value_by_client_keeps_invoice_join_path_for_deterministic_sql():
    context = build_query_context("billed value by client", CLIENTS_RELATIONSHIP_KB, use_vector_retrieval=False)

    assert "clients" in context["selected_table_names"]
    assert "invoices" in context["selected_table_names"]
    assert context["join_paths"]
    join_conditions = [
        edge["join_condition"]
        for join_path in context["join_paths"]
        for edge in join_path["path"]
    ]
    assert (
        "invoices.client_id = clients.client_id" in join_conditions
        or "clients.client_id = invoices.client_id" in join_conditions
    )
    assert context["route_recommendation"] == "deterministic_sql_required"


def test_show_all_agreements_uses_rule_based_and_succeeds(monkeypatch):
    kb = {
        "agreements": {
            "columns": [
                {"name": "agreement_id", "type": "INTEGER", "nullable": False, "semantic_type": "id"},
                {"name": "agreement_name", "type": "VARCHAR(100)", "nullable": False, "semantic_type": "name"},
            ],
            "primary_keys": ["agreement_id"],
            "foreign_keys": [],
            "relationships": [],
        }
    }
    service = QuestionService()
    monkeypatch.setattr(
        "core.question_service.generate_sql",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("AI should not be called for simple agreement listing")),
    )

    success, message, sql, error = service.process_question("show all agreements", kb, ai_backend="local")

    assert success is True
    assert "FROM agreements" in sql
    assert "SELECT agreement_id, agreement_name" in sql
    assert service.get_last_query_context()["route_used"] == "deterministic_sql_required"


def test_count_agreements_uses_rule_based_and_succeeds(monkeypatch):
    kb = {
        "agreements": {
            "columns": [
                {"name": "agreement_id", "type": "INTEGER", "nullable": False, "semantic_type": "id"},
                {"name": "agreement_name", "type": "VARCHAR(100)", "nullable": False, "semantic_type": "name"},
            ],
            "primary_keys": ["agreement_id"],
            "foreign_keys": [],
            "relationships": [],
        }
    }
    service = QuestionService()
    monkeypatch.setattr(
        "core.question_service.generate_sql",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("AI should not be called for simple agreement count")),
    )

    success, message, sql, error = service.process_question("count agreements", kb, ai_backend="local")

    assert success is True
    assert "COUNT(*)" in sql
    assert service.get_last_query_context()["route_used"] == "deterministic_sql_required"


def test_pipeline_deterministic_route_allows_strong_simple_list(monkeypatch):
    service = QuestionService()
    pipeline_context = {
        "normalized_question": "show all agreements",
        "route_recommendation": "deterministic_sql_required",
        "intent": {"intent_type": "list"},
        "retrieved_context": {},
        "query_context": {
            "route_recommendation": "deterministic_sql_required",
            "plan": {
                "question": "show all agreements",
                "intent": "list",
                "metric": None,
                "dimension": None,
                "filters": [],
                "date_range": None,
                "grouping": [],
                "sorting": None,
                "limit": 50,
                "question_terms": ["show", "all", "agreements"],
                "semantic_hints": set(),
            },
            "selected_tables": [
                {
                    "table": "agreements",
                    "confidence": 0.91,
                    "reason": "selected dynamically",
                    "selected_columns": [
                        {"column": "agreement_id", "semantic_type": "id", "confidence": 0.91, "reason": "selected"},
                        {"column": "agreement_name", "semantic_type": "name", "confidence": 0.91, "reason": "selected"},
                    ],
                }
            ],
            "selected_columns": [
                {"table": "agreements", "column": "agreement_id", "semantic_type": "id", "confidence": 0.91, "reason": "selected"},
                {"table": "agreements", "column": "agreement_name", "semantic_type": "name", "confidence": 0.91, "reason": "selected"},
            ],
            "selected_table_names": ["agreements"],
            "confidence": 0.91,
            "vector_results": {"table_names": ["agreements"], "columns": [], "glossary_terms": [], "relationships": []},
            "selected_knowledge_base": {
                "agreements": {
                    "columns": [
                        {"name": "agreement_id", "type": "INTEGER", "nullable": False, "semantic_type": "id"},
                        {"name": "agreement_name", "type": "VARCHAR(100)", "nullable": False, "semantic_type": "name"},
                    ],
                    "primary_keys": ["agreement_id"],
                    "foreign_keys": [],
                    "relationships": [],
                }
            },
            "knowledge_base": {
                "agreements": {
                    "columns": [
                        {"name": "agreement_id", "type": "INTEGER", "nullable": False, "semantic_type": "id"},
                        {"name": "agreement_name", "type": "VARCHAR(100)", "nullable": False, "semantic_type": "name"},
                    ],
                    "primary_keys": ["agreement_id"],
                    "foreign_keys": [],
                    "relationships": [],
                }
            },
        },
        "plan": {
            "question": "show all agreements",
            "intent": "list",
            "metric": None,
            "dimension": None,
            "filters": [],
            "date_range": None,
            "grouping": [],
            "sorting": None,
            "limit": 50,
            "question_terms": ["show", "all", "agreements"],
            "semantic_hints": set(),
        },
        "formula_evidence": [],
        "evidence_sources": [],
    }

    monkeypatch.setattr(
        "core.question_service.generate_sql",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("AI should not be called for strong simple list query")),
    )

    success, message, sql, error = service.process_question(
        "show all agreements",
        pipeline_context["query_context"]["knowledge_base"],
        ai_backend="local",
        pipeline_context=pipeline_context,
    )

    assert success is True
    assert "FROM agreements" in sql
    assert service.get_last_query_context()["route_used"] == "deterministic_sql_required"


def test_simple_limit_query_uses_rule_based(monkeypatch):
    service = QuestionService()
    context = _context(["alpha_records"], intent="list")
    context["plan"]["limit"] = 10
    monkeypatch.setattr("core.question_service.build_query_context", lambda *args, **kwargs: context)
    monkeypatch.setattr(
        "core.question_service.generate_simple_sql",
        lambda *args, **kwargs: "SELECT record_id, record_name FROM alpha_records LIMIT 10;",
    )
    monkeypatch.setattr("core.question_service.generate_sql", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("AI should not be called")))

    success, message, sql, error = service.process_question("show 10 records", GENERIC_KB, ai_backend="local")

    assert success is True
    assert sql == "SELECT record_id, record_name FROM alpha_records LIMIT 10;"
    assert service.get_last_query_context()["route_used"] == "deterministic_sql_required"


def test_simple_aggregate_query_returns_clean_not_implemented(monkeypatch):
    service = QuestionService()
    monkeypatch.setattr(
        "core.question_service.build_query_context",
        lambda *args, **kwargs: _context(
            ["alpha_records"],
            intent="total",
            metric="money",
            date_range={"start": "2026-06-01", "end_exclusive": "2026-07-01"},
        ),
    )
    monkeypatch.setattr(
        "core.question_service.generate_simple_sql",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("Rule-based generator should not be used for aggregate questions")),
    )
    monkeypatch.setattr(
        "core.question_service.generate_sql",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("Runtime AI SQL generation must remain disabled")),
    )

    success, message, sql, error = service.process_question("show total this month", GENERIC_KB, ai_backend="local")

    assert success is False
    _assert_complex_not_implemented(service, message, sql)


def test_complex_join_query_returns_clean_not_implemented(monkeypatch):
    service = QuestionService()
    monkeypatch.setattr(
        "core.question_service.build_query_context",
        lambda *args, **kwargs: _context(
            ["alpha_records", "beta_events"],
            intent="comparison",
            metric="money",
            dimension="owner",
            grouping=["owner"],
        ),
    )
    monkeypatch.setattr(
        "core.question_service.generate_simple_sql",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("Rule-based generator should not be used for multi-table grouped questions")),
    )
    monkeypatch.setattr(
        "core.question_service.generate_sql",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("Runtime AI SQL generation must remain disabled")),
    )

    success, message, sql, error = service.process_question("show totals by owner", GENERIC_KB, ai_backend="local")

    assert success is False
    _assert_complex_not_implemented(service, message, sql)


def test_top_5_clients_by_deal_value_returns_clean_not_implemented(monkeypatch):
    kb = {
        "clients": {
            "columns": [
                {"name": "client_id", "type": "INTEGER", "nullable": False, "semantic_type": "id"},
                {"name": "client_name", "type": "VARCHAR(100)", "nullable": False, "semantic_type": "name"},
            ],
            "primary_keys": ["client_id"],
            "foreign_keys": [],
            "relationships": [],
        },
        "deals": {
            "columns": [
                {"name": "deal_id", "type": "INTEGER", "nullable": False, "semantic_type": "id"},
                {"name": "client_id", "type": "INTEGER", "nullable": False, "semantic_type": "id"},
                {"name": "deal_value", "type": "DECIMAL(12,2)", "nullable": False, "semantic_type": "money"},
            ],
            "primary_keys": ["deal_id"],
            "foreign_keys": [{"column": "client_id", "referenced_table": "clients", "referenced_column": "client_id"}],
            "relationships": [],
        },
    }
    service = QuestionService()
    monkeypatch.setattr(
        "core.question_service.generate_sql",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("Runtime AI SQL generation must remain disabled")),
    )

    success, message, sql, error = service.process_question("top 5 clients by deal value", kb, ai_backend="local")

    assert success is False
    _assert_complex_not_implemented(service, message, sql)


def test_deal_value_by_client_returns_clean_not_implemented(monkeypatch):
    kb = {
        "clients": {
            "columns": [
                {"name": "client_id", "type": "INTEGER", "nullable": False, "semantic_type": "id"},
                {"name": "client_name", "type": "VARCHAR(100)", "nullable": False, "semantic_type": "name"},
            ],
            "primary_keys": ["client_id"],
            "foreign_keys": [],
            "relationships": [],
        },
        "deals": {
            "columns": [
                {"name": "deal_id", "type": "INTEGER", "nullable": False, "semantic_type": "id"},
                {"name": "client_id", "type": "INTEGER", "nullable": False, "semantic_type": "id"},
                {"name": "deal_value", "type": "DECIMAL(12,2)", "nullable": False, "semantic_type": "money"},
            ],
            "primary_keys": ["deal_id"],
            "foreign_keys": [{"column": "client_id", "referenced_table": "clients", "referenced_column": "client_id"}],
            "relationships": [],
        },
    }
    service = QuestionService()
    monkeypatch.setattr(
        "core.question_service.generate_sql",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("Runtime AI SQL generation must remain disabled")),
    )

    success, message, sql, error = service.process_question("deal value by client", kb, ai_backend="local")

    assert success is False
    _assert_complex_not_implemented(service, message, sql)


def test_inventory_by_storage_site_returns_clean_not_implemented(monkeypatch):
    kb = {
        "storage_sites": {
            "columns": [
                {"name": "storage_site_id", "type": "INTEGER", "nullable": False, "semantic_type": "id"},
                {"name": "site_name", "type": "VARCHAR(100)", "nullable": False, "semantic_type": "name"},
            ],
            "primary_keys": ["storage_site_id"],
            "foreign_keys": [],
            "relationships": [],
        },
        "inventory_positions": {
            "columns": [
                {"name": "position_id", "type": "INTEGER", "nullable": False, "semantic_type": "id"},
                {"name": "storage_site_id", "type": "INTEGER", "nullable": False, "semantic_type": "id"},
                {"name": "units_on_hand", "type": "DECIMAL(12,2)", "nullable": False, "semantic_type": "quantity"},
            ],
            "primary_keys": ["position_id"],
            "foreign_keys": [{"column": "storage_site_id", "referenced_table": "storage_sites", "referenced_column": "storage_site_id"}],
            "relationships": [],
        },
    }
    service = QuestionService()
    monkeypatch.setattr(
        "core.question_service.generate_sql",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("Runtime AI SQL generation must remain disabled")),
    )

    success, message, sql, error = service.process_question("inventory by storage site", kb, ai_backend="local")

    assert success is False
    _assert_complex_not_implemented(service, message, sql)


def test_billed_value_by_client_returns_clean_not_implemented(monkeypatch):
    kb = {
        "clients": {
            "columns": [
                {"name": "client_id", "type": "INTEGER", "nullable": False, "semantic_type": "id"},
                {"name": "client_name", "type": "VARCHAR(100)", "nullable": False, "semantic_type": "name"},
            ],
            "primary_keys": ["client_id"],
            "foreign_keys": [],
            "relationships": [],
        },
        "deals": {
            "columns": [
                {"name": "deal_id", "type": "INTEGER", "nullable": False, "semantic_type": "id"},
                {"name": "client_id", "type": "INTEGER", "nullable": False, "semantic_type": "id"},
            ],
            "primary_keys": ["deal_id"],
            "foreign_keys": [{"column": "client_id", "referenced_table": "clients", "referenced_column": "client_id"}],
            "relationships": [],
        },
        "billing_notes": {
            "columns": [
                {"name": "billing_note_id", "type": "INTEGER", "nullable": False, "semantic_type": "id"},
                {"name": "deal_id", "type": "INTEGER", "nullable": False, "semantic_type": "id"},
                {"name": "billed_value", "type": "DECIMAL(12,2)", "nullable": False, "semantic_type": "money"},
            ],
            "primary_keys": ["billing_note_id"],
            "foreign_keys": [{"column": "deal_id", "referenced_table": "deals", "referenced_column": "deal_id"}],
            "relationships": [],
        },
    }
    service = QuestionService()
    monkeypatch.setattr(
        "core.question_service.generate_sql",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("Runtime AI SQL generation must remain disabled")),
    )

    success, message, sql, error = service.process_question("billed value by client", kb, ai_backend="local")

    assert success is False
    _assert_complex_not_implemented(service, message, sql)


def test_pending_billed_amount_by_client_returns_clean_not_implemented_without_formula_invention(monkeypatch):
    kb = {
        "clients": {
            "columns": [
                {"name": "client_id", "type": "INTEGER", "nullable": False, "semantic_type": "id"},
                {"name": "client_name", "type": "VARCHAR(100)", "nullable": False, "semantic_type": "name"},
            ],
            "primary_keys": ["client_id"],
            "foreign_keys": [],
            "relationships": [],
        },
        "deals": {
            "columns": [
                {"name": "deal_id", "type": "INTEGER", "nullable": False, "semantic_type": "id"},
                {"name": "client_id", "type": "INTEGER", "nullable": False, "semantic_type": "id"},
            ],
            "primary_keys": ["deal_id"],
            "foreign_keys": [{"column": "client_id", "referenced_table": "clients", "referenced_column": "client_id"}],
            "relationships": [],
        },
        "billing_notes": {
            "columns": [
                {"name": "billing_note_id", "type": "INTEGER", "nullable": False, "semantic_type": "id"},
                {"name": "deal_id", "type": "INTEGER", "nullable": False, "semantic_type": "id"},
                {"name": "billed_value", "type": "DECIMAL(12,2)", "nullable": False, "semantic_type": "money"},
                {"name": "settled_value", "type": "DECIMAL(12,2)", "nullable": True, "semantic_type": "money"},
            ],
            "primary_keys": ["billing_note_id"],
            "foreign_keys": [{"column": "deal_id", "referenced_table": "deals", "referenced_column": "deal_id"}],
            "relationships": [],
        },
    }
    service = QuestionService()
    monkeypatch.setattr(
        "core.question_service.generate_sql",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("Runtime AI SQL generation must remain disabled")),
    )
    monkeypatch.setattr(
        "core.question_service.generate_sql_with_retry",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("Runtime AI retry must remain disabled")),
    )

    success, message, sql, error = service.process_question("pending billed amount by client", kb, ai_backend="local")

    assert success is False
    _assert_complex_not_implemented(service, message, sql)


def test_complex_pipeline_context_is_preserved_without_runtime_ai_generation(monkeypatch):
    service = QuestionService()
    pipeline_query_context = _context(
        ["alpha_records", "beta_events"],
        intent="comparison",
        metric="money",
        dimension="owner",
        grouping=["owner"],
    )
    possible_join_paths = [
        {
            "from_table": "alpha_records",
            "to_table": "beta_events",
            "path": [
                {
                    "from_table": "alpha_records",
                    "from_column": "owner_id",
                    "to_table": "beta_events",
                    "to_column": "owner_id",
                    "join_condition": "alpha_records.owner_id = beta_events.owner_id",
                }
            ],
            "length": 1,
        }
    ]
    pipeline_context = {
        "question": "show totals by owner",
        "normalized_question": "show totals by owner",
        "intent": {"intent_type": "comparison"},
        "retrieved_context": {
            "possible_join_paths": possible_join_paths,
            "measure_candidates": [{"table": "beta_events", "column": "event_total", "semantic_type": "money"}],
            "dimension_candidates": [{"table": "alpha_records", "column": "record_name", "semantic_type": "name"}],
            "filter_candidates": [],
            "formula_evidence": [],
            "retrieval_sources": ["kb_identifier", "vector"],
        },
        "query_context": {
            **pipeline_query_context,
            "measure_candidates": [{"table": "beta_events", "column": "event_total", "semantic_type": "money"}],
            "dimension_candidates": [{"table": "alpha_records", "column": "record_name", "semantic_type": "name"}],
            "filter_candidates": [],
            "join_paths": possible_join_paths,
            "complex_sql_plan": {
                "query_shape": "grouped_aggregation",
                "required_joins": ["alpha_records.owner_id = beta_events.owner_id"],
                "aggregation_type": "sum",
                "sql_skeleton_type": "grouped_aggregation",
            },
        },
        "plan": dict(pipeline_query_context["plan"]),
        "complex_sql_plan": {
            "query_shape": "grouped_aggregation",
            "required_joins": ["alpha_records.owner_id = beta_events.owner_id"],
            "aggregation_type": "sum",
            "sql_skeleton_type": "grouped_aggregation",
        },
        "formula_evidence": [],
        "evidence_sources": ["kb_identifier", "vector"],
    }

    monkeypatch.setattr("core.question_service.generate_simple_sql", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "core.question_service.build_query_context",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("pipeline context should be reused for this question")),
    )
    monkeypatch.setattr(
        "core.question_service.generate_sql",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("Runtime AI SQL generation must remain disabled")),
    )

    success, message, sql, error = service.process_question(
        "show totals by owner",
        GENERIC_KB,
        ai_backend="local",
        pipeline_context=pipeline_context,
    )

    assert success is False
    _assert_complex_not_implemented(service, message, sql)
    assert service.get_last_query_context()["measure_candidates"] == pipeline_context["retrieved_context"]["measure_candidates"]
    assert service.get_last_query_context()["dimension_candidates"] == pipeline_context["retrieved_context"]["dimension_candidates"]
    assert service.get_last_query_context()["join_paths"] == possible_join_paths


def test_kb_pipeline_does_not_import_sql_generation_modules():
    source = Path("kb_pipeline/database_service.py").read_text(encoding="utf-8")

    assert "from ai.sql_generator import" not in source
    assert "import ai.sql_generator" not in source


def test_legacy_erp_generator_remains_inert_and_unused():
    from ai.erp_query_generator import generate_erp_sql

    assert generate_erp_sql("show entries", {"generic_records": {"columns": []}}) is None

    active_runtime_sources = [
        Path("sql_pipeline/question_service.py").read_text(encoding="utf-8"),
        Path("query_pipeline/query_pipeline.py").read_text(encoding="utf-8"),
        Path("sql_pipeline/sql_generator.py").read_text(encoding="utf-8"),
    ]
    assert all("generate_erp_sql" not in source for source in active_runtime_sources)


def test_active_runtime_modules_do_not_reintroduce_retired_mapping_symbols():
    retired_symbols = [
        "_TABLE_ALIASES",
        "_BUSINESS_TERM_TABLE",
        "_try_pcsoft_business_sql",
    ]
    runtime_sources = [
        Path("sql_pipeline/simple_query_generator.py").read_text(encoding="utf-8"),
        Path("query_pipeline/query_planner.py").read_text(encoding="utf-8"),
        Path("kb_pipeline/business_glossary.py").read_text(encoding="utf-8"),
        Path("query_pipeline/question_normalizer.py").read_text(encoding="utf-8"),
    ]

    for symbol in retired_symbols:
        assert all(symbol not in source for source in runtime_sources)


def test_old_wrapper_paths_resolve_to_same_live_implementations():
    core_database_service = importlib.import_module("core.database_service")
    kb_database_service = importlib.import_module("kb_pipeline.database_service")
    db_connection = importlib.import_module("db.connection")
    kb_connection = importlib.import_module("kb_pipeline.connection")
    semantic_business_glossary = importlib.import_module("semantic.business_glossary")
    kb_business_glossary = importlib.import_module("kb_pipeline.business_glossary")
    semantic_relationship_graph = importlib.import_module("semantic.relationship_graph")
    kb_relationship_graph = importlib.import_module("kb_pipeline.relationship_graph")
    vector_store_module = importlib.import_module("vector_store")
    kb_embedding_service = importlib.import_module("kb_pipeline.vector.embedding_service")
    core_query_pipeline = importlib.import_module("core.query_pipeline")
    query_pipeline_module = importlib.import_module("query_pipeline.query_pipeline")
    ai_sql_generator = importlib.import_module("ai.sql_generator")
    sql_generator_module = importlib.import_module("sql_pipeline.sql_generator")
    utils_sql_validator = importlib.import_module("utils.sql_validator")
    sql_validator_module = importlib.import_module("sql_pipeline.sql_validator")
    conversation_memory = importlib.import_module("conversation.conversation_memory")
    query_conversation_memory = importlib.import_module("query_pipeline.conversation.conversation_memory")
    core_question_service = importlib.import_module("core.question_service")
    sql_question_service = importlib.import_module("sql_pipeline.question_service")

    assert core_database_service.DatabaseService is kb_database_service.DatabaseService
    assert db_connection.connect_engine is kb_connection.connect_engine
    assert semantic_business_glossary.generate_business_glossary is kb_business_glossary.generate_business_glossary
    assert semantic_relationship_graph.build_relationship_graph is kb_relationship_graph.build_relationship_graph
    assert vector_store_module.EmbeddingService is kb_embedding_service.EmbeddingService
    assert core_query_pipeline is query_pipeline_module
    assert ai_sql_generator is sql_generator_module
    assert utils_sql_validator is sql_validator_module
    assert conversation_memory is query_conversation_memory
    assert core_question_service is sql_question_service


def test_missing_formula_evidence_returns_clean_complex_failure(monkeypatch):
    knowledge_base = {
        "group_entities": {
            "columns": [
                {"name": "entity_id", "type": "INTEGER", "nullable": False, "semantic_type": "id"},
                {"name": "entity_label", "type": "VARCHAR(100)", "nullable": False, "semantic_type": "name"},
            ],
            "primary_keys": ["entity_id"],
            "foreign_keys": [],
            "relationships": [],
        },
        "link_rows": {
            "columns": [
                {"name": "entity_id", "type": "INTEGER", "nullable": False, "semantic_type": "id"},
                {"name": "measure_id", "type": "INTEGER", "nullable": False, "semantic_type": "id"},
            ],
            "primary_keys": [],
            "foreign_keys": [
                {"column": "entity_id", "referenced_table": "group_entities", "referenced_column": "entity_id"},
                {"column": "measure_id", "referenced_table": "measure_rows", "referenced_column": "measure_id"},
            ],
            "relationships": [],
        },
        "measure_rows": {
            "columns": [
                {"name": "measure_id", "type": "INTEGER", "nullable": False, "semantic_type": "id"},
                {"name": "gross_value", "type": "DECIMAL(12,2)", "nullable": True, "semantic_type": "money"},
                {"name": "settled_value", "type": "DECIMAL(12,2)", "nullable": True, "semantic_type": "money"},
                {"name": "state_flag", "type": "VARCHAR(30)", "nullable": True, "semantic_type": "status", "sample_values": ["open", "closed"]},
            ],
            "primary_keys": ["measure_id"],
            "foreign_keys": [],
            "relationships": [],
        },
    }
    service = QuestionService()
    monkeypatch.setattr(
        "core.question_service.generate_sql",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("Runtime AI SQL generation must remain disabled")),
    )
    monkeypatch.setattr("core.question_service.generate_simple_sql", lambda *args, **kwargs: None)

    success, message, sql, error = service.process_question("show open total by entity", knowledge_base, ai_backend="local")

    assert success is False
    _assert_complex_not_implemented(service, message, sql)


def test_generated_rule_based_sql_is_validated_before_return(monkeypatch):
    service = QuestionService()
    monkeypatch.setattr("core.question_service.build_query_context", lambda *args, **kwargs: _context(["alpha_records"], intent="list"))
    monkeypatch.setattr("core.question_service.generate_simple_sql", lambda *args, **kwargs: "SELECT record_name FROM LIMIT 50")
    monkeypatch.setattr(
        "core.question_service.generate_sql",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("Runtime AI fallback must remain disabled")),
    )

    success, message, sql, error = service.process_question("show records", GENERIC_KB, ai_backend="local")

    assert success is False
    assert sql is None
    assert "sql validation failed" in message.lower()
    assert service.get_last_query_context().get("route_used") != "ai"


def test_complex_query_does_not_attempt_runtime_ai_or_limit_postprocessing(monkeypatch):
    service = QuestionService()
    monkeypatch.setattr(
        "core.question_service.build_query_context",
        lambda *args, **kwargs: _context(
            ["alpha_records", "beta_events"],
            intent="comparison",
            metric="money",
            dimension="owner",
            grouping=["owner"],
        ),
    )
    monkeypatch.setattr("core.question_service.generate_simple_sql", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "core.question_service.generate_sql",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("Runtime AI SQL generation must remain disabled")),
    )

    success, message, sql, error = service.process_question("tell me totals by owner", GENERIC_KB, ai_backend="local")

    assert success is False
    _assert_complex_not_implemented(service, message, sql)


def test_top_n_query_returns_clean_complex_failure_without_sql(monkeypatch):
    service = QuestionService()
    monkeypatch.setattr(
        "core.question_service.build_query_context",
        lambda *args, **kwargs: _context(
            ["alpha_records", "beta_events"],
            intent="comparison",
            metric="money",
            dimension="owner",
            grouping=["owner"],
        ),
    )
    monkeypatch.setattr("core.question_service.generate_simple_sql", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "core.question_service.generate_sql",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("Runtime AI SQL generation must remain disabled")),
    )

    success, message, sql, error = service.process_question("show top 10 totals by owner", GENERIC_KB, ai_backend="local")

    assert success is False
    _assert_complex_not_implemented(service, message, sql)


def test_complex_query_does_not_attempt_runtime_ai_retry(monkeypatch):
    service = QuestionService()
    monkeypatch.setattr(
        "core.question_service.build_query_context",
        lambda *args, **kwargs: _context(
            ["alpha_records", "beta_events"],
            intent="comparison",
            metric="money",
            dimension="owner",
            grouping=["owner"],
        ),
    )
    monkeypatch.setattr("core.question_service.generate_simple_sql", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "core.question_service.generate_sql",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("Runtime AI SQL generation must remain disabled")),
    )
    monkeypatch.setattr(
        "core.question_service.generate_sql_with_retry",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("Runtime AI retry must remain disabled")),
    )

    success, message, sql, error = service.process_question("tell me totals by owner", GENERIC_KB, ai_backend="local")

    assert success is False
    _assert_complex_not_implemented(service, message, sql)


def test_query_planner_does_not_assign_business_specific_intents():
    knowledge_base = {
        "alpha_records": {
            "columns": [
                {"name": "record_id", "type": "INTEGER", "nullable": False, "semantic_type": "id"},
                {"name": "status_flag", "type": "VARCHAR(30)", "nullable": True, "semantic_type": "status", "sample_values": ["Pending", "Closed"]},
                {"name": "amount_total", "type": "DECIMAL(12,2)", "nullable": True, "semantic_type": "money"},
            ],
            "primary_keys": ["record_id"],
            "foreign_keys": [],
            "relationships": [],
        },
        "beta_locations": {
            "columns": [
                {"name": "location_name", "type": "VARCHAR(50)", "nullable": False, "semantic_type": "name"},
            ],
            "primary_keys": ["location_name"],
            "foreign_keys": [],
            "relationships": [],
        },
    }

    first = build_query_context("show pending records", knowledge_base, use_vector_retrieval=False)
    second = build_query_context("show current stock by warehouse", knowledge_base, use_vector_retrieval=False)

    assert first["plan"]["intent"] == "list"
    assert first["plan"]["filters"]
    assert second["plan"]["intent"] == "list"
    assert second["plan"]["dimension"] == "warehouse"


def test_simple_list_query_with_glossary_noise_selects_one_primary_table():
    context = build_query_context(
        "show all alpha records",
        NOISY_KB,
        NOISY_GLOSSARY,
        use_vector_retrieval=False,
    )

    assert context["selected_table_names"] == ["alpha_records"]
    assert len(context["selected_tables"]) == 1
    assert context["selected_tables"][0]["table"] == "alpha_records"


def test_simple_list_query_does_not_expand_bridge_tables():
    context = build_query_context(
        "show all alpha records",
        NOISY_KB,
        NOISY_GLOSSARY,
        use_vector_retrieval=False,
    )

    assert "beta_events" not in context["selected_table_names"]


def test_simple_list_query_routes_rule_based_despite_glossary_noise(monkeypatch):
    service = QuestionService()
    monkeypatch.setattr(
        "core.question_service.generate_sql",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("AI should not be called for simple browse questions")),
    )

    success, message, sql, error = service.process_question(
        "show all alpha records",
        NOISY_KB,
        business_glossary=NOISY_GLOSSARY,
        ai_backend="local",
    )

    assert success is True
    assert sql == "SELECT record_id, record_name FROM alpha_records LIMIT 50;"
    assert service.get_last_query_context()["route_used"] == "deterministic_sql_required"


def test_build_query_context_keeps_plain_table_browse_metric_free():
    glossary = {
        "accounts": {
            "mapped_columns": [
                {"table": "accounts", "column": "account_label", "confidence": "high"},
                {"table": "accounts", "column": "allowed_credit", "confidence": "high"},
            ],
            "business_terms": ["account"],
        }
    }
    kb = {
        "accounts": {
            "columns": [
                {"name": "account_id", "type": "INTEGER", "nullable": False, "semantic_type": "id"},
                {"name": "account_label", "type": "VARCHAR(100)", "nullable": False, "semantic_type": "name"},
                {"name": "allowed_credit", "type": "DECIMAL(12,2)", "nullable": True, "semantic_type": "money"},
            ],
            "primary_keys": ["account_id"],
            "foreign_keys": [],
        }
    }

    context = build_query_context("show all accounts", kb, glossary, use_vector_retrieval=False)

    assert context["plan"]["intent"] == "list"
    assert context["plan"]["metric"] is None


def test_build_query_context_detects_generic_sample_value_filter_and_explicit_year():
    kb = {
        "accounts": {
            "columns": [
                {"name": "account_id", "type": "INTEGER", "nullable": False, "semantic_type": "id"},
                {
                    "name": "town",
                    "type": "VARCHAR(50)",
                    "nullable": True,
                    "semantic_type": "name",
                    "sample_values": ["Mumbai", "Chennai"],
                },
                {"name": "record_state", "type": "VARCHAR(20)", "nullable": True, "semantic_type": "status"},
            ],
            "primary_keys": ["account_id"],
            "foreign_keys": [],
        },
        "deals": {
            "columns": [
                {"name": "deal_id", "type": "INTEGER", "nullable": False, "semantic_type": "id"},
                {"name": "booked_on", "type": "DATE", "nullable": True, "semantic_type": "date"},
            ],
            "primary_keys": ["deal_id"],
            "foreign_keys": [],
        },
    }

    accounts_context = build_query_context("tell me accounts from Mumbai", kb, use_vector_retrieval=False)
    deals_context = build_query_context("show deals in 2025", kb, use_vector_retrieval=False)

    assert accounts_context["plan"]["filters"]
    assert accounts_context["plan"]["filters"][0]["column"] == "town"
    assert accounts_context["plan"]["filters"][0]["value"] == "Mumbai"
    assert deals_context["plan"]["date_range"] == {
        "label": "year_2025",
        "start": "2025-01-01",
        "end_exclusive": "2026-01-01",
    }


def test_build_query_context_uses_sorting_not_grouping_for_sorted_by_queries():
    kb = {
        "items": {
            "columns": [
                {"name": "item_id", "type": "INTEGER", "nullable": False, "semantic_type": "id"},
                {"name": "item_label", "type": "VARCHAR(100)", "nullable": False, "semantic_type": "name"},
                {"name": "sell_rate", "type": "DECIMAL(12,2)", "nullable": False, "semantic_type": "percentage"},
            ],
            "primary_keys": ["item_id"],
            "foreign_keys": [],
        }
    }

    context = build_query_context("show items sorted by sell rate", kb, use_vector_retrieval=False)

    assert context["plan"]["intent"] == "list"
    assert context["plan"]["dimension"] is None
    assert context["plan"]["grouping"] == []
    assert context["plan"]["sorting"] == {"direction": "asc", "by": "sell rate"}


def test_build_query_context_treats_plain_top_n_browse_as_limited_list():
    kb = {
        "accounts": {
            "columns": [
                {"name": "account_id", "type": "INTEGER", "nullable": False, "semantic_type": "id"},
                {"name": "account_label", "type": "VARCHAR(100)", "nullable": False, "semantic_type": "name"},
            ],
            "primary_keys": ["account_id"],
            "foreign_keys": [],
        }
    }

    context = build_query_context("show top 5 accounts", kb, use_vector_retrieval=False)

    assert context["plan"]["intent"] == "list"
    assert context["plan"]["limit"] == 5
    assert context["plan"]["filters"] == []


def test_simple_sample_value_filter_fails_cleanly_without_runtime_ai(monkeypatch):
    kb = {
        "accounts": {
            "columns": [
                {"name": "account_id", "type": "INTEGER", "nullable": False, "semantic_type": "id"},
                {"name": "account_label", "type": "VARCHAR(100)", "nullable": False, "semantic_type": "name"},
                {
                    "name": "town",
                    "type": "VARCHAR(50)",
                    "nullable": True,
                    "semantic_type": "name",
                    "sample_values": ["Mumbai", "Chennai"],
                },
            ],
            "primary_keys": ["account_id"],
            "foreign_keys": [],
            "relationships": [],
        },
        "deals": {
            "columns": [
                {"name": "deal_id", "type": "INTEGER", "nullable": False, "semantic_type": "id"},
                {"name": "account_id", "type": "INTEGER", "nullable": False, "semantic_type": "id"},
                {"name": "gross_value", "type": "DECIMAL(12,2)", "nullable": True, "semantic_type": "money"},
            ],
            "primary_keys": ["deal_id"],
            "foreign_keys": [{"column": "account_id", "referenced_table": "accounts", "referenced_column": "account_id"}],
            "relationships": [],
        },
    }
    service = QuestionService()

    monkeypatch.setattr(
        "core.question_service.generate_sql",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("AI should not be called for simple single-table sample-value filters")),
    )

    success, message, sql, error = service.process_question(
        "tell me accounts from Mumbai",
        kb,
        ai_backend="local",
    )

    assert success is False
    assert sql is None
    assert (
        message
        == "This query was understood, but deterministic SQL generation for this query shape is not implemented yet: filtered_query."
    )
    assert service.get_last_query_context()["selected_table_names"][0] == "accounts"


def test_top_n_browse_currently_uses_rule_based_generator(monkeypatch):
    kb = {
        "accounts": {
            "columns": [
                {"name": "account_id", "type": "INTEGER", "nullable": False, "semantic_type": "id"},
                {"name": "account_label", "type": "VARCHAR(100)", "nullable": False, "semantic_type": "name"},
            ],
            "primary_keys": ["account_id"],
            "foreign_keys": [],
            "relationships": [],
        }
    }
    service = QuestionService()

    monkeypatch.setattr(
        "core.question_service.generate_simple_sql",
        lambda *args, **kwargs: "SELECT account_id, account_label FROM accounts LIMIT 5;",
    )
    monkeypatch.setattr(
        "core.question_service.generate_sql",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("Runtime AI SQL generation must remain disabled")),
    )

    success, message, sql, error = service.process_question("show top 5 accounts", kb, ai_backend="local")

    assert success is True
    assert sql == "SELECT account_id, account_label FROM accounts LIMIT 5;"
    assert service.get_last_query_context()["route_used"] == "deterministic_sql_required"


def test_single_table_total_without_safe_selected_metric_returns_clarification(monkeypatch):
    kb = {
        "operating_costs": {
            "columns": [
                {"name": "cost_id", "type": "INTEGER", "nullable": False, "semantic_type": "money"},
                {"name": "cost_type", "type": "VARCHAR(50)", "nullable": False, "semantic_type": "money"},
                {"name": "spent_value", "type": "DECIMAL(12,2)", "nullable": False, "semantic_type": "money"},
            ],
            "primary_keys": ["cost_id"],
            "foreign_keys": [],
            "relationships": [],
        }
    }
    service = QuestionService()

    monkeypatch.setattr(
        "core.question_service.generate_simple_sql",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("Rule-based generator should not be used for aggregate questions")),
    )
    monkeypatch.setattr(
        "core.question_service.generate_sql",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("Runtime AI SQL generation must remain disabled")),
    )

    success, message, sql, error = service.process_question("show total operating cost", kb, ai_backend="local")

    assert success is False
    assert sql is None
    assert "cannot choose metric safely" in message.lower()
    assert service.get_last_query_context()["route_used"] == "cannot_plan_safely"


def test_top_vector_table_beats_generic_glossary_aliases_for_simple_list():
    context = build_query_context(
        "show all primary entries",
        NOISY_KB,
        NOISY_GLOSSARY,
        vector_retriever=_DummyVectorRetriever("alpha_records"),
    )

    assert context["selected_table_names"] == ["alpha_records"]
    assert context["selected_tables"][0]["table"] == "alpha_records"


def test_simple_alias_query_collapses_to_single_primary_table_despite_noisy_vector_context():
    knowledge_base = {
        "customer_registry": {
            "columns": [
                {"name": "customer_id", "type": "INTEGER", "nullable": False, "semantic_type": "id"},
                {"name": "customer_name", "type": "VARCHAR(100)", "nullable": False, "semantic_type": "name"},
            ],
            "primary_keys": ["customer_id"],
            "foreign_keys": [],
            "relationships": [],
        },
        "product_catalog": {
            "columns": [
                {"name": "product_id", "type": "INTEGER", "nullable": False, "semantic_type": "id"},
                {"name": "product_name", "type": "VARCHAR(100)", "nullable": False, "semantic_type": "name"},
            ],
            "primary_keys": ["product_id"],
            "foreign_keys": [],
            "relationships": [],
        },
        "purchase_orders": {
            "columns": [
                {"name": "purchase_id", "type": "INTEGER", "nullable": False, "semantic_type": "id"},
                {"name": "purchase_code", "type": "VARCHAR(50)", "nullable": False, "semantic_type": "code"},
            ],
            "primary_keys": ["purchase_id"],
            "foreign_keys": [],
            "relationships": [],
        },
    }
    glossary = {
        "customer": {
            "description": "Customer records",
            "mapped_columns": [{"table": "customer_registry", "column": "customer_name", "confidence": "high"}],
            "business_terms": ["client"],
            "example_questions": ["show all client"],
        }
    }
    retriever = _NoisyAliasVectorRetriever(
        ["customer_registry", "product_catalog", "purchase_orders"],
        ranked_columns=[
            {"table_name": "customer_registry", "column_name": "customer_name", "semantic_type": "name"},
            {"table_name": "product_catalog", "column_name": "product_name", "semantic_type": "name"},
            {"table_name": "purchase_orders", "column_name": "purchase_code", "semantic_type": "code"},
        ],
    )

    context = build_query_context(
        "show all client",
        knowledge_base,
        glossary,
        vector_retriever=retriever,
    )

    assert context["selected_table_names"] == ["customer_registry"]
    assert context["selected_tables"][0]["table"] == "customer_registry"


def test_show_all_client_routes_rule_based_without_ai(monkeypatch):
    knowledge_base = {
        "customer_registry": {
            "columns": [
                {"name": "customer_id", "type": "INTEGER", "nullable": False, "semantic_type": "id"},
                {"name": "customer_name", "type": "VARCHAR(100)", "nullable": False, "semantic_type": "name"},
            ],
            "primary_keys": ["customer_id"],
            "foreign_keys": [],
            "relationships": [],
        },
        "product_catalog": {
            "columns": [
                {"name": "product_id", "type": "INTEGER", "nullable": False, "semantic_type": "id"},
                {"name": "product_name", "type": "VARCHAR(100)", "nullable": False, "semantic_type": "name"},
            ],
            "primary_keys": ["product_id"],
            "foreign_keys": [],
            "relationships": [],
        },
    }
    glossary = {
        "customer": {
            "description": "Customer records",
            "mapped_columns": [{"table": "customer_registry", "column": "customer_name", "confidence": "high"}],
            "business_terms": ["client"],
            "example_questions": ["show all client"],
        }
    }
    retriever = _NoisyAliasVectorRetriever(
        ["customer_registry", "product_catalog"],
        ranked_columns=[
            {"table_name": "customer_registry", "column_name": "customer_name", "semantic_type": "name"},
            {"table_name": "product_catalog", "column_name": "product_name", "semantic_type": "name"},
        ],
    )
    service = QuestionService()

    monkeypatch.setattr(
        "core.question_service.generate_sql",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("AI should not be called for clear simple alias browse questions")),
    )

    success, message, sql, error = service.process_question(
        "show all client",
        knowledge_base,
        business_glossary=glossary,
        vector_retriever=retriever,
        ai_backend="local",
    )

    assert success is True
    assert sql == "SELECT customer_id, customer_name FROM customer_registry LIMIT 50;"
    assert service.get_last_query_context()["route_used"] == "deterministic_sql_required"
    assert service.get_last_query_context()["selected_table_names"] == ["customer_registry"]


def test_ambiguous_simple_table_match_returns_clean_clarification(monkeypatch):
    knowledge_base = {
        "alpha_records": {
            "columns": [
                {"name": "record_id", "type": "INTEGER", "nullable": False, "semantic_type": "id"},
                {"name": "record_name", "type": "VARCHAR(100)", "nullable": False, "semantic_type": "name"},
            ],
            "primary_keys": ["record_id"],
            "foreign_keys": [],
            "relationships": [],
        },
        "beta_records": {
            "columns": [
                {"name": "record_id", "type": "INTEGER", "nullable": False, "semantic_type": "id"},
                {"name": "record_name", "type": "VARCHAR(100)", "nullable": False, "semantic_type": "name"},
            ],
            "primary_keys": ["record_id"],
            "foreign_keys": [],
            "relationships": [],
        },
    }
    retriever = _NoisyAliasVectorRetriever(
        ["alpha_records", "beta_records"],
        ranked_columns=[
            {"table_name": "alpha_records", "column_name": "record_name", "semantic_type": "name"},
            {"table_name": "beta_records", "column_name": "record_name", "semantic_type": "name"},
        ],
    )
    service = QuestionService()

    monkeypatch.setattr(
        "core.question_service.generate_sql",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("AI should not be called for ambiguous simple browse questions")),
    )

    success, message, sql, error = service.process_question(
        "show all records",
        knowledge_base,
        vector_retriever=retriever,
        ai_backend="local",
    )

    assert success is False
    assert sql is None
    assert message
    assert service.get_last_query_context()["selected_table_names"] == ["alpha_records", "beta_records"]


BRIDGE_KB = {
    "entity_groups": {
        "columns": [
            {"name": "entity_id", "type": "INTEGER", "nullable": False, "semantic_type": "id"},
            {"name": "display_label", "type": "VARCHAR(100)", "nullable": False, "semantic_type": "name"},
        ],
        "primary_keys": ["entity_id"],
        "foreign_keys": [],
        "relationships": [],
    },
    "link_records": {
        "columns": [
            {"name": "link_id", "type": "INTEGER", "nullable": False, "semantic_type": "id"},
            {"name": "entity_id", "type": "INTEGER", "nullable": False, "semantic_type": "id"},
            {"name": "event_id", "type": "INTEGER", "nullable": False, "semantic_type": "id"},
        ],
        "primary_keys": ["link_id"],
        "foreign_keys": [
            {"column": "entity_id", "referenced_table": "entity_groups", "referenced_column": "entity_id"},
            {"column": "event_id", "referenced_table": "measure_events", "referenced_column": "event_id"},
        ],
        "relationships": [],
    },
    "measure_events": {
        "columns": [
            {"name": "event_id", "type": "INTEGER", "nullable": False, "semantic_type": "id"},
            {"name": "amount_value", "type": "DECIMAL(12,2)", "nullable": True, "semantic_type": "money"},
            {"name": "state_flag", "type": "VARCHAR(30)", "nullable": True, "semantic_type": "status", "sample_values": ["open", "closed"]},
        ],
        "primary_keys": ["event_id"],
        "foreign_keys": [],
        "relationships": [],
    },
}


def test_complex_grouped_amount_query_promotes_bridge_table_into_ai_context():
    context = build_query_context("show open amount by entity", BRIDGE_KB, use_vector_retrieval=False)

    assert "entity_groups" in context["selected_table_names"]
    assert "measure_events" in context["selected_table_names"]
    assert "link_records" in context["selected_table_names"]
    assert set(context["selected_knowledge_base"]) >= {"entity_groups", "link_records", "measure_events"}

    bridge_entry = next(entry for entry in context["selected_tables"] if entry["table"] == "link_records")
    bridge_columns = {column["column"] for column in bridge_entry["selected_columns"]}
    assert {"entity_id", "event_id"} <= bridge_columns

    display_entry = next(entry for entry in context["selected_tables"] if entry["table"] == "entity_groups")
    assert display_entry["selected_columns"][0]["column"] == "display_label"

    join_conditions = [
        edge["join_condition"]
        for join_path in context["join_paths"]
        for edge in join_path["path"]
    ]
    assert (
        "entity_groups.entity_id = link_records.entity_id" in join_conditions
        or "link_records.entity_id = entity_groups.entity_id" in join_conditions
    )
    assert (
        "link_records.event_id = measure_events.event_id" in join_conditions
        or "measure_events.event_id = link_records.event_id" in join_conditions
    )


def test_complex_grouped_amount_query_returns_clean_complex_failure(monkeypatch):
    service = QuestionService()
    monkeypatch.setattr(
        "core.question_service.generate_sql",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("Runtime AI SQL generation must remain disabled")),
    )
    monkeypatch.setattr(
        "core.question_service.generate_sql_with_retry",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("Runtime AI retry must remain disabled")),
    )

    success, message, sql, error = service.process_question("show open amount by entity", BRIDGE_KB, ai_backend="local")

    assert success is False
    _assert_complex_not_implemented(service, message, sql)


def test_grouped_amount_repair_uses_metric_table_not_dimension_money_column(monkeypatch):
    knowledge_base = {
        "entity_groups": {
            "columns": [
                {"name": "entity_id", "type": "INTEGER", "nullable": False, "semantic_type": "id"},
                {"name": "display_label", "type": "VARCHAR(100)", "nullable": False, "semantic_type": "name"},
                {"name": "allowed_value", "type": "DECIMAL(12,2)", "nullable": True, "semantic_type": "money"},
            ],
            "primary_keys": ["entity_id"],
            "foreign_keys": [],
            "relationships": [],
        },
        "link_records": BRIDGE_KB["link_records"],
        "measure_events": {
            "columns": [
                {"name": "event_id", "type": "INTEGER", "nullable": False, "semantic_type": "id"},
                {"name": "billed_value", "type": "DECIMAL(12,2)", "nullable": True, "semantic_type": "money"},
                {"name": "settled_value", "type": "DECIMAL(12,2)", "nullable": True, "semantic_type": "money"},
                {
                    "name": "state_flag",
                    "type": "VARCHAR(30)",
                    "nullable": True,
                    "semantic_type": "status",
                    "sample_values": ["open", "closed"],
                },
            ],
            "primary_keys": ["event_id"],
            "foreign_keys": [],
            "relationships": [],
        },
    }
    service = QuestionService()
    monkeypatch.setattr(
        "core.question_service.generate_sql",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("Runtime AI SQL generation must remain disabled")),
    )
    monkeypatch.setattr(
        "core.question_service.generate_sql_with_retry",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("Runtime AI retry must remain disabled")),
    )

    success, message, sql, error = service.process_question("show open billed amount by entity", knowledge_base, ai_backend="local")

    assert success is False
    _assert_complex_not_implemented(service, message, sql)


def test_pending_billed_amount_by_account_uses_repair_before_retry(monkeypatch, caplog):
    knowledge_base = {
        "accounts": {
            "columns": [
                {"name": "account_id", "type": "INTEGER", "nullable": False, "semantic_type": "id"},
                {"name": "account_label", "type": "VARCHAR(100)", "nullable": False, "semantic_type": "name"},
                {"name": "allowed_credit", "type": "DECIMAL(12,2)", "nullable": True, "semantic_type": "money"},
            ],
            "primary_keys": ["account_id"],
            "foreign_keys": [],
            "relationships": [],
        },
        "deals": {
            "columns": [
                {"name": "deal_id", "type": "INTEGER", "nullable": False, "semantic_type": "id"},
                {"name": "account_id", "type": "INTEGER", "nullable": False, "semantic_type": "id"},
            ],
            "primary_keys": ["deal_id"],
            "foreign_keys": [
                {"column": "account_id", "referenced_table": "accounts", "referenced_column": "account_id"},
            ],
            "relationships": [],
        },
        "billing_notes": {
            "columns": [
                {"name": "billing_note_id", "type": "INTEGER", "nullable": False, "semantic_type": "id"},
                {"name": "deal_id", "type": "INTEGER", "nullable": False, "semantic_type": "id"},
                {"name": "billed_value", "type": "DECIMAL(12,2)", "nullable": True, "semantic_type": "money"},
                {"name": "settled_value", "type": "DECIMAL(12,2)", "nullable": True, "semantic_type": "money"},
                {
                    "name": "settlement_state",
                    "type": "VARCHAR(30)",
                    "nullable": True,
                    "semantic_type": "status",
                    "sample_values": ["pending", "settled"],
                },
            ],
            "primary_keys": ["billing_note_id"],
            "foreign_keys": [
                {"column": "deal_id", "referenced_table": "deals", "referenced_column": "deal_id"},
            ],
            "relationships": [],
        },
    }
    service = QuestionService()
    monkeypatch.setattr(
        "core.question_service.generate_sql",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("Runtime AI SQL generation must remain disabled")),
    )
    monkeypatch.setattr(
        "core.question_service.generate_sql_with_retry",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("Runtime AI retry must remain disabled")),
    )
    monkeypatch.setattr("core.question_service.generate_simple_sql", lambda *args, **kwargs: None)

    success, message, sql, error = service.process_question("show pending billed amount by account", knowledge_base, ai_backend="local")

    assert success is False
    _assert_complex_not_implemented(service, message, sql)
    assert "AI retry did not meet validation requirements" not in caplog.text


def test_pending_billed_amount_repair_uses_dynamic_formula_evidence_when_present(monkeypatch):
    knowledge_base = {
        "accounts": {
            "columns": [
                {"name": "account_id", "type": "INTEGER", "nullable": False, "semantic_type": "id"},
                {"name": "account_label", "type": "VARCHAR(100)", "nullable": False, "semantic_type": "name"},
            ],
            "primary_keys": ["account_id"],
            "foreign_keys": [],
            "relationships": [],
        },
        "deals": {
            "columns": [
                {"name": "deal_id", "type": "INTEGER", "nullable": False, "semantic_type": "id"},
                {"name": "account_id", "type": "INTEGER", "nullable": False, "semantic_type": "id"},
            ],
            "primary_keys": ["deal_id"],
            "foreign_keys": [
                {"column": "account_id", "referenced_table": "accounts", "referenced_column": "account_id"},
            ],
            "relationships": [],
        },
        "billing_notes": {
            "columns": [
                {"name": "billing_note_id", "type": "INTEGER", "nullable": False, "semantic_type": "id"},
                {"name": "deal_id", "type": "INTEGER", "nullable": False, "semantic_type": "id"},
                {"name": "billed_value", "type": "DECIMAL(12,2)", "nullable": True, "semantic_type": "money"},
                {"name": "settled_value", "type": "DECIMAL(12,2)", "nullable": True, "semantic_type": "money"},
                {"name": "settlement_state", "type": "VARCHAR(30)", "nullable": True, "semantic_type": "status"},
            ],
            "primary_keys": ["billing_note_id"],
            "foreign_keys": [
                {"column": "deal_id", "referenced_table": "deals", "referenced_column": "deal_id"},
            ],
            "relationships": [],
        },
    }
    service = QuestionService()
    real_build_query_context = build_query_context

    def build_context_with_formula(*args, **kwargs):
        context = real_build_query_context(*args, **kwargs)
        context["formula_evidence"] = [
            {
                "table": "billing_notes",
                "column": "billed_value",
                "operation": "difference",
                "secondary_column": "settled_value",
                "alias": "pending_billed_amount",
                "source": "ai_semantic_metadata",
            }
        ]
        return context

    monkeypatch.setattr("core.question_service.build_query_context", build_context_with_formula)
    monkeypatch.setattr(
        "core.question_service.generate_sql",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("Runtime AI SQL generation must remain disabled")),
    )
    monkeypatch.setattr(
        "core.question_service.generate_sql_with_retry",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("Runtime AI retry must remain disabled")),
    )
    monkeypatch.setattr("core.question_service.generate_simple_sql", lambda *args, **kwargs: None)

    success, message, sql, error = service.process_question("show pending billed amount by account", knowledge_base, ai_backend="local")

    assert success is False
    _assert_complex_not_implemented(service, message, sql)
    assert service.get_last_query_context()["formula_evidence"]


def test_complex_grouped_amount_empty_from_fails_cleanly_without_join_path(monkeypatch):
    broken_kb = {
        "entity_groups": BRIDGE_KB["entity_groups"],
        "measure_events": BRIDGE_KB["measure_events"],
    }
    service = QuestionService()
    monkeypatch.setattr(
        "core.question_service.generate_sql",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("Runtime AI SQL generation must remain disabled")),
    )
    monkeypatch.setattr(
        "core.question_service.generate_sql_with_retry",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("Runtime AI retry must remain disabled")),
    )
    monkeypatch.setattr("core.question_service.generate_simple_sql", lambda *args, **kwargs: None)

    success, message, sql, error = service.process_question("show open amount by entity", broken_kb, ai_backend="local")

    assert success is False
    assert sql is None
    assert "could not be planned safely" in message.lower()
    assert service.get_last_query_context()["route_used"] == "cannot_plan_safely"
