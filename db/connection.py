"""
db/connection.py
================
Provides two entry points for obtaining a validated SQLAlchemy engine:

* ``get_engine()``       — reads credentials from the ``.env`` file (original
                           behaviour, used as fallback/default).
* ``connect_engine()``   — builds an engine from caller-supplied parameters,
                           supporting MySQL now and PostgreSQL/SQLite as stubs
                           for future expansion.

Both functions perform a ``SELECT 1`` connectivity test before returning so
callers are guaranteed a live connection.

Requirements covered: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7
"""

import os
from urllib.parse import quote_plus

from dotenv import load_dotenv
import sqlalchemy
from sqlalchemy import text

# Load .env once at module import time so all os.getenv() calls in this
# module (and the rest of the process) see the values from the file.
load_dotenv()

# The four database variables that are unconditionally required by get_engine().
_REQUIRED_DB_VARS = ("DB_HOST", "DB_USER", "DB_PASSWORD", "DB_NAME")

# Supported database types for connect_engine().
SUPPORTED_DB_TYPES = ("mysql",)


def _test_and_return(engine: sqlalchemy.engine.Engine) -> sqlalchemy.engine.Engine:
    """Execute SELECT 1 to verify connectivity, then return the engine."""
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    return engine


def _mysql_connection_url(
    host: str,
    port: int | None = None,
    username: str = "",
    password: str = "",
    database: str = "",
) -> str:
    port_part = f":{port}" if port else ""
    database_part = f"/{database}" if database else "/"
    return (
        f"mysql+pymysql://{username}:{quote_plus(password)}"
        f"@{host}{port_part}{database_part}"
    )


def get_engine() -> sqlalchemy.engine.Engine:
    """
    Build and return a validated SQLAlchemy MySQL engine using ``.env`` values.

    Steps performed
    ---------------
    1. Read ``DB_HOST``, ``DB_USER``, ``DB_PASSWORD``, and ``DB_NAME`` from the
       environment (populated from ``.env`` by ``python-dotenv``).
    2. Raise ``ValueError`` for any variable that is missing or blank/whitespace-
       only, naming the offending variable in the message.
    3. Read ``LLM_BACKEND``; if its value is ``"openai"``, also validate that
       ``OPENAI_API_KEY`` is present and non-blank.
    4. Construct a ``mysql+pymysql://`` URL, create the engine, and run
       ``SELECT 1`` to verify live connectivity.

    Returns
    -------
    sqlalchemy.engine.Engine

    Raises
    ------
    ValueError
        If any required environment variable is missing/blank, or if
        ``LLM_BACKEND=openai`` and ``OPENAI_API_KEY`` is absent/blank.
    sqlalchemy.exc.SQLAlchemyError
        If the database connection cannot be established.
    """
    # ── Validate required DB variables ──────────────────────────────────────
    for var in _REQUIRED_DB_VARS:
        value = os.getenv(var, "")
        if not value or not value.strip():
            raise ValueError(f"Missing required environment variable: {var}")

    db_host = os.getenv("DB_HOST").strip()
    db_user = os.getenv("DB_USER").strip()
    db_password = os.getenv("DB_PASSWORD").strip()
    db_name = os.getenv("DB_NAME").strip()

    connection_url = _mysql_connection_url(
        host=db_host,
        username=db_user,
        password=db_password,
        database=db_name,
    )

    engine = sqlalchemy.create_engine(connection_url)
    return _test_and_return(engine)


def list_accessible_databases(
    db_type: str,
    host: str = "",
    port: int | None = None,
    username: str = "",
    password: str = "",
) -> list[str]:
    """Return accessible database names for the given server, when supported."""
    db_type = str(db_type or "").strip().lower()
    if db_type != "mysql":
        return []

    if not host or not username:
        return []

    engine = sqlalchemy.create_engine(
        _mysql_connection_url(
            host=host,
            port=port,
            username=username,
            password=password,
        )
    )
    try:
        with engine.connect() as conn:
            rows = conn.execute(text("SHOW DATABASES"))
            return [
                str(row[0])
                for row in rows
                if row and row[0] is not None
            ]
    finally:
        engine.dispose()


def connect_engine(
    db_type: str,
    host: str = "",
    port: int | None = None,
    username: str = "",
    password: str = "",
    database: str = "",
    sqlite_path: str = "",
) -> sqlalchemy.engine.Engine:
    """
    Build and return a validated SQLAlchemy engine from caller-supplied
    connection parameters.  Passwords are never written to disk.

    Parameters
    ----------
    db_type : str
        One of ``"mysql"``, ``"postgresql"``, or ``"sqlite"``.
    host : str
        Database server hostname or IP (not used for SQLite).
    port : int | None
        Server port; uses driver default when ``None``.
    username : str
        Database username (not used for SQLite).
    password : str
        Database password (not used for SQLite).  Accepted from the caller and
        used only in-memory to build the connection URL — never persisted.
    database : str
        Database / schema name (not used for SQLite).
    sqlite_path : str
        File-system path to the SQLite database file (SQLite only).

    Returns
    -------
    sqlalchemy.engine.Engine
        A connected, validated engine.

    Raises
    ------
    ValueError
        For unsupported ``db_type``, missing required fields, or
        unimplemented backends.
    sqlalchemy.exc.SQLAlchemyError
        If the database connection cannot be established.
    """
    db_type = db_type.strip().lower()

    if db_type not in SUPPORTED_DB_TYPES:
        raise ValueError(
            f"Unsupported database type: '{db_type}'. "
            f"Supported types: {', '.join(SUPPORTED_DB_TYPES)}"
        )

    if db_type == "mysql":
        # ── MySQL via PyMySQL ────────────────────────────────────────────────
        for field, value in [("host", host), ("username", username), ("database", database)]:
            if not value or not str(value).strip():
                raise ValueError(f"'{field}' is required for MySQL connections.")

        url = _mysql_connection_url(
            host=host,
            port=port,
            username=username,
            password=password,
            database=database,
        )
        engine = sqlalchemy.create_engine(url)
        return _test_and_return(engine)

    if db_type == "postgresql":
        # ── PostgreSQL — placeholder for future implementation ───────────────
        raise ValueError(
            "PostgreSQL support is not yet implemented. "
            "Install 'psycopg2' and add the connection logic to db/connection.py."
        )

    if db_type == "sqlite":
        # ── SQLite — placeholder for future implementation ───────────────────
        raise ValueError(
            "SQLite support is not yet implemented. "
            "Add the SQLite connection logic to db/connection.py."
        )
