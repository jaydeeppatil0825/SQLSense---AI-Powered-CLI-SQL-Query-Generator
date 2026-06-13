"""
tests/test_action_detector.py
===============================
Tests for conversation action detection.
"""

import pytest

from conversation.action_detector import detect_conversation_action


def test_action_detector_detects_chart():
    """Test that action detector detects chart action."""
    action = detect_conversation_action("show chart for this")
    assert action == "chart"


def test_action_detector_detects_generate_chart():
    """Test that action detector detects generate chart action."""
    action = detect_conversation_action("generate chart")
    assert action == "chart"


def test_action_detector_detects_insights():
    """Test that action detector detects insights action."""
    action = detect_conversation_action("give insights for this")
    assert action == "insights"


def test_action_detector_detects_explain():
    """Test that action detector detects explain action."""
    action = detect_conversation_action("explain this result")
    assert action == "insights"


def test_action_detector_detects_new_chat():
    """Test that action detector detects new chat action."""
    action = detect_conversation_action("new chat")
    assert action == "new_chat"


def test_action_detector_detects_clear_chat():
    """Test that action detector detects clear chat action."""
    action = detect_conversation_action("clear chat")
    assert action == "new_chat"


def test_action_detector_detects_repeat_last_sql():
    """Test that action detector detects repeat last SQL action."""
    action = detect_conversation_action("show last sql")
    assert action == "repeat_last_sql"


def test_action_detector_detects_show_history():
    """Test that action detector detects show history action."""
    action = detect_conversation_action("show conversation history")
    assert action == "show_history"


def test_action_detector_returns_none_for_normal_question():
    """Test that action detector returns None for normal questions."""
    action = detect_conversation_action("Show all customers")
    assert action is None


def test_action_detector_returns_none_for_empty():
    """Test that action detector returns None for empty input."""
    action = detect_conversation_action("")
    assert action is None
