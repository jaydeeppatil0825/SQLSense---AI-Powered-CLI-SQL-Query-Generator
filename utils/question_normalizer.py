"""
utils/question_normalizer.py
============================
Normalizes short, incomplete, or simple natural-language inputs into clear
standalone questions before SQL generation.
"""

from __future__ import annotations

import re
from typing import Optional, Tuple

from utils.logger import get_logger


# Table name mappings for simple inputs
_TABLE_NORMALIZATION = {
    "customers": "Show all customers",
    "customer": "Show all customers",
    "products": "Show all products",
    "product": "Show all products",
    "orders": "Show all orders",
    "order": "Show all orders",
    "payments": "Show all payments",
    "payment": "Show all payments",
    "employees": "Show all employees",
    "employee": "Show all employees",
    "support_tickets": "Show all support tickets",
    "support ticket": "Show all support tickets",
    "tickets": "Show all support tickets",
    "ticket": "Show all support tickets",
}

# Sales/revenue patterns (simple string matching)
_SALES_PATTERNS = [
    ("sales", "total sales"),
    ("total sale", "total sales"),
    ("monthly sale", "monthly sales"),
    ("month wise sale", "monthly sales"),
    ("month-wise sale", "monthly sales"),
    ("monthly sales", "monthly sales"),
    ("month wise sales", "monthly sales"),
    ("month-wise sales", "monthly sales"),
    ("revenue", "total revenue"),
]

# Aggregation patterns (simple string matching)
_AGGREGATION_PATTERNS = [
    ("city wise sales", "Show total sales by customer city"),
    ("city-wise sales", "Show total sales by customer city"),
    ("revenue category", "Show revenue by product category"),
    ("revenue by category", "Show revenue by product category"),
]

# Top/best patterns (simple string matching)
_TOP_PATTERNS = [
    ("top customer", "Show top 5 customers by total sales"),
    ("best customer", "Show top 5 customers by total sales"),
    ("top product", "Show top selling products by quantity"),
    ("best product", "Show top selling products by quantity"),
]

# Payment status patterns (simple string matching)
_PAYMENT_PATTERNS = [
    ("pending money", "Show pending payments"),
    ("paid amount", "Show total paid amount"),
    ("total paid", "Show total paid amount"),
]

# Latest/recent patterns (simple string matching)
_LATEST_PATTERNS = [
    ("latest order", "Show latest orders"),
    ("latest orders", "Show latest orders"),
    ("recent order", "Show latest orders"),
    ("recent orders", "Show latest orders"),
]


def normalize_question(user_input: str) -> Tuple[str, bool]:
    """
    Normalize a short or incomplete user input into a clear standalone question.
    
    Args:
        user_input: The raw user input string
    
    Returns:
        Tuple of (normalized_question, was_normalized)
        - normalized_question: The clear, expanded question
        - was_normalized: True if normalization was applied, False if input was already clear
    
    Examples:
        >>> normalize_question("customers")
        ("Show all customers", True)
        
        >>> normalize_question("Show all customers")
        ("Show all customers", False)
    """
    logger = get_logger()
    
    if not user_input or not user_input.strip():
        return user_input, False
    
    original = user_input.strip()
    lower_input = original.lower()
    
    # Check if input is already a clear question (has verbs like "show", "list", "count", etc.)
    if _is_already_clear_question(lower_input):
        logger.debug(f"Input is already a clear question: {original}")
        return original, False
    
    # Try table name normalization
    for pattern, normalized in _TABLE_NORMALIZATION.items():
        if pattern == lower_input:
            logger.debug(f"Normalized '{original}' to '{normalized}'")
            return normalized, True
    
    # Try sales patterns (simple string matching)
    for pattern, normalized in _SALES_PATTERNS:
        # Check if input matches the pattern exactly (or with different spacing)
        if lower_input == pattern or lower_input.replace(" ", "") == pattern.replace(" ", ""):
            if "monthly" in normalized:
                result = f"Show {normalized}"
            else:
                result = f"Show {normalized} from orders"
            logger.debug(f"Normalized '{original}' to '{result}'")
            return result, True
    
    # Try aggregation patterns (simple string matching)
    for pattern, normalized in _AGGREGATION_PATTERNS:
        if lower_input == pattern or lower_input.replace(" ", "") == pattern.replace(" ", ""):
            logger.debug(f"Normalized '{original}' to '{normalized}'")
            return normalized, True
    
    # Try top/best patterns (simple string matching)
    for pattern, normalized in _TOP_PATTERNS:
        if lower_input == pattern or lower_input.replace(" ", "") == pattern.replace(" ", ""):
            logger.debug(f"Normalized '{original}' to '{normalized}'")
            return normalized, True
    
    # Try payment patterns (simple string matching)
    for pattern, normalized in _PAYMENT_PATTERNS:
        if lower_input == pattern or lower_input.replace(" ", "") == pattern.replace(" ", ""):
            logger.debug(f"Normalized '{original}' to '{normalized}'")
            return normalized, True
    
    # Try latest patterns (simple string matching)
    for pattern, normalized in _LATEST_PATTERNS:
        if lower_input == pattern or lower_input.replace(" ", "") == pattern.replace(" ", ""):
            logger.debug(f"Normalized '{original}' to '{normalized}'")
            return normalized, True
    
    # If no pattern matched, return original as-is
    logger.debug(f"No normalization pattern matched for: {original}")
    return original, False


def _is_already_clear_question(question: str) -> bool:
    """
    Check if the input is already a clear, complete question.
    
    A clear question typically starts with action verbs like:
    - show, list, display, get, give, find, search
    - count, how many, how much
    - what, which, who
    
    Args:
        question: The lowercased question string
    
    Returns:
        True if the input appears to be a clear question
    """
    # Action verbs that indicate a clear question
    action_verbs = {
        "show", "list", "display", "get", "give", "find", "search",
        "count", "calculate", "sum", "average",
        "what", "which", "who", "when", "where", "how",
    }
    
    # Check if question starts with an action verb
    words = question.split()
    if words and words[0] in action_verbs:
        return True
    
    # Check for "how many" or "how much" patterns
    if question.startswith("how many") or question.startswith("how much"):
        return True
    
    # Check for "can you" pattern
    if question.startswith("can you"):
        return True
    
    # Check for minimum length (very short inputs are likely not clear questions)
    if len(question.split()) < 2:
        return False
    
    # Check if it contains "all" followed by a table name
    if "all" in words:
        return True
    
    return False


def is_too_ambiguous(user_input: str) -> bool:
    """
    Check if the input is too ambiguous to normalize safely.
    
    Args:
        user_input: The raw user input string
    
    Returns:
        True if input is too ambiguous and requires clarification
    """
    if not user_input or not user_input.strip():
        return True
    
    # Single character inputs are too ambiguous
    if len(user_input.strip()) <= 2:
        return True
    
    # Inputs with only special characters
    if re.match(r"^[\W_]+$", user_input.strip()):
        return True
    
    return False
