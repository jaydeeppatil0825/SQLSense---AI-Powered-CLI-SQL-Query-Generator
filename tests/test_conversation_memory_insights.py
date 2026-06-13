"""
tests/test_conversation_memory_insights.py
===========================================
Tests for conversation memory insight skip tracking.
"""

import pytest

from conversation.conversation_memory import ConversationMemory


def test_conversation_memory_tracks_insights_skipped():
    """Test that ConversationMemory tracks whether insights were skipped."""
    memory = ConversationMemory()
    
    memory.add_turn(
        user_question="Show all customers",
        is_follow_up=False,
        rewritten_question="Show all customers",
        generated_sql="SELECT * FROM customers LIMIT 50;",
    )
    
    # Update with insights skipped
    memory.update_execution_results(
        rows=[{"id": 1, "name": "John"}],
        insights=[],
        insights_skipped=True,
    )
    
    assert memory.last_insights_skipped is True
    assert memory.turns[-1]["insights_skipped"] is True


def test_conversation_memory_tracks_insights_generated():
    """Test that ConversationMemory tracks when insights were generated."""
    memory = ConversationMemory()
    
    memory.add_turn(
        user_question="Show all customers",
        is_follow_up=False,
        rewritten_question="Show all customers",
        generated_sql="SELECT * FROM customers LIMIT 50;",
    )
    
    # Update with insights generated
    memory.update_execution_results(
        rows=[{"id": 1, "name": "John"}],
        insights=["Test insight 1", "Test insight 2"],
        insights_skipped=False,
    )
    
    assert memory.last_insights_skipped is False
    assert memory.turns[-1]["insights_skipped"] is False
    assert memory.last_insights == ["Test insight 1", "Test insight 2"]


def test_conversation_memory_clears_insights_skipped_on_clear_context():
    """Test that clear_context resets insights_skipped to False."""
    memory = ConversationMemory()
    
    memory.add_turn(
        user_question="Show all customers",
        is_follow_up=False,
        rewritten_question="Show all customers",
        generated_sql="SELECT * FROM customers LIMIT 50;",
    )
    
    memory.update_execution_results(
        rows=[{"id": 1, "name": "John"}],
        insights=[],
        insights_skipped=True,
    )
    
    assert memory.last_insights_skipped is True
    
    memory.clear_context()
    
    assert memory.last_insights_skipped is False


def test_conversation_memory_get_last_context_includes_insights_skipped():
    """Test that get_last_context includes insights_skipped."""
    memory = ConversationMemory()
    
    memory.add_turn(
        user_question="Show all customers",
        is_follow_up=False,
        rewritten_question="Show all customers",
        generated_sql="SELECT * FROM customers LIMIT 50;",
    )
    
    memory.update_execution_results(
        rows=[{"id": 1, "name": "John"}],
        insights=[],
        insights_skipped=True,
    )
    
    context = memory.get_last_context()
    
    assert "last_insights_skipped" in context
    assert context["last_insights_skipped"] is True


def test_conversation_memory_save_session_includes_insights_skipped():
    """Test that save_session includes insights_skipped in the saved data."""
    memory = ConversationMemory(session_id="test_456")
    
    memory.add_turn(
        user_question="Show all customers",
        is_follow_up=False,
        rewritten_question="Show all customers",
        generated_sql="SELECT * FROM customers LIMIT 50;",
    )
    
    memory.update_execution_results(
        rows=[{"id": 1, "name": "John"}],
        insights=[],
        insights_skipped=True,
    )
    
    import tempfile
    from pathlib import Path
    
    with tempfile.TemporaryDirectory() as tmpdir:
        output_dir = Path(tmpdir)
        file_path = memory.save_session(str(output_dir))
        
        # Load the saved file and check for insights_skipped
        import json
        with open(file_path, 'r', encoding='utf-8') as f:
            saved_data = json.load(f)
        
        assert "last_insights_skipped" in saved_data
        assert saved_data["last_insights_skipped"] is True
