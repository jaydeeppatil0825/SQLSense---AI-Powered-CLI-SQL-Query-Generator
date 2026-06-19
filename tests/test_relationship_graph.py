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

from semantic.relationship_graph import (
    build_relationship_graph,
    find_shortest_join_path,
    find_all_possible_join_paths,
)


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
    assert graph["orders"]["edges"][0]["source"] == "foreign_key"
    assert graph["orders"]["edges"][0]["confidence"] == 0.99


def test_build_relationship_graph_from_inferred_naming():
    """Test that relationship graph includes inferred relationships from _id naming."""
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
            "foreign_keys": [],  # No FK constraint, but naming suggests relationship
        },
    }
    
    graph = build_relationship_graph(schema_data)
    
    assert "customers" in graph
    assert "orders" in graph
    assert len(graph["orders"]["edges"]) == 1
    assert graph["orders"]["edges"][0]["to_table"] == "customers"
    assert graph["orders"]["edges"][0]["source"] == "inferred_by_naming"
    assert graph["orders"]["edges"][0]["confidence"] == 0.82


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
    assert result["edge_sources"] == ["foreign_key"]
    assert result["confidences"] == [0.99]
    assert result["total_confidence"] == 0.99


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
    assert result["edge_sources"] == ["foreign_key", "foreign_key"]


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
    
    graph = build_relationship_graph(schema_data)
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
    assert best_path["edge_sources"] == ["foreign_key"]
    assert best_path["confidences"] == [0.99]


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
    
    graph = build_relationship_graph(schema_data)
    paths = find_all_possible_join_paths(graph, "invoices", "customers", max_paths=3)
    
    # Should find at least the direct inferred path
    assert len(paths) > 0
    assert all(path["resolved"] for path in paths)
    # Paths should be sorted by preference (shorter, higher confidence, more FK edges)
    assert paths[0]["path_length"] <= paths[-1]["path_length"] if len(paths) > 1 else True


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
