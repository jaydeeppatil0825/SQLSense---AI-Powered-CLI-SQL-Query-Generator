"""Neutral pure predicates and shared constants for deterministic planning."""

from __future__ import annotations

import re
from typing import Any

from query_pipeline.planner.text_utils import (
    _content_terms,
    _humanize,
    _normalize,
    _safe_float,
    _singularize_token,
    _tokenize,
)

_UNSAFE_QUERY_RE = re.compile(
    r"\b(insert|update|delete|drop|alter|truncate|create|grant|revoke)\b",
    re.IGNORECASE,
)

_GENERIC_ROLE_TERMS = {
    "amount",
    "value",
    "status",
    "type",
    "category",
    "total",
}

_SCORING_TIERS = {
    "exact_normalized_column": 1.0,
    "owner_qualified_exact": 0.99,
    "kb_glossary_semantic": 0.92,
    "numeric_metric_eligible": 0.74,
    "dimension_type_eligible": 0.7,
    "sample_value_filter_match": 0.88,
    "direct_graph_compatible": 0.96,
    "selected_join_path_agreement": 1.0,
    "aggregate_ranking_keyword_agreement": 0.93,
    "source_phrase_agreement": 0.9,
}

_NUMERIC_METRIC_SEMANTIC_TYPES = {
    "money",
    "quantity",
    "percentage",
    "numeric_candidate",
    "number",
    "decimal",
    "integer",
    "float",
}

_NON_METRIC_SEMANTIC_TYPES = {
    "status",
    "text",
    "text_candidate",
    "category",
    "category_candidate",
    "date",
    "name",
    "code",
    "id",
    "reference",
}

_DIMENSION_SEMANTIC_TYPES = {
    "status",
    "text",
    "text_candidate",
    "category",
    "category_candidate",
    "date",
    "name",
    "code",
    "reference",
}

_LEADING_STATUS_ENTITY_MODIFIERS = {
    "active",
    "inactive",
    "pending",
    "paid",
    "unpaid",
    "cancelled",
    "canceled",
    "completed",
    "delivered",
    "shipped",
    "failed",
    "refunded",
}


def strip_leading_status_entity_modifier(phrase: str) -> str:
    """Return entity phrase without a leading generic status modifier.

    The removed token is still handled by filter resolution; this helper only
    keeps table-scope resolution from treating phrases like "delivered orders"
    as an unknown entity.
    """
    tokens = _tokenize(phrase)
    if len(tokens) < 2:
        return str(phrase or "").strip()
    first = _singularize_token(tokens[0])
    if first not in _LEADING_STATUS_ENTITY_MODIFIERS:
        return str(phrase or "").strip()
    remainder = " ".join(tokens[1:]).strip()
    return remainder or str(phrase or "").strip()


def _aggregate_function_hint(question: str) -> str | None:
    normalized = _normalize(question)
    if re.search(r"\b(average|avg|mean)\b", normalized):
        return "avg"
    if re.search(r"\b(total|sum)\b", normalized):
        return "sum"
    if re.search(r"\b(highest|maximum|max|largest|most)\b", normalized):
        return "max"
    if re.search(r"\b(lowest|minimum|min|smallest|least)\b", normalized):
        return "min"
    return None


def _extract_limit(question: str) -> int | None:
    match = re.search(
        r"\b(?:top|first|last|latest|recent|limit|show|get|fetch)\s+(\d+)\b"
        r"|\b(\d+)\s+(?:rows?|records?|results?|items?)\b",
        question,
        re.IGNORECASE,
    )
    if match:
        return int(match.group(1) or match.group(2))
    return None


def _explicit_metric_candidate_count(
    question: str,
    metric_candidates: list[dict[str, Any]],
) -> int:
    normalized_question = _normalize(question)
    question_tokens = set(_content_terms(question))
    explicit_count = 0
    seen: set[tuple[str, str]] = set()

    for candidate in metric_candidates or []:
        table_name = str(candidate.get("table") or "").strip()
        column_name = str(candidate.get("column") or "").strip()
        if not column_name:
            continue
        signature = (table_name, column_name)
        if signature in seen:
            continue

        matched_terms = [
            str(value).strip().lower()
            for value in (candidate.get("matched_terms") or [])
            if str(value).strip()
        ]
        candidate_tokens = set(_tokenize(column_name))
        explicit = False
        for term in matched_terms:
            term_tokens = set(_tokenize(term))
            if term and term in normalized_question:
                explicit = True
                break
            if term_tokens and term_tokens <= question_tokens:
                explicit = True
                break
        if not explicit and candidate_tokens and candidate_tokens <= question_tokens:
            explicit = True
        if explicit:
            explicit_count += 1
            seen.add(signature)

    return explicit_count


def _question_requests_multiple_metrics(question: str) -> bool:
    normalized_question = _normalize(question)
    normalized_question = re.sub(
        r"\bbetween\s+.+?\s+and\s+.+?(?=\s+(?:by|per|each|group(?:ed)?\s+by|sorted|ordered|where|with|for|from|limit\b)|$)",
        "",
        normalized_question,
        flags=re.IGNORECASE,
    )
    return bool(re.search(r"\b(and|,)\b", normalized_question))


def classify_query_shape(
    *,
    question: str,
    intent: dict[str, Any] | None,
    retrieved_context: dict[str, Any] | None,
    plan: dict[str, Any],
    selected_tables: list[dict[str, Any]],
    selected_columns: list[dict[str, Any]],
    metric_candidates: list[dict[str, Any]],
    dimension_candidates: list[dict[str, Any]],
    filter_candidates: list[dict[str, Any]],
    join_paths: list[dict[str, Any]],
    formula_evidence: list[dict[str, Any]],
) -> str:
    """Centralized planner query-shape classifier used before route selection."""
    del retrieved_context, selected_columns, dimension_candidates, formula_evidence

    table_count = len([entry for entry in selected_tables if entry.get("table")])
    requested_dimensions = list((intent or {}).get("requested_dimensions") or plan.get("requested_dimensions") or [])
    requested_metrics = list((intent or {}).get("requested_metrics") or plan.get("requested_metrics") or [])
    requested_filters = list((intent or {}).get("requested_filters") or plan.get("requested_filters") or [])
    has_grouping = bool(plan.get("grouping") or plan.get("dimension") or requested_dimensions)
    has_filters = bool(filter_candidates or plan.get("filters") or plan.get("date_range") or requested_filters)
    has_join_paths = bool(join_paths)
    has_join = has_join_paths or table_count > 1
    intent_type = str((intent or {}).get("intent_type") or "").strip().lower()
    planner_intent = str(plan.get("intent") or "").strip().lower()
    aggregate_hint = _aggregate_function_hint(question)
    is_count = (
        planner_intent == "count"
        or intent_type == "count"
        or str((intent or {}).get("aggregate_function") or "").strip().lower() == "count"
    )
    is_ranking = bool(
        planner_intent == "top_n"
        or intent_type in {"ranking", "sorted_list"}
        or (intent or {}).get("requested_sort")
        or plan.get("sorting")
    )
    has_explicit_rank_count = bool(
        re.search(r"\b(?:top|bottom|lowest|highest|first|last)\s+\d+\b", question, re.IGNORECASE)
    )
    explicit_metric_count = _explicit_metric_candidate_count(question, metric_candidates)
    has_metric = bool(metric_candidates or requested_metrics)

    if bool((intent or {}).get("unsafe")) or _UNSAFE_QUERY_RE.search(question):
        return "blocked_unsafe"
    if explicit_metric_count > 1 and _question_requests_multiple_metrics(question):
        return "multi_metric_aggregate"
    if is_ranking and has_explicit_rank_count:
        return "ranking_query"
    if is_ranking:
        return "ranking_query"
    if has_grouping and (has_metric or is_count):
        return "grouped_aggregate"
    if has_filters:
        return "filtered_query"
    if aggregate_hint and table_count == 1 and not has_grouping and not has_join:
        return "single_table_aggregate"
    if has_join_paths:
        return "joined_lookup"
    if is_count and table_count == 1:
        return "single_table_count"
    if planner_intent == "list" and table_count == 1:
        return "single_table_list"
    return "unknown"


def _merge_candidate_columns(*groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for group in groups:
        for entry in group:
            table_name = str(entry.get("table", "")).strip()
            column_name = str(entry.get("column", "")).strip()
            if not table_name or not column_name:
                continue
            key = (table_name, column_name)
            existing = merged.get(key)
            if not existing:
                merged[key] = dict(entry)
                continue
            existing["score"] = max(_safe_float(existing.get("score")), _safe_float(entry.get("score")))
            existing["matched_terms"] = list(dict.fromkeys(list(existing.get("matched_terms") or []) + list(entry.get("matched_terms") or [])))
            existing["is_measure"] = bool(existing.get("is_measure")) or bool(entry.get("is_measure"))
            existing["is_dimension"] = bool(existing.get("is_dimension")) or bool(entry.get("is_dimension"))
            existing["source"] = existing.get("source") if existing.get("source") == "vector" else entry.get("source", existing.get("source"))
    results = list(merged.values())
    results.sort(key=lambda item: (-_safe_float(item.get("score")), str(item.get("table") or ""), str(item.get("column") or "")))
    return results


def _is_simple_primary_table_question(plan: dict[str, Any]) -> bool:
    if (
        str(plan.get("intent") or "") in {"count", "total", "average"}
        and not plan.get("dimension")
        and not plan.get("grouping")
    ):
        return True

    return (
        str(plan.get("intent") or "") in {"list", "count"}
        and not plan.get("dimension")
        and not plan.get("grouping")
        and not plan.get("filters")
        and not plan.get("date_range")
        and not (set(plan.get("semantic_hints") or set()) & {"date", "status"})
    )


def _table_name_matches_question(question: str, table_name: str) -> bool:
    normalized_question = _normalize(question)
    human_table = _humanize(table_name)
    if human_table and human_table in normalized_question:
        return True

    question_terms = set(_tokenize(question))
    table_terms: set[str] = set()
    for token in _tokenize(table_name):
        table_terms.add(token)
        table_terms.add(_singularize_token(token))

    return bool(table_terms & question_terms)


def _primary_table_for_simple_question_from_entries(
    question: str,
    plan: dict[str, Any],
    table_entries: list[dict[str, Any]],
) -> str | None:
    """Choose one dominant table for simple single-table list/count questions."""
    if not _is_simple_primary_table_question(plan) or not table_entries:
        return None

    ordered = [
        entry for entry in table_entries
        if str(entry.get("table", "")).strip()
    ]
    if not ordered:
        return None

    top_entry = ordered[0]
    top_table = str(top_entry.get("table", "")).strip()
    top_score = _safe_float(top_entry.get("score", top_entry.get("confidence", 0.0)))
    second_score = (
        _safe_float(ordered[1].get("score", ordered[1].get("confidence", 0.0)))
        if len(ordered) > 1
        else 0.0
    )
    direct_match = _table_name_matches_question(question, top_table)
    second_direct_match = (
        _table_name_matches_question(question, str(ordered[1].get("table", "")).strip())
        if len(ordered) > 1
        else False
    )

    if len(ordered) == 1:
        return top_table if (direct_match or top_score >= 0.6) else None

    if direct_match and not second_direct_match and top_score >= second_score:
        return top_table

    if top_score >= 0.85 and second_score < 0.7:
        return top_table

    if top_score >= second_score + 0.2 and top_score >= 0.65:
        return top_table

    return None


def _glossary_alias_hits_question(question: str, term: str, term_data: dict[str, Any]) -> bool:
    normalized_question = _normalize(question)
    question_terms = set(_content_terms(question))
    normalized_term = _normalize(term)
    term_tokens = set(_tokenize(term))

    if normalized_term and normalized_term in normalized_question:
        return True
    if term_tokens and term_tokens <= question_terms:
        return True

    for alias in (term_data.get("primary_terms", []) or term_data.get("business_terms", []) or []):
        normalized_alias = _normalize(alias)
        alias_tokens = set(_tokenize(alias))
        if normalized_alias and normalized_alias in normalized_question:
            return True
        if alias_tokens and alias_tokens <= question_terms:
            return True

    return False


def _glossary_mapped_tables(term_data: dict[str, Any]) -> set[str]:
    return {
        str(mapping.get("table", "")).strip()
        for mapping in term_data.get("mapped_columns", []) or []
        if str(mapping.get("table", "")).strip()
    }


def _has_strong_glossary_table_match(
    question: str,
    table_name: str,
    glossary_matches: list[tuple[str, dict[str, Any]]],
) -> bool:
    for term, term_data in glossary_matches:
        mapped_tables = _glossary_mapped_tables(term_data)
        if table_name not in mapped_tables:
            continue
        if len(mapped_tables) != 1:
            continue
        if _glossary_alias_hits_question(question, term, term_data):
            return True
    return False


def _expand_selected_tables(
    knowledge_base: dict,
    selected_names: list[str],
    dimension: str | None,
    plan: dict[str, Any] | None = None,
) -> list[str]:
    selected = list(selected_names)
    if plan and _is_simple_primary_table_question(plan):
        return selected

    dimension_tokens = set(_tokenize(dimension or ""))

    for table_name in list(selected_names):
        table_data = knowledge_base.get(table_name, {})
        for relationship in table_data.get("relationships", []):
            target_table = relationship.get("from_table") if relationship.get("direction") == "incoming" else relationship.get("to_table")
            if target_table not in knowledge_base or target_table in selected:
                continue

            target_tokens = set(_tokenize(target_table))
            target_tokens.update(knowledge_base[target_table].get("table_tokens", []))
            if dimension_tokens and dimension_tokens & target_tokens:
                selected.append(target_table)
            elif relationship.get("confidence", 0) >= 0.92 and len(selected) < 4:
                selected.append(target_table)

    return selected


def _table_phrase_score(phrase: str, table_name: str) -> float:
    phrase_tokens = {_singularize_token(token) for token in _tokenize(phrase)}
    table_tokens = {_singularize_token(token) for token in _tokenize(table_name)}
    if not phrase_tokens or not table_tokens:
        return 0.0
    if phrase_tokens == table_tokens:
        return 1.0
    if phrase_tokens <= table_tokens or table_tokens <= phrase_tokens:
        return 0.82
    return round((len(phrase_tokens & table_tokens) / len(phrase_tokens)) * 0.6, 4)


def _resolve_join_table(
    phrase: str,
    knowledge_base: dict[str, Any],
    retrieved_tables: list[dict[str, Any]] | None = None,
) -> tuple[str | None, str]:
    requested_tokens = {_singularize_token(token) for token in _tokenize(phrase)}
    retrieval_scores: dict[str, float] = {}
    for candidate in retrieved_tables or []:
        table_name = str(candidate.get("table") or "")
        candidate_terms = [table_name, *(candidate.get("matched_terms") or [])]
        if any(
            {_singularize_token(token) for token in _tokenize(term)} == requested_tokens
            for term in candidate_terms
            if _tokenize(term)
        ):
            retrieval_scores[table_name] = max(
                retrieval_scores.get(table_name, 0.0),
                min(_safe_float(candidate.get("score")), 0.96),
            )
    ranked = sorted(
        (
            (max(_table_phrase_score(phrase, table_name), retrieval_scores.get(table_name, 0.0)), table_name)
            for table_name in knowledge_base
        ),
        key=lambda item: (-item[0], item[1]),
    )
    ranked = [item for item in ranked if item[0] > 0]
    if not ranked:
        return None, "missing"
    if len(ranked) > 1 and abs(ranked[0][0] - ranked[1][0]) < 0.08 and ranked[0][0] < 1.0:
        return None, "ambiguous"
    return ranked[0][1], "resolved"


def _required_join_predicates(join_paths: list[dict[str, Any]]) -> list[str]:
    predicates: list[str] = []
    seen: set[str] = set()
    for join_path in join_paths:
        for edge in join_path.get("path", []) or []:
            join_condition = str(edge.get("join_condition") or "").strip()
            if not join_condition:
                from_table = str(edge.get("from_table") or "").strip()
                from_column = str(edge.get("from_column") or "").strip()
                to_table = str(edge.get("to_table") or "").strip()
                to_column = str(edge.get("to_column") or "").strip()
                if from_table and from_column and to_table and to_column:
                    join_condition = f"{from_table}.{from_column} = {to_table}.{to_column}"
            if join_condition and join_condition not in seen:
                seen.add(join_condition)
                predicates.append(join_condition)
    return predicates
