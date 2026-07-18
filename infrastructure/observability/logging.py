from __future__ import annotations

import json
import logging as py_logging
import os
from typing import Any

from infrastructure.observability.context import current_context
from infrastructure.observability.redaction import redact


LOGGER_NAME = "sqlsense.observability"


class JsonFormatter(py_logging.Formatter):
    def format(self, record: py_logging.LogRecord) -> str:
        payload = getattr(record, "observability_payload", None)
        if isinstance(payload, dict):
            return json.dumps(redact(payload), sort_keys=True, default=str)
        return super().format(record)


def configure_structured_logging() -> py_logging.Logger:
    logger = py_logging.getLogger(LOGGER_NAME)
    if logger.handlers:
        return logger
    handler = py_logging.StreamHandler()
    handler.setFormatter(
        JsonFormatter()
        if os.getenv("SQLSENSE_JSON_LOGS", "").strip().lower() in {"1", "true", "yes", "on"}
        else py_logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
    )
    logger.addHandler(handler)
    logger.setLevel(py_logging.INFO)
    logger.propagate = True
    return logger


def event(
    name: str,
    *,
    component: str,
    stage: str,
    status: str,
    duration_ms: float | None = None,
    reason_code: str = "",
    **metadata: Any,
) -> None:
    try:
        ctx = current_context().to_dict()
        payload = {
            "event": name,
            "component": component,
            "stage": stage,
            "status": status,
            "reason_code": reason_code,
            **ctx,
            "metadata": redact(metadata),
        }
        if duration_ms is not None:
            payload["duration_ms"] = round(float(duration_ms), 3)
        configure_structured_logging().info(
            "sqlsense_event",
            extra={"observability_payload": payload},
        )
    except Exception:
        try:
            py_logging.getLogger(LOGGER_NAME).debug("observability logging failed", exc_info=True)
        except Exception:
            pass
