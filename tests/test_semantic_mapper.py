from semantic.semantic_mapper import SEMANTIC_MAP, add_semantic_mapping


def test_empty_schema_returns_input_unchanged():
    schema = {}
    assert add_semantic_mapping(schema) is schema


def test_semantic_map_backward_compatibility_alias_is_empty():
    assert SEMANTIC_MAP == {}


def test_legacy_domain_specific_types_are_reduced_to_candidates_without_strong_facts():
    schema = {
        "client_directory": {
            "columns": [
                {"name": "customer_name", "semantic_type": "customer"},
                {"name": "warehouse_code", "semantic_type": "warehouse"},
            ]
        }
    }

    result = add_semantic_mapping(schema)

    assert result["client_directory"]["columns"][0]["semantic_type"] == "text_candidate"
    assert result["client_directory"]["columns"][1]["semantic_type"] == "category_candidate"


def test_numeric_types_only_become_numeric_candidates():
    schema = {
        "transactions": {
            "columns": [
                {"name": "amount", "type": "DECIMAL(10,2)"},
                {"name": "total_price", "type": "numeric"},
                {"name": "cost", "type": "float"},
                {"name": "outstanding_balance", "type": "double precision"},
            ]
        }
    }

    result = add_semantic_mapping(schema)

    assert all(
        column["semantic_type"] == "numeric_candidate"
        for column in result["transactions"]["columns"]
    )


def test_integer_like_types_do_not_become_quantity_without_ai_enrichment():
    schema = {
        "inventory": {
            "columns": [
                {"name": "quantity", "type": "int"},
                {"name": "qty", "type": "bigint"},
                {"name": "stock_level", "type": "smallint"},
                {"name": "units", "type": "integer"},
            ]
        }
    }

    result = add_semantic_mapping(schema)

    assert all(
        column["semantic_type"] == "numeric_candidate"
        for column in result["inventory"]["columns"]
    )


def test_date_types_are_strong_facts_and_set_is_date():
    schema = {
        "events": {
            "columns": [
                {"name": "created_at", "type": "datetime"},
                {"name": "updated_at", "type": "timestamp"},
                {"name": "start_date", "type": "date"},
            ]
        }
    }

    result = add_semantic_mapping(schema)

    for column in result["events"]["columns"]:
        assert column["semantic_type"] == "date"
        assert column["structural_facts"]["is_date"] is True
        assert column["planner_roles"]["date_candidate"] is True


def test_boolean_type_and_boolean_profile_values_are_strong_facts():
    schema = {
        "tasks": {
            "columns": [
                {"name": "is_active", "type": "boolean"},
                {"name": "enabled_flag", "type": "varchar", "sample_values": [True, False, True]},
                {"name": "completed", "type": "varchar"},
            ]
        }
    }

    result = add_semantic_mapping(schema)

    assert result["tasks"]["columns"][0]["semantic_type"] == "boolean"
    assert result["tasks"]["columns"][1]["semantic_type"] == "boolean"
    assert result["tasks"]["columns"][2]["semantic_type"] == "text_candidate"


def test_id_columns_win_over_existing_ai_guesses():
    schema = {
        "records": {
            "primary_keys": ["record_id"],
            "foreign_keys": [{"column": "owner_id"}],
            "columns": [
                {"name": "record_id", "type": "int", "semantic_type": "money"},
                {"name": "owner_id", "type": "int", "semantic_type": "name"},
                {"name": "created_on", "type": "timestamp", "semantic_type": "text_candidate"},
            ],
        }
    }

    result = add_semantic_mapping(schema)

    assert result["records"]["columns"][0]["semantic_type"] == "id"
    assert result["records"]["columns"][1]["semantic_type"] == "id"
    assert result["records"]["columns"][2]["semantic_type"] == "date"
    assert result["records"]["columns"][2]["structural_facts"]["is_date"] is True
    assert result["records"]["columns"][2]["planner_roles"]["date_candidate"] is True


def test_legacy_rich_types_do_not_replace_core_candidate_types():
    schema = {
        "orders": {
            "columns": [
                {"name": "final_amount", "type": "decimal", "semantic_type": "money"},
                {"name": "customer_label", "type": "varchar", "semantic_type": "name"},
            ]
        }
    }

    result = add_semantic_mapping(schema)

    assert result["orders"]["columns"][0]["semantic_type"] == "numeric_candidate"
    assert result["orders"]["columns"][0]["planner_roles"]["measure_candidate"] is True
    assert result["orders"]["columns"][1]["semantic_type"] == "text_candidate"
    assert result["orders"]["columns"][1]["planner_roles"]["dimension_candidate"] is True
