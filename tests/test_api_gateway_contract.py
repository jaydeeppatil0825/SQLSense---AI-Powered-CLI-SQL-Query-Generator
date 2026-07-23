import pytest
from fastapi.testclient import TestClient

import api_gateway.app as gateway_app


class _Db:
    knowledge_base_metadata = {"schema_hash": "abc123"}
    knowledge_base_origin = "built"

    def __init__(self):
        self.config = {"db_type": "mysql", "host": "localhost", "username": "root", "database": "demo"}

    def get_db_config(self):
        return dict(self.config)


class _Result:
    def __init__(self):
        self.last_query_context = None

    def get_last_query_context(self):
        return self.last_query_context


class FakeAppService:
    last_instance = None

    def __init__(self):
        FakeAppService.last_instance = self
        self.database_service = _Db()
        self.result_service = _Result()
        self.connected = False
        self.ready = False
        self.last_sql = None
        self.last_rows = []
        self.last_context = {}
        self.execute_calls = []
        self.disconnected = False

    def connect_database_and_prepare(self, **kwargs):
        if kwargs.get("database") == "bad":
            return False, "Connection failed password=secret mysql://root:secret@localhost/bad", {"password": "secret"}
        self.connected = True
        self.ready = True
        return True, "connected", {"connected": True, "password": kwargs.get("password")}

    def disconnect_database(self):
        self.connected = False
        self.ready = False
        self.disconnected = True
        return True, "Database disconnected"

    def is_database_connected(self):
        return self.connected

    def is_database_ready(self):
        return self.ready

    def get_knowledge_base(self):
        if not self.ready:
            return None
        return {
            "orders": {
                "columns": [
                    {"name": "order_id", "type": "int", "nullable": False, "is_primary_key": True},
                    {"name": "customer_id", "type": "int", "nullable": False, "is_foreign_key": True},
                    {
                        "name": "order_date",
                        "type": "date",
                        "nullable": True,
                        "planner_roles": {"date_eligible": True},
                    },
                    {
                        "name": "total_amount",
                        "type": "decimal",
                        "nullable": False,
                        "planner_roles": {"numeric_metric_eligible": True},
                        "sample_values": [10, 20],
                    },
                ],
                "primary_keys": ["order_id"],
                "foreign_keys": [
                    {
                        "column": "customer_id",
                        "referenced_table": "customers",
                        "referenced_column": "customer_id",
                    }
                ],
            }
        }

    def get_vector_status(self):
        return {"index_status": "ready", "retriever": {"document_count": 3}, "chroma": {"ready": True}}

    def get_last_build_summary(self):
        return {"tables": 1}

    def get_last_prepare_report(self):
        return {"database_ready": self.ready}

    def rebuild_or_refresh_knowledge_base(self, **kwargs):
        if not self.connected:
            return False, "No database connection", None
        self.ready = True
        return True, "rebuilt", {"orders": {}}

    def reset_conversation(self):
        self.last_sql = None
        self.last_rows = []
        self.last_context = {}

    def process_question(self, question, ai_backend=None):
        if "delete" in question.lower():
            return {
                "success": False,
                "message": "Unsafe request blocked. Only SELECT questions are allowed.",
                "route": "blocked_unsafe",
                "query_context": {"route_used": "blocked_unsafe", "query_shape": "blocked_unsafe"},
            }
        if "ambiguous" in question.lower():
            return {
                "success": False,
                "message": "Cannot choose metric safely",
                "route": "cannot_plan_safely",
                "query_context": {"route_used": "cannot_plan_safely", "query_shape": "single_table_aggregate"},
            }
        if "unsupported" in question.lower():
            return {
                "success": False,
                "message": "Unsupported query shape",
                "route": "cannot_plan_safely",
                "query_context": {"route_used": "cannot_plan_safely", "query_shape": "unsupported"},
            }
        self.last_sql = "SELECT order_id FROM orders LIMIT 50;"
        self.last_context = {
            "route_used": "deterministic_sql_required",
            "query_shape": "single_table_list",
            "selected_join_path": {"path_source": "relationship_graph"},
        }
        self.result_service.last_query_context = self.last_context
        return {
            "success": True,
            "message": "SQL generated successfully",
            "route": "deterministic_sql_required",
            "sql": self.last_sql,
            "generated_sql": self.last_sql,
            "validation_result": {"is_valid": True, "reason": "SQL is valid"},
            "query_context": self.last_context,
        }

    def execute_sql(self, sql, revalidate=True):
        self.execute_calls.append((sql, revalidate, self.result_service.get_last_query_context()))
        if sql != self.last_sql:
            return False, "SQL mismatch", None
        self.last_rows = [{"order_id": 1}]
        return True, "Query executed successfully", list(self.last_rows)

    def get_last_query_context(self):
        return self.last_context

    def get_last_sql(self):
        return self.last_sql

    def get_last_rows(self):
        return list(self.last_rows)

    def get_last_row_count(self):
        return len(self.last_rows)


@pytest.fixture()
def client(monkeypatch):
    gateway_app.reset_sessions_for_tests()
    monkeypatch.setattr(gateway_app, "AppService", FakeAppService)
    with TestClient(gateway_app.app) as test_client:
        yield test_client
    gateway_app.reset_sessions_for_tests()


def post(client, action, payload=None):
    return client.post("/api/v1/gateway", json={"action": action, "payload": payload or {}}).json()


def test_health_without_database(client):
    assert client.get("/health").json() == {"ok": True, "service": "sqlsense-gateway"}


@pytest.mark.parametrize(
    "action",
    [
        "session.status",
        "session.reset",
        "database.disconnect",
        "database.status",
        "knowledge.status",
        "knowledge.rebuild",
        "schema.list",
        "query.last",
        "history.list",
        "history.clear",
    ],
)
def test_empty_payload_actions_reject_unexpected_fields(client, action):
    response = post(client, action, {"unexpected": True})
    assert response["ok"] is False
    assert response["error"]["code"] == "invalid_payload"


def test_unknown_action_and_top_level_unexpected_field_rejected(client):
    assert post(client, "sql.execute", {"sql": "SELECT 1"})["error"]["code"] == "unknown_action"
    response = client.post("/api/v1/gateway", json={"action": "session.status", "payload": {}, "extra": True}).json()
    assert response["ok"] is False
    assert response["error"]["code"] == "invalid_request"


def test_database_connect_success_and_failed_connection_redacts_password(client):
    ok = post(
        client,
        "database.connect",
        {"username": "root", "password": "secret", "database": "demo"},
    )
    assert ok["ok"] is True
    assert "secret" not in str(ok)

    bad = post(
        client,
        "database.connect",
        {"username": "root", "password": "secret", "database": "bad"},
    )
    assert bad["ok"] is False
    assert bad["error"]["code"] == "database_connect_failed"
    assert "secret" not in str(bad)


def test_query_ask_rejects_client_sql_and_executes_only_stored_sql(client):
    invalid = post(client, "query.ask", {"question": "show orders", "sql": "SELECT 1"})
    assert invalid["ok"] is False
    assert invalid["error"]["code"] == "invalid_payload"

    response = post(client, "query.ask", {"question": "show orders"})
    assert response["ok"] is True
    assert response["data"]["sql"] == "SELECT order_id FROM orders LIMIT 50;"
    assert response["data"]["rows"] == [{"order_id": 1}]
    assert response["data"]["row_count"] == 1

    service = next(iter(gateway_app.store._sessions.values()))
    assert service.execute_calls == [
        ("SELECT order_id FROM orders LIMIT 50;", True, service.last_context)
    ]


@pytest.mark.parametrize(
    "question,code,shape",
    [
        ("ambiguous amount", "cannot_plan_safely", "single_table_aggregate"),
        ("unsupported analytics", "cannot_plan_safely", "unsupported"),
        ("delete orders", "blocked_unsafe", "blocked_unsafe"),
    ],
)
def test_query_ask_fail_closed_cases(client, question, code, shape):
    response = post(client, "query.ask", {"question": question})
    assert response["ok"] is False
    assert response["error"]["code"] == code
    assert response["error"]["details"]["query"]["query_shape"] == shape


def test_query_history_records_success_and_rejection_safely(client):
    success = post(client, "query.ask", {"question": "show orders"})
    rejected = post(client, "query.ask", {"question": "delete orders"})
    history = post(client, "history.list")

    assert success["ok"] is True
    assert rejected["ok"] is False
    assert history["ok"] is True
    entries = history["data"]["entries"]
    assert [entry["status"] for entry in entries] == ["rejected", "success"]
    assert entries[0]["question"] == "delete orders"
    assert entries[0]["error"]["code"] == "blocked_unsafe"
    assert entries[1]["sql"] == "SELECT order_id FROM orders LIMIT 50;"
    assert "rows" not in entries[1]
    assert "query_context" not in str(entries)
    assert "selected_join_path" not in str(entries)
    assert "password" not in str(entries).lower()


def test_invalid_query_payload_does_not_create_history(client):
    response = post(client, "query.ask", {"question": "show orders", "sql": "SELECT 1"})
    history = post(client, "history.list")

    assert response["ok"] is False
    assert history["data"]["entries"] == []


def test_history_is_session_scoped_and_clear_is_current_session_only(client):
    first = post(client, "query.ask", {"question": "show orders"})
    cookie = client.cookies.get(gateway_app.SESSION_COOKIE)

    other_client = TestClient(gateway_app.app)
    try:
        other = post(other_client, "history.list")
        assert other["data"]["entries"] == []

        clear = post(client, "history.clear")
        assert clear["data"] == {"cleared": 1, "count": 0}
        assert post(client, "history.list")["data"]["entries"] == []
        assert post(other_client, "history.list")["data"]["entries"] == []
    finally:
        other_client.close()
    assert first["request_id"] != cookie


def test_history_limit_eviction_and_newest_first(client):
    for index in range(55):
        post(client, "query.ask", {"question": f"show orders {index}"})

    entries = post(client, "history.list")["data"]["entries"]
    assert len(entries) == 50
    assert entries[0]["question"] == "show orders 54"
    assert entries[-1]["question"] == "show orders 5"


def test_session_reset_clears_history(client):
    post(client, "query.ask", {"question": "show orders"})
    assert post(client, "history.list")["data"]["count"] == 1

    post(client, "session.reset")

    assert post(client, "history.list")["data"]["entries"] == []


def test_missing_database_and_kb_not_ready_state(client):
    response = post(client, "database.status")
    assert response["ok"] is True
    assert response["data"]["connected"] is False
    assert response["data"]["ready"] is False

    knowledge = post(client, "knowledge.status")
    assert knowledge["ok"] is True
    assert knowledge["data"]["kb_status"] == "not_ready"


def test_schema_list_returns_frontend_schema_contract(client):
    post(client, "database.connect", {"username": "root", "password": "secret", "database": "demo"})

    response = post(client, "schema.list")

    assert response["ok"] is True
    table = response["data"]["tables"][0]
    assert table["name"] == "orders"
    assert table["type"] == "table"
    assert table["columnCount"] == 4
    columns = {column["name"]: column for column in table["columns"]}
    assert columns["order_id"]["primaryKey"] is True
    assert columns["customer_id"]["foreignKey"] == {"table": "customers", "column": "customer_id"}
    assert columns["order_date"]["plannerRole"] == "date"
    assert columns["total_amount"]["plannerRole"] == "metric"
    assert columns["total_amount"]["samples"] == ["10", "20"]


def test_disconnect_and_session_reset_cleanup(client):
    post(client, "database.connect", {"username": "root", "password": "secret", "database": "demo"})
    service = next(iter(gateway_app.store._sessions.values()))

    disconnect = post(client, "database.disconnect")
    assert disconnect["ok"] is True
    assert service.disconnected is True

    post(client, "database.connect", {"username": "root", "password": "secret", "database": "demo"})
    old = next(iter(gateway_app.store._sessions.values()))
    reset = post(client, "session.reset")
    assert reset["ok"] is True
    assert old.disconnected is True
    assert next(iter(gateway_app.store._sessions.values())) is not old
