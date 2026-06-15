"""Core flow tests for ask-question and execute-last-SQL behavior."""
from sqlalchemy import Column, Integer, MetaData, Table, create_engine

from core.app_service import AppService


KB = {
    "orders": {
        "columns": [
            {"name": "order_id", "type": "INTEGER", "nullable": False},
            {"name": "final_amount", "type": "INTEGER", "nullable": True},
        ],
        "primary_keys": ["order_id"],
        "foreign_keys": [],
    }
}


def _service_with_orders():
    engine = create_engine("sqlite:///:memory:")
    metadata = MetaData()
    orders = Table(
        "orders",
        metadata,
        Column("order_id", Integer, primary_key=True),
        Column("final_amount", Integer),
    )
    metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(orders.insert(), [{"order_id": 1, "final_amount": 100}])

    service = AppService()
    service.database_service.engine = engine
    service.database_service.knowledge_base = KB
    return service


def test_process_question_saves_sql_and_execute_last_sql_uses_same_sql(monkeypatch):
    service = _service_with_orders()
    monkeypatch.setattr(
        "core.question_service.generate_sql",
        lambda user_question, knowledge_base, backend=None, query_plan=None, selected_tables=None: "SELECT SUM(final_amount) AS total_sales FROM orders;",
    )
    monkeypatch.setattr(
        "core.question_service.generate_sql_with_retry",
        lambda user_question, knowledge_base, backend, first_attempt_sql, validation_reason, query_plan=None, selected_tables=None: "SELECT SUM(final_amount) AS total_sales FROM orders;",
    )

    success, message, sql, error = service.process_question("show total sales", ai_backend="local")

    assert success is True
    assert error is None
    assert sql == "SELECT SUM(final_amount) AS total_sales FROM orders LIMIT 50;"
    assert service.get_last_sql() == sql

    exec_success, exec_message, rows = service.execute_sql(service.get_last_sql(), revalidate=True)

    assert exec_success is True
    assert rows == [{"total_sales": 100}]
    assert service.get_last_sql() == sql


def test_destructive_natural_language_question_is_blocked():
    service = _service_with_orders()

    success, message, sql, error = service.process_question("delete all customers", ai_backend="local")

    assert success is False
    assert message == "Unsafe request blocked. Only SELECT questions are allowed."
    assert sql is None
    assert service.get_last_sql() is None
