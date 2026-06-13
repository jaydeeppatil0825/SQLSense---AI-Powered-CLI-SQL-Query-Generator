"""Database schema reflection utilities."""

from __future__ import annotations

from sqlalchemy import MetaData


_KNOWN_RELATIONSHIPS: tuple[tuple[str, str, str, str], ...] = (
    ("orders", "customer_id", "customers", "customer_id"),
    ("order_items", "order_id", "orders", "order_id"),
    ("order_items", "product_id", "products", "product_id"),
    ("payments", "order_id", "orders", "order_id"),
    ("support_tickets", "customer_id", "customers", "customer_id"),
    ("support_tickets", "order_id", "orders", "order_id"),
)


def _table_has_column(metadata: MetaData, table_name: str, column_name: str) -> bool:
    table = metadata.tables.get(table_name)
    return table is not None and column_name in table.columns


def _relationship_exists(
    foreign_keys: list[dict],
    column: str,
    referenced_table: str,
    referenced_column: str,
) -> bool:
    return any(
        fk.get("column") == column
        and fk.get("referenced_table") == referenced_table
        and fk.get("referenced_column") == referenced_column
        for fk in foreign_keys
    )


def read_database_schema(engine) -> dict:
    """Reflect database tables and return JSON-serializable schema metadata.

    The returned dictionary is keyed by table name. Each value contains
    ``columns``, ``primary_keys``, and ``foreign_keys`` entries as described in
    the project requirements.
    """
    metadata = MetaData()

    try:
        metadata.reflect(bind=engine)
    except Exception as exc:
        raise RuntimeError(f"Failed to reflect database schema: {exc}") from exc

    schema_data = {}
    for table_name, table in metadata.tables.items():
        columns = [
            {
                "name": column.name,
                "type": str(column.type),
                "nullable": bool(column.nullable),
            }
            for column in table.columns
        ]

        primary_keys = [column.name for column in table.primary_key.columns]

        foreign_keys = []
        for foreign_key in table.foreign_keys:
            foreign_keys.append(
                {
                    "column": foreign_key.parent.name,
                    "referenced_table": foreign_key.column.table.name,
                    "referenced_column": foreign_key.column.name,
                }
            )

        # Some internal/demo databases are created without declared FK
        # constraints. Preserve explicit FK reflection, then add trusted
        # PCSoft relationship hints only when both columns exist.
        for local_table, local_col, ref_table, ref_col in _KNOWN_RELATIONSHIPS:
            if local_table != table_name:
                continue
            if not _table_has_column(metadata, local_table, local_col):
                continue
            if not _table_has_column(metadata, ref_table, ref_col):
                continue
            if _relationship_exists(foreign_keys, local_col, ref_table, ref_col):
                continue
            foreign_keys.append(
                {
                    "column": local_col,
                    "referenced_table": ref_table,
                    "referenced_column": ref_col,
                    "inferred": True,
                }
            )

        schema_data[table_name] = {
            "columns": columns,
            "primary_keys": primary_keys,
            "foreign_keys": foreign_keys,
        }

    return schema_data
