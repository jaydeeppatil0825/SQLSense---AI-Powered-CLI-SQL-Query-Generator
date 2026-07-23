"""Cloud AI semantic provider wrappers."""

from __future__ import annotations

from typing import Any, Callable

from .local_provider import AISemanticProvider


class CloudSemanticProvider(AISemanticProvider):
    def __init__(
        self,
        *,
        provider_name: str,
        backend: str,
        health_check: Callable[[], tuple[bool, str]] | None,
        enrich_func: Callable[[dict[str, Any], str], dict[str, Any]],
    ) -> None:
        super().__init__(
            provider_name=provider_name,
            provider_type="cloud",
            backend=backend,
            health_check=health_check,
            enrich_func=enrich_func,
        )
