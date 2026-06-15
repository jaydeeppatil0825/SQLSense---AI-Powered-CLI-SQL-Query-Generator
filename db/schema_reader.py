"""Database schema reflection utilities."""

from __future__ import annotations

from sqlalchemy import MetaData

from semantic.erp_metadata import detect_relationships


def _relationship_exists(
    foreign_keys: list[dict],
    column: str,
    referenced_table: str,
    referenced_column: str,
) -> bool:
    return any(
        foreign_key.get("column") == column
        and foreign_key.get("referenced_table") == referenced_table
        and foreign_key.get("referenced_column") == referenced_column
        for foreign_key in foreign_keys
    )


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

    for relationship in detect_relationships(schema_data):
        if relationship.get("source") == "foreign_key":
            continue

        foreign_keys = schema_data[relationship["from_table"]]["foreign_keys"]
        if _relationship_exists(
            foreign_keys,
            relationship["from_column"],
            relationship["to_table"],
            relationship["to_column"],
        ):
            continue

        foreign_keys.append(
            {
                "column": relationship["from_column"],
                "referenced_table": relationship["to_table"],
                "referenced_column": relationship["to_column"],
                "inferred": True,
            }
        )

    return schema_data
