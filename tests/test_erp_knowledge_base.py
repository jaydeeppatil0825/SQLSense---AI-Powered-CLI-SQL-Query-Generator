from semantic.erp_metadata import enrich_knowledge_base_for_erp, summarize_knowledge_base


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
    assert enriched["sales_invoices"]["columns"][3]["confidence"] >= 0.9
    assert enriched["sales_invoices"]["columns"][3]["reason"]
    assert enriched["sales_invoices"]["table_name"] == "sales_invoices"

    relationships = enriched["sales_invoices"]["relationships"]
    assert relationships
    assert relationships[0]["from_table"] == "sales_invoices"
    assert relationships[0]["to_table"] == "customers"
    assert relationships[0]["confidence"] >= 0.8
    assert relationships[0]["reason"]


def test_vendor_and_supplier_tables_are_not_classified_as_sales():
    knowledge_base = {
        "suppliers": {
            "columns": [
                {"name": "supplier_id", "type": "INTEGER"},
                {"name": "supplier_name", "type": "VARCHAR(100)"},
            ],
            "primary_keys": ["supplier_id"],
            "foreign_keys": [],
        },
        "vendor_payments": {
            "columns": [
                {"name": "payment_id", "type": "INTEGER"},
                {"name": "vendor_id", "type": "INTEGER"},
                {"name": "payment_amount", "type": "DECIMAL(10,2)"},
            ],
            "primary_keys": ["payment_id"],
            "foreign_keys": [],
        },
    }

    enriched = enrich_knowledge_base_for_erp(knowledge_base)

    assert enriched["suppliers"]["module"] == "master data"
    assert enriched["vendor_payments"]["module"] == "finance"


def test_invalid_business_purpose_falls_back_to_rule_based_text():
    knowledge_base = {
        "vendors": {
            "columns": [
                {"name": "vendor_id", "type": "INTEGER"},
                {"name": "vendor_name", "type": "VARCHAR(100)"},
            ],
            "primary_keys": ["vendor_id"],
            "foreign_keys": [],
            "business_purpose": ".99",
        },
        "sales_orders": {
            "columns": [
                {"name": "order_id", "type": "INTEGER"},
                {"name": "customer_id", "type": "INTEGER"},
            ],
            "primary_keys": ["order_id"],
            "foreign_keys": [],
            "business_purpose": "What's In Stock?",
        },
    }

    enriched = enrich_knowledge_base_for_erp(knowledge_base)

    assert enriched["vendors"]["business_purpose"] != ".99"
    assert enriched["sales_orders"]["business_purpose"] != "What's In Stock?"
    assert "Stores" in enriched["vendors"]["business_purpose"]
    assert "Stores" in enriched["sales_orders"]["business_purpose"]


def test_relationships_are_attached_to_both_fact_and_master_tables():
    knowledge_base = {
        "purchase_orders": {
            "columns": [
                {"name": "purchase_id", "type": "INTEGER", "sample_values": [1, 2]},
                {"name": "vendor_id", "type": "INTEGER", "sample_values": [10, 20]},
            ],
            "primary_keys": ["purchase_id"],
            "foreign_keys": [],
        },
        "vendors": {
            "columns": [
                {"name": "vendor_id", "type": "INTEGER", "sample_values": [10, 20]},
                {"name": "vendor_name", "type": "VARCHAR(100)"},
            ],
            "primary_keys": ["vendor_id"],
            "foreign_keys": [],
        },
    }

    enriched = enrich_knowledge_base_for_erp(knowledge_base)

    purchase_relationships = enriched["purchase_orders"]["relationships"]
    vendor_relationships = enriched["vendors"]["relationships"]

    assert any(
        relationship["from_table"] == "purchase_orders"
        and relationship["to_table"] == "vendors"
        for relationship in purchase_relationships
    )
    assert any(
        relationship["from_table"] == "purchase_orders"
        and relationship["to_table"] == "vendors"
        for relationship in vendor_relationships
    )
    assert any(relationship["direction"] == "incoming" for relationship in vendor_relationships)


def test_build_summary_reports_low_confidence_and_missing_relationships():
    knowledge_base = {
        "inventory_balance": {
            "columns": [
                {"name": "warehouse_id", "type": "INTEGER", "sample_values": [1, 2]},
            ],
            "primary_keys": ["balance_id"],
            "foreign_keys": [],
        },
        "warehouses": {
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

    assert "inventory" in summary["modules_detected"] or "master data" in summary["modules_detected"]
    assert summary["relationship_count"] >= 1
    assert "categories" in summary["tables_with_missing_relationships"]
