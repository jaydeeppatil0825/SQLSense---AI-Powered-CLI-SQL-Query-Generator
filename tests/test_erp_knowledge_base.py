from semantic.erp_metadata import enrich_knowledge_base_for_erp


def test_erp_enrichment_adds_modules_semantics_and_relationship_confidence():
    knowledge_base = {
        "sales_invoices": {
            "columns": [
                {"name": "invoice_id", "type": "INTEGER"},
                {"name": "customer_id", "type": "INTEGER", "sample_values": [1, 2]},
                {"name": "invoice_date", "type": "DATE"},
                {"name": "final_amount", "type": "DECIMAL(10,2)"},
                {"name": "gst_amount", "type": "DECIMAL(10,2)"},
            ],
            "primary_keys": ["invoice_id"],
            "foreign_keys": [],
            "row_count": 120,
        },
        "customers": {
            "columns": [
                {"name": "customer_id", "type": "INTEGER", "sample_values": [1, 2]},
                {"name": "customer_name", "type": "VARCHAR(100)"},
            ],
            "primary_keys": ["customer_id"],
            "foreign_keys": [],
            "row_count": 20,
        },
    }

    enriched = enrich_knowledge_base_for_erp(knowledge_base)

    assert enriched["sales_invoices"]["module"] == "sales"
    assert enriched["customers"]["module"] in {"CRM/support", "master data"}
    assert enriched["sales_invoices"]["columns"][3]["semantic_type"] == "money"
    assert enriched["sales_invoices"]["columns"][4]["semantic_type"] == "tax"

    relationships = enriched["sales_invoices"]["relationships"]
    assert relationships
    assert relationships[0]["from_table"] == "sales_invoices"
    assert relationships[0]["to_table"] == "customers"
    assert relationships[0]["confidence"] >= 0.8
    assert relationships[0]["reason"]
