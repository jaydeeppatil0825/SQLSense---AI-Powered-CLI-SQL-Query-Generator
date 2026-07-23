"""JSON store for learned alias candidates."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Iterable

from semantic_learning.learned_aliases import LearnedAliasCandidate, alias_key, utc_now


def default_store_path() -> Path:
    return Path(os.environ.get("SQLSENSE_LEARNED_ALIAS_STORE", ".sqlsense/semantic_learning/learned_alias_candidates.json"))


class FileCandidateStore:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path is not None else default_store_path()

    def load(self) -> list[LearnedAliasCandidate]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return []
        if not isinstance(data, dict):
            return []
        return [
            LearnedAliasCandidate.from_dict(item)
            for item in data.get("candidates", [])
            if isinstance(item, dict)
        ]

    def save(self, candidates: Iterable[LearnedAliasCandidate]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "contract_version": "learned-alias-store-v1",
            "candidates": [candidate.to_dict() for candidate in sorted(candidates, key=alias_key)],
        }
        self.path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    def upsert_many(self, candidates: Iterable[LearnedAliasCandidate]) -> list[LearnedAliasCandidate]:
        existing = {alias_key(candidate): candidate for candidate in self.load()}
        for candidate in candidates:
            key = alias_key(candidate)
            if key in existing:
                current = existing[key]
                current.support_count += max(1, int(candidate.support_count or 1))
                current.last_seen_at = utc_now()
                current.confidence_score = min(1.0, round(current.confidence_score + 0.1, 4))
                current.evidence_examples_hashes = list(dict.fromkeys(
                    current.evidence_examples_hashes + candidate.evidence_examples_hashes
                ))[:20]
            else:
                existing[key] = candidate
        values = list(existing.values())
        self.save(values)
        return values
