"""Data profiling helpers for reflected database schemas."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy

from sqlalchemy import text


_NUMERIC_TYPE_PREFIXES = ("INT", "BIGINT", "SMALLINT", "TINYINT", "DECIMAL", "NUMERIC", "FLOAT", "DOUBLE")
_DATETIME_TYPE_PREFIXES = ("DATE", "DATETIME", "TIMESTAMP", "TIME")


def _quote_identifier(identifier: str) -> str:
    """Quote a MySQL identifier and escape embedded backticks."""
    return f"`{str(identifier).replace('`', '``')}`"


def _first_mapping(result):
    """Return the first mapping-like row from a SQLAlchemy result or mock."""
    if hasattr(result, "mappings"):
        mappings = result.mappings()
        if hasattr(mappings, "first"):
            row = mappings.first()
            if row is not None:
                return dict(row)
        if hasattr(mappings, "fetchone"):
            row = mappings.fetchone()
            if row is not None:
                return dict(row)

    if hasattr(result, "fetchone"):
        row = result.fetchone()
        if row is None:
            return None
        if hasattr(row, "_mapping"):
            return dict(row._mapping)
        if isinstance(row, Mapping):
            return dict(row)

    return None


def _all_sample_values(result) -> list:
    """Extract sample values from a SQLAlchemy result or a lightweight mock."""
    rows = []
    if hasattr(result, "mappings"):
        mapped = result.mappings()
        if hasattr(mapped, "all"):
            rows = mapped.all()
        elif hasattr(mapped, "fetchall"):
            rows = mapped.fetchall()
    elif hasattr(result, "fetchall"):
        rows = result.fetchall()

    values = []
    for row in rows:
        if hasattr(row, "_mapping"):
            mapping = row._mapping
            values.append(mapping.get("sample_value", next(iter(mapping.values()), None)))
        elif isinstance(row, Mapping):
            values.append(row.get("sample_value", next(iter(row.values()), None)))
        elif isinstance(row, (tuple, list)):
            values.append(row[0] if row else None)
        else:
            values.append(row)
    return values


def _is_min_max_type(column_type: str) -> bool:
    """Return True when a column type should receive min/max profiling."""
    normalized = str(column_type or "").upper()
    return normalized.startswith(_NUMERIC_TYPE_PREFIXES) or normalized.startswith(_DATETIME_TYPE_PREFIXES)


def profile_database_data(schema_data: dict, engine) -> dict:
    """Profile row counts, column aggregates, samples, and min/max values.

    Table-level and column-level failures are recorded in the returned
    structure under ``row_count_error`` and ``profile_error`` instead of being
    raised, allowing profiling to continue for the rest of the database.
    """
    if not schema_data:
        return {}

    profiled = deepcopy(schema_data)

    with engine.connect() as connection:
        for table_name, table_data in profiled.items():
            table_sql = _quote_identifier(table_name)

            try:
                result = connection.execute(text(f"SELECT COUNT(*) AS row_count FROM {table_sql}"))
                row = _first_mapping(result)
                table_data["row_count"] = int((row or {}).get("row_count", 0))
            except Exception as exc:
                table_data["row_count_error"] = str(exc)

            for column in table_data.get("columns", []):
                column_name = column.get("name")
                column_sql = _quote_identifier(column_name)

                try:
                    aggregate_sql = text(
                        "SELECT "
                        f"SUM(CASE WHEN {column_sql} IS NULL THEN 1 ELSE 0 END) AS null_count, "
                        f"SUM(CASE WHEN {column_sql} IS NOT NULL THEN 1 ELSE 0 END) AS non_null_count, "
                        f"COUNT(DISTINCT {column_sql}) AS unique_count "
                        f"FROM {table_sql}"
                    )
                    aggregate_row = _first_mapping(connection.execute(aggregate_sql)) or {}
                    column["null_count"] = int(aggregate_row.get("null_count") or 0)
                    column["non_null_count"] = int(aggregate_row.get("non_null_count") or 0)
                    column["unique_count"] = int(aggregate_row.get("unique_count") or 0)

                    sample_sql = text(
                        f"SELECT DISTINCT {column_sql} AS sample_value "
                        f"FROM {table_sql} "
                        f"WHERE {column_sql} IS NOT NULL "
                        "LIMIT 5"
                    )
                    column["sample_values"] = _all_sample_values(connection.execute(sample_sql))[:5]

                    if _is_min_max_type(column.get("type", "")):
                        min_max_sql = text(
                            f"SELECT MIN({column_sql}) AS min_value, "
                            f"MAX({column_sql}) AS max_value "
                            f"FROM {table_sql}"
                        )
                        min_max_row = _first_mapping(connection.execute(min_max_sql)) or {}
                        column["min_value"] = min_max_row.get("min_value")
                        column["max_value"] = min_max_row.get("max_value")
                except Exception as exc:
                    column["profile_error"] = str(exc)

    return profiled
