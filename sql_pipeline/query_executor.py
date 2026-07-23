"""Safe SQL execution for the SQL Generation Pipeline."""

from __future__ import annotations

from sqlalchemy import text

from sql_pipeline.sql_validator import validate_sql, validate_sql_contract


def execute_query(
    sql: str,
    engine,
    knowledge_base: dict | None = None,
    selected_join_path: dict | None = None,
    query_context: dict | None = None,
    deterministic_query_plan=None,
) -> list[dict]:
    """Validate and execute a read-only SELECT query using a SQLAlchemy engine."""
    if deterministic_query_plan is not None:
        validation_result = validate_sql_contract(
            sql,
            knowledge_base or {},
            deterministic_query_plan=deterministic_query_plan,
            selected_join_path=selected_join_path,
            query_context=query_context,
        )
        if not validation_result.valid:
            if validation_result.violations:
                raise ValueError(validation_result.violations[0].message)
            raise ValueError(validation_result.reason_code or "SQL validation failed")

    is_valid, reason = validate_sql(sql)
    if not is_valid:
        raise ValueError(reason)

    if not str(sql).strip().upper().startswith("SELECT"):
        raise ValueError("Only SELECT queries are permitted.")

    # If knowledge_base provided, also run structure validation.
    if knowledge_base:
        from sql_pipeline.sql_validator import validate_sql_structure
        struct_ok, struct_reason = validate_sql_structure(
            sql,
            knowledge_base,
            selected_join_path=selected_join_path,
            query_context=query_context,
        )
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
