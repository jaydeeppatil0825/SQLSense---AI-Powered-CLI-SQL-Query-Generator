"""
conversation/question_rewriter.py
===================================
Rewrites follow-up questions into standalone questions using AI or rule-based fallback.
"""

from __future__ import annotations

import os
import re

from ai.sql_generator import _call_ai_backend
from utils.logger import get_logger


def rewrite_follow_up_question(
    user_question: str,
    conversation_memory,
    knowledge_base: dict,
    business_glossary: dict | None = None,
    ai_backend: str = "local",
) -> str:
    """
    Rewrite a follow-up question into a complete standalone question.
    
    Args:
        user_question: The user's follow-up question
        conversation_memory: The ConversationMemory instance
        knowledge_base: The knowledge base
        business_glossary: Optional business glossary
        ai_backend: The AI backend to use ("local" or "nvidia")
    
    Returns:
        Rewritten standalone question
    """
    logger = get_logger()
    
    context = conversation_memory.get_last_context()
    last_question = context.get("last_rewritten_question") or context.get("last_user_question")
    
    if not last_question:
        logger.warning("No previous question found, returning original question")
        return user_question
    
    # Try AI-based rewriting first
    if ai_backend == "nvidia" or ai_backend == "local":
        try:
            rewritten = _rewrite_with_ai(user_question, last_question, ai_backend)
            if rewritten:
                logger.info(f"AI rewrite successful: '{user_question}' -> '{rewritten}'")
                return rewritten
            else:
                logger.warning("AI rewrite returned empty, falling back to rule-based")
        except Exception as exc:
            logger.warning(f"AI rewrite failed: {exc}, falling back to rule-based")
    
    # Fall back to rule-based rewriting
    rewritten = _rewrite_with_rules(user_question, last_question)
    logger.info(f"Rule-based rewrite: '{user_question}' -> '{rewritten}'")
    return rewritten


def _rewrite_with_ai(user_question: str, last_question: str, ai_backend: str) -> str:
    """
    Rewrite the question using AI.
    
    Args:
        user_question: The user's follow-up question
        last_question: The previous question
        ai_backend: The AI backend to use
    
    Returns:
        Rewritten question or None if AI fails
    """
    prompt = f"""You are a SQL question rewriter. Your task is to rewrite follow-up questions into complete standalone questions.

Previous question: {last_question}
Follow-up question: {user_question}

Rewrite the follow-up question into a complete standalone question that can be understood without context.

Rules:
- Return ONLY the rewritten question.
- Do NOT return SQL.
- Do NOT explain your reasoning.
- Do NOT use markdown code blocks.
- Preserve the original business intent.
- Use previous context only when necessary.
- If the follow-up asks for a chart or insights, return a special action instead: "action: chart" or "action: insights"

Examples:
Previous: Show all customers
Follow-up: Where do they live?
Rewritten: Show customer names and cities from customers

Previous: Show monthly sales
Follow-up: Now only for Mumbai
Rewritten: Show monthly sales for customers in Mumbai

Previous: Show top 5 customers by total sales
Follow-up: Make it top 10
Rewritten: Show top 10 customers by total sales

Previous: Show revenue by product category
Follow-up: Sort highest first
Rewritten: Show revenue by product category sorted by highest revenue first

Rewritten question:"""

    messages = [{"role": "user", "content": prompt}]
    response = _call_ai_backend(messages, ai_backend)
    
    if not response:
        return None
    
    # Clean the response
    response = response.strip()
    
    # Remove markdown code blocks if present
    if response.startswith("```"):
        response = re.sub(r"```(?:json)?\n?", "", response)
        response = re.sub(r"\n```", "", response)
    
    # Remove common prefixes
    response = re.sub(r"^(Rewritten question:|Question:|Rewritten:)\s*", "", response, flags=re.IGNORECASE)
    
    # Check for action responses
    if response.lower().startswith("action:"):
        return response
    
    return response.strip()


def _rewrite_with_rules(user_question: str, last_question: str) -> str:
    """
    Rewrite the question using rule-based patterns.
    
    Args:
        user_question: The user's follow-up question
        last_question: The previous question
    
    Returns:
        Rewritten question
    """
    question_lower = user_question.lower().strip()
    
    # Pattern: "where do they live" after customers
    if re.search(r"where do (they|them) live", question_lower):
        if "customer" in last_question.lower():
            return "Show customer names and cities from customers"
        elif "employee" in last_question.lower():
            return "Show employee names and cities from employees"
        else:
            return f"{last_question} with location information"
    
    # Pattern: "make it top N"
    match = re.search(r"make it top (\d+)", question_lower)
    if match:
        new_limit = match.group(1)
        return re.sub(r"top \d+", f"top {new_limit}", last_question, flags=re.IGNORECASE)
    
    # Pattern: "make it N"
    match = re.search(r"make it (\d+)", question_lower)
    if match:
        new_limit = match.group(1)
        # Try to find and replace a number in the last question
        if re.search(r"\d+", last_question):
            return re.sub(r"\d+", new_limit, last_question, count=1)
        else:
            return f"Show top {new_limit} {last_question}"
    
    # Pattern: "now only [city/filter]"
    match = re.search(r"now only (\w+)", question_lower)
    if match:
        filter_value = match.group(1)
        return f"{last_question} for {filter_value}"
    
    # Pattern: "only [status]"
    match = re.search(r"only (\w+)", question_lower)
    if match:
        filter_value = match.group(1)
        if "paid" in filter_value:
            return f"{last_question} where payment_status = 'Paid'"
        elif "unpaid" in filter_value:
            return f"{last_question} where payment_status = 'Pending'"
        elif "pending" in filter_value:
            return f"{last_question} where status = 'Pending'"
        elif "cancelled" in filter_value:
            return f"{last_question} where status = 'Cancelled'"
        elif "delivered" in filter_value:
            return f"{last_question} where order_status = 'Delivered'"
        elif "shipped" in filter_value:
            return f"{last_question} where order_status = 'Shipped'"
        elif "active" in filter_value:
            return f"{last_question} where status = 'Active'"
        elif "inactive" in filter_value:
            return f"{last_question} where status = 'Inactive'"
        else:
            return f"{last_question} filtered by {filter_value}"
    
    # Pattern: "sort it by highest/lowest"
    if "sort highest first" in question_lower or "sort it highest" in question_lower:
        return f"{last_question} sorted by highest value first"
    elif "sort lowest first" in question_lower or "sort it lowest" in question_lower:
        return f"{last_question} sorted by lowest value first"
    elif "sort it" in question_lower:
        return f"{last_question} sorted"
    
    # Pattern: "compare with [value]"
    match = re.search(r"compare with (\w+)", question_lower)
    if match:
        compare_value = match.group(1)
        return f"{last_question} compared with {compare_value}"
    
    # Pattern: "what about [term]"
    match = re.search(r"what about (\w+)", question_lower)
    if match:
        term = match.group(1)
        # Replace the main entity in the last question
        return re.sub(r"\b(customers|orders|products|employees)\b", term, last_question, flags=re.IGNORECASE)
    
    # Pattern: "show their [attribute]"
    match = re.search(r"show (their|their) (\w+)", question_lower)
    if match:
        attribute = match.group(2)
        return f"Show {attribute} from {last_question}"
    
    # Default: append to last question
    return f"{last_question} {user_question}"
