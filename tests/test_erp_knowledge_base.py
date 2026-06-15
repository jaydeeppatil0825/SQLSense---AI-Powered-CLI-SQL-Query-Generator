from semantic.erp_metadata import enrich_knowledge_base_for_erp, summarize_knowledge_base


def test_enrichment_adds_generic_modules_semantics_and_relationship_confidence():
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

    enriched = enrich_knowledge_base_for_erp(knowledge_base)

    assert enriched["invoice_headers"]["module"] == "transaction"
    assert enriched["client_directory"]["module"] == "reference"
    assert enriched["invoice_headers"]["columns"][3]["semantic_type"] == "money"
    assert enriched["invoice_headers"]["columns"][4]["semantic_type"] == "status"
    assert enriched["invoice_headers"]["columns"][3]["confidence"] >= 0.9
    assert enriched["invoice_headers"]["columns"][3]["reason"]
    assert enriched["invoice_headers"]["table_name"] == "invoice_headers"

    relationships = enriched["invoice_headers"]["relationships"]
    assert relationships
    assert relationships[0]["from_table"] == "invoice_headers"
    assert relationships[0]["to_table"] == "client_directory"
    assert relationships[0]["confidence"] >= 0.8
    assert relationships[0]["reason"]


def test_reference_and_transaction_tables_are_not_forced_into_demo_modules():
    knowledge_base = {
        "supplier_directory": {
            "columns": [
                {"name": "supplier_id", "type": "INTEGER"},
                {"name": "supplier_name", "type": "VARCHAR(100)"},
            ],
            "primary_keys": ["supplier_id"],
            "foreign_keys": [],
        },
        "payment_entries": {
            "columns": [
                {"name": "payment_id", "type": "INTEGER"},
                {"name": "supplier_id", "type": "INTEGER"},
                {"name": "payment_amount", "type": "DECIMAL(10,2)"},
            ],
            "primary_keys": ["payment_id"],
            "foreign_keys": [],
        },
    }

    enriched = enrich_knowledge_base_for_erp(knowledge_base)

    assert enriched["supplier_directory"]["module"] == "reference"
    assert enriched["payment_entries"]["module"] == "transaction"


def test_invalid_business_purpose_falls_back_to_rule_based_text():
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

    enriched = enrich_knowledge_base_for_erp(knowledge_base)

    assert enriched["supplier_directory"]["business_purpose"] != ".99"
    assert enriched["invoice_headers"]["business_purpose"] != "What's In Stock?"
    assert "Stores" in enriched["supplier_directory"]["business_purpose"]
    assert "Stores" in enriched["invoice_headers"]["business_purpose"]


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

    enriched = enrich_knowledge_base_for_erp(knowledge_base)

    purchase_relationships = enriched["purchase_records"]["relationships"]
    supplier_relationships = enriched["supplier_directory"]["relationships"]

    assert any(
        relationship["from_table"] == "purchase_records"
        and relationship["to_table"] == "supplier_directory"
        for relationship in purchase_relationships
    )
    assert any(
        relationship["from_table"] == "purchase_records"
        and relationship["to_table"] == "supplier_directory"
        for relationship in supplier_relationships
    )
    assert any(relationship["direction"] == "incoming" for relationship in supplier_relationships)


def test_build_summary_reports_low_confidence_and_missing_relationships():
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

    enriched = enrich_knowledge_base_for_erp(knowledge_base)
    summary = summarize_knowledge_base(enriched)

    assert "snapshot" in summary["modules_detected"] or "reference" in summary["modules_detected"]
    assert summary["relationship_count"] >= 1
    assert "categories" in summary["tables_with_missing_relationships"]
