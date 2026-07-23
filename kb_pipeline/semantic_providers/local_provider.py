"""AI provider wrapper for build-time semantic mapping."""

from __future__ import annotations

from typing import Any, Callable

from kb_pipeline.ai_semantic_enricher import get_last_enrichment_reason

from .base import ProviderAttempt, SemanticProviderResult
from .response_parser import validate_enriched_knowledge_base
from .schema_label_contract import utc_now_iso

HealthCheck = Callable[[], tuple[bool, str]]
EnrichFunc = Callable[[dict[str, Any], str], dict[str, Any]]


class AISemanticProvider:
    def __init__(
        self,
        *,
        provider_name: str,
        provider_type: str,
        backend: str,
        health_check: HealthCheck | None,
        enrich_func: EnrichFunc,
    ) -> None:
        self.provider_name = provider_name
        self.provider_type = provider_type
        self.backend = backend
        self._health_check = health_check
        self._enrich_func = enrich_func

    def health_check(self) -> tuple[bool, str]:
        if self._health_check is None:
            return True, "health check skipped"
        return self._health_check()

    def map_schema_semantics(self, knowledge_base: dict[str, Any]) -> SemanticProviderResult:
        ok, message = self.health_check()
        if not ok:
            return SemanticProviderResult(
                status="failed",
                knowledge_base=knowledge_base,
                provider_used="",
                provider_attempts=[
                    ProviderAttempt(self.provider_name, self.provider_type, "failed", _safe_reason(message))
                ],
                reason=_safe_reason(message),
            )

        enriched = self._enrich_func(knowledge_base, self.backend)
        if enriched is knowledge_base or enriched == knowledge_base:
            reason = get_last_enrichment_reason() or "provider returned no enrichment"
            return SemanticProviderResult(
                status="failed",
                knowledge_base=knowledge_base,
                provider_used="",
                provider_attempts=[
                    ProviderAttempt(self.provider_name, self.provider_type, "failed", reason)
                ],
                reason=reason,
            )

        validate_enriched_knowledge_base(enriched, knowledge_base)
        _mark_ai_metadata(enriched, self.provider_name, "ai_enrichment")
        return SemanticProviderResult(
            status="enriched",
            knowledge_base=enriched,
            provider_used=self.provider_name,
            provider_attempts=[
                ProviderAttempt(self.provider_name, self.provider_type, "success", "semantic mapping accepted")
            ],
            fallback_used=False,
            ai_enabled=True,
            metadata={"generated_at": utc_now_iso()},
        )


def _mark_ai_metadata(knowledge_base: dict[str, Any], provider: str, source: str) -> None:
    for table_data in knowledge_base.values():
        table_ai = table_data.setdefault("ai_metadata", {})
        if isinstance(table_ai, dict):
            table_ai.setdefault("provider", provider)
            table_ai.setdefault("source", source)
        for column in table_data.get("columns", []):
            column_ai = column.get("ai_metadata")
            if isinstance(column_ai, dict):
                column_ai.setdefault("provider", provider)
                column_ai.setdefault("source", source)


def _safe_reason(value: Any) -> str:
    text = str(value or "").replace("\n", " ").strip()
    for token in ("api_key", "apikey", "authorization", "bearer", "password", "token"):
        text = text.replace(token, "[redacted]")
    return text[:160]
