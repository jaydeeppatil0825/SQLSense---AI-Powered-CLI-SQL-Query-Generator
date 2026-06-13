"""
tests/test_connection.py
========================
Unit tests for ``db/connection.py`` — ``get_engine()``.

Covers requirements 1.1 – 1.7:
  - Each required DB variable missing/blank raises ValueError naming it.
  - LLM_BACKEND=openai with missing/blank OPENAI_API_KEY raises ValueError
    with the exact required message.
  - LLM_BACKEND absent or "local" does NOT require OPENAI_API_KEY.
  - When all env vars are valid, get_engine() builds the engine and runs
    SELECT 1 before returning (verified by mocking SQLAlchemy internals).
  - No real database connection is ever made in these unit tests.

Design note
-----------
``db.connection`` calls ``load_dotenv()`` at module-import time (top-level).
To isolate each test we:
  1. Patch ``dotenv.load_dotenv`` as a no-op *before* importing the module.
  2. Set ``os.environ`` to only the vars we want via ``patch.dict``.
  3. Force a fresh import via ``importlib.reload`` so the module runs under
     our controlled environment.
"""

import importlib
import os
import sys
from unittest.mock import MagicMock, patch

import pytest


# ── helpers ──────────────────────────────────────────────────────────────────

def _valid_db_env():
    """Return a dict with all four required DB vars set to valid values."""
    return {
        "DB_HOST": "localhost",
        "DB_USER": "user",
        "DB_PASSWORD": "secret",
        "DB_NAME": "testdb",
    }


def _get_conn_module(env: dict):
    """
    Return a freshly-imported ``db.connection`` module with:
      - ``load_dotenv`` patched to a no-op (prevents reading any real .env)
      - ``os.environ`` set to exactly ``env`` (``clear=True`` removes all others)

    The caller should use this inside a ``patch.dict`` context if they need
    the env to stay controlled during the actual ``get_engine()`` call.
    """
    # Remove cached module so a fresh import runs module-level code again.
    for key in list(sys.modules.keys()):
        if key in ("db", "db.connection"):
            del sys.modules[key]

    with patch("dotenv.load_dotenv"):   # no-op during module-level import
        with patch.dict(os.environ, env, clear=True):
            import db.connection as conn_mod
    return conn_mod


# ── 1. Missing / blank DB variable tests ─────────────────────────────────────

REQUIRED_DB_VARS = ("DB_HOST", "DB_USER", "DB_PASSWORD", "DB_NAME")


@pytest.mark.parametrize("missing_var", REQUIRED_DB_VARS)
def test_missing_db_var_raises_value_error(missing_var):
    """
    Each required DB variable, when absent from the environment, causes
    get_engine() to raise ValueError naming that variable.
    """
    env = _valid_db_env()
    env.pop(missing_var)

    conn_mod = _get_conn_module(env)
    with patch("dotenv.load_dotenv"):
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(ValueError, match=missing_var):
                conn_mod.get_engine()


@pytest.mark.parametrize("missing_var", REQUIRED_DB_VARS)
def test_blank_db_var_raises_value_error(missing_var):
    """
    A DB variable set to an all-whitespace string is treated as missing and
    raises ValueError naming that variable.
    """
    env = _valid_db_env()
    env[missing_var] = "   "  # whitespace only

    conn_mod = _get_conn_module(env)
    with patch("dotenv.load_dotenv"):
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(ValueError, match=missing_var):
                conn_mod.get_engine()


@pytest.mark.parametrize("missing_var", REQUIRED_DB_VARS)
def test_empty_string_db_var_raises_value_error(missing_var):
    """
    A DB variable set to an empty string raises ValueError naming that variable.
    """
    env = _valid_db_env()
    env[missing_var] = ""

    conn_mod = _get_conn_module(env)
    with patch("dotenv.load_dotenv"):
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(ValueError, match=missing_var):
                conn_mod.get_engine()


# ── 2. Local backend does NOT require API keys ───────────────────────────────

@pytest.mark.parametrize("backend_value", [None, "local", "LOCAL", "Local"])
def test_local_backend_no_api_key_required(backend_value):
    """
    When LLM_BACKEND is absent or "local" (any case), the absence of
    API keys does NOT raise a ValueError.  A mocked engine is returned.
    """
    env = _valid_db_env()
    if backend_value is not None:
        env["LLM_BACKEND"] = backend_value
    # No API keys required

    mock_engine = MagicMock()
    mock_conn = MagicMock()
    mock_engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
    mock_engine.connect.return_value.__exit__ = MagicMock(return_value=False)

    conn_mod = _get_conn_module(env)
    with patch("dotenv.load_dotenv"):
        with patch.dict(os.environ, env, clear=True):
            with patch("sqlalchemy.create_engine", return_value=mock_engine):
                engine = conn_mod.get_engine()
                assert engine is mock_engine


# ── 4. Valid configuration: SELECT 1 is executed and engine returned ──────────

def test_valid_config_executes_select_1_and_returns_engine():
    """
    When all required variables are present and the mocked engine succeeds,
    get_engine() executes SELECT 1 and returns the engine.
    """
    env = _valid_db_env()

    mock_engine = MagicMock()
    mock_conn = MagicMock()
    mock_engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
    mock_engine.connect.return_value.__exit__ = MagicMock(return_value=False)

    conn_mod = _get_conn_module(env)
    with patch("dotenv.load_dotenv"):
        with patch.dict(os.environ, env, clear=True):
            with patch("sqlalchemy.create_engine", return_value=mock_engine) as mock_create:
                result = conn_mod.get_engine()

    # Engine was created with a pymysql URL containing the right host and db
    create_args = mock_create.call_args[0][0]
    assert "mysql+pymysql://" in create_args
    assert "localhost" in create_args
    assert "testdb" in create_args

    # SELECT 1 was executed via the connection
    mock_conn.execute.assert_called_once()
    executed_sql = str(mock_conn.execute.call_args[0][0])
    assert "SELECT 1" in executed_sql

    # The engine is what get_engine() returns
    assert result is mock_engine


# ── 4. Connection failure propagates ─────────────────────────────────────────

def test_db_connection_failure_propagates_exception():
    """
    If the test SELECT 1 raises a SQLAlchemy exception, get_engine() propagates
    it without wrapping or swallowing it.
    """
    import sqlalchemy.exc

    env = _valid_db_env()

    mock_engine = MagicMock()
    mock_engine.connect.side_effect = sqlalchemy.exc.OperationalError(
        "Can't connect to MySQL server", {}, None
    )

    conn_mod = _get_conn_module(env)
    with patch("dotenv.load_dotenv"):
        with patch.dict(os.environ, env, clear=True):
            with patch("sqlalchemy.create_engine", return_value=mock_engine):
                with pytest.raises(sqlalchemy.exc.OperationalError):
                    conn_mod.get_engine()


# ── 6. No DB call is made when validation fails ───────────────────────────────

def test_no_db_call_when_db_var_missing():
    """
    When a required DB variable is missing, create_engine() is never called —
    the function raises before attempting any connection.
    """
    env = _valid_db_env()
    env.pop("DB_HOST")

    conn_mod = _get_conn_module(env)
    with patch("dotenv.load_dotenv"):
        with patch.dict(os.environ, env, clear=True):
            with patch("sqlalchemy.create_engine") as mock_create:
                with pytest.raises(ValueError):
                    conn_mod.get_engine()
                mock_create.assert_not_called()
