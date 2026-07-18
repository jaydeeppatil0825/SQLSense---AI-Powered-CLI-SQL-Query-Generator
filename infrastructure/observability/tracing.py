from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
import sys
import time
from typing import Any, Iterator, Protocol


class Tracer(Protocol):
    def span(self, name: str, **attributes: Any) -> Iterator[dict[str, Any]]: ...


@dataclass
class NoopTracer:
    @contextmanager
    def span(self, name: str, **attributes: Any) -> Iterator[dict[str, Any]]:
        yield {}


@dataclass
class InMemoryTracer:
    spans: list[dict[str, Any]] = field(default_factory=list)

    @contextmanager
    def span(self, name: str, **attributes: Any) -> Iterator[dict[str, Any]]:
        record = {"name": name, "attributes": dict(attributes), "status": "started"}
        started = time.perf_counter()
        try:
            yield record
            record["status"] = "ok"
        except Exception as exc:
            record["status"] = "error"
            record["error_type"] = type(exc).__name__
            raise
        finally:
            record["duration_ms"] = round((time.perf_counter() - started) * 1000, 3)
            self.spans.append(record)


_tracer: Tracer = NoopTracer()


def set_tracer(tracer: Tracer | None) -> None:
    global _tracer
    _tracer = tracer or NoopTracer()


def get_tracer() -> Tracer:
    return _tracer


@contextmanager
def safe_span(name: str, **attributes: Any) -> Iterator[dict[str, Any]]:
    manager = None
    try:
        manager = _tracer.span(name, **attributes)
        span = manager.__enter__()
    except Exception:
        yield {}
        return
    try:
        yield span
    except Exception:
        exc_info = sys.exc_info()
        try:
            manager.__exit__(*exc_info)
        except Exception:
            pass
        raise
    else:
        try:
            manager.__exit__(None, None, None)
        except Exception:
            pass
