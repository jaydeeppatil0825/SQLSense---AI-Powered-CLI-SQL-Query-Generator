from __future__ import annotations

from contextlib import contextmanager
import time
from typing import Any, Iterator

from infrastructure.observability.logging import event as log_event
from infrastructure.observability.metrics import safe_increment, safe_observe
from infrastructure.observability.tracing import safe_span


def event(
    name: str,
    *,
    component: str,
    stage: str,
    status: str,
    reason_code: str = "",
    **metadata: Any,
) -> None:
    log_event(name, component=component, stage=stage, status=status, reason_code=reason_code, **metadata)
    safe_increment("sqlsense_events_total", event=name, component=component, stage=stage, status=status)


@contextmanager
def timed_stage(
    event_name: str,
    *,
    component: str,
    stage: str,
    span_name: str | None = None,
    reason_code: str = "",
    **metadata: Any,
) -> Iterator[dict[str, Any]]:
    started = time.perf_counter()
    status = "success"
    with safe_span(span_name or f"{component}.{stage}", component=component, stage=stage, **metadata):
        try:
            yield metadata
        except Exception:
            status = "failed"
            raise
        finally:
            duration_ms = round((time.perf_counter() - started) * 1000, 3)
            safe_metadata = {
                key: value
                for key, value in metadata.items()
                if key not in {"component", "stage", "status", "duration_ms", "reason_code"}
            }
            log_event(
                event_name,
                component=component,
                stage=stage,
                status=status,
                duration_ms=duration_ms,
                reason_code=reason_code,
                **safe_metadata,
            )
            safe_increment(
                "sqlsense_stage_total",
                component=component,
                stage=stage,
                status=status,
            )
            safe_observe(
                "sqlsense_stage_duration_ms",
                duration_ms,
                component=component,
                stage=stage,
                status=status,
            )
