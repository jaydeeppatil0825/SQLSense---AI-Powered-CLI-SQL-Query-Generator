"""Thin FastAPI BFF for the existing SQLSense AppService."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import re
import time
from typing import Any, Callable
from uuid import uuid4

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ValidationError

try:
    from pydantic import ConfigDict
except ImportError:  # pragma: no cover - pydantic v1 fallback
    ConfigDict = None

from core.app_service import AppService
from infrastructure.observability import bind_context, event as observe_event, new_context


SESSION_COOKIE = "sqlsense_session"
DEV_ORIGINS = (
    "http://localhost:3000",
    "http://localhost:5173",
    "http://127.0.0.1:3000",
    "http://127.0.0.1:5173",
)


class StrictModel(BaseModel):
    if ConfigDict is not None:
        model_config = ConfigDict(extra="forbid")
    else:  # pragma: no cover - pydantic v1 fallback
        class Config:
            extra = "forbid"


class GatewayRequest(StrictModel):
    action: str
    payload: dict[str, Any] = Field(default_factory=dict)


class EmptyPayload(StrictModel):
    pass


class DatabaseConnectPayload(StrictModel):
    db_type: str = "mysql"
    host: str = "localhost"
    port: int | None = None
    username: str = ""
    password: str = ""
    database: str = ""
    sqlite_path: str = ""
    use_ai_enrichment: bool = False
    ai_backend: str | None = None


class KnowledgeRebuildPayload(StrictModel):
    use_ai_enrichment: bool = False
    ai_backend: str | None = None


class QueryAskPayload(StrictModel):
    question: str = Field(min_length=1)


def _model_data(model: BaseModel) -> dict[str, Any]:
    return model.model_dump() if hasattr(model, "model_dump") else model.dict()


def _redact_text(value: str) -> str:
    value = re.sub(r"(?i)(password\s*[=:]\s*)[^&\s]+", r"\1<redacted>", value)
    value = re.sub(r"(?i)(://[^:\s/@]+:)[^@\s]+@", r"\1<redacted>@", value)
    return value


def _redact(value: Any) -> Any:
    if isinstance(value, str):
        return _redact_text(value)
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            if "password" in str(key).lower():
                redacted[key] = "<redacted>"
            else:
                redacted[key] = _redact(item)
        return redacted
    return value


def _error(code: str, message: str, details: Any = None) -> dict[str, Any]:
    payload = {"code": code, "message": _redact_text(str(message))}
    if details is not None:
        payload["details"] = _redact(details)
    return payload


class SessionStore:
    def __init__(self) -> None:
        self._sessions: dict[str, AppService] = {}
        self._history: dict[str, list[dict[str, Any]]] = {}

    def get(self, request: Request, response: Response) -> tuple[str, AppService]:
        session_id = request.cookies.get(SESSION_COOKIE) or uuid4().hex
        service = self._sessions.setdefault(session_id, AppService())
        response.set_cookie(
            SESSION_COOKIE,
            session_id,
            httponly=True,
            samesite="lax",
        )
        return session_id, service

    def reset(self, session_id: str) -> AppService:
        old = self._sessions.get(session_id)
        if old is not None:
            old.disconnect_database()
        service = AppService()
        self._sessions[session_id] = service
        self.clear_history(session_id)
        return service

    def history(self, session_id: str) -> list[dict[str, Any]]:
        return self._history.setdefault(session_id, [])

    def add_history(self, session_id: str, entry: dict[str, Any]) -> None:
        entries = self.history(session_id)
        entries.insert(0, _redact(entry))
        del entries[50:]

    def clear_history(self, session_id: str) -> int:
        count = len(self._history.get(session_id, []))
        self._history[session_id] = []
        return count


store = SessionStore()
app = FastAPI(title="SQLSense API Gateway", version="1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(DEV_ORIGINS),
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["content-type"],
)


def _safe_db_config(service: AppService) -> dict[str, Any]:
    config = dict(service.database_service.get_db_config())
    config.pop("password", None)
    return _redact(config)


def _knowledge_status(service: AppService) -> dict[str, Any]:
    metadata = dict(service.database_service.knowledge_base_metadata)
    vector = service.get_vector_status()
    return _redact(
        {
            "database_ready": service.is_database_ready(),
            "database": _safe_db_config(service),
            "knowledge_base_loaded": bool(service.get_knowledge_base()),
            "knowledge_base_origin": service.database_service.knowledge_base_origin,
            "schema_hash": metadata.get("schema_hash", ""),
            "kb_status": "ready" if service.get_knowledge_base() else "not_ready",
            "vector_status": vector.get("index_status", "unknown"),
            "vector": {
                "index_status": vector.get("index_status", "unknown"),
                "document_count": (vector.get("retriever") or {}).get("document_count", 0),
                "chroma_ready": (vector.get("chroma") or {}).get("ready", False),
            },
            "last_build": service.get_last_build_summary(),
            "last_prepare": service.get_last_prepare_report(),
        }
    )


def _session_status(session_id: str, service: AppService) -> dict[str, Any]:
    return {
        "session_id": session_id,
        "database_connected": service.is_database_connected(),
        "database_ready": service.is_database_ready(),
        "has_last_sql": bool(service.get_last_sql()),
    }


def _payload(model: type[StrictModel], raw: dict[str, Any]) -> StrictModel:
    return model(**raw)


def _handle_session_status(session_id: str, service: AppService, raw: dict[str, Any]) -> dict[str, Any]:
    _payload(EmptyPayload, raw)
    return _session_status(session_id, service)


def _handle_session_reset(session_id: str, service: AppService, raw: dict[str, Any]) -> dict[str, Any]:
    _payload(EmptyPayload, raw)
    service = store.reset(session_id)
    return _session_status(session_id, service)


def _handle_database_connect(session_id: str, service: AppService, raw: dict[str, Any]) -> dict[str, Any]:
    payload = _payload(DatabaseConnectPayload, raw)
    ok, message, report = service.connect_database_and_prepare(**_model_data(payload))
    if not ok:
        raise GatewayActionError("database_connect_failed", message, report)
    return {"message": message, "database": _safe_db_config(service), "prepare": _redact(report)}


def _handle_database_disconnect(session_id: str, service: AppService, raw: dict[str, Any]) -> dict[str, Any]:
    _payload(EmptyPayload, raw)
    ok, message = service.disconnect_database()
    return {"disconnected": ok, "message": message}


def _handle_database_status(session_id: str, service: AppService, raw: dict[str, Any]) -> dict[str, Any]:
    _payload(EmptyPayload, raw)
    return {
        "connected": service.is_database_connected(),
        "ready": service.is_database_ready(),
        "database": _safe_db_config(service),
    }


def _handle_knowledge_status(session_id: str, service: AppService, raw: dict[str, Any]) -> dict[str, Any]:
    _payload(EmptyPayload, raw)
    return _knowledge_status(service)


def _handle_knowledge_rebuild(session_id: str, service: AppService, raw: dict[str, Any]) -> dict[str, Any]:
    payload = _payload(KnowledgeRebuildPayload, raw)
    ok, message, _ = service.rebuild_or_refresh_knowledge_base(**_model_data(payload))
    if not ok:
        raise GatewayActionError("knowledge_rebuild_failed", message)
    return {"message": message, "knowledge": _knowledge_status(service)}


def _query_context_summary(context: dict[str, Any]) -> dict[str, Any]:
    return _redact(
        {
            "route": context.get("route_used") or context.get("route") or context.get("route_recommendation"),
            "route_reason": context.get("route_reason"),
            "query_shape": context.get("query_shape"),
            "clause_shape": (context.get("clause_plan") or {}).get("clause_shape"),
            "selected_join_path": context.get("selected_join_path"),
            "warnings": context.get("warnings") or [],
            "missing_evidence": context.get("missing_evidence") or [],
            "ambiguities": context.get("ambiguities") or [],
        }
    )


def _handle_query_ask(session_id: str, service: AppService, raw: dict[str, Any]) -> dict[str, Any]:
    payload = _payload(QueryAskPayload, raw)
    started = time.perf_counter()
    result = service.process_question(payload.question)
    context = result.get("query_context") or service.get_last_query_context() or {}
    sql = result.get("sql") or result.get("generated_sql")
    if not result.get("success") or not sql:
        raise GatewayActionError(
            str(result.get("route") or "cannot_plan_safely"),
            str(result.get("error") or result.get("message") or "Question was rejected"),
            {"query": _query_context_summary(context), "validation": result.get("validation_result") or {}},
        )

    executed, exec_message, rows = service.execute_sql(sql, revalidate=True)
    duration_ms = round((time.perf_counter() - started) * 1000, 3)
    if not executed:
        raise GatewayActionError(
            "execution_failed",
            exec_message,
            {"query": _query_context_summary(context), "validation": result.get("validation_result") or {}},
        )
    rows = rows or []
    return {
        "route": result.get("route"),
        "query_shape": context.get("query_shape"),
        "sql": sql,
        "validation": result.get("validation_result") or {},
        "query": _query_context_summary(context),
        "columns": list(rows[0].keys()) if rows else [],
        "rows": rows,
        "row_count": len(rows),
        "execution": {"executed": True, "message": exec_message, "duration_ms": duration_ms},
    }


def _handle_query_last(session_id: str, service: AppService, raw: dict[str, Any]) -> dict[str, Any]:
    _payload(EmptyPayload, raw)
    context = service.result_service.get_last_query_context() or service.get_last_query_context() or {}
    rows = service.get_last_rows() or []
    return {
        "sql": service.get_last_sql(),
        "columns": list(rows[0].keys()) if rows else [],
        "rows": rows,
        "row_count": service.get_last_row_count() or 0,
        "query": _query_context_summary(context),
    }


def _handle_history_list(session_id: str, service: AppService, raw: dict[str, Any]) -> dict[str, Any]:
    _payload(EmptyPayload, raw)
    entries = deepcopy(store.history(session_id))
    return {"entries": entries, "count": len(entries), "limit": 50}


def _handle_history_clear(session_id: str, service: AppService, raw: dict[str, Any]) -> dict[str, Any]:
    _payload(EmptyPayload, raw)
    cleared = store.clear_history(session_id)
    return {"cleared": cleared, "count": 0}


def _history_entry_from_success(request_id: str, question: str, data: dict[str, Any]) -> dict[str, Any]:
    execution = data.get("execution") or {}
    return {
        "id": uuid4().hex,
        "request_id": request_id,
        "question": question,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "success",
        "route": data.get("route"),
        "query_shape": data.get("query_shape"),
        "sql": data.get("sql"),
        "row_count": data.get("row_count"),
        "duration_ms": execution.get("duration_ms"),
    }


def _history_entry_from_error(request_id: str, question: str, exc: "GatewayActionError") -> dict[str, Any]:
    return {
        "id": uuid4().hex,
        "request_id": request_id,
        "question": question,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "rejected",
        "route": exc.code,
        "query_shape": None,
        "sql": None,
        "row_count": None,
        "duration_ms": None,
        "error": _error(exc.code, exc.message),
    }


class GatewayActionError(Exception):
    def __init__(self, code: str, message: str, details: Any = None) -> None:
        self.code = code
        self.message = message
        self.details = details


Handler = Callable[[str, AppService, dict[str, Any]], dict[str, Any]]
HANDLERS: dict[str, Handler] = {
    "session.status": _handle_session_status,
    "session.reset": _handle_session_reset,
    "database.connect": _handle_database_connect,
    "database.disconnect": _handle_database_disconnect,
    "database.status": _handle_database_status,
    "knowledge.status": _handle_knowledge_status,
    "knowledge.rebuild": _handle_knowledge_rebuild,
    "query.ask": _handle_query_ask,
    "query.last": _handle_query_last,
    "history.list": _handle_history_list,
    "history.clear": _handle_history_clear,
}


@app.get("/health")
def health() -> dict[str, Any]:
    return {"ok": True, "service": "sqlsense-gateway"}


@app.exception_handler(RequestValidationError)
def gateway_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=200,
        content={
            "ok": False,
            "request_id": uuid4().hex,
            "action": "",
            "data": None,
            "error": _error("invalid_request", "Invalid gateway request", exc.errors()),
        },
    )


@app.post("/api/v1/gateway")
def gateway(request_payload: GatewayRequest, request: Request, response: Response) -> dict[str, Any]:
    request_id = uuid4().hex
    session_id, service = store.get(request, response)
    action = request_payload.action
    context = new_context(
        request_id=request_id,
        session_id=session_id,
        database_identity=service.database_service.get_db_config(),
        schema_fingerprint=str(service.database_service.knowledge_base_metadata.get("schema_fingerprint", "") or ""),
    )
    with bind_context(context):
        observe_event("request_received", component="api_gateway", stage="gateway", status="received", action=action)
        return _gateway_with_context(request_id, session_id, service, action, request_payload)


def _gateway_with_context(
    request_id: str,
    session_id: str,
    service: AppService,
    action: str,
    request_payload: GatewayRequest,
) -> dict[str, Any]:
    try:
        handler = HANDLERS.get(action)
        if handler is None:
            raise GatewayActionError("unknown_action", f"Unknown action: {action}")
        data = handler(session_id, service, request_payload.payload)
        if action == "query.ask":
            question = str(request_payload.payload.get("question", ""))
            store.add_history(session_id, _history_entry_from_success(request_id, question, data))
        observe_event("request_completed", component="api_gateway", stage="gateway", status="success", action=action)
        return {
            "ok": True,
            "request_id": request_id,
            "action": action,
            "data": _redact(data),
            "error": None,
        }
    except ValidationError as exc:
        observe_event("request_rejected", component="api_gateway", stage="gateway", status="failed", reason_code="invalid_payload", action=action)
        return {
            "ok": False,
            "request_id": request_id,
            "action": action,
            "data": None,
            "error": _error("invalid_payload", "Invalid action payload", exc.errors()),
        }
    except GatewayActionError as exc:
        if action == "query.ask":
            question = str(request_payload.payload.get("question", ""))
            store.add_history(session_id, _history_entry_from_error(request_id, question, exc))
        observe_event("request_rejected", component="api_gateway", stage="gateway", status="failed", reason_code=exc.code, action=action)
        return {
            "ok": False,
            "request_id": request_id,
            "action": action,
            "data": None,
            "error": _error(exc.code, exc.message, exc.details),
        }
    except Exception:
        observe_event("request_failed", component="api_gateway", stage="gateway", status="failed", reason_code="internal_error", action=action)
        return {
            "ok": False,
            "request_id": request_id,
            "action": action,
            "data": None,
            "error": _error("internal_error", "Gateway request failed"),
        }


def reset_sessions_for_tests() -> None:
    for service in list(store._sessions.values()):
        service.disconnect_database()
    store._sessions.clear()
    store._history.clear()
