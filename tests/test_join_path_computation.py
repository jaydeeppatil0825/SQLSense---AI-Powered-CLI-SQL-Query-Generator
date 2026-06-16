"""
Test dynamic join path computation for complex multi-table queries.
"""

import pytest
from core.query_planner import (
    _build_fk_relationship_graph,
    _find_shortest_path,
    _compute_join_paths,
    _find_bridge_tables,
    _add_missing_tables_for_columns,
)


def test_build_fk_relationship_graph():
    """Test building FK relationship graph from knowledge base."""
    kb = {
        "table_a": {
            "foreign_keys": [
                {"from_table": "table_a", "column": "b_id", "to_table": "table_b", "referenced_column": "id"}
            ]
        },
        "table_b": {
            "foreign_keys": [
                {"from_table": "table_b", "column": "c_id", "to_table": "table_c", "referenced_column": "id"}
            ]
        },
        "table_c": {
            "foreign_keys": [
                {"from_table": "table_c", "column": "d_id", "to_table": "table_d", "referenced_column": "id"}
            ]
        },
        "table_d": {
            "foreign_keys": []
        }
    }
    
    graph = _build_fk_relationship_graph(kb)
    
    assert "table_a" in graph
    assert "table_b" in graph
    assert "table_c" in graph
    assert "table_d" in graph
    
    # Check outgoing edges
    assert len(graph["table_a"]["outgoing"]) == 1
    assert graph["table_a"]["outgoing"][0]["to_table"] == "table_b"
    assert graph["table_a"]["outgoing"][0]["from_column"] == "b_id"
    
    assert len(graph["table_b"]["outgoing"]) == 1
    assert graph["table_b"]["outgoing"][0]["to_table"] == "table_c"
    
    # Check incoming edges
    assert len(graph["table_b"]["incoming"]) == 1
    assert graph["table_b"]["incoming"][0]["from_table"] == "table_a"
    
    assert len(graph["table_c"]["incoming"]) == 1
    assert graph["table_c"]["incoming"][0]["from_table"] == "table_b"


def test_find_shortest_path_direct():
    """Test finding shortest path between directly connected tables."""
    kb = {
        "table_a": {
            "foreign_keys": [
                {"from_table": "table_a", "column": "b_id", "to_table": "table_b", "referenced_column": "id"}
            ]
        },
        "table_b": {
            "foreign_keys": []
        }
    }
    
    graph = _build_fk_relationship_graph(kb)
    path = _find_shortest_path(graph, "table_a", "table_b")
    
    assert path is not None
    assert len(path) == 1
    assert path[0]["to_table"] == "table_b"
    assert path[0]["from_column"] == "b_id"


def test_find_shortest_path_indirect():
    """Test finding shortest path through bridge tables."""
    kb = {
        "table_a": {
            "foreign_keys": [
                {"from_table": "table_a", "column": "b_id", "to_table": "table_b", "referenced_column": "id"}
            ]
        },
        "table_b": {
            "foreign_keys": [
                {"from_table": "table_b", "column": "c_id", "to_table": "table_c", "referenced_column": "id"}
            ]
        },
        "table_c": {
            "foreign_keys": []
        }
    }
    
    graph = _build_fk_relationship_graph(kb)
    path = _find_shortest_path(graph, "table_a", "table_c")
    
    assert path is not None
    assert len(path) == 2
    assert path[0]["to_table"] == "table_b"
    assert path[1]["to_table"] == "table_c"


def test_find_shortest_path_no_path():
    """Test when no path exists between tables."""
    kb = {
        "table_a": {
            "foreign_keys": []
        },
        "table_b": {
            "foreign_keys": []
        }
    }
    
    graph = _build_fk_relationship_graph(kb)
    path = _find_shortest_path(graph, "table_a", "table_b")
    
    assert path is None


def test_compute_join_paths():
    """Test computing join paths between multiple selected tables."""
    kb = {
        "table_a": {
            "foreign_keys": [
                {"from_table": "table_a", "column": "b_id", "to_table": "table_b", "referenced_column": "id"}
            ]
        },
        "table_b": {
            "foreign_keys": [
                {"from_table": "table_b", "column": "c_id", "to_table": "table_c", "referenced_column": "id"}
            ]
        },
        "table_c": {
            "foreign_keys": []
        }
    }
    
    selected_tables = ["table_a", "table_c"]
    join_paths = _compute_join_paths(selected_tables, kb)
    
    assert len(join_paths) > 0
    assert any(jp["from_table"] == "table_a" and jp["to_table"] == "table_c" for jp in join_paths)
    # Should find path through table_b
    path = next(jp for jp in join_paths if jp["from_table"] == "table_a" and jp["to_table"] == "table_c")
    assert path["length"] == 2
    assert path["path"][0]["from_table"] == "table_a"
    assert path["path"][0]["join_condition"] == "table_a.b_id = table_b.id"
    assert path["path"][1]["from_table"] == "table_b"
    assert path["path"][1]["join_condition"] == "table_b.c_id = table_c.id"


def test_find_bridge_tables():
    """Test finding bridge tables to connect disconnected selected tables."""
    kb = {
        "table_a": {
            "foreign_keys": [
                {"from_table": "table_a", "column": "b_id", "to_table": "table_b", "referenced_column": "id"}
            ]
        },
        "table_b": {
            "foreign_keys": [
                {"from_table": "table_b", "column": "c_id", "to_table": "table_c", "referenced_column": "id"}
            ]
        },
        "table_c": {
            "foreign_keys": []
        },
        "table_d": {
            "foreign_keys": []  # Disconnected from the chain
        }
    }
    
    # Select disconnected tables (table_d is not reachable from table_a)
    selected_tables = ["table_a", "table_d"]
    bridge_tables = _find_bridge_tables(selected_tables, kb)
    
    # table_a and table_d are disconnected, but there's no path between them
    # So no bridge tables should be found
    assert bridge_tables == []


def test_add_missing_tables_for_columns():
    """Test adding tables when selected columns belong to tables not in selected_tables."""
    kb = {
        "table_a": {"columns": []},
        "table_b": {"columns": []},
        "table_c": {"columns": []}
    }
    
    selected_tables = ["table_a"]
    selected_columns = [
        {"table": "table_a", "column": "id"},
        {"table": "table_b", "column": "name"},  # This table is not in selected_tables
        {"table": "table_c", "column": "value"}  # This table is not in selected_tables
    ]
    
    updated_tables, updated_columns = _add_missing_tables_for_columns(
        selected_tables, selected_columns, kb
    )
    
    assert "table_b" in updated_tables
    assert "table_c" in updated_tables
    assert len(updated_tables) == 3


def test_complex_chain_a_b_c_d():
    """Test the full chain A -> B -> C -> D scenario."""
    kb = {
        "table_a": {
            "foreign_keys": [
                {"from_table": "table_a", "column": "b_id", "to_table": "table_b", "referenced_column": "id"}
            ],
            "columns": [
                {"name": "id", "type": "int", "semantic_type": "id"},
                {"name": "name", "type": "varchar", "semantic_type": "name"},
                {"name": "b_id", "type": "int", "semantic_type": "id"}
            ]
        },
        "table_b": {
            "foreign_keys": [
                {"from_table": "table_b", "column": "c_id", "to_table": "table_c", "referenced_column": "id"}
            ],
            "columns": [
                {"name": "id", "type": "int", "semantic_type": "id"},
                {"name": "amount", "type": "decimal", "semantic_type": "money"},
                {"name": "c_id", "type": "int", "semantic_type": "id"}
            ]
        },
        "table_c": {
            "foreign_keys": [
                {"from_table": "table_c", "column": "d_id", "to_table": "table_d", "referenced_column": "id"}
            ],
            "columns": [
                {"name": "id", "type": "int", "semantic_type": "id"},
                {"name": "quantity", "type": "int", "semantic_type": "quantity"},
                {"name": "d_id", "type": "int", "semantic_type": "id"}
            ]
        },
        "table_d": {
            "foreign_keys": [],
            "columns": [
                {"name": "id", "type": "int", "semantic_type": "id"},
                {"name": "description", "type": "varchar", "semantic_type": "text"}
            ]
        }
    }
    
    # Test path from A to D
    graph = _build_fk_relationship_graph(kb)
    path = _find_shortest_path(graph, "table_a", "table_d")
    
    assert path is not None
    assert len(path) == 3
    assert path[0]["to_table"] == "table_b"
    assert path[1]["to_table"] == "table_c"
    assert path[2]["to_table"] == "table_d"
    
    # Test bridge table detection - table_a and table_d ARE connected via the chain
    # So no bridge tables should be needed
    selected_tables = ["table_a", "table_d"]
    bridge_tables = _find_bridge_tables(selected_tables, kb)
    
    # Since table_a and table_d are connected via the FK chain, no bridge tables needed
    assert bridge_tables == []
    
    # Test join paths computation
    join_paths = _compute_join_paths(selected_tables, kb)
    assert len(join_paths) > 0
    path_ad = next(jp for jp in join_paths if jp["from_table"] == "table_a" and jp["to_table"] == "table_d")
    assert path_ad["length"] == 3
    assert path_ad["path"][0]["join_condition"] == "table_a.b_id = table_b.id"
    assert path_ad["path"][1]["join_condition"] == "table_b.c_id = table_c.id"
    assert path_ad["path"][2]["join_condition"] == "table_c.d_id = table_d.id"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
