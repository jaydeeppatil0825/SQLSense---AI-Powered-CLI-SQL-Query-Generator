from core.context_retriever import retrieve_context


KB = {
    "accounts": {
        "business_description": "Stores account records used in deals.",
        "columns": [
            {
                "name": "account_id",
                "type": "INTEGER",
                "semantic_type": "id",
            },
            {
                "name": "account_label",
                "type": "VARCHAR(100)",
                "semantic_type": "name",
                "is_dimension": True,
                "business_description": "Display label or name for the account.",
                "business_terms": ["account label", "account name"],
            },
        ],
        "primary_keys": ["account_id"],
        "foreign_keys": [],
        "relationships": [],
    },
    "deals": {
        "business_description": "Stores deal records linked to accounts.",
        "columns": [
            {
                "name": "deal_id",
                "type": "INTEGER",
                "semantic_type": "id",
            },
            {
                "name": "account_id",
                "type": "INTEGER",
                "semantic_type": "id",
                "is_foreign_key": True,
            },
            {
                "name": "deal_value",
                "type": "DECIMAL(12,2)",
                "semantic_type": "money",
                "is_measure": True,
                "business_description": "Monetary value of the deal.",
                "business_terms": ["deal value", "deal amount"],
            },
        ],
        "primary_keys": ["deal_id"],
        "foreign_keys": [
            {"column": "account_id", "referenced_table": "accounts", "referenced_column": "account_id"},
        ],
        "relationships": [],
    },
    "billing_notes": {
        "business_description": "Stores billed and settled values for deals.",
        "columns": [
            {
                "name": "billing_note_id",
                "type": "INTEGER",
                "semantic_type": "id",
            },
            {
                "name": "deal_id",
                "type": "INTEGER",
                "semantic_type": "id",
                "is_foreign_key": True,
            },
            {
                "name": "billed_value",
                "type": "DECIMAL(12,2)",
                "semantic_type": "money",
                "is_measure": True,
                "business_description": "Billed value recorded for the note.",
                "business_terms": ["billed amount", "billed value"],
            },
            {
                "name": "settlement_state",
                "type": "VARCHAR(30)",
                "semantic_type": "status",
                "business_description": "Settlement status of the note.",
                "business_terms": ["settlement status", "pending billed amount"],
            },
        ],
        "primary_keys": ["billing_note_id"],
        "foreign_keys": [
            {"column": "deal_id", "referenced_table": "deals", "referenced_column": "deal_id"},
        ],
        "relationships": [],
    },
}

GLOSSARY = {
    "deal value": {
        "description": "Deal value metric",
        "mapped_columns": [{"table": "deals", "column": "deal_value", "confidence": "high"}],
        "business_terms": ["deal amount"],
        "sources": ["ai_semantic_metadata"],
    },
    "account": {
        "description": "Account records",
        "mapped_columns": [{"table": "accounts", "column": "account_label", "confidence": "high"}],
        "business_terms": ["account label", "account name"],
        "sources": ["schema_identifier"],
    },
    "pending billed amount": {
        "description": "Pending billed amount phrase preserved from semantic metadata.",
        "mapped_columns": [{"table": "billing_notes", "column": "settlement_state", "confidence": "medium"}],
        "business_terms": ["pending billed amount"],
        "sources": ["ai_semantic_metadata"],
    },
}


def test_context_retriever_matches_runtime_account_table_names():
    intent = {
        "requested_dimensions": [],
        "requested_metrics": [],
        "requested_filters": [],
        "raw_business_terms": ["accounts"],
    }

    context = retrieve_context("show all accounts", intent, KB, business_glossary=GLOSSARY, vector_retriever=None)

    assert context["matched_tables"]
    assert context["matched_tables"][0]["table"] == "accounts"
    assert context["matched_tables"][0]["score"] >= 0.9
    assert "runtime_table_name" in context["matched_tables"][0]["evidence_sources"]
    assert "kb_identifier" in context["retrieval_sources"]


def test_context_retriever_collapses_simple_direct_table_match_before_glossary_expansion():
    noisy_kb = {
        "partners": {
            "business_description": "Stores partner records.",
            "columns": [{"name": "partner_name", "type": "VARCHAR(100)", "semantic_type": "name", "is_dimension": True}],
            "primary_keys": ["partner_id"],
            "foreign_keys": [],
            "relationships": [],
        },
        "contracts": {
            "business_description": "Stores contract records linked to partners.",
            "columns": [{"name": "partner_id", "type": "INTEGER", "semantic_type": "id"}],
            "primary_keys": ["contract_id"],
            "foreign_keys": [{"column": "partner_id", "referenced_table": "partners", "referenced_column": "partner_id"}],
            "relationships": [],
        },
        "bills": {
            "business_description": "Stores bill records linked to partners.",
            "columns": [{"name": "partner_id", "type": "INTEGER", "semantic_type": "id"}],
            "primary_keys": ["bill_id"],
            "foreign_keys": [{"column": "partner_id", "referenced_table": "partners", "referenced_column": "partner_id"}],
            "relationships": [],
        },
    }
    noisy_glossary = {
        "partners": {
            "description": "Partner records",
            "mapped_columns": [
                {"table": "partners", "column": "partner_name", "confidence": "high"},
                {"table": "contracts", "column": "partner_id", "confidence": "medium"},
                {"table": "bills", "column": "partner_id", "confidence": "medium"},
            ],
            "business_terms": ["partner"],
            "sources": ["schema_identifier"],
        }
    }
    intent = {
        "intent_type": "list",
        "requested_dimensions": [],
        "requested_metrics": [],
        "requested_filters": [],
        "raw_business_terms": ["partners"],
        "needs_grouping": False,
        "needs_join": False,
    }

    context = retrieve_context("show all partners", intent, noisy_kb, business_glossary=noisy_glossary, vector_retriever=None)

    assert [entry["table"] for entry in context["matched_tables"]] == ["partners"]
    assert all(entry["table"] == "partners" for entry in context["matched_columns"])


def test_context_retriever_exact_runtime_column_names_score_high():
    intent = {
        "requested_dimensions": [],
        "requested_metrics": ["deal_value"],
        "requested_filters": [],
        "raw_business_terms": ["deal_value"],
    }

    context = retrieve_context("show deal_value", intent, KB, business_glossary=GLOSSARY, vector_retriever=None)

    assert context["matched_columns"]
    top_column = context["matched_columns"][0]
    assert top_column["table"] == "deals"
    assert top_column["column"] == "deal_value"
    assert top_column["score"] >= 0.9
    assert "runtime_column_name" in top_column["evidence_sources"]


def test_context_retriever_finds_metric_dimension_and_relationship_context():
    intent = {
        "requested_dimensions": ["accounts"],
        "requested_metrics": ["deal value"],
        "requested_filters": [],
        "raw_business_terms": ["accounts", "deal value"],
    }

    context = retrieve_context("top 5 accounts by deal value", intent, KB, business_glossary=GLOSSARY, vector_retriever=None)

    matched_table_names = [entry["table"] for entry in context["matched_tables"]]
    measure_columns = {(entry["table"], entry["column"]) for entry in context["measure_candidates"]}
    dimension_columns = {(entry["table"], entry["column"]) for entry in context["dimension_candidates"]}

    assert "accounts" in matched_table_names
    assert "deals" in matched_table_names
    assert ("deals", "deal_value") in measure_columns
    assert ("accounts", "account_label") in dimension_columns
    assert context["matched_relationships"]
    assert any("dynamic_glossary" in entry.get("evidence_sources", []) for entry in context["matched_glossary_terms"])
    assert any("fk_relationship_context" in entry.get("evidence_sources", []) for entry in context["matched_relationships"])
    assert any(path["from_table"] == "accounts" and path["to_table"] == "deals" for path in context["possible_join_paths"])


def test_context_retriever_preserves_pending_billed_amount_without_formula_invention():
    intent = {
        "requested_dimensions": ["account"],
        "requested_metrics": ["pending billed amount"],
        "requested_filters": [],
        "raw_business_terms": ["pending billed amount", "account"],
    }

    context = retrieve_context("pending billed amount by account", intent, KB, business_glossary=GLOSSARY, vector_retriever=None)

    assert "pending billed amount" in context["query_terms"]
    assert any(entry["term"] == "pending billed amount" for entry in context["matched_glossary_terms"])
    assert all("-" not in str(entry) for entry in context["measure_candidates"])
    assert context.get("formula_evidence") in (None, [])


def test_context_retriever_returns_fk_relationship_context_for_related_matches():
    intent = {
        "requested_dimensions": ["accounts"],
        "requested_metrics": ["deal value"],
        "requested_filters": [],
        "raw_business_terms": ["accounts", "deal value"],
    }

    context = retrieve_context("deal value by account", intent, KB, business_glossary=GLOSSARY, vector_retriever=None)

    relationships = context["matched_relationships"]
    assert relationships
    assert any(
        relationship["from_table"] == "deals"
        and relationship["to_table"] == "accounts"
        and relationship["from_column"] == "account_id"
        for relationship in relationships
    )
    assert any("matched_table_support" in relationship.get("evidence_sources", []) for relationship in relationships)


def test_context_retriever_does_not_use_fixed_alias_shortcuts():
    alias_free_kb = {
        "accounts": KB["accounts"],
    }
    intent = {
        "requested_dimensions": ["customers"],
        "requested_metrics": [],
        "requested_filters": [],
        "raw_business_terms": ["customers"],
    }

    context = retrieve_context("show all customers", intent, alias_free_kb, business_glossary={}, vector_retriever=None)

    assert context["matched_tables"] == []
    assert context["confidence"] <= 0.25


def test_context_retriever_unrelated_terms_stay_low_confidence():
    intent = {
        "requested_dimensions": ["nebula"],
        "requested_metrics": ["galactic flux"],
        "requested_filters": [],
        "raw_business_terms": ["nebula", "galactic flux"],
    }

    context = retrieve_context("show galactic flux by nebula", intent, KB, business_glossary=GLOSSARY, vector_retriever=None)

    assert context["matched_tables"] == []
    assert context["matched_columns"] == []
    assert context["confidence"] <= 0.25
