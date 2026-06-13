"""
tests/test_followup_detector.py
================================
Tests for follow-up question detection.
"""

import pytest

from conversation.followup_detector import detect_follow_up
from conversation.conversation_memory import ConversationMemory


def test_followup_detector_returns_false_with_no_previous_context():
    """Test that follow-up detector returns False when there's no previous context."""
    memory = ConversationMemory()
    
    is_follow_up, reason = detect_follow_up("Where do they live?", memory)
    
    assert is_follow_up is False
    assert reason == "no_previous_context"


def test_followup_detector_detects_pronoun_followup():
    """Test that follow-up detector detects pronoun-based follow-ups."""
    memory = ConversationMemory()
    memory.add_turn(
        user_question="Show all customers",
        is_follow_up=False,
        rewritten_question="Show all customers",
        generated_sql="SELECT * FROM customers LIMIT 50;",
    )
    
    is_follow_up, reason = detect_follow_up("Where do they live?", memory)
    
    assert is_follow_up is True
    assert reason == "followup_indicator"


def test_followup_detector_detects_now_only_followup():
    """Test that follow-up detector detects 'now only' follow-ups."""
    memory = ConversationMemory()
    memory.add_turn(
        user_question="Show monthly sales",
        is_follow_up=False,
        rewritten_question="Show monthly sales",
        generated_sql="SELECT * FROM sales LIMIT 50;",
    )
    
    is_follow_up, reason = detect_follow_up("Now only Mumbai", memory)
    
    assert is_follow_up is True


def test_followup_detector_detects_make_it_followup():
    """Test that follow-up detector detects 'make it' follow-ups."""
    memory = ConversationMemory()
    memory.add_turn(
        user_question="Show top 5 customers by total sales",
        is_follow_up=False,
        rewritten_question="Show top 5 customers by total sales",
        generated_sql="SELECT * FROM customers LIMIT 50;",
    )
    
    is_follow_up, reason = detect_follow_up("Make it top 10", memory)
    
    assert is_follow_up is True


def test_followup_detector_returns_false_for_new_table_question():
    """Test that follow-up detector returns False for new table questions."""
    memory = ConversationMemory()
    memory.add_turn(
        user_question="Show all customers",
        is_follow_up=False,
        rewritten_question="Show all customers",
        generated_sql="SELECT * FROM customers LIMIT 50;",
    )
    
    is_follow_up, reason = detect_follow_up("Show all products", memory)
    
    assert is_follow_up is False
    # The detector may return "ambiguous" or "new_table_detected" depending on the logic
    assert reason in ("ambiguous", "new_table_detected")


def test_followup_detector_returns_false_for_new_question():
    """Test that follow-up detector returns False for clear new questions."""
    memory = ConversationMemory()
    memory.add_turn(
        user_question="Show all customers",
        is_follow_up=False,
        rewritten_question="Show all customers",
        generated_sql="SELECT * FROM customers LIMIT 50;",
    )
    
    is_follow_up, reason = detect_follow_up("Show monthly sales", memory)
    
    assert is_follow_up is False


def test_followup_detector_detects_sort_followup():
    """Test that follow-up detector detects sort follow-ups."""
    memory = ConversationMemory()
    memory.add_turn(
        user_question="Show revenue by product category",
        is_follow_up=False,
        rewritten_question="Show revenue by product category",
        generated_sql="SELECT * FROM sales LIMIT 50;",
    )
    
    is_follow_up, reason = detect_follow_up("Sort it highest first", memory)
    
    assert is_follow_up is True


def test_followup_detector_detects_what_about_followup():
    """Test that follow-up detector detects 'what about' follow-ups."""
    memory = ConversationMemory()
    memory.add_turn(
        user_question="Show pending payments",
        is_follow_up=False,
        rewritten_question="Show pending payments",
        generated_sql="SELECT * FROM payments LIMIT 50;",
    )
    
    is_follow_up, reason = detect_follow_up("What about paid ones?", memory)
    
    assert is_follow_up is True


def test_followup_detector_detects_only_paid_followup():
    """Test that follow-up detector detects 'only paid' follow-ups."""
    memory = ConversationMemory()
    memory.add_turn(
        user_question="Show all orders",
        is_follow_up=False,
        rewritten_question="Show all orders",
        generated_sql="SELECT * FROM orders LIMIT 50;",
    )
    
    is_follow_up, reason = detect_follow_up("Only paid ones", memory)
    
    assert is_follow_up is True
