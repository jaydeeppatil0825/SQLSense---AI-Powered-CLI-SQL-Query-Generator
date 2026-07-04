"""No-op Chroma product telemetry used by SQLSense local retrieval."""

from __future__ import annotations

from chromadb.telemetry.product import ProductTelemetryClient, ProductTelemetryEvent


class NoOpProductTelemetry(ProductTelemetryClient):
    """Disable optional product telemetry without invoking PostHog."""

    def capture(self, event: ProductTelemetryEvent) -> None:
        return None
