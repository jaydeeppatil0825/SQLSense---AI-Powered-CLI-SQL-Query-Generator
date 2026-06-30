"""
tests/test_relationship_graph.py
================================
Tests for dynamic relationship graph and BFS join path finding.

Tests verify:
- Direct FK path found
- Multi-hop path found
- FK path preferred over weak inferred path
- No path returns unresolved
- No hardcoded table/column/business logic
"""

from sqlalchemy import Column, Integer, MetaData, Table, create_engine

from kb_pipeline.knowledge_base_builder import build_knowledge_base
from semantic.relationship_graph import (
    build_relationship_graph,
    find_shortest_join_path,
    find_all_possible_join_paths,
)
from kb_pipeline.schema_facts import enrich_knowledge_base_schema_facts
from query_pipeline import query_planner


def test_runtime_planner_normalization_does_not_infer_relationships(monkeypatch):
    captured = {}

    def fake_enrich(knowledge_base, *, infer_relationships=True):
        captured["infer_relationships"] = infer_relationships
        return knowledge_base

    monkeypatch.setattr(query_planner, "enrich_knowledge_base_schema_facts", fake_enrich)

    normalized = query_planner._enriched_kb({"records": {"columns": []}})

    assert normalized == {"records": {"columns": []}}
    assert captured["infer_relationships"] is False


def test_build_relationship_graph_from_fk_constraints():
    """Test that relationship graph is built from real FK constraints."""
    schema_data = {
        "customers": {
            "columns": [{"name": "customer_id", "type": "INTEGER"}],
            "primary_keys": ["customer_id"],
            "foreign_keys": [],
        },
        "orders": {
            "columns": [
                {"name": "order_id", "type": "INTEGER"},
                {"name": "customer_id", "type": "INTEGER"},
            ],
            "primary_keys": ["order_id"],
            "foreign_keys": [
                {
                    "column": "customer_id",
                    "referenced_table": "customers",
                    "referenced_column": "customer_id",
                }
            ],
        },
    }
    
    graph = build_relationship_graph(schema_data)
    
    assert "customers" in graph
    assert "orders" in graph
    assert len(graph["orders"]["edges"]) == 1
    assert graph["orders"]["edges"][0]["to_table"] == "customers"
    edge = graph["orders"]["edges"][0]
    assert edge["source"] == "database_metadata"
    assert edge["relationship_type"] == "foreign_key"
    assert edge["confidence"] == 1.0
    assert edge["safe_for_planner"] is True
    assert edge["evidence"] == ["foreign_key_constraint"]


def test_build_relationship_graph_from_inferred_naming():
    """Test that relationship graph includes inferred relationships from _id naming."""
    schema_data = {
        "customers": {
            "columns": [{"name": "customer_id", "type": "INTEGER", "sample_values": [1, 2, 3]}],
            "primary_keys": ["customer_id"],
            "foreign_keys": [],
        },
        "orders": {
            "columns": [
                {"name": "order_id", "type": "INTEGER"},
                {"name": "customer_id", "type": "INTEGER", "sample_values": [1, 2, 3]},
            ],
            "primary_keys": ["order_id"],
            "foreign_keys": [],  # No FK constraint, but naming suggests relationship
        },
    }
    
    knowledge_base = enrich_knowledge_base_schema_facts(schema_data)
    graph = build_relationship_graph(knowledge_base)
    
    assert "customers" in graph
    assert "orders" in graph
    assert len(graph["orders"]["edges"]) == 1
    assert graph["orders"]["edges"][0]["to_table"] == "customers"
    edge = graph["orders"]["edges"][0]
    assert edge["source"] == "kb_build_inference"
    assert edge["relationship_type"] == "inferred"
    assert edge["confidence"] >= 0.85
    assert edge["safe_for_planner"] is True
    assert edge["is_fallback"] is True
    assert {"naming_pattern", "compatible_data_type", "target_key_or_unique", "strong_sample_overlap"} <= set(edge["evidence"])
    assert "sample values overlap" in edge["reason"].lower()


def test_fallback_relationship_is_created_during_fresh_kb_build():
    engine = create_engine("sqlite:///:memory:")
    metadata = MetaData()
    customers = Table(
        "customers",
        metadata,
        Column("customer_id", Integer, primary_key=True),
    )
    orders = Table(
        "orders",
        metadata,
        Column("order_id", Integer, primary_key=True),
        Column("customer_id", Integer, nullable=False),
    )
    metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(customers.insert(), [{"customer_id": 10}, {"customer_id": 20}])
        connection.execute(
            orders.insert(),
            [
                {"order_id": 1, "customer_id": 10},
                {"order_id": 2, "customer_id": 20},
            ],
        )

    knowledge_base = build_knowledge_base(engine)
    relationship = next(
        item
        for item in knowledge_base["orders"]["relationships"]
        if item.get("direction") == "many-to-one"
    )

    assert knowledge_base["orders"]["foreign_keys"] == []
    assert relationship["relationship_type"] == "inferred"
    assert relationship["source"] == "kb_build_inference"
    assert relationship["safe_for_planner"] is True
    assert "strong_sample_overlap" in relationship["evidence"]


def test_find_shortest_join_path_direct_fk():
    """Test that direct FK path is found correctly."""
    schema_data = {
        "customers": {
            "columns": [{"name": "customer_id", "type": "INTEGER"}],
            "primary_keys": ["customer_id"],
            "foreign_keys": [],
        },
        "orders": {
            "columns": [
                {"name": "order_id", "type": "INTEGER"},
                {"name": "customer_id", "type": "INTEGER"},
            ],
            "primary_keys": ["order_id"],
            "foreign_keys": [
                {
                    "column": "customer_id",
                    "referenced_table": "customers",
                    "referenced_column": "customer_id",
                }
            ],
        },
    }
    
    graph = build_relationship_graph(schema_data)
    result = find_shortest_join_path(graph, "orders", "customers")
    
    assert result["resolved"] is True
    assert result["path"] == ["orders", "customers"]
    assert result["path_length"] == 1
    assert result["join_columns"] == [("customer_id", "customer_id")]
    assert result["edge_sources"] == ["database_metadata"]
    assert result["confidences"] == [1.0]
    assert result["total_confidence"] == 1.0


def test_find_shortest_join_path_multi_hop():
    """Test that multi-hop path is found correctly."""
    schema_data = {
        "customers": {
            "columns": [{"name": "customer_id", "type": "INTEGER"}],
            "primary_keys": ["customer_id"],
            "foreign_keys": [],
        },
        "orders": {
            "columns": [
                {"name": "order_id", "type": "INTEGER"},
                {"name": "customer_id", "type": "INTEGER"},
            ],
            "primary_keys": ["order_id"],
            "foreign_keys": [
                {
                    "column": "customer_id",
                    "referenced_table": "customers",
                    "referenced_column": "customer_id",
                }
            ],
        },
        "order_items": {
            "columns": [
                {"name": "item_id", "type": "INTEGER"},
                {"name": "order_id", "type": "INTEGER"},
            ],
            "primary_keys": ["item_id"],
            "foreign_keys": [
                {
                    "column": "order_id",
                    "referenced_table": "orders",
                    "referenced_column": "order_id",
                }
            ],
        },
    }
    
    graph = build_relationship_graph(schema_data)
    result = find_shortest_join_path(graph, "order_items", "customers")
    
    assert result["resolved"] is True
    assert result["path"] == ["order_items", "orders", "customers"]
    assert result["path_length"] == 2
    assert len(result["join_columns"]) == 2
    assert result["edge_sources"] == ["database_metadata", "database_metadata"]


def test_find_shortest_join_path_no_path():
    """Test that unresolved is returned when no path exists."""
    schema_data = {
        "customers": {
            "columns": [{"name": "customer_id", "type": "INTEGER"}],
            "primary_keys": ["customer_id"],
            "foreign_keys": [],
        },
        "products": {
            "columns": [{"name": "product_id", "type": "INTEGER"}],
            "primary_keys": ["product_id"],
            "foreign_keys": [],
        },
    }
    
    knowledge_base = enrich_knowledge_base_schema_facts(schema_data)
    graph = build_relationship_graph(knowledge_base)
    result = find_shortest_join_path(graph, "customers", "products")
    
    assert result["resolved"] is False
    assert result["path"] == []
    assert result["path_length"] == 0
    assert result["reason"] == "No valid join path found between tables"


def test_find_shortest_join_path_same_table():
    """Test that same table returns resolved with zero hops."""
    schema_data = {
        "customers": {
            "columns": [{"name": "customer_id", "type": "INTEGER"}],
            "primary_keys": ["customer_id"],
            "foreign_keys": [],
        },
    }
    
    graph = build_relationship_graph(schema_data)
    result = find_shortest_join_path(graph, "customers", "customers")
    
    assert result["resolved"] is True
    assert result["path"] == ["customers"]
    assert result["path_length"] == 0
    assert result["total_confidence"] == 1.0


def test_fk_path_preferred_over_inferred():
    """Test that FK path is preferred over weak inferred path when both exist."""
    schema_data = {
        "customers": {
            "columns": [{"name": "customer_id", "type": "INTEGER"}],
            "primary_keys": ["customer_id"],
            "foreign_keys": [],
        },
        "orders": {
            "columns": [
                {"name": "order_id", "type": "INTEGER"},
                {"name": "customer_id", "type": "INTEGER"},
            ],
            "primary_keys": ["order_id"],
            "foreign_keys": [
                {
                    "column": "customer_id",
                    "referenced_table": "customers",
                    "referenced_column": "customer_id",
                }
            ],
        },
        "invoices": {
            "columns": [
                {"name": "invoice_id", "type": "INTEGER"},
                {"name": "customer_id", "type": "INTEGER"},
            ],
            "primary_keys": ["invoice_id"],
            "foreign_keys": [],  # No FK, but naming suggests relationship
        },
    }
    
    graph = build_relationship_graph(schema_data)
    paths = find_all_possible_join_paths(graph, "orders", "customers", max_paths=5)
    
    # First path should be the direct FK from orders to customers
    assert len(paths) > 0
    best_path = paths[0]
    assert best_path["resolved"] is True
    assert best_path["path"] == ["orders", "customers"]
    assert best_path["edge_sources"] == ["database_metadata"]
    assert best_path["confidences"] == [1.0]


def test_find_all_possible_join_paths_multiple_paths():
    """Test that multiple possible paths are returned and sorted correctly."""
    schema_data = {
        "customers": {
            "columns": [{"name": "customer_id", "type": "INTEGER"}],
            "primary_keys": ["customer_id"],
            "foreign_keys": [],
        },
        "orders": {
            "columns": [
                {"name": "order_id", "type": "INTEGER"},
                {"name": "customer_id", "type": "INTEGER"},
            ],
            "primary_keys": ["order_id"],
            "foreign_keys": [
                {
                    "column": "customer_id",
                    "referenced_table": "customers",
                    "referenced_column": "customer_id",
                }
            ],
        },
        "invoices": {
            "columns": [
                {"name": "invoice_id", "type": "INTEGER"},
                {"name": "customer_id", "type": "INTEGER"},
            ],
            "primary_keys": ["invoice_id"],
            "foreign_keys": [],  # Inferred relationship
        },
    }
    
    knowledge_base = enrich_knowledge_base_schema_facts(schema_data)
    graph = build_relationship_graph(knowledge_base)
    paths = find_all_possible_join_paths(graph, "invoices", "customers", max_paths=3)
    
    # Should find at least the direct inferred path
    assert len(paths) > 0
    assert all(path["resolved"] for path in paths)
    # Paths should be sorted by preference (shorter, higher confidence, more FK edges)
    assert paths[0]["path_length"] <= paths[-1]["path_length"] if len(paths) > 1 else True


def test_inferred_relationship_requires_strong_generic_evidence():
    schema_data = {
        "customers": {
            "columns": [{"name": "customer_id", "type": "INTEGER", "sample_values": [10, 20]}],
            "primary_keys": ["customer_id"],
            "foreign_keys": [],
        },
        "orders": {
            "columns": [
                {"name": "order_id", "type": "INTEGER"},
                {"name": "customer_id", "type": "VARCHAR(20)", "sample_values": ["x-1", "x-2"]},
            ],
            "primary_keys": ["order_id"],
            "foreign_keys": [],
        },
    }

    knowledge_base = enrich_knowledge_base_schema_facts(schema_data)
    graph = build_relationship_graph(knowledge_base)

    assert graph["orders"]["edges"] == []


def test_runtime_graph_does_not_infer_from_matching_names_alone():
    schema_data = {
        "records": {
            "columns": [{"name": "record_id", "type": "INTEGER"}],
            "primary_keys": ["record_id"],
            "foreign_keys": [],
        },
        "events": {
            "columns": [{"name": "record_id", "type": "INTEGER"}],
            "primary_keys": [],
            "foreign_keys": [],
        },
    }

    runtime_graph = build_relationship_graph(schema_data)
    kb_build_graph = build_relationship_graph(schema_data, infer_relationships=True)

    assert runtime_graph["events"]["edges"] == []
    assert kb_build_graph["events"]["edges"]


def test_no_hardcoded_table_names():
    """Test that no hardcoded table/column/business names are used."""
    # Use generic table names to ensure no hardcoding
    schema_data = {
        "table_a": {
            "columns": [{"name": "id", "type": "INTEGER"}],
            "primary_keys": ["id"],
            "foreign_keys": [],
        },
        "table_b": {
            "columns": [
                {"name": "id", "type": "INTEGER"},
                {"name": "table_a_id", "type": "INTEGER"},
            ],
            "primary_keys": ["id"],
            "foreign_keys": [
                {
                    "column": "table_a_id",
                    "referenced_table": "table_a",
                    "referenced_column": "id",
                }
            ],
        },
    }
    
    graph = build_relationship_graph(schema_data)
    result = find_shortest_join_path(graph, "table_b", "table_a")
    
    assert result["resolved"] is True
    assert result["path"] == ["table_b", "table_a"]
    # Should work with any table names, not just specific business terms


def test_bidirectional_edges():
    """Test that graph has bidirectional edges for traversal."""
    schema_data = {
        "customers": {
            "columns": [{"name": "customer_id", "type": "INTEGER"}],
            "primary_keys": ["customer_id"],
            "foreign_keys": [],
        },
        "orders": {
            "columns": [
                {"name": "order_id", "type": "INTEGER"},
                {"name": "customer_id", "type": "INTEGER"},
            ],
            "primary_keys": ["order_id"],
            "foreign_keys": [
                {
                    "column": "customer_id",
                    "referenced_table": "customers",
                    "referenced_column": "customer_id",
                }
            ],
        },
    }
    
    graph = build_relationship_graph(schema_data)
    
    # Should be able to traverse both directions
    result_forward = find_shortest_join_path(graph, "orders", "customers")
    result_reverse = find_shortest_join_path(graph, "customers", "orders")
    
    assert result_forward["resolved"] is True
    assert result_reverse["resolved"] is True
    assert result_forward["path"] == ["orders", "customers"]
    assert result_reverse["path"] == ["customers", "orders"]
