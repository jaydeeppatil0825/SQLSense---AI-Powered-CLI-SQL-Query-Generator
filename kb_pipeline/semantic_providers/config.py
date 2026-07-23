"""Environment-backed semantic provider configuration."""

from __future__ import annotations

from dataclasses import dataclass
import os


@dataclass(frozen=True)
class SemanticProviderConfig:
    enable_ai_semantic_enrichment: bool = True
    provider_order: str = "ollama,nvidia,rule_based"
    local_provider_url: str = ""
    cloud_provider_name: str = "nvidia"
    model_name: str = ""
    timeout_seconds: int = 60
    max_retries: int = 1
    max_schema_payload_size: int = 250_000
    allow_cloud_provider: bool = False
    fallback_to_rule_based: bool = True

    @classmethod
    def from_env(cls) -> "SemanticProviderConfig":
        return cls(
            enable_ai_semantic_enrichment=_bool_env("ENABLE_AI_SEMANTIC_ENRICHMENT", True),
            provider_order=os.getenv("SEMANTIC_PROVIDER_ORDER", "ollama,nvidia,rule_based"),
            local_provider_url=os.getenv("SEMANTIC_LOCAL_PROVIDER_URL", ""),
            cloud_provider_name=os.getenv("SEMANTIC_CLOUD_PROVIDER_NAME", "nvidia"),
            model_name=os.getenv("SEMANTIC_MODEL_NAME", ""),
            timeout_seconds=_int_env("SEMANTIC_TIMEOUT_SECONDS", 60),
            max_retries=_int_env("SEMANTIC_MAX_RETRIES", 1),
            max_schema_payload_size=_int_env("SEMANTIC_MAX_SCHEMA_PAYLOAD_SIZE", 250_000),
            allow_cloud_provider=_bool_env("SEMANTIC_ALLOW_CLOUD_PROVIDER", False),
            fallback_to_rule_based=_bool_env("SEMANTIC_FALLBACK_TO_RULE_BASED", True),
        )


def _bool_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default
