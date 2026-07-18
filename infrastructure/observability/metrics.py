from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from threading import RLock
from typing import Any, Protocol


class Metrics(Protocol):
    def increment(self, name: str, value: int = 1, **labels: Any) -> None: ...
    def observe(self, name: str, value: float, **labels: Any) -> None: ...
    def gauge(self, name: str, value: float, **labels: Any) -> None: ...


def _key(name: str, labels: dict[str, Any]) -> tuple[str, tuple[tuple[str, str], ...]]:
    return name, tuple(sorted((str(key), str(value)) for key, value in labels.items()))


@dataclass
class NoopMetrics:
    def increment(self, name: str, value: int = 1, **labels: Any) -> None:
        return None

    def observe(self, name: str, value: float, **labels: Any) -> None:
        return None

    def gauge(self, name: str, value: float, **labels: Any) -> None:
        return None


@dataclass
class InMemoryMetrics:
    counters: dict[tuple[str, tuple[tuple[str, str], ...]], int] = field(default_factory=lambda: defaultdict(int))
    histograms: dict[tuple[str, tuple[tuple[str, str], ...]], list[float]] = field(default_factory=lambda: defaultdict(list))
    gauges: dict[tuple[str, tuple[tuple[str, str], ...]], float] = field(default_factory=dict)
    _lock: RLock = field(default_factory=RLock)

    def increment(self, name: str, value: int = 1, **labels: Any) -> None:
        with self._lock:
            self.counters[_key(name, labels)] += int(value)

    def observe(self, name: str, value: float, **labels: Any) -> None:
        with self._lock:
            self.histograms[_key(name, labels)].append(float(value))

    def gauge(self, name: str, value: float, **labels: Any) -> None:
        with self._lock:
            self.gauges[_key(name, labels)] = float(value)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "counters": {repr(key): value for key, value in self.counters.items()},
                "histograms": {repr(key): list(value) for key, value in self.histograms.items()},
                "gauges": {repr(key): value for key, value in self.gauges.items()},
            }


_metrics: Metrics = NoopMetrics()


def set_metrics(metrics: Metrics | None) -> None:
    global _metrics
    _metrics = metrics or NoopMetrics()


def get_metrics() -> Metrics:
    return _metrics


def safe_increment(name: str, value: int = 1, **labels: Any) -> None:
    try:
        _metrics.increment(name, value, **labels)
    except Exception:
        pass


def safe_observe(name: str, value: float, **labels: Any) -> None:
    try:
        _metrics.observe(name, value, **labels)
    except Exception:
        pass


def safe_gauge(name: str, value: float, **labels: Any) -> None:
    try:
        _metrics.gauge(name, value, **labels)
    except Exception:
        pass
