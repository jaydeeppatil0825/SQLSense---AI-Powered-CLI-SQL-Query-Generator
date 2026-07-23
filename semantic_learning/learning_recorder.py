"""Optional recorder for successful deterministic query vocabulary."""

from __future__ import annotations

from typing import Any

from semantic_learning.candidate_store import FileCandidateStore
from semantic_learning.candidate_validator import validate_candidate
from semantic_learning.learned_aliases import LearnedAliasCandidate, normalize_phrase, stable_hash


class LearningRecorder:
    def __init__(self, store: FileCandidateStore | None = None):
        self.store = store or FileCandidateStore()

    def record_successful_execution(
        self,
        *,
        query_context: dict[str, Any],
        knowledge_base: dict[str, Any],
        database_identity_hash: str,
        schema_fingerprint: str,
    ) -> list[LearnedAliasCandidate]:
        if not isinstance(query_context, dict) or not isinstance(query_context.get("deterministic_query_plan"), dict):
            return []
        example_hash = stable_hash({
            "question": (query_context.get("plan") or {}).get("question", ""),
            "shape": query_context.get("query_shape", ""),
            "schema": schema_fingerprint,
        })
        candidates = [
            candidate for candidate in self._extract_candidates(query_context, database_identity_hash, schema_fingerprint, example_hash)
            if candidate.normalized_phrase
        ]
        valid = [
            validate_candidate(candidate, knowledge_base, schema_fingerprint=schema_fingerprint)
            for candidate in candidates
        ]
        valid = [candidate for candidate in valid if candidate.status != "rejected"]
        return self.store.upsert_many(valid) if valid else []

    def _extract_candidates(
        self,
        query_context: dict[str, Any],
        database_identity_hash: str,
        schema_fingerprint: str,
        example_hash: str,
    ) -> list[LearnedAliasCandidate]:
        candidates: list[LearnedAliasCandidate] = []
        for entry in query_context.get("selected_tables") or []:
            table = str((entry or {}).get("table") or "")
            phrase = normalize_phrase(table)
            if table and phrase:
                candidates.append(self._candidate(database_identity_hash, schema_fingerprint, phrase, "table", table, "", "table_entity", example_hash))
        metric = query_context.get("selected_metric") if isinstance(query_context.get("selected_metric"), dict) else {}
        if metric:
            phrase = normalize_phrase(" ".join(str(value) for value in [metric.get("table"), metric.get("column")] if value))
            candidates.append(self._candidate(database_identity_hash, schema_fingerprint, phrase, "metric", metric.get("table", ""), metric.get("column", ""), "metric", example_hash))
        for dimension in query_context.get("selected_dimensions") or []:
            if not isinstance(dimension, dict):
                continue
            phrase = normalize_phrase(" ".join(str(value) for value in [dimension.get("table"), dimension.get("column")] if value))
            candidates.append(self._candidate(database_identity_hash, schema_fingerprint, phrase, "dimension", dimension.get("table", ""), dimension.get("column", ""), "dimension", example_hash))
        for item in query_context.get("selected_filters") or []:
            if not isinstance(item, dict):
                continue
            value = str(item.get("value") or "")
            phrase = normalize_phrase(" ".join(str(part) for part in [item.get("table"), item.get("column"), value] if part))
            candidates.append(self._candidate(database_identity_hash, schema_fingerprint, phrase, "value", item.get("table", ""), item.get("column", ""), "filter_value", example_hash, value=value))
        return candidates

    def _candidate(
        self,
        database_identity_hash: str,
        schema_fingerprint: str,
        phrase: str,
        target_type: str,
        table: str,
        column: str,
        semantic_role: str,
        example_hash: str,
        *,
        value: str = "",
    ) -> LearnedAliasCandidate:
        return LearnedAliasCandidate(
            database_identity_hash=database_identity_hash,
            schema_fingerprint=schema_fingerprint,
            phrase=phrase,
            target_type=target_type,
            table=str(table or ""),
            column=str(column or ""),
            value=value,
            semantic_role=semantic_role,
            evidence_examples_hashes=[example_hash],
        )
