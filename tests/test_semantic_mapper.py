from semantic.semantic_mapper import SEMANTIC_MAP, add_semantic_mapping


def test_empty_schema_returns_input_unchanged():
    schema = {}

    assert add_semantic_mapping(schema) is schema


def test_semantic_mapping_assigns_first_matching_type_and_general_fallback():
    schema = {
        "orders": {
            "columns": [
                {"name": "customer_name", "semantic_type": "old"},
                {"name": "mystery_code"},
            ]
        }
    }

    result = add_semantic_mapping(schema)

    assert result["orders"]["columns"][0]["semantic_type"] == SEMANTIC_MAP["customer"]
    assert result["orders"]["columns"][1]["semantic_type"] == "general"
