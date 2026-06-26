"""
kb_pipeline/connection.py
=========================
Connection helpers for the active SQLSense runtime.

This module is intentionally MySQL-only in the current checkout. It provides
two entry points for obtaining a validated SQLAlchemy engine:

* ``get_engine()`` reads credentials from environment variables.
* ``connect_engine()`` builds an engine from caller-supplied parameters.

Both functions execute ``SELECT 1`` before returning so callers only receive a
live connection.
"""

import os
from urllib.parse import quote_plus

from dotenv import load_dotenv
import sqlalchemy
from sqlalchemy import text


load_dotenv()

_REQUIRED_DB_VARS = ("DB_HOST", "DB_USER", "DB_PASSWORD", "DB_NAME")
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
    Build and return a validated SQLAlchemy MySQL engine using environment values.

    Raises ``ValueError`` when required DB variables are missing or blank.
    Raises ``sqlalchemy.exc.SQLAlchemyError`` when connectivity fails.
    """
    for var in _REQUIRED_DB_VARS:
        value = os.getenv(var, "")
        if not value or not value.strip():
            raise ValueError(f"Missing required environment variable: {var}")

    connection_url = _mysql_connection_url(
        host=os.getenv("DB_HOST", "").strip(),
        username=os.getenv("DB_USER", "").strip(),
        password=os.getenv("DB_PASSWORD", "").strip(),
        database=os.getenv("DB_NAME", "").strip(),
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
            host=str(host).strip(),
            port=port,
            username=str(username).strip(),
            password=password,
        )
    )
    try:
        with engine.connect() as conn:
            rows = conn.execute(text("SHOW DATABASES"))
            return [str(row[0]) for row in rows if row and row[0] is not None]
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
    Build and return a validated SQLAlchemy MySQL engine from caller-supplied
    connection parameters.

    ``sqlite_path`` is kept only for backward-compatible call signatures and is
    ignored in the current MySQL-only runtime.
    """
    db_type = str(db_type or "").strip().lower()
    del sqlite_path

    if db_type not in SUPPORTED_DB_TYPES:
        raise ValueError(
            f"Unsupported database type: '{db_type}'. "
            f"Supported types: {', '.join(SUPPORTED_DB_TYPES)}"
        )

    for field, value in (("host", host), ("username", username), ("database", database)):
        if not value or not str(value).strip():
            raise ValueError(f"'{field}' is required for MySQL connections.")

    url = _mysql_connection_url(
        host=str(host).strip(),
        port=port,
        username=str(username).strip(),
        password=password,
        database=str(database).strip(),
    )
    engine = sqlalchemy.create_engine(url)
    return _test_and_return(engine)
