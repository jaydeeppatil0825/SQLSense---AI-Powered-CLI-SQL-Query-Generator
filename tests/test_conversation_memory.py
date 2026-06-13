"""
tests/test_conversation_memory.py
==================================
Tests for conversation memory management.
"""

import tempfile
from pathlib import Path

import pytest

from conversation.conversation_memory import ConversationMemory


def test_conversation_memory_starts_session():
    """Test that ConversationMemory starts a session with required fields."""
    memory = ConversationMemory()
    
    assert memory.session_id is not None
    assert memory.started_at is not None
    assert memory.status == "active"
    assert memory.ended_at is None
    assert memory.turns == []
    assert memory.last_user_question is None
    assert memory.last_rewritten_question is None
    assert memory.last_generated_sql is None


def test_conversation_memory_adds_turn():
    """Test that ConversationMemory adds turns correctly."""
    memory = ConversationMemory()
    
    memory.add_turn(
        user_question="Show all customers",
        is_follow_up=False,
        rewritten_question="Show all customers",
        generated_sql="SELECT * FROM customers LIMIT 50;",
        row_count=10,
    )
    
    assert len(memory.turns) == 1
    assert memory.turns[0]["turn_id"] == 1
    assert memory.turns[0]["user_question"] == "Show all customers"
    assert memory.turns[0]["is_follow_up"] is False
    assert memory.turns[0]["rewritten_question"] == "Show all customers"
    assert memory.turns[0]["generated_sql"] == "SELECT * FROM customers LIMIT 50;"
    assert memory.turns[0]["row_count"] == 10


def test_conversation_memory_adds_multiple_turns():
    """Test that ConversationMemory adds multiple turns with incrementing IDs."""
    memory = ConversationMemory()
    
    memory.add_turn(
        user_question="Show all customers",
        is_follow_up=False,
        rewritten_question="Show all customers",
        generated_sql="SELECT * FROM customers LIMIT 50;",
    )
    
    memory.add_turn(
        user_question="Where do they live?",
        is_follow_up=True,
        rewritten_question="Show customer names and cities from customers",
        generated_sql="SELECT name, city FROM customers LIMIT 50;",
    )
    
    assert len(memory.turns) == 2
    assert memory.turns[0]["turn_id"] == 1
    assert memory.turns[1]["turn_id"] == 2
    assert memory.turns[1]["is_follow_up"] is True


def test_conversation_memory_gets_last_context():
    """Test that get_last_context returns correct context."""
    memory = ConversationMemory()
    
    memory.add_turn(
        user_question="Show all customers",
        is_follow_up=False,
        rewritten_question="Show all customers",
        generated_sql="SELECT * FROM customers LIMIT 50;",
        row_count=10,
    )
    
    context = memory.get_last_context()
    
    assert context["last_user_question"] == "Show all customers"
    assert context["last_rewritten_question"] == "Show all customers"
    assert context["last_generated_sql"] == "SELECT * FROM customers LIMIT 50;"
    assert context["turn_count"] == 1


def test_conversation_memory_clears_context():
    """Test that clear_context clears the last context."""
    memory = ConversationMemory()
    
    memory.add_turn(
        user_question="Show all customers",
        is_follow_up=False,
        rewritten_question="Show all customers",
        generated_sql="SELECT * FROM customers LIMIT 50;",
    )
    
    memory.clear_context()
    
    context = memory.get_last_context()
    
    assert context["last_user_question"] is None
    assert context["last_rewritten_question"] is None
    assert context["last_generated_sql"] is None


def test_conversation_memory_updates_execution_results():
    """Test that update_execution_results updates the latest turn."""
    memory = ConversationMemory()
    
    memory.add_turn(
        user_question="Show all customers",
        is_follow_up=False,
        rewritten_question="Show all customers",
        generated_sql="SELECT * FROM customers LIMIT 50;",
    )
    
    rows = [{"id": 1, "name": "John"}, {"id": 2, "name": "Jane"}]
    memory.update_execution_results(rows=rows, chart_path="charts/test.png", insights=["Test insight"])
    
    assert memory.turns[0]["row_count"] == 2
    assert memory.turns[0]["chart_path"] == "charts/test.png"
    assert memory.turns[0]["insights"] == ["Test insight"]
    assert memory.last_rows == rows
    assert memory.last_chart_path == "charts/test.png"
    assert memory.last_insights == ["Test insight"]


def test_conversation_memory_ends_session():
    """Test that end_session sets ended_at and status."""
    memory = ConversationMemory()
    
    memory.end_session()
    
    assert memory.ended_at is not None
    assert memory.status == "ended"


def test_conversation_memory_saves_session():
    """Test that save_session saves to JSON file."""
    memory = ConversationMemory(session_id="test_123")
    
    memory.add_turn(
        user_question="Show all customers",
        is_follow_up=False,
        rewritten_question="Show all customers",
        generated_sql="SELECT * FROM customers LIMIT 50;",
    )
    
    with tempfile.TemporaryDirectory() as tmpdir:
        output_dir = Path(tmpdir)
        file_path = memory.save_session(str(output_dir))
        
        assert Path(file_path).exists()
        assert "session_test_123.json" in file_path


def test_conversation_memory_gets_recent_turns():
    """Test that get_recent_turns returns the most recent turns."""
    memory = ConversationMemory()
    
    for i in range(10):
        memory.add_turn(
            user_question=f"Question {i}",
            is_follow_up=False,
            rewritten_question=f"Question {i}",
            generated_sql=f"SELECT * FROM table{i} LIMIT 50;",
        )
    
    recent_turns = memory.get_recent_turns(5)
    
    assert len(recent_turns) == 5
    assert recent_turns[0]["turn_id"] == 6
    assert recent_turns[4]["turn_id"] == 10


def test_conversation_memory_to_dict():
    """Test that to_dict returns a dictionary representation."""
    memory = ConversationMemory()
    
    memory.add_turn(
        user_question="Show all customers",
        is_follow_up=False,
        rewritten_question="Show all customers",
        generated_sql="SELECT * FROM customers LIMIT 50;",
    )
    
    memory_dict = memory.to_dict()
    
    assert memory_dict["session_id"] == memory.session_id
    assert memory_dict["status"] == "active"
    assert len(memory_dict["turns"]) == 1
