"""Generic hybrid routing tests for rule-based vs AI SQL generation."""

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
    return {
        "plan": plan,
        "selected_tables": selected_tables,
        "selected_columns": selected_columns,
        "selected_table_names": list(selected_table_names),
        "selected_knowledge_base": selected_kb,
        "warnings": [],
        "confidence": confidence,
        "knowledge_base": GENERIC_KB,
        "vector_results": {"table_names": list(selected_table_names), "columns": [], "glossary_terms": [], "relationships": []},
        "vector_used": False,
    }


def test_simple_table_listing_uses_rule_based(monkeypatch):
    service = QuestionService()
    monkeypatch.setattr("core.question_service.build_query_context", lambda *args, **kwargs: _context(["alpha_records"], intent="list"))
    monkeypatch.setattr("core.question_service.generate_simple_sql", lambda *args, **kwargs: "SELECT * FROM alpha_records LIMIT 50;")

    def fail_ai(*args, **kwargs):
        raise AssertionError("AI should not be called for simple list questions")

    monkeypatch.setattr("core.question_service.generate_sql", fail_ai)

    success, message, sql, error = service.process_question("show records", GENERIC_KB, ai_backend="local")

    assert success is True
    assert sql == "SELECT * FROM alpha_records LIMIT 50;"
    assert service.get_last_query_context()["route_used"] == "rule-based"


def test_legacy_simple_generator_signature_still_uses_rule_based(monkeypatch):
    service = QuestionService()
    monkeypatch.setattr("core.question_service.build_query_context", lambda *args, **kwargs: _context(["alpha_records"], intent="list"))
    monkeypatch.setattr(
        "core.question_service.generate_simple_sql",
        lambda user_question, knowledge_base: "SELECT record_id, record_name FROM alpha_records LIMIT 50;",
    )
    monkeypatch.setattr(
        "core.question_service.generate_sql",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("AI should not be called")),
    )

    success, message, sql, error = service.process_question("show records", GENERIC_KB, ai_backend="local")

    assert success is True
    assert sql == "SELECT record_id, record_name FROM alpha_records LIMIT 50;"
    assert service.get_last_query_context()["route_used"] == "rule-based"


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
    assert service.get_last_query_context()["route_used"] == "rule-based"


def test_simple_limit_query_uses_rule_based(monkeypatch):
    service = QuestionService()
    context = _context(["alpha_records"], intent="list")
    context["plan"]["limit"] = 10
    monkeypatch.setattr("core.question_service.build_query_context", lambda *args, **kwargs: context)
    monkeypatch.setattr("core.question_service.generate_simple_sql", lambda *args, **kwargs: "SELECT * FROM alpha_records LIMIT 10;")
    monkeypatch.setattr("core.question_service.generate_sql", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("AI should not be called")))

    success, message, sql, error = service.process_question("show 10 records", GENERIC_KB, ai_backend="local")

    assert success is True
    assert sql == "SELECT * FROM alpha_records LIMIT 10;"
    assert service.get_last_query_context()["route_used"] == "rule-based"


def test_simple_aggregate_query_uses_rule_based(monkeypatch):
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
        lambda *args, **kwargs: (
            "SELECT SUM(amount_total) AS total_amount_total "
            "FROM alpha_records WHERE created_on >= '2026-06-01' AND created_on < '2026-07-01';"
        ),
    )
    monkeypatch.setattr("core.question_service.generate_sql", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("AI should not be called")))

    success, message, sql, error = service.process_question("show total this month", GENERIC_KB, ai_backend="local")

    assert success is True
    assert "SUM(amount_total)" in sql
    assert service.get_last_query_context()["route_used"] == "rule-based"


def test_low_confidence_simple_looking_query_goes_to_ai(monkeypatch):
    service = QuestionService()
    ai_called = {"value": False}
    monkeypatch.setattr("core.question_service.build_query_context", lambda *args, **kwargs: _context(["alpha_records"], intent="list", confidence=0.35))
    monkeypatch.setattr("core.question_service.generate_simple_sql", lambda *args, **kwargs: "SELECT * FROM alpha_records LIMIT 50;")

    def fake_generate_sql(*args, **kwargs):
        ai_called["value"] = True
        return "SELECT record_id, record_name FROM alpha_records LIMIT 50;"

    monkeypatch.setattr("core.question_service.generate_sql", fake_generate_sql)

    success, message, sql, error = service.process_question("show entries", GENERIC_KB, ai_backend="local")

    assert success is True
    assert ai_called["value"] is True
    assert service.get_last_query_context()["route_used"] == "ai"


def test_legacy_ai_generator_signature_still_works(monkeypatch):
    service = QuestionService()
    monkeypatch.setattr("core.question_service.build_query_context", lambda *args, **kwargs: _context(["alpha_records"], intent="list", confidence=0.35))
    monkeypatch.setattr("core.question_service.generate_simple_sql", lambda *args, **kwargs: "SELECT * FROM alpha_records LIMIT 50;")
    monkeypatch.setattr(
        "core.question_service.generate_sql",
        lambda user_question, knowledge_base: "SELECT record_id, record_name FROM alpha_records LIMIT 50;",
    )

    success, message, sql, error = service.process_question("show entries", GENERIC_KB, ai_backend="local")

    assert success is True
    assert sql == "SELECT record_id, record_name FROM alpha_records LIMIT 50;"
    assert service.get_last_query_context()["route_used"] == "ai"


def test_complex_join_query_goes_to_ai(monkeypatch):
    service = QuestionService()
    ai_called = {"value": False}
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
    monkeypatch.setattr("core.question_service.generate_simple_sql", lambda *args, **kwargs: "SELECT * FROM alpha_records LIMIT 50;")

    def fake_generate_sql(*args, **kwargs):
        ai_called["value"] = True
        return (
            "SELECT a.record_name, SUM(b.event_total) AS total_event_total "
            "FROM alpha_records a JOIN beta_events b ON a.owner_id = b.owner_id "
            "GROUP BY a.record_name LIMIT 50;"
        )

    monkeypatch.setattr("core.question_service.generate_sql", fake_generate_sql)

    success, message, sql, error = service.process_question("show totals by owner", GENERIC_KB, ai_backend="local")

    assert success is True
    assert ai_called["value"] is True
    assert "JOIN beta_events" in sql
    assert service.get_last_query_context()["route_used"] == "ai"


def test_generated_rule_based_sql_is_validated_before_return(monkeypatch):
    service = QuestionService()
    monkeypatch.setattr("core.question_service.build_query_context", lambda *args, **kwargs: _context(["alpha_records"], intent="list"))
    monkeypatch.setattr("core.question_service.generate_simple_sql", lambda *args, **kwargs: "SELECT record_name FROM LIMIT 50")
    monkeypatch.setattr(
        "core.question_service.generate_sql",
        lambda *args, **kwargs: "SELECT record_id, record_name FROM alpha_records LIMIT 50;",
    )

    success, message, sql, error = service.process_question("show records", GENERIC_KB, ai_backend="local")

    assert success is True
    assert sql == "SELECT record_id, record_name FROM alpha_records LIMIT 50;"
    assert "FROM LIMIT" not in sql
    assert service.get_last_query_context()["route_used"] == "ai"


def test_complex_ai_query_does_not_get_default_limit(monkeypatch):
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
        lambda *args, **kwargs: (
            "SELECT a.record_name, SUM(b.event_total) AS total_event_total "
            "FROM alpha_records a JOIN beta_events b ON a.owner_id = b.owner_id "
            "GROUP BY a.record_name"
        ),
    )

    success, message, sql, error = service.process_question("tell me totals by owner", GENERIC_KB, ai_backend="local")

    assert success is True
    assert "LIMIT" not in sql.upper()


def test_explicit_limit_is_added_only_when_requested(monkeypatch):
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
        lambda *args, **kwargs: (
            "SELECT a.record_name, SUM(b.event_total) AS total_event_total "
            "FROM alpha_records a JOIN beta_events b ON a.owner_id = b.owner_id "
            "GROUP BY a.record_name"
        ),
    )

    success, message, sql, error = service.process_question("show top 10 totals by owner", GENERIC_KB, ai_backend="local")

    assert success is True
    assert sql.endswith("LIMIT 10")


def test_invalid_ai_sql_does_not_receive_limit_before_retry(monkeypatch):
    service = QuestionService()
    captured = {}
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
    monkeypatch.setattr("core.question_service.generate_sql", lambda *args, **kwargs: "SELECT record_name FROM")

    def fake_retry(*args, **kwargs):
        captured["first_attempt_sql"] = kwargs["first_attempt_sql"]
        return (
            "SELECT a.record_name, SUM(b.event_total) AS total_event_total "
            "FROM alpha_records a JOIN beta_events b ON a.owner_id = b.owner_id "
            "GROUP BY a.record_name"
        )

    monkeypatch.setattr("core.question_service.generate_sql_with_retry", fake_retry)

    success, message, sql, error = service.process_question("tell me totals by owner", GENERIC_KB, ai_backend="local")

    assert success is True
    assert captured["first_attempt_sql"] == "SELECT record_name FROM"
    assert "FROM LIMIT" not in captured["first_attempt_sql"]


def test_legacy_ai_retry_signature_keeps_validation_context(monkeypatch):
    service = QuestionService()
    captured = {}
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
    monkeypatch.setattr("core.question_service.generate_sql", lambda *args, **kwargs: "SELECT record_name FROM")

    def fake_retry(
        user_question,
        knowledge_base,
        backend,
        first_attempt_sql,
        validation_reason,
        validation_context=None,
    ):
        captured["first_attempt_sql"] = first_attempt_sql
        captured["validation_context"] = validation_context
        return (
            "SELECT a.record_name, SUM(b.event_total) AS total_event_total "
            "FROM alpha_records a JOIN beta_events b ON a.owner_id = b.owner_id "
            "GROUP BY a.record_name"
        )

    monkeypatch.setattr("core.question_service.generate_sql_with_retry", fake_retry)

    success, message, sql, error = service.process_question("tell me totals by owner", GENERIC_KB, ai_backend="local")

    assert success is True
    assert captured["first_attempt_sql"] == "SELECT record_name FROM"
    assert captured["validation_context"] is not None
    assert captured["validation_context"]["selected_tables"]
    assert service.get_last_query_context()["route_used"] == "ai-retry"


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
    assert service.get_last_query_context()["route_used"] == "rule-based"


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
    assert service.get_last_query_context()["route_used"] == "rule-based"
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
    assert "Please specify one of" in message
