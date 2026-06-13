"""
utils/file_utils.py

Provides JSON persistence helpers for the AI SQL Query Generator.
Used to save and load the knowledge base between sessions.

Why a custom serializer?
------------------------
When profiling a real database, SQLAlchemy returns Python types that the
standard json.dump() cannot handle:

  - decimal.Decimal  (e.g. SUM / AVG results)
  - datetime.date / datetime.datetime / datetime.time  (date columns)
  - bytes  (BLOB columns)

make_json_serializable() converts each of these to a plain JSON-safe value
so the knowledge base can always be saved without errors.
"""

import json
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path


def make_json_serializable(obj):
    """
    Convert a single non-serializable database value to a JSON-safe type.

    This function is passed to json.dump() as the ``default`` argument.
    Python calls it automatically for any object that json cannot serialize.

    Conversions applied
    -------------------
    Decimal    → float   (preserves numeric precision well enough for display)
    datetime   → str     ISO-8601 format, e.g. "2024-01-15T10:30:00"
    date       → str     ISO-8601 format, e.g. "2024-01-15"
    time       → str     ISO-8601 format, e.g. "10:30:00"
    bytes      → str     decoded as UTF-8; undecodable bytes are replaced
    anything else → str  fallback using Python's built-in str()

    Args:
        obj: The value that json.dump() could not serialize.

    Returns:
        A JSON-safe Python value (str, float, int, etc.).
    """
    # Decimal comes from MySQL DECIMAL/NUMERIC columns and aggregate functions.
    if isinstance(obj, Decimal):
        return float(obj)

    # datetime must be checked before date because datetime is a subclass of date.
    if isinstance(obj, datetime):
        return obj.isoformat()

    if isinstance(obj, date):
        return obj.isoformat()

    if isinstance(obj, time):
        return obj.isoformat()

    # bytes come from BLOB / BINARY columns.
    if isinstance(obj, bytes):
        return obj.decode("utf-8", errors="replace")

    # Catch-all: convert anything else to its string representation.
    return str(obj)


def save_json(data: dict, file_path: str) -> None:
    """
    Serialize ``data`` to a JSON file at ``file_path``, indented with 4 spaces.

    Uses ``make_json_serializable`` as the ``default`` encoder so that
    database-native types (Decimal, date, datetime, bytes …) are converted
    automatically instead of raising a TypeError.

    Creates any missing parent directories automatically before writing.

    Args:
        data:      The dictionary to serialize and persist.
        file_path: Destination path for the JSON file (str or path-like).

    Raises:
        Exception: If the file cannot be written or serialization fails,
                   with a message containing the file path and the cause.
    """
    path = Path(file_path)
    try:
        # Create parent directories (e.g. semantic/) if they don't exist yet.
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as fh:
            # default=make_json_serializable handles Decimal, date, bytes, etc.
            json.dump(data, fh, indent=4, default=make_json_serializable)
    except (OSError, TypeError, ValueError) as exc:
        raise Exception(
            f"Failed to save JSON to '{file_path}': {exc}"
        ) from exc


def load_json(file_path: str) -> dict:
    """
    Deserialize and return JSON content from `file_path`.

    Args:
        file_path: Path to the JSON file to read (str or path-like).

    Returns:
        The parsed dictionary contained in the file.

    Raises:
        FileNotFoundError: If `file_path` does not exist, with a message
                           indicating the missing path.
        ValueError:        If the file exists but contains malformed JSON,
                           with a message indicating the path and that the
                           content is invalid JSON.
    """
    path = Path(file_path)

    if not path.exists():
        raise FileNotFoundError(
            f"Knowledge base file not found: '{file_path}'. "
            "Run option 1 to build it first."
        )

    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"File '{file_path}' contains invalid JSON: {exc}"
        ) from exc
