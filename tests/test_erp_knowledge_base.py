from semantic.erp_metadata import enrich_knowledge_base_schema_facts, summarize_knowledge_base


def test_enrichment_adds_schema_facts_without_module_labels():
    knowledge_base = {
        "invoice_headers": {
            "columns": [
                {"name": "invoice_id", "type": "INTEGER"},
                {"name": "client_id", "type": "INTEGER", "sample_values": [1, 2]},
                {"name": "invoice_date", "type": "DATE"},
                {"name": "total_due", "type": "DECIMAL(10,2)"},
                {"name": "workflow_status", "type": "VARCHAR(20)"},
            ],
            "primary_keys": ["invoice_id"],
            "foreign_keys": [],
            "row_count": 120,
        },
        "client_directory": {
            "columns": [
                {"name": "client_id", "type": "INTEGER", "sample_values": [1, 2]},
                {"name": "client_name", "type": "VARCHAR(100)"},
            ],
            "primary_keys": ["client_id"],
            "foreign_keys": [],
            "row_count": 20,
        },
    }

    enriched = enrich_knowledge_base_schema_facts(knowledge_base)

    assert "module" not in enriched["invoice_headers"]
    assert "module" not in enriched["client_directory"]
    assert enriched["invoice_headers"]["columns"][3]["semantic_type"] == "numeric_candidate"
    assert enriched["invoice_headers"]["columns"][4]["semantic_type"] == "category_candidate"
    assert enriched["invoice_headers"]["columns"][3]["confidence"] >= 0.65
    assert enriched["invoice_headers"]["columns"][3]["reason"]
    assert enriched["invoice_headers"]["table_name"] == "invoice_headers"
    assert enriched["invoice_headers"]["business_purpose"] == "Stores records for invoice header."

    relationships = enriched["invoice_headers"]["relationships"]
    assert relationships
    assert relationships[0]["from_table"] == "invoice_headers"
    assert relationships[0]["to_table"] == "client_directory"
    assert relationships[0]["confidence"] >= 0.8
    assert relationships[0]["reason"]
    assert relationships[0]["is_inferred"] is True
    assert "compatible_data_type" in relationships[0]["evidence"]


def test_existing_module_field_is_removed_from_generated_schema_context():
    knowledge_base = {
        "supplier_directory": {
            "module": "reference",
            "columns": [
                {"name": "supplier_id", "type": "INTEGER"},
                {"name": "supplier_name", "type": "VARCHAR(100)"},
            ],
            "primary_keys": ["supplier_id"],
            "foreign_keys": [],
        },
        "payment_entries": {
            "module": "transaction",
            "columns": [
                {"name": "payment_id", "type": "INTEGER"},
                {"name": "supplier_id", "type": "INTEGER"},
                {"name": "payment_amount", "type": "DECIMAL(10,2)"},
            ],
            "primary_keys": ["payment_id"],
            "foreign_keys": [],
        },
    }

    enriched = enrich_knowledge_base_schema_facts(knowledge_base)

    assert "module" not in enriched["supplier_directory"]
    assert "module" not in enriched["payment_entries"]


def test_invalid_business_purpose_falls_back_to_neutral_text():
    knowledge_base = {
        "supplier_directory": {
            "columns": [
                {"name": "supplier_id", "type": "INTEGER"},
                {"name": "supplier_name", "type": "VARCHAR(100)"},
            ],
            "primary_keys": ["supplier_id"],
            "foreign_keys": [],
            "business_purpose": ".99",
        },
        "invoice_headers": {
            "columns": [
                {"name": "invoice_id", "type": "INTEGER"},
                {"name": "client_id", "type": "INTEGER"},
            ],
            "primary_keys": ["invoice_id"],
            "foreign_keys": [],
            "business_purpose": "What's In Stock?",
        },
    }

    enriched = enrich_knowledge_base_schema_facts(knowledge_base)

    assert enriched["supplier_directory"]["business_purpose"] != ".99"
    assert enriched["invoice_headers"]["business_purpose"] != "What's In Stock?"
    assert enriched["supplier_directory"]["business_purpose"] == "Stores records for supplier directory."
    assert enriched["invoice_headers"]["business_purpose"] == "Stores records for invoice header."


def test_relationships_are_attached_to_both_related_tables():
    knowledge_base = {
        "purchase_records": {
            "columns": [
                {"name": "purchase_id", "type": "INTEGER", "sample_values": [1, 2]},
                {"name": "supplier_id", "type": "INTEGER", "sample_values": [10, 20]},
            ],
            "primary_keys": ["purchase_id"],
            "foreign_keys": [],
        },
        "supplier_directory": {
            "columns": [
                {"name": "supplier_id", "type": "INTEGER", "sample_values": [10, 20]},
                {"name": "supplier_name", "type": "VARCHAR(100)"},
            ],
            "primary_keys": ["supplier_id"],
            "foreign_keys": [],
        },
    }

    enriched = enrich_knowledge_base_schema_facts(knowledge_base)

    purchase_relationships = enriched["purchase_records"]["relationships"]
    supplier_relationships = enriched["supplier_directory"]["relationships"]

    assert any(
        relationship["from_table"] == "purchase_records"
        and relationship["to_table"] == "supplier_directory"
        and relationship["source"] == "kb_build_inference"
        and relationship["relationship_type"] == "inferred"
        and relationship["safe_for_planner"] is True
        for relationship in purchase_relationships
    )
    assert any(
        relationship["from_table"] == "purchase_records"
        and relationship["to_table"] == "supplier_directory"
        for relationship in supplier_relationships
    )
    assert any(relationship["direction"] == "incoming" for relationship in supplier_relationships)


def test_build_summary_reports_schema_context_low_confidence_and_missing_relationships():
    knowledge_base = {
        "stock_positions": {
            "columns": [
                {"name": "warehouse_id", "type": "INTEGER", "sample_values": [1, 2]},
            ],
            "primary_keys": ["stock_id"],
            "foreign_keys": [],
        },
        "warehouse_directory": {
            "columns": [
                {"name": "warehouse_id", "type": "INTEGER", "sample_values": [1, 2]},
            ],
            "primary_keys": ["warehouse_id"],
            "foreign_keys": [],
        },
        "categories": {
            "columns": [{"name": "category_id", "type": "INTEGER"}],
            "primary_keys": ["category_id"],
            "foreign_keys": [],
        },
    }

    enriched = enrich_knowledge_base_schema_facts(knowledge_base)
    summary = summarize_knowledge_base(enriched)

    assert summary["schema_contexts"] == {"schema_only": 3}
    assert summary["modules_detected"] == {"schema_only": 3}
    assert summary["relationship_count"] >= 1
    assert "categories" in summary["tables_with_missing_relationships"]
