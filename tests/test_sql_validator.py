"""
tests/test_sql_validator.py

Unit tests for utils/sql_validator.py — validate_sql() and add_limit_if_missing().
Covers Requirements 8.1 – 8.10.
"""

import pytest
from utils.sql_validator import validate_sql, add_limit_if_missing


# ---------------------------------------------------------------------------
# validate_sql — empty / None / whitespace  (Req 8.1)
# ---------------------------------------------------------------------------

class TestValidateSqlEmpty:
    def test_none_input(self):
        valid, msg = validate_sql(None)
        assert valid is False
        assert msg == "SQL must be a string."

    def test_empty_string(self):
        valid, msg = validate_sql("")
        assert valid is False
        assert msg == "SQL query is empty or missing."

    def test_whitespace_only(self):
        valid, msg = validate_sql("   \t\n  ")
        assert valid is False
        assert msg == "SQL query is empty or missing."


# ---------------------------------------------------------------------------
# validate_sql — SELECT prefix check  (Req 8.2, 8.3)
# ---------------------------------------------------------------------------

class TestValidateSqlSelectPrefix:
    def test_update_rejected(self):
        valid, msg = validate_sql("UPDATE users SET name='x'")
        assert valid is False
        assert msg == "Only SELECT queries are allowed."

    def test_insert_as_prefix_rejected(self):
        valid, msg = validate_sql("INSERT INTO t VALUES (1)")
        assert valid is False
        assert msg == "Only SELECT queries are allowed."

    def test_lowercase_select_accepted(self):
        valid, _ = validate_sql("select * from users")
        assert valid is True

    def test_mixed_case_select_accepted(self):
        valid, _ = validate_sql("SeLeCt id FROM users")
        assert valid is True

    def test_leading_whitespace_stripped(self):
        valid, _ = validate_sql("  SELECT 1")
        assert valid is True


# ---------------------------------------------------------------------------
# validate_sql — forbidden keyword detection  (Req 8.4, 8.5)
# ---------------------------------------------------------------------------

FORBIDDEN_KEYWORDS = ["DROP", "DELETE", "UPDATE", "INSERT", "ALTER", "TRUNCATE", "CREATE", "RECREATE"]

class TestValidateSqlForbiddenKeywords:
    @pytest.mark.parametrize("keyword", FORBIDDEN_KEYWORDS)
    def test_forbidden_keyword_uppercase(self, keyword):
        sql = f"SELECT * FROM t WHERE 1=1; {keyword} TABLE x"
        valid, msg = validate_sql(sql)
        # Note: semicolon mid-string may trigger the multiple-statements check first;
        # test that it is rejected for either reason.
        assert valid is False

    @pytest.mark.parametrize("keyword", FORBIDDEN_KEYWORDS)
    def test_forbidden_keyword_lowercase_in_select(self, keyword):
        """Keyword embedded after SELECT — should be caught by forbidden-keyword check."""
        sql = f"SELECT * FROM t; {keyword.lower()} TABLE x"
        valid, msg = validate_sql(sql)
        assert valid is False

    def test_drop_as_whole_word(self):
        sql = "SELECT drop_column FROM t"
        # 'drop_column' is NOT a whole-word match for DROP, so it should pass the
        # keyword check (but still be a valid SELECT).
        valid, msg = validate_sql(sql)
        assert valid is True
        assert msg == "SQL is safe"

    def test_delete_as_whole_word_in_column_name(self):
        # 'deletedAt' should NOT trigger the DELETE keyword check
        sql = "SELECT deletedAt FROM t"
        valid, msg = validate_sql(sql)
        assert valid is True

    def test_drop_as_standalone_word_rejected(self):
        sql = "SELECT * FROM t WHERE 1; DROP TABLE users"
        valid, msg = validate_sql(sql)
        assert valid is False

    def test_truncate_detected(self):
        # TRUNCATE embedded (whole word, no semicolon trick)
        sql = "SELECT TRUNCATE(3.14, 1)"
        # TRUNCATE as a SQL function — still a forbidden whole word
        valid, msg = validate_sql(sql)
        assert valid is False
        assert "TRUNCATE" in msg

    def test_create_detected(self):
        sql = "SELECT * FROM t UNION ALL CREATE TABLE x (id INT)"
        valid, msg = validate_sql(sql)
        assert valid is False
        assert "CREATE" in msg

    def test_replace_detected(self):
        sql = "SELECT REPLACE(name, 'a', 'b') FROM users"
        # REPLACE as a SQL function — still flagged by the safety validator
        valid, msg = validate_sql(sql)
        assert valid is False
        assert "REPLACE" in msg

    def test_error_message_names_first_keyword(self):
        # DROP appears before DELETE — message should name DROP
        sql = "SELECT 1 DROP DELETE"
        valid, msg = validate_sql(sql)
        assert valid is False
        assert "DROP" in msg


# ---------------------------------------------------------------------------
# validate_sql — multiple statements / mid-string semicolons  (Req 8.6, 8.7)
# ---------------------------------------------------------------------------

class TestValidateSqlSemicolon:
    def test_trailing_semicolon_allowed(self):
        valid, msg = validate_sql("SELECT * FROM users;")
        assert valid is True
        assert msg == "SQL is safe"

    def test_trailing_semicolon_with_space_allowed(self):
        valid, msg = validate_sql("SELECT * FROM users ;")
        assert valid is True

    def test_mid_string_semicolon_rejected(self):
        valid, msg = validate_sql("SELECT 1; SELECT 2")
        assert valid is False
        assert msg == "Multiple SQL statements are not allowed."

    def test_multiple_semicolons_rejected(self):
        valid, msg = validate_sql("SELECT 1; SELECT 2; SELECT 3")
        assert valid is False
        assert msg == "Multiple SQL statements are not allowed."

    def test_no_semicolon_passes(self):
        valid, msg = validate_sql("SELECT id, name FROM customers WHERE active = 1")
        assert valid is True
        assert msg == "SQL is safe"


class TestValidateSqlComments:
    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT * FROM users -- hide the rest",
            "SELECT * FROM users # hide the rest",
            "SELECT /*+ hint */ * FROM users",
            "SELECT /*!50000 1 */ FROM users",
        ],
    )
    def test_comments_rejected(self, sql):
        valid, msg = validate_sql(sql)

        assert valid is False
        assert msg == "SQL comments are not allowed."


# ---------------------------------------------------------------------------
# validate_sql — passing case  (Req 8.8)
# ---------------------------------------------------------------------------

class TestValidateSqlPass:
    def test_simple_select(self):
        valid, msg = validate_sql("SELECT * FROM orders")
        assert valid is True
        assert msg == "SQL is safe"

    def test_select_with_join(self):
        sql = ("SELECT o.id, c.name FROM orders o "
               "JOIN customers c ON o.customer_id = c.id "
               "WHERE o.status = 'open'")
        valid, msg = validate_sql(sql)
        assert valid is True
        assert msg == "SQL is safe"

    def test_select_with_limit(self):
        valid, msg = validate_sql("SELECT * FROM products LIMIT 10")
        assert valid is True
        assert msg == "SQL is safe"

    def test_select_with_trailing_semicolon_and_limit(self):
        valid, msg = validate_sql("SELECT id FROM t LIMIT 5;")
        assert valid is True
        assert msg == "SQL is safe"


# ---------------------------------------------------------------------------
# add_limit_if_missing — appends LIMIT when absent  (Req 8.9)
# ---------------------------------------------------------------------------

class TestAddLimitIfMissing:
    def test_appends_limit_no_semicolon(self):
        result = add_limit_if_missing("SELECT * FROM users")
        assert result == "SELECT * FROM users LIMIT 50"

    def test_appends_limit_before_trailing_semicolon(self):
        result = add_limit_if_missing("SELECT * FROM users;")
        assert result == "SELECT * FROM users LIMIT 50;"

    def test_appends_limit_before_semicolon_with_space(self):
        result = add_limit_if_missing("SELECT * FROM users ;")
        assert result == "SELECT * FROM users LIMIT 50 ;"

    def test_custom_limit_value(self):
        result = add_limit_if_missing("SELECT * FROM t", limit=100)
        assert result == "SELECT * FROM t LIMIT 100"

    def test_default_limit_is_50(self):
        result = add_limit_if_missing("SELECT id FROM t")
        assert "LIMIT 50" in result


# ---------------------------------------------------------------------------
# add_limit_if_missing — unchanged when LIMIT already present  (Req 8.10)
# ---------------------------------------------------------------------------

class TestAddLimitIfMissingIdempotent:
    def test_limit_already_present_uppercase(self):
        sql = "SELECT * FROM users LIMIT 10"
        assert add_limit_if_missing(sql) == sql

    def test_limit_already_present_lowercase(self):
        sql = "SELECT * FROM users limit 10"
        assert add_limit_if_missing(sql) == sql

    def test_limit_already_present_mixed_case(self):
        sql = "SELECT * FROM users LiMiT 25"
        assert add_limit_if_missing(sql) == sql

    def test_limit_present_with_semicolon(self):
        sql = "SELECT * FROM users LIMIT 5;"
        assert add_limit_if_missing(sql) == sql

    def test_idempotent_double_application(self):
        """Applying twice must yield the same result as applying once."""
        sql = "SELECT * FROM orders"
        once = add_limit_if_missing(sql)
        twice = add_limit_if_missing(once)
        assert once == twice

    def test_idempotent_when_limit_present(self):
        sql = "SELECT * FROM orders LIMIT 20"
        assert add_limit_if_missing(add_limit_if_missing(sql)) == add_limit_if_missing(sql)
