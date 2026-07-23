"""Deterministic fallback semantic provider."""

from __future__ import annotations

from typing import Any

from .base import ProviderAttempt, SemanticProviderResult
from .schema_label_contract import utc_now_iso


class RuleBasedSemanticProvider:
    provider_name = "rule_based"
    provider_type = "deterministic"

    def health_check(self) -> tuple[bool, str]:
        return True, "deterministic fallback available"

    def map_schema_semantics(self, knowledge_base: dict[str, Any]) -> SemanticProviderResult:
        return SemanticProviderResult(
            status="fallback",
            knowledge_base=knowledge_base,
            provider_used=self.provider_name,
            provider_attempts=[
                ProviderAttempt(self.provider_name, self.provider_type, "success", "rule-based fallback used")
            ],
            fallback_used=True,
            ai_enabled=False,
            reason="rule-based fallback used",
            metadata={"generated_at": utc_now_iso()},
        )
