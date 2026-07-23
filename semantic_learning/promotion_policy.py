"""Conservative learned-alias promotion policy."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from semantic_learning.candidate_validator import validate_candidate
from semantic_learning.learned_aliases import LearnedAliasCandidate, utc_now


@dataclass(frozen=True)
class PromotionPolicy:
    support_threshold: int = 3
    require_admin_review: bool = False


def apply_promotion_policy(
    candidates: list[LearnedAliasCandidate],
    knowledge_base: dict[str, Any],
    *,
    schema_fingerprint: str = "",
    policy: PromotionPolicy | None = None,
) -> list[LearnedAliasCandidate]:
    policy = policy or PromotionPolicy()
    approved = [candidate for candidate in candidates if candidate.status == "approved"]
    result: list[LearnedAliasCandidate] = []
    for candidate in candidates:
        item = validate_candidate(candidate, knowledge_base, schema_fingerprint=schema_fingerprint, approved_aliases=approved)
        if item.status == "rejected":
            result.append(item)
            continue
        if item.status == "approved":
            result.append(item)
            continue
        if item.reject_count:
            result.append(item)
            continue
        if item.support_count < policy.support_threshold:
            result.append(item)
            continue
        if policy.require_admin_review and item.source != "admin_review":
            item.status = "pending"
            item.promotion_reason = "waiting_for_admin_review"
            result.append(item)
            continue
        same_phrase = [
            other for other in candidates
            if other.normalized_phrase == item.normalized_phrase
            and (other.table, other.column, other.value) != (item.table, item.column, item.value)
            and other.support_count >= item.support_count
        ]
        if same_phrase:
            item.status = "pending"
            item.promotion_reason = "conflicting_candidate"
            result.append(item)
            continue
        item.status = "approved"
        item.last_seen_at = utc_now()
        item.promotion_reason = f"support_count>={policy.support_threshold}"
        result.append(item)
    return result


def approved_aliases_for_schema(candidates: list[LearnedAliasCandidate], schema_fingerprint: str) -> list[LearnedAliasCandidate]:
    return [
        candidate for candidate in candidates
        if candidate.status == "approved" and candidate.schema_fingerprint == schema_fingerprint
    ]
