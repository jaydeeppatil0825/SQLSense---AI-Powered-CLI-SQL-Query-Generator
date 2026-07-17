"""Filter resolution helpers for the deterministic planner."""

from __future__ import annotations

import re
from typing import Any

from kb_pipeline.schema_facts import (
    column_business_description,
    column_business_terms,
    column_sample_values,
    resolved_semantic_type,
)

_LOCATION_FILTER_TOKENS = {"city", "country", "location", "region", "area", "province"}
_STATUS_FILTER_TOKENS = {"status"}
_CATEGORY_FILTER_TOKENS = {"category", "segment", "type"}
_STATUS_VALUE_TOKENS = {
    "active",
    "inactive",
    "pending",
    "paid",
    "unpaid",
    "partial",
    "cancelled",
    "canceled",
    "completed",
    "delivered",
    "shipped",
    "refunded",
}


def _planner():
    from query_pipeline import query_planner as _qp

    return _qp


def _normalize(text: str) -> str:
    return _planner()._normalize(text)


def _humanize(text: str) -> str:
    return _planner()._humanize(text)


def _singularize_token(token: str) -> str:
    return _planner()._singularize_token(token)


def _tokenize(text: str) -> list[str]:
    return _planner()._tokenize(text)


def _sample_value_matches(value: str, sample: Any) -> bool:
    return _humanize(str(value or "")) == _humanize(str(sample or ""))


def _normalized_sample_values(column: dict[str, Any]) -> list[tuple[str, str]]:
    values = []
    for raw_value in column_sample_values(column):
        if raw_value is None:
            continue
        normalized = _normalize(str(raw_value))
        if normalized:
            values.append((normalized, str(raw_value)))
    return values


def _column_can_hold_status(column: dict[str, Any]) -> bool:
    return resolved_semantic_type(column) == "status"


def _column_supports_sample_filter(column: dict[str, Any]) -> bool:
    semantic_type = resolved_semantic_type(column)
    if semantic_type in {"status", "name", "text", "code", "reference"}:
        return True

    column_type = _normalize(str(column.get("type", "")))
    return any(token in column_type for token in ("char", "text", "enum"))


def _detect_runtime_filters(question: str, candidate_tables: dict[str, Any]) -> list[dict[str, Any]]:
    normalized_question = _normalize(question)
    question_terms = set(_tokenize(question))
    requested_limit = _planner()._extract_limit(question)
    filters: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()

    for table_name, table_data in candidate_tables.items():
        for column in table_data.get("columns", []):
            if not _column_supports_sample_filter(column):
                continue
            column_name = str(column.get("name", ""))
            for normalized_value, raw_value in _normalized_sample_values(column):
                value_terms = set(_tokenize(normalized_value))
                if not value_terms:
                    continue
                if requested_limit is not None and normalized_value == str(requested_limit):
                    continue
                if not value_terms <= question_terms:
                    continue

                signature = (table_name, column_name, raw_value)
                if signature in seen:
                    continue
                seen.add(signature)
                filters.append(
                    {
                        "type": "status" if _column_can_hold_status(column) else "value",
                        "table": table_name,
                        "column": column_name,
                        "field_phrase": column_name,
                        "raw_phrase": normalized_value,
                        "operator": "eq",
                        "value": raw_value,
                        "value_phrase": normalized_value,
                        "values": [raw_value],
                        "conjunction": "",
                        "term": normalized_value,
                    }
                )

    return filters


def _extract_preposition_filter_value(question: str) -> str | None:
    import re

    match = re.search(r"\b(?:from|in)\s+([A-Za-z][A-Za-z0-9 ]*)$", str(question or "").strip(), re.IGNORECASE)
    if not match:
        return None
    value = match.group(1).strip()
    value = re.split(r"\b(?:by|with|where|and|or|order|sorted|latest|top)\b", value)[0].strip()
    if not value or re.fullmatch(r"20\d{2}", value, re.IGNORECASE):
        return None
    return value


def _column_supports_generic_text_filter(column: dict[str, Any]) -> bool:
    semantic_type = resolved_semantic_type(column)
    if semantic_type in {"status", "date", "id", "money", "quantity", "percentage"}:
        return False
    column_type = _normalize(str(column.get("type", "")))
    return any(token in column_type for token in ("char", "text", "enum"))


def _column_metadata_tokens(column: dict[str, Any]) -> set[str]:
    tokens = set(_tokenize(str(column.get("name", ""))))
    tokens.update(_tokenize(column_business_description(column)))
    for term in column_business_terms(column):
        tokens.update(_tokenize(str(term)))
    return tokens


def _generic_filter_column_score(column: dict[str, Any], filter_value: str) -> float:
    score = 0.0
    semantic_type = resolved_semantic_type(column)
    if semantic_type in {"name", "text", "code", "reference"}:
        score += 1.0

    normalized_filter = _normalize(filter_value)
    filter_terms = set(_tokenize(filter_value))
    metadata_overlap = len(filter_terms & _column_metadata_tokens(column))
    if metadata_overlap:
        score += metadata_overlap * 0.45

    for normalized_value, _ in _normalized_sample_values(column):
        if normalized_filter == normalized_value:
            score += 4.0
            break
        if normalized_filter in normalized_value or normalized_value in normalized_filter:
            score += 1.5
            break

    return score


def _detect_generic_value_filters(question: str, candidate_tables: dict[str, Any]) -> list[dict[str, Any]]:
    filter_value = _extract_preposition_filter_value(question)
    if not filter_value:
        return []

    ranked: list[tuple[float, dict[str, Any]]] = []
    for table_name, table_data in candidate_tables.items():
        for column in table_data.get("columns", []):
            if not _column_supports_generic_text_filter(column):
                continue
            score = _generic_filter_column_score(column, filter_value)
            if score <= 0:
                continue
            ranked.append(
                (
                    score,
                    {
                        "type": "value",
                        "table": table_name,
                        "column": str(column.get("name", "")),
                        "value": filter_value,
                        "term": filter_value,
                    },
                )
            )

    ranked.sort(key=lambda item: (-item[0], item[1]["table"], item[1]["column"]))
    if not ranked:
        return []
    top_score = ranked[0][0]
    return [filter_data for score, filter_data in ranked if score == top_score][:1]


def _structured_filter_entries(
    filter_candidates: list[dict[str, Any]],
    requested_filters: list[str],
    structured_filters: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    filters: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    clauses = [dict(entry) for entry in (structured_filters or []) if isinstance(entry, dict)]
    if not clauses:
        clauses = [
            {
                "raw_phrase": str(term).strip(),
                "field": "",
                "operator": "unknown",
                "value": str(term).strip(),
                "conjunction": None if index == 0 else "and",
            }
            for index, term in enumerate(requested_filters)
            if str(term).strip()
        ]

    for index, clause in enumerate(clauses):
        ranked_candidates = sorted(
            (
                (_filter_field_match_score(entry, clause), entry)
                for entry in filter_candidates
            ),
            key=lambda item: (
                -item[0],
                str(item[1].get("table") or ""),
                str(item[1].get("column") or ""),
            ),
        )
        if not ranked_candidates or ranked_candidates[0][0] <= 0:
            continue
        entry = ranked_candidates[0][1]
        table_name = str(entry.get("table", "")).strip()
        column_name = str(entry.get("column", "")).strip()
        if not table_name or not column_name:
            continue
        matched_terms = list(entry.get("matched_terms") or [])
        raw_phrase = str(clause.get("raw_phrase") or "").strip()
        value = clause.get("value", clause.get("value_phrase", ""))
        term = str(matched_terms[0] if matched_terms else raw_phrase or value).strip()
        signature = (table_name, column_name, raw_phrase or str(value) or term)
        if signature in seen:
            continue
        seen.add(signature)
        selected = {
            "type": "value",
            "table": table_name,
            "column": column_name,
            "value": value,
            "term": raw_phrase or term,
            "operator": str(clause.get("operator") or "unknown"),
            "field_phrase": str(clause.get("field") or clause.get("field_phrase") or ""),
            "value_phrase": clause.get("value", clause.get("value_phrase", "")),
            "conjunction": clause.get("conjunction"),
            "raw_phrase": raw_phrase,
            "evidence_score": float(entry.get("score") or 0.0),
        }
        if clause.get("filter_kind") == "date_interval":
            selected.update(
                {
                    "filter_kind": "date_interval",
                    "interval_granularity": clause.get("interval_granularity"),
                    "date_column_phrase": clause.get("date_column_phrase"),
                }
            )
        filters.append(selected)
    return filters[:4]


def _filter_field_match_score(entry: dict[str, Any], clause: dict[str, Any]) -> float:
    field_phrase = str(clause.get("field_phrase") or clause.get("field") or "").strip()
    if not field_phrase:
        return float(entry.get("score") or 0.0)
    normalized_field = _normalize(field_phrase)
    field_tokens = {_singularize_token(token) for token in _tokenize(field_phrase)}
    column_text = str(entry.get("column") or "").replace("_", " ")
    normalized_column = _normalize(column_text)
    column_tokens = {_singularize_token(token) for token in _tokenize(column_text)}
    qualified_tokens = {
        _singularize_token(token)
        for token in _tokenize(f"{entry.get('table') or ''} {column_text}")
    }
    lexical_score = 0.0
    if normalized_column == normalized_field:
        lexical_score = 1.0
    elif field_tokens and field_tokens <= column_tokens:
        lexical_score = 0.9
    elif field_tokens and field_tokens <= qualified_tokens:
        lexical_score = 0.88
    elif field_tokens:
        lexical_score = (len(field_tokens & qualified_tokens) / len(field_tokens)) * 0.7

    for text in [str(value) for value in (entry.get("matched_terms") or [])]:
        normalized_text = _normalize(text)
        text_tokens = set(_tokenize(text))
        if not normalized_text or not text_tokens:
            continue
        if normalized_text == normalized_field:
            lexical_score = max(lexical_score, 0.82)
        elif field_tokens and field_tokens <= text_tokens:
            lexical_score = max(lexical_score, 0.75)
        elif field_tokens:
            overlap = len(field_tokens & text_tokens) / len(field_tokens)
            lexical_score = max(lexical_score, overlap * 0.6)
    evidence_score = min(float(entry.get("score") or 0.0), 1.0)
    return round((lexical_score * 0.9) + (evidence_score * 0.1), 4) if lexical_score else 0.0


def _is_date_schema_column(column: dict[str, Any]) -> bool:
    column_type = str(column.get("type") or column.get("data_type") or "").strip().lower()
    return bool(
        column.get("is_date")
        or resolved_semantic_type(column) == "date"
        or any(marker in column_type for marker in ("date", "time", "timestamp"))
    )


def _date_filter_candidates(
    knowledge_base: dict[str, Any],
    allowed_tables: set[str],
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for table_name in sorted(allowed_tables):
        for column in knowledge_base.get(table_name, {}).get("columns", []) or []:
            column_name = str(column.get("name") or "").strip()
            if not column_name or not _is_date_schema_column(column):
                continue
            candidates.append(
                {
                    "table": table_name,
                    "column": column_name,
                    "semantic_type": "date",
                    "is_date": True,
                    "is_dimension": True,
                    "score": 1.0,
                    "matched_terms": [
                        column_name.replace("_", " "),
                        f"{table_name} {column_name}".replace("_", " "),
                    ],
                    "source": "schema_date_evidence",
                }
            )
    return candidates


def _resolve_interval_clause(
    clause: dict[str, Any],
    *,
    knowledge_base: dict[str, Any],
    allowed_tables: set[str],
    preferred_table: str | None = None,
    preferred_owner_phrases: list[str] | None = None,
    allow_preferred_owner_date: bool = False,
) -> tuple[dict[str, Any] | None, str]:
    if clause.get("filter_kind") != "date_interval":
        return None, "not_interval"
    candidates = _date_filter_candidates(knowledge_base, allowed_tables)
    field_phrase = str(
        clause.get("date_column_phrase") or clause.get("field_phrase") or clause.get("field") or ""
    ).strip()
    if field_phrase:
        ranked = sorted(
            (
                (_filter_field_match_score(candidate, {**clause, "field_phrase": field_phrase}), candidate)
                for candidate in candidates
            ),
            key=lambda item: (-item[0], item[1]["table"], item[1]["column"]),
        )
        ranked = [item for item in ranked if item[0] > 0]
        if not ranked:
            return None, "missing"
        if len(ranked) > 1 and abs(ranked[0][0] - ranked[1][0]) < 0.08:
            return None, "ambiguous"
        candidate = dict(ranked[0][1])
    else:
        if len(candidates) != 1 and allow_preferred_owner_date:
            preferred = _resolve_preferred_owner_date_candidate(
                candidates,
                preferred_table=preferred_table,
                preferred_owner_phrases=preferred_owner_phrases or [],
            )
            if preferred is not None:
                candidate = dict(preferred)
            else:
                return None, "ambiguous" if candidates else "missing"
        elif len(candidates) != 1:
            return None, "ambiguous" if candidates else "missing"
        else:
            candidate = dict(candidates[0])

    candidate.update(
        {
            "type": "value",
            "value": clause.get("value"),
            "term": str(clause.get("raw_phrase") or ""),
            "operator": str(clause.get("operator") or ""),
            "field_phrase": field_phrase,
            "value_phrase": clause.get("value_phrase"),
            "values": list(clause.get("values") or []),
            "conjunction": clause.get("conjunction"),
            "raw_phrase": str(clause.get("raw_phrase") or ""),
            "filter_kind": "date_interval",
            "interval_granularity": clause.get("interval_granularity"),
            "date_column_phrase": field_phrase,
            "evidence_score": float(candidate.get("score") or 1.0),
        }
    )
    return candidate, "resolved"


def _resolve_preferred_owner_date_candidate(
    candidates: list[dict[str, Any]],
    *,
    preferred_table: str | None,
    preferred_owner_phrases: list[str],
) -> dict[str, Any] | None:
    if not preferred_table:
        return None
    owner_tokens: set[str] = set()
    for phrase in preferred_owner_phrases:
        owner_tokens.update(
            _singularize_token(token)
            for token in _tokenize(phrase)
            if token not in {"total", "sum", "average", "avg", "count", "amount", "value", "cost", "price"}
        )
    owner_tokens.update(
        _singularize_token(token)
        for token in _tokenize(preferred_table)
        if token not in {"table", "data"}
    )
    if not owner_tokens:
        return None
    ranked: list[tuple[int, str, dict[str, Any]]] = []
    for candidate in candidates:
        if str(candidate.get("table") or "") != preferred_table:
            continue
        column_tokens = {
            _singularize_token(token)
            for token in _tokenize(str(candidate.get("column") or ""))
        }
        overlap = len(owner_tokens & column_tokens)
        if overlap:
            ranked.append((overlap, str(candidate.get("column") or ""), candidate))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    if not ranked:
        return None
    if len(ranked) > 1 and ranked[0][0] == ranked[1][0]:
        return None
    return dict(ranked[0][2])


def _resolve_interval_filters_for_scope(
    selected_filters: list[dict[str, Any]],
    structured_filters: list[dict[str, Any]],
    *,
    knowledge_base: dict[str, Any],
    allowed_tables: set[str],
    preferred_table: str | None = None,
    preferred_owner_phrases: list[str] | None = None,
    allow_preferred_owner_date: bool = False,
) -> tuple[list[dict[str, Any]], str]:
    interval_clauses = [
        dict(entry) for entry in structured_filters
        if isinstance(entry, dict) and entry.get("filter_kind") == "date_interval"
    ]
    if not interval_clauses:
        return selected_filters, ""
    resolved = [
        dict(entry) for entry in selected_filters
        if entry.get("filter_kind") != "date_interval"
    ]
    for clause in interval_clauses:
        selected, status = _resolve_interval_clause(
            clause,
            knowledge_base=knowledge_base,
            allowed_tables=allowed_tables,
            preferred_table=preferred_table,
            preferred_owner_phrases=preferred_owner_phrases or [],
            allow_preferred_owner_date=allow_preferred_owner_date,
        )
        if status != "resolved" or selected is None:
            return resolved, f"date interval column evidence is {status}"
        resolved.append(selected)
    return resolved, ""


def _filter_path_tables(
    *,
    selected_tables: list[dict[str, Any]],
    selected_join_path: dict[str, Any] | None = None,
    join_paths: list[dict[str, Any]] | None = None,
) -> set[str]:
    tables = {
        str(entry.get("table") or "").strip()
        for entry in selected_tables
        if isinstance(entry, dict) and str(entry.get("table") or "").strip()
    }
    paths = []
    if isinstance(selected_join_path, dict):
        paths.append(selected_join_path)
    paths.extend(path for path in (join_paths or []) if isinstance(path, dict))
    for path in paths:
        base = str(path.get("base_table") or "").strip()
        if base:
            tables.add(base)
        tables.update(str(table).strip() for table in (path.get("joined_tables") or []) if str(table).strip())
        for edge in path.get("edges") or []:
            if not isinstance(edge, dict):
                continue
            for key in ("from_table", "to_table"):
                value = str(edge.get(key) or "").strip()
                if value:
                    tables.add(value)
    return tables


def _schema_has_column(knowledge_base: dict[str, Any], table_name: str, column_name: str) -> bool:
    if table_name not in knowledge_base:
        return True
    return any(
        isinstance(column, dict) and str(column.get("name") or "").strip() == column_name
        for column in (knowledge_base.get(table_name, {}).get("columns", []) or [])
    )


def _filter_decision(
    entry: dict[str, Any],
    *,
    clause_scope: str,
    knowledge_base: dict[str, Any],
    path_tables: set[str],
) -> dict[str, Any]:
    table_name = str(entry.get("table") or "").strip()
    column_name = str(entry.get("column") or entry.get("field") or "").strip()
    reason = ""
    if not table_name or not column_name:
        reason = "filter_column_missing"
    elif table_name not in path_tables:
        reason = "filter_table_outside_selected_path"
    elif not _schema_has_column(knowledge_base, table_name, column_name):
        reason = "filter_column_not_in_schema"
    source = str(entry.get("source") or entry.get("value_source") or entry.get("term") or "").strip()
    score = float(entry.get("score") or entry.get("evidence_score") or 1.0)
    return {
        "status": "resolved" if not reason else "unsupported",
        "clause_scope": clause_scope,
        "table": table_name,
        "column": column_name,
        "operator": str(entry.get("operator") or "").strip(),
        "value": entry.get("value"),
        "values": list(entry.get("values") or []),
        "normalized_value": _normalize(str(entry.get("value_phrase") or entry.get("value") or "")),
        "value_source": source,
        "owner_entity": table_name,
        "evidence_tier": str(entry.get("source") or "selected_filter"),
        "score": score,
        "score_reasons": ["selected_filter", "path_compatible"] if not reason else [],
        "penalties": [],
        "selected_path_compatible": bool(table_name and table_name in path_tables),
        "ambiguity_group_key": "" if not reason else f"{clause_scope}:{table_name}.{column_name}",
        "rejected_candidates": [],
        "reason_code": reason,
    }


def build_filter_decision_contract(
    *,
    selected_filters: list[dict[str, Any]],
    selected_having: list[dict[str, Any]],
    knowledge_base: dict[str, Any],
    selected_tables: list[dict[str, Any]],
    selected_join_path: dict[str, Any] | None = None,
    join_paths: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    path_tables = _filter_path_tables(
        selected_tables=selected_tables,
        selected_join_path=selected_join_path,
        join_paths=join_paths,
    )
    where_decisions = [
        _filter_decision(
            dict(entry),
            clause_scope="where",
            knowledge_base=knowledge_base,
            path_tables=path_tables,
        )
        for entry in selected_filters
        if isinstance(entry, dict)
    ]
    having_decisions = [
        _filter_decision(
            dict(entry),
            clause_scope="having",
            knowledge_base=knowledge_base,
            path_tables=path_tables,
        )
        for entry in selected_having
        if isinstance(entry, dict)
    ]
    unresolved = [
        dict(entry)
        for entry in [*where_decisions, *having_decisions]
        if entry.get("status") != "resolved"
    ]
    conjunctions = [
        str(entry.get("conjunction") or "").strip().lower()
        for entry in selected_filters
        if isinstance(entry, dict) and str(entry.get("conjunction") or "").strip()
    ]
    if len(set(value for value in conjunctions if value in {"and", "or"})) > 1:
        unresolved.append(
            {
                "status": "unsupported",
                "clause_scope": "where",
                "reason_code": "mixed_filter_conjunctions_not_supported",
                "ambiguity_group_key": "filter_conjunction",
                "rejected_candidates": [],
            }
        )
    return {
        "overall_status": "resolved" if not unresolved else "unsupported",
        "resolved_where_filters": [entry for entry in where_decisions if entry.get("status") == "resolved"],
        "resolved_having_filters": [entry for entry in having_decisions if entry.get("status") == "resolved"],
        "rejected_filters": [],
        "unresolved_filters": unresolved,
        "conjunction_structure": conjunctions,
    }


def _has_structured_filter_ambiguity(
    filter_candidates: list[dict[str, Any]],
    structured_filters: list[dict[str, Any]],
) -> bool:
    for clause in structured_filters:
        ranked: list[tuple[float, str, str]] = []
        seen: set[tuple[str, str]] = set()
        for candidate in filter_candidates:
            table_name = str(candidate.get("table") or "").strip()
            column_name = str(candidate.get("column") or "").strip()
            signature = (table_name, column_name)
            if not table_name or not column_name or signature in seen:
                continue
            seen.add(signature)
            score = _filter_field_match_score(candidate, clause)
            if score > 0:
                ranked.append((score, table_name, column_name))
        ranked.sort(key=lambda item: (-item[0], item[1], item[2]))
        if len(ranked) >= 2 and abs(ranked[0][0] - ranked[1][0]) < 0.08:
            return True
    return False


def _joined_aggregate_filter_contract(
    intent: dict[str, Any],
    filter_candidates: list[dict[str, Any]],
    allowed_tables: set[str],
    knowledge_base: dict[str, Any],
    base_table: str | None = None,
    preferred_date_phrases: list[str] | None = None,
    allow_preferred_owner_date: bool = False,
) -> tuple[list[dict[str, Any]], str]:
    structured_filters = [
        dict(entry)
        for entry in (intent.get("structured_filters") or [])
        if isinstance(entry, dict)
    ]
    if not structured_filters:
        return [], ""

    selected: list[dict[str, Any]] = []
    for clause in structured_filters:
        if clause.get("filter_kind") == "date_interval":
            selected_filter, status = _resolve_interval_clause(
                clause,
                knowledge_base=knowledge_base,
                allowed_tables=allowed_tables,
                preferred_table=base_table,
                preferred_owner_phrases=preferred_date_phrases or [],
                allow_preferred_owner_date=allow_preferred_owner_date,
            )
            if status != "resolved" or selected_filter is None:
                return [], f"joined date interval evidence is {status}"
            selected.append(selected_filter)
            continue
        field_phrase = str(clause.get("field_phrase") or clause.get("field") or "").strip()
        table_name = str(clause.get("table") or "").strip()
        column_name = str(clause.get("column") or "").strip()
        if table_name and column_name:
            table_data = knowledge_base.get(table_name) if isinstance(knowledge_base, dict) else {}
            known_columns = {
                str(column.get("name") or "").strip()
                for column in (table_data or {}).get("columns", []) or []
                if isinstance(column, dict)
            }
            if table_name in allowed_tables and column_name in known_columns:
                selected_clause = dict(clause)
                table_columns = (table_data or {}).get("columns", []) or []
                matched_column = next(
                    (
                        column for column in table_columns
                        if isinstance(column, dict)
                        and str(column.get("name") or "").strip() == column_name
                    ),
                    None,
                )
                if matched_column is not None:
                    value_phrase = str(
                        clause.get("value_phrase")
                        or clause.get("value")
                        or clause.get("raw_phrase")
                        or ""
                    ).strip()
                    for sample in column_sample_values(matched_column):
                        if _sample_value_matches(value_phrase, sample):
                            selected_clause["value"] = sample
                            selected_clause["values"] = [sample]
                            break
                selected.append(selected_clause)
                continue
            return [], "joined WHERE field evidence is missing"
        resolved, status = _planner()._resolve_role_candidate(
            field_phrase,
            filter_candidates,
            allowed_tables=allowed_tables,
            role="filter",
        )
        if status != "resolved":
            value_phrase = str(clause.get("value_phrase") or clause.get("value") or "").strip()
            exact_matches = _exact_filter_column_matches(
                field_phrase,
                knowledge_base,
                allowed_tables,
                value_phrase=value_phrase,
            )
            if len(exact_matches) == 1:
                resolved = exact_matches
                status = "resolved"
        if status != "resolved" or len(resolved) != 1:
            return [], f"joined WHERE field evidence is {status}"
        candidate = dict(resolved[0])
        candidate.update(
            {
                "field_phrase": field_phrase,
                "raw_phrase": str(clause.get("raw_phrase") or ""),
                "operator": str(clause.get("operator") or ""),
                "value": clause.get("value"),
                "value_phrase": clause.get("value_phrase"),
                "values": list(clause.get("values") or []),
                "conjunction": clause.get("conjunction"),
            }
        )
        selected.append(candidate)
    return selected, ""


def _exact_filter_column_matches(
    field_phrase: str,
    knowledge_base: dict[str, Any],
    allowed_tables: set[str],
    *,
    value_phrase: str = "",
) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    normalized_field = _humanize(field_phrase)
    if not normalized_field:
        return matches
    for table_name in sorted(allowed_tables):
        for column in knowledge_base.get(table_name, {}).get("columns", []) or []:
            column_name = str(column.get("name") or "").strip()
            if not column_name or _humanize(column_name) != normalized_field:
                continue
            matches.append(
                {
                    "table": table_name,
                    "column": column_name,
                    "semantic_type": resolved_semantic_type(column),
                    "core_semantic_type": resolved_semantic_type(column),
                    "data_type": column.get("type") or column.get("data_type") or "",
                    "type": column.get("type") or column.get("data_type") or "",
                    "is_measure": bool(column.get("is_measure")),
                    "is_dimension": bool(column.get("is_dimension")),
                    "is_date": bool(column.get("is_date")),
                    "score": 1.0,
                    "matched_terms": [field_phrase],
                    "source": "schema_exact_filter",
                }
            )
    value_phrase = str(value_phrase or "").strip()
    if value_phrase and len(matches) > 1:
        sample_matches: list[dict[str, Any]] = []
        for match in matches:
            table_name = str(match.get("table") or "")
            column_name = str(match.get("column") or "")
            column = next(
                (
                    entry for entry in knowledge_base.get(table_name, {}).get("columns", []) or []
                    if str(entry.get("name") or "").strip() == column_name
                ),
                None,
            )
            if column and any(_sample_value_matches(value_phrase, sample) for sample in column_sample_values(column)):
                sample_matches.append(match)
        if sample_matches:
            return sample_matches
    return matches


def _build_sample_value_filter(
    *,
    value_phrase: str,
    knowledge_base: dict[str, Any],
    allowed_tables: set[str],
    owner_table: str | None = None,
    source: str,
) -> tuple[dict[str, Any] | None, str]:
    value_phrase = str(value_phrase or "").strip()
    if not value_phrase:
        return None, "missing"
    matches: list[dict[str, Any]] = []
    tables = {owner_table} if owner_table else set(allowed_tables)
    for table_name in tables:
        if table_name not in allowed_tables:
            continue
        for column in knowledge_base.get(table_name, {}).get("columns", []) or []:
            column_name = str(column.get("name") or "").strip()
            if not column_name:
                continue
            matched_sample = None
            for sample in column_sample_values(column):
                if _sample_value_matches(value_phrase, sample):
                    matched_sample = sample
                    break
            if matched_sample is None:
                continue
            matches.append(
                {
                    "table": table_name,
                    "column": column_name,
                    "field_phrase": column_name,
                    "raw_phrase": value_phrase,
                    "operator": "eq",
                    "value": matched_sample,
                    "value_phrase": value_phrase,
                    "values": [matched_sample],
                    "conjunction": "",
                    "source": source,
                }
            )
    if len(matches) == 1:
        return matches[0], "resolved"
    if len(matches) > 1:
        return None, "ambiguous"
    return None, "missing"


def _column_filter_role(column: dict[str, Any]) -> str:
    semantic_type = resolved_semantic_type(column)
    column_tokens = {_singularize_token(token) for token in _tokenize(str(column.get("name") or ""))}
    if semantic_type == "status" or column_tokens & _STATUS_FILTER_TOKENS:
        return "status"
    if column_tokens & _LOCATION_FILTER_TOKENS:
        return "location"
    if column_tokens & _CATEGORY_FILTER_TOKENS:
        return "category"
    return ""


def _build_single_role_value_filter(
    *,
    value_phrase: str,
    knowledge_base: dict[str, Any],
    table_name: str,
    role: str,
    source: str,
) -> tuple[dict[str, Any] | None, str]:
    value_phrase = str(value_phrase or "").strip()
    if not value_phrase:
        return None, "missing"
    matches: list[dict[str, Any]] = []
    table_data = knowledge_base.get(table_name) or {}
    for column in table_data.get("columns", []) or []:
        column_name = str(column.get("name") or "").strip()
        if not column_name or _column_filter_role(column) != role:
            continue
        matches.append(
            {
                "table": table_name,
                "column": column_name,
                "field_phrase": column_name,
                "raw_phrase": value_phrase,
                "operator": "eq",
                "value": value_phrase,
                "value_phrase": value_phrase,
                "values": [value_phrase],
                "conjunction": "",
                "source": source,
            }
        )
    if len(matches) == 1:
        return matches[0], "resolved"
    if len(matches) > 1:
        return None, "ambiguous"
    return None, "missing"


def _source_scope_as_filter(
    source_phrase: str,
    knowledge_base: dict[str, Any],
    allowed_tables: set[str],
) -> tuple[dict[str, Any] | None, str]:
    phrase_tokens = {_singularize_token(token) for token in _tokenize(source_phrase)}
    if len(allowed_tables) == 1:
        owner_table = next(iter(allowed_tables))
        owner_tokens = {_singularize_token(token) for token in _tokenize(owner_table)}
        direct_filter, direct_status = _build_sample_value_filter(
            value_phrase=source_phrase,
            knowledge_base=knowledge_base,
            allowed_tables=allowed_tables,
            owner_table=owner_table,
            source="source_scope_value_filter",
        )
        if direct_status == "resolved":
            return direct_filter, direct_status
        if not owner_tokens or not owner_tokens <= phrase_tokens:
            location_filter, location_status = _build_single_role_value_filter(
                value_phrase=source_phrase,
                knowledge_base=knowledge_base,
                table_name=owner_table,
                role="location",
                source="source_scope_value_filter",
            )
            if location_status == "resolved":
                return location_filter, location_status
    owner_matches: list[tuple[float, str, set[str]]] = []
    for table_name in allowed_tables:
        table_tokens = {_singularize_token(token) for token in _tokenize(table_name)}
        if not table_tokens or not table_tokens <= phrase_tokens:
            continue
        score = _planner()._table_phrase_score(source_phrase, table_name)
        if score > 0:
            owner_matches.append((score, table_name, table_tokens))
    owner_matches.sort(key=lambda item: (-item[0], item[1]))
    if not owner_matches:
        location_matches: list[dict[str, Any]] = []
        for table_name in allowed_tables:
            location_filter, location_status = _build_single_role_value_filter(
                value_phrase=source_phrase,
                knowledge_base=knowledge_base,
                table_name=table_name,
                role="location",
                source="source_scope_value_filter",
            )
            if location_status == "resolved" and location_filter is not None:
                location_matches.append(location_filter)
            elif location_status == "ambiguous":
                return None, "ambiguous"
        if len(location_matches) == 1:
            return location_matches[0], "resolved"
        if len(location_matches) > 1:
            return None, "ambiguous"
        return None, "missing"
    if len(owner_matches) > 1 and abs(owner_matches[0][0] - owner_matches[1][0]) < 0.08:
        return None, "ambiguous"
    _, owner_table, owner_tokens = owner_matches[0]
    value_tokens = [token for token in _tokenize(source_phrase) if _singularize_token(token) not in owner_tokens]
    value_phrase = " ".join(value_tokens).strip()
    sample_filter, sample_status = _build_sample_value_filter(
        value_phrase=value_phrase,
        knowledge_base=knowledge_base,
        allowed_tables=allowed_tables,
        owner_table=owner_table,
        source="source_scope_value_filter",
    )
    if sample_status == "resolved":
        return sample_filter, sample_status
    value_token_set = {_singularize_token(token) for token in _tokenize(value_phrase)}
    if value_token_set & _STATUS_VALUE_TOKENS:
        for token in _tokenize(value_phrase):
            if _singularize_token(token) not in _STATUS_VALUE_TOKENS:
                continue
            status_filter, status = _build_single_role_value_filter(
                value_phrase=token,
                knowledge_base=knowledge_base,
                table_name=owner_table,
                role="status",
                source="source_scope_value_filter",
            )
            if status == "resolved":
                return status_filter, status
        status_filter, status = _build_single_role_value_filter(
            value_phrase=value_phrase,
            knowledge_base=knowledge_base,
            table_name=owner_table,
            role="status",
            source="source_scope_value_filter",
        )
        if status == "resolved":
            return status_filter, status
    category_filter, category_status = _build_single_role_value_filter(
        value_phrase=value_phrase,
        knowledge_base=knowledge_base,
        table_name=owner_table,
        role="category",
        source="source_scope_value_filter",
    )
    if category_status == "resolved":
        return category_filter, category_status
    status_filter, status = _build_single_role_value_filter(
        value_phrase=value_phrase,
        knowledge_base=knowledge_base,
        table_name=owner_table,
        role="status",
        source="source_scope_value_filter",
    )
    if status == "resolved":
        return status_filter, status
    return sample_filter, sample_status


def _source_scope_as_filters(
    source_phrase: str,
    knowledge_base: dict[str, Any],
    allowed_tables: set[str],
) -> tuple[list[dict[str, Any]], str]:
    parts = _source_scope_filter_parts(source_phrase, allowed_tables)
    if not parts:
        single, status = _source_scope_as_filter(source_phrase, knowledge_base, allowed_tables)
        return ([single] if single is not None else []), status

    resolved: list[dict[str, Any]] = []
    for phrase, conjunction in parts:
        selected, status = _source_scope_as_filter(phrase, knowledge_base, allowed_tables)
        if status != "resolved" or selected is None:
            return [], status
        selected = dict(selected)
        selected["conjunction"] = "" if not resolved else conjunction
        resolved.append(selected)
    return resolved, "resolved"


def _source_scope_filter_parts(source_phrase: str, allowed_tables: set[str]) -> list[tuple[str, str]]:
    phrase = _normalize(str(source_phrase or ""))
    if not phrase:
        return []

    def has_non_owner_tokens(value: str) -> bool:
        tokens = {_singularize_token(token) for token in _tokenize(value)}
        if not tokens:
            return False
        for table_name in allowed_tables:
            table_tokens = {_singularize_token(token) for token in _tokenize(table_name)}
            if table_tokens and table_tokens <= tokens and not (tokens - table_tokens):
                return False
        return True

    parts: list[tuple[str, str]] = []
    in_match = re.search(r"\s+in\s+", phrase, flags=re.IGNORECASE)
    if in_match:
        prefix = phrase[: in_match.start()].strip()
        tail = phrase[in_match.end() :].strip()
        if has_non_owner_tokens(prefix):
            parts.append((prefix, "and"))
        tail_parts = [
            _normalize(part)
            for part in re.split(r"\s+(or|and)\s+", tail, flags=re.IGNORECASE)
        ]
        conjunction = "and"
        for part in tail_parts:
            if part in {"and", "or"}:
                conjunction = part
                continue
            if part:
                parts.append((part, conjunction))
        return parts

    split = [
        _normalize(part)
        for part in re.split(r"\s+(and|or)\s+", phrase, flags=re.IGNORECASE)
    ]
    conjunction = "and"
    for part in split:
        if part in {"and", "or"}:
            conjunction = part
            continue
        if part and has_non_owner_tokens(part):
            parts.append((part, conjunction))
    return parts if len(parts) > 1 else []


def _apply_implicit_sample_filter_contract(
    context: dict[str, Any],
    knowledge_base: dict[str, Any],
) -> dict[str, Any]:
    intent = context.get("intent") if isinstance(context.get("intent"), dict) else {}
    query_shape = str(context.get("query_shape") or "")
    if query_shape not in {"single_table_list", "filtered_query", "joined_lookup"}:
        return context
    if query_shape == "joined_lookup":
        lookup_request = intent.get("join_lookup_request") if isinstance(intent.get("join_lookup_request"), dict) else {}
        if lookup_request.get("requested") or intent.get("requested_output_fields"):
            return context
    structured_filters = [
        dict(entry)
        for entry in (intent.get("structured_filters") or [])
        if isinstance(entry, dict)
    ]
    selected_filters = [
        dict(entry)
        for entry in (context.get("selected_filters") or [])
        if isinstance(entry, dict)
    ]
    has_only_date_intervals = bool(structured_filters or selected_filters) and all(
        entry.get("filter_kind") == "date_interval"
        for entry in [*structured_filters, *selected_filters]
    )
    if query_shape == "single_table_list":
        if (structured_filters or selected_filters) and not has_only_date_intervals:
            return context
        if intent.get("requested_filters") and not has_only_date_intervals:
            return context
    elif not (
        (structured_filters or selected_filters)
        and all(entry.get("filter_kind") == "date_interval" for entry in [*structured_filters, *selected_filters])
    ):
        return context
    if str(intent.get("intent_type") or "").strip().lower() not in {"list", "filter"}:
        return context
    selected_table_names = [
        str(value).strip()
        for value in (context.get("selected_table_names") or [])
        if str(value).strip()
    ]
    phrase = str(
        next(iter(intent.get("source_scope") or []), "")
        or intent.get("source_scope_phrase")
        or intent.get("target_entity_phrase")
        or ""
    ).strip()
    if not phrase:
        return context
    if len(selected_table_names) != 1:
        resolved_table, resolved_status = _planner()._resolve_join_table(phrase, knowledge_base, [])
        if resolved_status != "resolved" or not resolved_table:
            return context
        selected_table_names = [resolved_table]
    implicit_filters, status = _source_scope_as_filters(
        phrase,
        knowledge_base,
        {selected_table_names[0]},
    )
    if status != "resolved" or not implicit_filters:
        return context

    selected_table = selected_table_names[0]
    narrowed_selected_tables = [
        dict(entry)
        for entry in (context.get("selected_tables") or [])
        if isinstance(entry, dict) and str(entry.get("table") or "").strip() == selected_table
    ] or [{"table": selected_table, "confidence": 1.0, "source": "implicit_single_table_filter"}]
    narrowed_selected_columns = [
        dict(entry)
        for entry in (context.get("selected_columns") or [])
        if isinstance(entry, dict) and str(entry.get("table") or "").strip() == selected_table
    ]
    planned = dict(context)
    planned_intent = dict(intent)
    planned_intent["structured_filters"] = [*structured_filters, *implicit_filters]
    requested_filters = [
        str(value).strip()
        for value in (intent.get("requested_filters") or [])
        if str(value).strip()
    ]
    for implicit_filter in implicit_filters:
        implicit_requested_filter = str(
            implicit_filter.get("raw_phrase") or implicit_filter.get("value_phrase") or ""
        ).strip()
        if implicit_requested_filter and implicit_requested_filter not in requested_filters:
            requested_filters.append(implicit_requested_filter)
    planned_intent["requested_filters"] = requested_filters
    planned_filters = [*selected_filters, *implicit_filters]
    if has_only_date_intervals:
        planned_filters, interval_reason = _resolve_interval_filters_for_scope(
            planned_filters,
            structured_filters,
            knowledge_base=knowledge_base,
            allowed_tables={selected_table_names[0]},
            preferred_table=selected_table_names[0],
            preferred_owner_phrases=[phrase, selected_table_names[0]],
            allow_preferred_owner_date=True,
        )
        if interval_reason:
            return context
    planned.update(
        {
            "intent": planned_intent,
            "query_shape": "filtered_query",
            "limit": context.get("limit") or 50,
            "selected_tables": narrowed_selected_tables,
            "selected_table_names": [selected_table],
            "selected_filters": planned_filters,
            "filter_candidates": _planner()._merge_candidate_columns(
                implicit_filters,
                [entry for entry in (context.get("filter_candidates") or []) if isinstance(entry, dict)],
            ),
            "selected_columns": _planner()._merge_candidate_columns(
                narrowed_selected_columns,
                implicit_filters,
            ),
            "required_evidence": ["selected_table", "filter_candidate"],
            "missing_evidence": [
                entry
                for entry in (context.get("missing_evidence") or [])
                if entry != "missing_filter_column"
            ],
            "missing_evidence_flags": {
                **dict(context.get("missing_evidence_flags") or {}),
                "missing_filter_column": False,
            },
            "route_recommendation": "deterministic_sql_required",
            "route": "deterministic_sql_required",
            "route_used": "deterministic_sql_required",
            "route_reason": "clear deterministic table and filter evidence",
            "planner_reason": "clear deterministic table and filter evidence",
            "can_plan": True,
        }
    )
    planned["plan"] = {**dict(planned.get("plan") or {}), "filters": planned_filters, "limit": planned.get("limit") or 50}
    clause_plan = dict(planned.get("clause_plan") or {})
    clause_plan["clause_shape"] = "where_only"
    clause_plan["limit"] = planned.get("limit") or 50
    requires = dict(clause_plan.get("requires") or {})
    requires["where"] = True
    requires["limit"] = True
    clause_plan["requires"] = requires
    decision_path = []
    for entry in clause_plan.get("decision_path") or []:
        node = dict(entry)
        if node.get("node") == "query_shape":
            node["status"] = "resolved"
            node["reason"] = "resolved clause shape 'where_only'"
        elif node.get("node") == "where":
            node["status"] = "resolved"
            node["reason"] = "row-level sample value filter resolved from KB profile evidence"
        elif node.get("node") == "clause_shape":
            node["status"] = "resolved"
            node["reason"] = "final clause shape 'where_only' is complete"
        elif node.get("node") == "route":
            node["status"] = "resolved"
            node["reason"] = "deterministic SQL generation is supported for this clause shape"
        decision_path.append(node)
    clause_plan["decision_path"] = decision_path
    planned["clause_plan"] = clause_plan
    return planned
