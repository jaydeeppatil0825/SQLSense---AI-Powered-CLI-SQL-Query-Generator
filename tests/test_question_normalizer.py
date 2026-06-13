"""
Tests for question normalization functionality.
"""

import pytest

from utils.question_normalizer import normalize_question, is_too_ambiguous, _is_already_clear_question


def test_normalize_customers():
    """Test that 'customers' normalizes to 'Show all customers'."""
    result, was_normalized = normalize_question("customers")
    assert result == "Show all customers"
    assert was_normalized is True


def test_normalize_customer_singular():
    """Test that 'customer' normalizes to 'Show all customers'."""
    result, was_normalized = normalize_question("customer")
    assert result == "Show all customers"
    assert was_normalized is True


def test_normalize_products():
    """Test that 'products' normalizes to 'Show all products'."""
    result, was_normalized = normalize_question("products")
    assert result == "Show all products"
    assert was_normalized is True


def test_normalize_orders():
    """Test that 'orders' normalizes to 'Show all orders'."""
    result, was_normalized = normalize_question("orders")
    assert result == "Show all orders"
    assert was_normalized is True


def test_normalize_payments():
    """Test that 'payments' normalizes to 'Show all payments'."""
    result, was_normalized = normalize_question("payments")
    assert result == "Show all payments"
    assert was_normalized is True


def test_normalize_employees():
    """Test that 'employees' normalizes to 'Show all employees'."""
    result, was_normalized = normalize_question("employees")
    assert result == "Show all employees"
    assert was_normalized is True


def test_normalize_support_tickets():
    """Test that 'support_tickets' normalizes to 'Show all support tickets'."""
    result, was_normalized = normalize_question("support_tickets")
    assert result == "Show all support tickets"
    assert was_normalized is True


def test_normalize_tickets():
    """Test that 'tickets' normalizes to 'Show all support tickets'."""
    result, was_normalized = normalize_question("tickets")
    assert result == "Show all support tickets"
    assert was_normalized is True


def test_normalize_sales():
    """Test that 'sales' normalizes to 'Show total sales from orders'."""
    result, was_normalized = normalize_question("sales")
    assert result == "Show total sales from orders"
    assert was_normalized is True


def test_normalize_total_sale():
    """Test that 'total sale' normalizes to 'Show total sales from orders'."""
    result, was_normalized = normalize_question("total sale")
    assert result == "Show total sales from orders"
    assert was_normalized is True


def test_normalize_monthly_sale():
    """Test that 'monthly sale' normalizes to 'Show monthly sales'."""
    result, was_normalized = normalize_question("monthly sale")
    assert result == "Show monthly sales"
    assert was_normalized is True


def test_normalize_month_wise_sale():
    """Test that 'month wise sale' normalizes to 'Show monthly sales'."""
    result, was_normalized = normalize_question("month wise sale")
    assert result == "Show monthly sales"
    assert was_normalized is True


def test_normalize_month_wise_sale_hyphen():
    """Test that 'month-wise sale' normalizes to 'Show monthly sales'."""
    result, was_normalized = normalize_question("month-wise sale")
    assert result == "Show monthly sales"
    assert was_normalized is True


def test_normalize_city_wise_sales():
    """Test that 'city wise sales' normalizes to 'Show total sales by customer city'."""
    result, was_normalized = normalize_question("city wise sales")
    assert result == "Show total sales by customer city"
    assert was_normalized is True


def test_normalize_revenue_category():
    """Test that 'revenue category' normalizes to 'Show revenue by product category'."""
    result, was_normalized = normalize_question("revenue category")
    assert result == "Show revenue by product category"
    assert was_normalized is True


def test_normalize_top_customer():
    """Test that 'top customer' normalizes to 'Show top 5 customers by total sales'."""
    result, was_normalized = normalize_question("top customer")
    assert result == "Show top 5 customers by total sales"
    assert was_normalized is True


def test_normalize_best_customer():
    """Test that 'best customer' normalizes to 'Show top 5 customers by total sales'."""
    result, was_normalized = normalize_question("best customer")
    assert result == "Show top 5 customers by total sales"
    assert was_normalized is True


def test_normalize_top_product():
    """Test that 'top product' normalizes to 'Show top selling products by quantity'."""
    result, was_normalized = normalize_question("top product")
    assert result == "Show top selling products by quantity"
    assert was_normalized is True


def test_normalize_pending_money():
    """Test that 'pending money' normalizes to 'Show pending payments'."""
    result, was_normalized = normalize_question("pending money")
    assert result == "Show pending payments"
    assert was_normalized is True


def test_normalize_paid_amount():
    """Test that 'paid amount' normalizes to 'Show total paid amount'."""
    result, was_normalized = normalize_question("paid amount")
    assert result == "Show total paid amount"
    assert was_normalized is True


def test_normalize_latest_order():
    """Test that 'latest order' normalizes to 'Show latest orders'."""
    result, was_normalized = normalize_question("latest order")
    assert result == "Show latest orders"
    assert was_normalized is True


def test_normalize_latest_orders():
    """Test that 'latest orders' normalizes to 'Show latest orders'."""
    result, was_normalized = normalize_question("latest orders")
    assert result == "Show latest orders"
    assert was_normalized is True


def test_no_normalization_for_clear_questions():
    """Test that clear questions are not modified."""
    result, was_normalized = normalize_question("Show all customers")
    assert result == "Show all customers"
    assert was_normalized is False


def test_no_normalization_for_list_questions():
    """Test that list questions are not modified."""
    result, was_normalized = normalize_question("List all products")
    assert result == "List all products"
    assert was_normalized is False


def test_no_normalization_for_count_questions():
    """Test that count questions are not modified."""
    result, was_normalized = normalize_question("How many orders are there?")
    assert result == "How many orders are there?"
    assert was_normalized is False


def test_no_normalization_for_what_questions():
    """Test that what questions are not modified."""
    result, was_normalized = normalize_question("What is our total sales amount?")
    assert result == "What is our total sales amount?"
    assert was_normalized is False


def test_no_normalization_for_can_you_questions():
    """Test that can you questions are not modified."""
    result, was_normalized = normalize_question("Can you show me all employees?")
    assert result == "Can you show me all employees?"
    assert was_normalized is False


def test_is_too_ambiguous_empty_string():
    """Test that empty string is too ambiguous."""
    assert is_too_ambiguous("") is True


def test_is_too_ambiguous_whitespace():
    """Test that whitespace is too ambiguous."""
    assert is_too_ambiguous("   ") is True


def test_is_too_ambiguous_single_char():
    """Test that single character is too ambiguous."""
    assert is_too_ambiguous("a") is True


def test_is_too_ambiguous_two_chars():
    """Test that two characters is too ambiguous."""
    assert is_too_ambiguous("ab") is True


def test_is_too_ambiguous_special_chars():
    """Test that special characters are too ambiguous."""
    assert is_too_ambiguous("!!!") is True


def test_is_too_ambiguous_normal_input():
    """Test that normal input is not too ambiguous."""
    assert is_too_ambiguous("customers") is False


def test_is_already_clear_question_show():
    """Test that questions starting with 'show' are clear."""
    assert _is_already_clear_question("show all customers") is True


def test_is_already_clear_question_list():
    """Test that questions starting with 'list' are clear."""
    assert _is_already_clear_question("list all products") is True


def test_is_already_clear_question_count():
    """Test that questions starting with 'count' are clear."""
    assert _is_already_clear_question("count orders") is True


def test_is_already_clear_question_what():
    """Test that questions starting with 'what' are clear."""
    assert _is_already_clear_question("what is total sales") is True


def test_is_already_clear_question_which():
    """Test that questions starting with 'which' are clear."""
    assert _is_already_clear_question("which customers are active") is True


def test_is_already_clear_question_how_many():
    """Test that 'how many' questions are clear."""
    assert _is_already_clear_question("how many customers do we have") is True


def test_is_already_clear_question_how_much():
    """Test that 'how much' questions are clear."""
    assert _is_already_clear_question("how much money has been paid") is True


def test_is_already_clear_question_can_you():
    """Test that 'can you' questions are clear."""
    assert _is_already_clear_question("can you show me all orders") is True


def test_is_already_clear_question_all():
    """Test that questions with 'all' are clear."""
    assert _is_already_clear_question("all customers") is True


def test_is_already_clear_question_short():
    """Test that very short inputs are not clear questions."""
    assert _is_already_clear_question("customers") is False


def test_is_already_clear_question_single_word():
    """Test that single word inputs are not clear questions."""
    assert _is_already_clear_question("sales") is False
