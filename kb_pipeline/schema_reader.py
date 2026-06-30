"""Database schema reflection utilities."""

from __future__ import annotations

from sqlalchemy import MetaData


def read_database_schema(engine) -> dict:
    """
    Reflect database tables and return JSON-serializable schema metadata.
    """
    metadata = MetaData()

    try:
        metadata.reflect(bind=engine)
    except Exception as exc:
        raise RuntimeError(f"Failed to reflect database schema: {exc}") from exc

    schema_data = {}
    for table_name, table in metadata.tables.items():
        schema_data[table_name] = {
            "columns": [
                {
                    "name": column.name,
                    "type": str(column.type),
                    "nullable": bool(column.nullable),
                }
                for column in table.columns
            ],
            "primary_keys": [column.name for column in table.primary_key.columns],
            "foreign_keys": [
                {
                    "column": foreign_key.parent.name,
                    "referenced_table": foreign_key.column.table.name,
                    "referenced_column": foreign_key.column.name,
                }
                for foreign_key in table.foreign_keys
            ],
        }

    return schema_data
