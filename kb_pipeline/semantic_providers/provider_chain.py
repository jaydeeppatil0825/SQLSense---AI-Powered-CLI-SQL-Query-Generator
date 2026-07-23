"""AI-first semantic provider chain for KB builds."""

from __future__ import annotations

import os
from typing import Any, Callable, Iterable

from core.ai_backend_service import check_ollama_status, get_ai_backend_service
from kb_pipeline.ai_semantic_enricher import enrich_knowledge_base_with_ai
from utils.logger import get_logger

from .base import ProviderAttempt, SemanticProvider, SemanticProviderResult
from .cloud_provider import CloudSemanticProvider
from .config import SemanticProviderConfig
from .local_provider import AISemanticProvider
from .rule_based_provider import RuleBasedSemanticProvider

logger = get_logger()

DEFAULT_PROVIDER_ORDER = ("ollama", "nvidia", "rule_based")


class SemanticProviderChain:
    def __init__(
        self,
        providers: Iterable[SemanticProvider],
        *,
        ai_enabled: bool = True,
        fallback_to_rule_based: bool = True,
    ) -> None:
        self.providers = list(providers)
        self.ai_enabled = ai_enabled
        self.fallback_to_rule_based = fallback_to_rule_based

    def map_schema_semantics(self, knowledge_base: dict[str, Any]) -> SemanticProviderResult:
        attempts: list[ProviderAttempt] = []
        for provider in self.providers:
            if provider.provider_type == "deterministic" and self.ai_enabled and not self.fallback_to_rule_based:
                continue
            if provider.provider_type != "deterministic" and not self.ai_enabled:
                attempts.append(ProviderAttempt(provider.provider_name, provider.provider_type, "skipped", "AI disabled"))
                continue
            try:
                result = provider.map_schema_semantics(knowledge_base)
            except Exception as exc:
                reason = _safe_reason(exc)
                attempts.append(ProviderAttempt(provider.provider_name, provider.provider_type, "failed", reason))
                logger.info("Semantic provider failed: %s (%s)", provider.provider_name, reason)
                continue

            attempts.extend(result.provider_attempts)
            if result.status in {"enriched", "fallback"}:
                result.provider_attempts = attempts
                if result.status == "fallback":
                    result.reason = _fallback_reason(attempts, result.reason)
                result.ai_enabled = self.ai_enabled
                return result

        return SemanticProviderResult(
            status="failed_closed",
            knowledge_base=knowledge_base,
            provider_used="",
            provider_attempts=attempts,
            fallback_used=False,
            ai_enabled=self.ai_enabled,
            reason="No semantic provider produced a valid mapping",
        )


def build_default_provider_chain(
    *,
    ai_enabled: bool = True,
    provider_order: str | None = None,
    fallback_to_rule_based: bool = True,
    allow_cloud_provider: bool | None = None,
    enrich_func: Callable[[dict[str, Any], str], dict[str, Any]] = enrich_knowledge_base_with_ai,
    local_health_check: Callable[[], tuple[bool, str]] = check_ollama_status,
    nvidia_health_check: Callable[[], tuple[bool, str]] | None = None,
) -> SemanticProviderChain:
    config = SemanticProviderConfig.from_env()
    order = _provider_order(provider_order)
    allow_cloud = config.allow_cloud_provider if allow_cloud_provider is None else allow_cloud_provider
    providers: list[SemanticProvider] = []

    for name in order:
        if name in {"ollama", "local"}:
            providers.append(
                AISemanticProvider(
                    provider_name="ollama",
                    provider_type="local",
                    backend="local",
                    health_check=local_health_check,
                    enrich_func=enrich_func,
                )
            )
        elif name == "nvidia":
            if allow_cloud:
                providers.append(
                    CloudSemanticProvider(
                        provider_name="nvidia",
                        backend="nvidia",
                        health_check=nvidia_health_check
                        or (lambda: get_ai_backend_service().test_backend_connection("nvidia")),
                        enrich_func=enrich_func,
                    )
                )
            else:
                providers.append(_SkippedProvider("nvidia", "cloud", "cloud provider disabled"))
        elif name == "rule_based":
            providers.append(RuleBasedSemanticProvider())
        else:
            providers.append(_SkippedProvider(name, "unknown", "unsupported semantic provider"))

    if fallback_to_rule_based and not any(provider.provider_type == "deterministic" for provider in providers):
        providers.append(RuleBasedSemanticProvider())
    return SemanticProviderChain(
        providers,
        ai_enabled=ai_enabled,
        fallback_to_rule_based=fallback_to_rule_based,
    )


class _SkippedProvider:
    def __init__(self, provider_name: str, provider_type: str, reason: str) -> None:
        self.provider_name = provider_name
        self.provider_type = provider_type
        self.reason = reason

    def health_check(self) -> tuple[bool, str]:
        return False, self.reason

    def map_schema_semantics(self, knowledge_base: dict[str, Any]) -> SemanticProviderResult:
        return SemanticProviderResult(
            status="failed",
            knowledge_base=knowledge_base,
            provider_used="",
            provider_attempts=[ProviderAttempt(self.provider_name, self.provider_type, "skipped", self.reason)],
            reason=self.reason,
        )


def _provider_order(value: str | None) -> tuple[str, ...]:
    raw = value or os.getenv("SEMANTIC_PROVIDER_ORDER") or ",".join(DEFAULT_PROVIDER_ORDER)
    names = tuple(name.strip().lower() for name in raw.split(",") if name.strip())
    return names or DEFAULT_PROVIDER_ORDER


def _fallback_reason(attempts: list[ProviderAttempt], default: str) -> str:
    for attempt in attempts:
        if attempt.provider_type != "deterministic" and attempt.reason:
            return attempt.reason
    return default


def _safe_reason(exc: Any) -> str:
    text = str(exc or "").replace("\n", " ").strip()
    for token in ("api_key", "apikey", "authorization", "bearer", "password", "token"):
        text = text.replace(token, "[redacted]")
    return text[:160]
