"""Tests for generic question normalization behavior."""

from utils.question_normalizer import (
    _is_already_clear_question,
    is_too_ambiguous,
    normalize_question,
)


def test_normalize_preserves_business_words_for_sales_question():
    result, was_normalized = normalize_question("show sales by month")
    assert result == "show sales by month"
    assert was_normalized is False


def test_normalize_preserves_business_words_for_top_question():
    result, was_normalized = normalize_question("top customers by revenue")
    assert result == "top customers by revenue"
    assert was_normalized is False


def test_normalize_preserves_pending_billed_question():
    result, was_normalized = normalize_question("pending billed amount by account")
    assert result == "pending billed amount by account"
    assert was_normalized is False


def test_normalize_only_trims_and_collapses_whitespace():
    result, was_normalized = normalize_question("  pending   billed amount   by   account  ")
    assert result == "pending billed amount by account"
    assert was_normalized is True


def test_normalize_removes_control_characters():
    result, was_normalized = normalize_question("show\x00all\x1fcustomers")
    assert result == "show all customers"
    assert was_normalized is True


def test_normalize_does_not_inject_table_name_for_single_business_word():
    result, was_normalized = normalize_question("customers")
    assert result == "customers"
    assert was_normalized is False


def test_normalize_does_not_infer_templates_for_short_business_phrase():
    result, was_normalized = normalize_question("latest order")
    assert result == "latest order"
    assert was_normalized is False


def test_normalize_none_input_is_safe():
    result, was_normalized = normalize_question(None)
    assert result is None
    assert was_normalized is False


def test_is_too_ambiguous_empty_string():
    assert is_too_ambiguous("") is True


def test_is_too_ambiguous_whitespace():
    assert is_too_ambiguous("   ") is True


def test_is_too_ambiguous_single_char():
    assert is_too_ambiguous("a") is True


def test_is_too_ambiguous_two_chars():
    assert is_too_ambiguous("ab") is True


def test_is_too_ambiguous_special_chars():
    assert is_too_ambiguous("!!!") is True


def test_is_too_ambiguous_normal_input():
    assert is_too_ambiguous("customers") is False


def test_is_already_clear_question_show():
    assert _is_already_clear_question("show all customers") is True


def test_is_already_clear_question_list():
    assert _is_already_clear_question("list all products") is True


def test_is_already_clear_question_count():
    assert _is_already_clear_question("count orders") is True


def test_is_already_clear_question_what():
    assert _is_already_clear_question("what is total sales") is True


def test_is_already_clear_question_which():
    assert _is_already_clear_question("which customers are active") is True


def test_is_already_clear_question_how_many():
    assert _is_already_clear_question("how many customers do we have") is True


def test_is_already_clear_question_how_much():
    assert _is_already_clear_question("how much money has been paid") is True


def test_is_already_clear_question_can_you():
    assert _is_already_clear_question("can you show me all orders") is True


def test_is_already_clear_question_all():
    assert _is_already_clear_question("all customers") is True


def test_is_already_clear_question_short():
    assert _is_already_clear_question("customers") is False


def test_is_already_clear_question_single_word():
    assert _is_already_clear_question("sales") is False
