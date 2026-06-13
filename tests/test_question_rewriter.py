"""
tests/test_question_rewriter.py
================================
Tests for follow-up question rewriting.
"""

import pytest

from conversation.question_rewriter import rewrite_follow_up_question
from conversation.conversation_memory import ConversationMemory


def test_rewriter_fallback_converts_where_do_they_live():
    """Test that rewriter converts 'where do they live' after customers."""
    memory = ConversationMemory()
    memory.add_turn(
        user_question="Show all customers",
        is_follow_up=False,
        rewritten_question="Show all customers",
        generated_sql="SELECT * FROM customers LIMIT 50;",
    )
    
    knowledge_base = {"customers": {"columns": [{"name": "city", "type": "VARCHAR"}]}}
    
    rewritten = rewrite_follow_up_question(
        "Where do they live?",
        memory,
        knowledge_base,
        None,
        "local",
    )
    
    # The rewriter should produce a question about customer location
    assert "customer" in rewritten.lower()
    # Accept either "city" or "address" or "location"
    assert any(term in rewritten.lower() for term in ["city", "address", "location"])


def test_rewriter_fallback_converts_make_it_top_10():
    """Test that rule-based fallback converts 'make it top 10' after top 5."""
    memory = ConversationMemory()
    memory.add_turn(
        user_question="Show top 5 customers by total sales",
        is_follow_up=False,
        rewritten_question="Show top 5 customers by total sales",
        generated_sql="SELECT * FROM customers LIMIT 50;",
    )
    
    knowledge_base = {}
    
    rewritten = rewrite_follow_up_question(
        "Make it top 10",
        memory,
        knowledge_base,
        None,
        "local",
    )
    
    assert "top 10" in rewritten.lower()
    assert "top 5" not in rewritten.lower()


def test_rewriter_fallback_converts_now_only_mumbai():
    """Test that rule-based fallback converts 'now only Mumbai' after monthly sales."""
    memory = ConversationMemory()
    memory.add_turn(
        user_question="Show monthly sales",
        is_follow_up=False,
        rewritten_question="Show monthly sales",
        generated_sql="SELECT * FROM sales LIMIT 50;",
    )
    
    knowledge_base = {}
    
    rewritten = rewrite_follow_up_question(
        "Now only Mumbai",
        memory,
        knowledge_base,
        None,
        "local",
    )
    
    assert "mumbai" in rewritten.lower()
    assert "monthly sales" in rewritten.lower()


def test_rewriter_fallback_converts_only_paid():
    """Test that rewriter converts 'only paid' to paid filter."""
    memory = ConversationMemory()
    memory.add_turn(
        user_question="Show all orders",
        is_follow_up=False,
        rewritten_question="Show all orders",
        generated_sql="SELECT * FROM orders LIMIT 50;",
    )
    
    knowledge_base = {}
    
    rewritten = rewrite_follow_up_question(
        "Only paid ones",
        memory,
        knowledge_base,
        None,
        "local",
    )
    
    # The rewriter should produce a question about paid orders
    assert "paid" in rewritten.lower()


def test_rewriter_fallback_converts_sort_highest():
    """Test that rule-based fallback converts 'sort highest first'."""
    memory = ConversationMemory()
    memory.add_turn(
        user_question="Show revenue by product category",
        is_follow_up=False,
        rewritten_question="Show revenue by product category",
        generated_sql="SELECT * FROM sales LIMIT 50;",
    )
    
    knowledge_base = {}
    
    rewritten = rewrite_follow_up_question(
        "Sort highest first",
        memory,
        knowledge_base,
        None,
        "local",
    )
    
    assert "highest" in rewritten.lower()
    assert "sorted" in rewritten.lower()


def test_rewriter_returns_original_when_no_previous_question():
    """Test that rewriter returns original question when there's no previous question."""
    memory = ConversationMemory()
    
    knowledge_base = {}
    
    rewritten = rewrite_follow_up_question(
        "Show all customers",
        memory,
        knowledge_base,
        None,
        "local",
    )
    
    assert rewritten == "Show all customers"


def test_rewriter_appends_ambiguous_followup():
    """Test that rewriter appends ambiguous follow-up to last question."""
    memory = ConversationMemory()
    memory.add_turn(
        user_question="Show all customers",
        is_follow_up=False,
        rewritten_question="Show all customers",
        generated_sql="SELECT * FROM customers LIMIT 50;",
    )
    
    knowledge_base = {}
    
    rewritten = rewrite_follow_up_question(
        "with email",
        memory,
        knowledge_base,
        None,
        "local",
    )
    
    # The rewriter should produce a question about customers with email
    assert "customer" in rewritten.lower() or "email" in rewritten.lower()
