"""Safe SQL execution for the SQL Generation Pipeline."""

from __future__ import annotations

from sqlalchemy import text

from utils.sql_validator import validate_sql


def execute_query(sql: str, engine, knowledge_base: dict | None = None) -> list[dict]:
    """Validate and execute a read-only SELECT query using a SQLAlchemy engine."""
    is_valid, reason = validate_sql(sql)
    if not is_valid:
        raise ValueError(reason)

    if not str(sql).strip().upper().startswith("SELECT"):
        raise ValueError("Only SELECT queries are permitted.")

    # If knowledge_base provided, also run structure validation.
    if knowledge_base:
        from utils.sql_validator import validate_sql_structure
        struct_ok, struct_reason = validate_sql_structure(sql, knowledge_base)
        if not struct_ok:
            raise ValueError(f"SQL structure invalid: {struct_reason}")

    try:
        with engine.connect() as connection:
            result = connection.execute(text(sql))
            if hasattr(result, "mappings"):
                return [dict(row) for row in result.mappings().all()]

            rows = result.fetchall() if hasattr(result, "fetchall") else []
            keys = list(result.keys()) if hasattr(result, "keys") else []
            output = []
            for row in rows:
                if hasattr(row, "_mapping"):
                    output.append(dict(row._mapping))
                elif isinstance(row, dict):
                    output.append(row)
                else:
                    output.append(dict(zip(keys, row)))
            return output
    except ValueError:
        raise
    except Exception as exc:
        raise RuntimeError(f"Database query failed: {exc}") from exc
