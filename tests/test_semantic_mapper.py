from semantic.semantic_mapper import SEMANTIC_MAP, GENERIC_SEMANTIC_PATTERNS, add_semantic_mapping


def test_empty_schema_returns_input_unchanged():
    schema = {}
    assert add_semantic_mapping(schema) is schema


def test_semantic_mapping_reclassifies_legacy_domain_specific_types():
    schema = {
        "client_directory": {
            "columns": [
                {"name": "customer_name", "semantic_type": "customer"},
                {"name": "warehouse_code", "semantic_type": "warehouse"},
            ]
        }
    }

    result = add_semantic_mapping(schema)

    assert result["client_directory"]["columns"][0]["semantic_type"] == "name"
    assert result["client_directory"]["columns"][1]["semantic_type"] == "code"


def test_generic_patterns_classify_money_columns():
    schema = {
        "transactions": {
            "columns": [
                {"name": "amount"},
                {"name": "total_price"},
                {"name": "cost"},
                {"name": "outstanding_balance"},
            ]
        }
    }

    result = add_semantic_mapping(schema)

    assert result["transactions"]["columns"][0]["semantic_type"] == "money"
    assert result["transactions"]["columns"][1]["semantic_type"] == "money"
    assert result["transactions"]["columns"][2]["semantic_type"] == "money"
    assert result["transactions"]["columns"][3]["semantic_type"] == "money"


def test_generic_patterns_classify_quantity_columns():
    schema = {
        "inventory": {
            "columns": [
                {"name": "quantity"},
                {"name": "qty"},
                {"name": "stock_level"},
                {"name": "units"},
            ]
        }
    }

    result = add_semantic_mapping(schema)

    assert result["inventory"]["columns"][0]["semantic_type"] == "quantity"
    assert result["inventory"]["columns"][1]["semantic_type"] == "quantity"
    assert result["inventory"]["columns"][2]["semantic_type"] == "quantity"
    assert result["inventory"]["columns"][3]["semantic_type"] == "quantity"


def test_generic_patterns_classify_date_columns():
    schema = {
        "events": {
            "columns": [
                {"name": "created_at"},
                {"name": "updated_at"},
                {"name": "start_date"},
                {"name": "invoice_date"},
            ]
        }
    }

    result = add_semantic_mapping(schema)

    assert result["events"]["columns"][0]["semantic_type"] == "date"
    assert result["events"]["columns"][1]["semantic_type"] == "date"
    assert result["events"]["columns"][2]["semantic_type"] == "date"
    assert result["events"]["columns"][3]["semantic_type"] == "date"


def test_generic_patterns_classify_status_columns():
    schema = {
        "tasks": {
            "columns": [
                {"name": "status"},
                {"name": "is_active"},
                {"name": "is_enabled"},
                {"name": "completed"},
            ]
        }
    }

    result = add_semantic_mapping(schema)

    assert result["tasks"]["columns"][0]["semantic_type"] == "status"
    assert result["tasks"]["columns"][1]["semantic_type"] == "boolean"
    assert result["tasks"]["columns"][2]["semantic_type"] == "boolean"
    assert result["tasks"]["columns"][3]["semantic_type"] == "status"


def test_data_type_inference():
    schema = {
        "generic_table": {
            "columns": [
                {"name": "some_field", "type": "int"},
                {"name": "another_field", "type": "decimal"},
                {"name": "text_field", "type": "varchar"},
                {"name": "date_field", "type": "date"},
            ]
        }
    }

    result = add_semantic_mapping(schema)

    assert result["generic_table"]["columns"][0]["semantic_type"] == "id"
    assert result["generic_table"]["columns"][1]["semantic_type"] == "money"
    assert result["generic_table"]["columns"][2]["semantic_type"] == "text"
    assert result["generic_table"]["columns"][3]["semantic_type"] == "date"


def test_sample_value_inference():
    schema = {
        "data": {
            "columns": [
                {"name": "is_active_flag", "sample_values": [True, False, True]},
                {"name": "generic_percent", "sample_values": [25, 50, 75, 100]},
                {"name": "generic_date", "sample_values": ["2024-01-01", "2024-02-01"]},
            ]
        }
    }

    result = add_semantic_mapping(schema)

    assert result["data"]["columns"][0]["semantic_type"] == "boolean"
    assert result["data"]["columns"][1]["semantic_type"] == "percentage"
    assert result["data"]["columns"][2]["semantic_type"] == "date"
