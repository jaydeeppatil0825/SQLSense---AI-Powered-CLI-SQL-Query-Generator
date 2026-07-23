"""Provider contracts for build-time semantic enrichment."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from .schema_label_contract import SEMANTIC_MAPPING_CONTRACT_VERSION


@dataclass(frozen=True)
class ProviderAttempt:
    provider_name: str
    provider_type: str
    status: str
    reason: str = ""


@dataclass
class SemanticProviderResult:
    status: str
    knowledge_base: dict[str, Any]
    provider_used: str
    provider_attempts: list[ProviderAttempt] = field(default_factory=list)
    fallback_used: bool = False
    ai_enabled: bool = True
    reason: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_metadata(self) -> dict[str, Any]:
        return {
            "enrichment_contract_version": SEMANTIC_MAPPING_CONTRACT_VERSION,
            "provider_used": self.provider_used,
            "provider_attempts": [attempt.__dict__ for attempt in self.provider_attempts],
            "fallback_used": self.fallback_used,
            "ai_enabled": self.ai_enabled,
            "status": self.status,
            "reason": self.reason,
            **self.metadata,
        }


class SemanticProvider(Protocol):
    provider_name: str
    provider_type: str

    def health_check(self) -> tuple[bool, str]:
        ...

    def map_schema_semantics(self, knowledge_base: dict[str, Any]) -> SemanticProviderResult:
        ...
