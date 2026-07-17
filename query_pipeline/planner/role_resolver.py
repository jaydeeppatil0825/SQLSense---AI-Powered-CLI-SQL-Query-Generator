"""Role/table/metric/dimension resolution helpers for the deterministic planner."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from kb_pipeline.relationship_graph import (
    build_relationship_graph,
    find_safe_direct_join_relationships,
)
from kb_pipeline.schema_facts import (
    column_business_description,
    column_business_terms,
    resolved_semantic_type,
)
from query_pipeline.planner.schema_utils import (
    _candidate_semantic_type,
    _is_numeric_sql_type,
    _is_textual_sql_type,
)


def _planner():
    from query_pipeline import query_planner as _qp

    return _qp


def _normalize(text: str) -> str:
    return _planner()._normalize(text)


def _humanize(text: str) -> str:
    return _planner()._humanize(text)


def _tokenize(text: str) -> list[str]:
    return _planner()._tokenize(text)


def _singularize_token(token: str) -> str:
    return _planner()._singularize_token(token)


def _field_tokens(text: str) -> set[str]:
    tokens = {_singularize_token(token) for token in _tokenize(text)}
    if "number" in tokens:
        tokens.add("no")
    if "no" in tokens:
        tokens.add("number")
    return tokens


def _content_terms(question: str) -> list[str]:
    return _planner()._content_terms(question)


def _aggregate_function_hint(question: str) -> str | None:
    return _planner()._aggregate_function_hint(question)


def _safe_float(value: Any, default: float = 0.0) -> float:
    return _planner()._safe_float(value, default)


def _should_preserve_simple_list_metric(plan: dict[str, Any]) -> bool:
    if str(plan.get("intent") or "") != "list":
        return True
    if plan.get("dimension") or plan.get("grouping"):
        return True
    return False


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
    top_score = float(top_entry.get("score", top_entry.get("confidence", 0.0)) or 0.0)
    second_score = (
        float(ordered[1].get("score", ordered[1].get("confidence", 0.0)) or 0.0)
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


def _question_text_for_table(table_name: str, table_data: dict) -> str:
    pieces = [
        table_name,
        _humanize(table_name),
        str(table_data.get("business_purpose", "")),
        str(table_data.get("business_description", "")),
    ]
    for column in table_data.get("columns", []):
        pieces.append(str(column.get("name", "")))
        pieces.append(column_business_description(column))
        pieces.extend(str(term) for term in column_business_terms(column))
    return " ".join(piece for piece in pieces if piece)


def _table_score(
    plan: dict[str, Any],
    table_name: str,
    table_data: dict[str, Any],
    glossary_matches: list[tuple[str, dict[str, Any]]],
    vector_results: dict[str, Any] | None,
) -> tuple[float, list[str]]:
    score = 0.0
    reasons: list[str] = []
    question_terms = set(plan.get("question_terms", []))
    table_text = _question_text_for_table(table_name, table_data).lower()
    table_tokens = set(_tokenize(table_text))

    overlap = len(question_terms & table_tokens)
    if overlap:
        score += overlap * 0.45
        reasons.append(f"matched {overlap} question term(s) in table metadata")

    if _table_name_matches_question(plan.get("question", ""), table_name):
        score += 1.6
        reasons.append("table name matches the question directly")

    for semantic_hint in plan.get("semantic_hints", set()):
        if any(resolved_semantic_type(column) == semantic_hint for column in table_data.get("columns", [])):
            score += 0.8
            reasons.append(f"contains {semantic_hint} column(s)")

    dimension = str(plan.get("dimension") or "").strip()
    if dimension:
        dimension_tokens = set(_tokenize(dimension))
        if dimension_tokens & table_tokens:
            score += 0.9
            reasons.append(f"dimension '{dimension}' matched table metadata")

    if plan.get("date_range") and any(resolved_semantic_type(column) == "date" for column in table_data.get("columns", [])):
        score += 0.6
        reasons.append("date filter needs a date column")

    if any(filter_data.get("type") == "status" for filter_data in plan.get("filters", [])):
        if any(resolved_semantic_type(column) == "status" for column in table_data.get("columns", [])):
            score += 0.6
            reasons.append("status filter needs a status column")

    for term, term_data in glossary_matches:
        direct_term_match = _planner()._glossary_alias_hits_question(plan.get("question", ""), term, term_data)
        mapped_tables = _planner()._glossary_mapped_tables(term_data)
        mapped_table_count = len(mapped_tables)
        glossary_boost = 1.1 if direct_term_match else 0.35
        if _is_simple_primary_table_question(plan):
            if direct_term_match and mapped_table_count == 1:
                glossary_boost = 1.35
            elif direct_term_match and mapped_table_count == 2:
                glossary_boost = 0.55
            elif direct_term_match:
                glossary_boost = 0.2
            else:
                glossary_boost = 0.1
        for mapping in term_data.get("mapped_columns", []):
            if mapping.get("table") == table_name:
                score += glossary_boost
                reasons.append(f"glossary term '{term}' mapped to this table")
                break

    if vector_results:
        vector_table_names = set(vector_results.get("table_names") or [])
        if table_name in vector_table_names:
            score += 1.8
            reasons.append("vector retrieval nominated this table")
        vector_columns = vector_results.get("columns") or []
        column_matches = [col for col in vector_columns if col.get("table_name") == table_name]
        if column_matches:
            score += min(1.2, 0.4 * len(column_matches))
            reasons.append("vector retrieval nominated columns in this table")
        for term_meta in vector_results.get("glossary_terms") or []:
            if table_name in (term_meta.get("table_names") or []):
                score += 0.8
                reasons.append("vector glossary retrieval matched this table")
                break

    return score, reasons


def _column_score(
    plan: dict[str, Any],
    table_name: str,
    column: dict[str, Any],
    glossary_matches: list[tuple[str, dict[str, Any]]],
    vector_results: dict[str, Any] | None,
) -> tuple[float, list[str]]:
    score = 0.0
    reasons: list[str] = []
    column_name = str(column.get("name", "")).lower()
    semantic_type = resolved_semantic_type(column)
    question_terms = set(plan.get("question_terms", []))
    column_tokens = set(_tokenize(column_name))
    column_tokens.update(_tokenize(column_business_description(column)))
    for term in column_business_terms(column):
        column_tokens.update(_tokenize(term))

    overlap = len(question_terms & column_tokens)
    if overlap:
        score += overlap * 0.55
        reasons.append(f"matched {overlap} question term(s)")

    if semantic_type in plan.get("semantic_hints", set()):
        score += 1.1
        reasons.append(f"semantic type matched '{semantic_type}'")

    dimension = str(plan.get("dimension") or "").strip()
    if dimension:
        dimension_tokens = set(_tokenize(dimension))
        table_tokens = set(_tokenize(table_name))
        if dimension_tokens & column_tokens:
            score += 1.0
            reasons.append(f"dimension '{dimension}' matched this column")
        if dimension_tokens & table_tokens and semantic_type in {"name", "text", "code", "reference"}:
            score += 1.4
            reasons.append("display-style column matched the grouping table")
        if dimension_tokens & table_tokens and column_tokens & {"name", "label", "title", "display", "segment", "code"}:
            score += 0.6
            reasons.append("column looks suitable as a grouping label")

    if semantic_type == "date" and plan.get("date_range"):
        score += 1.0
        reasons.append("date filter needs a date column")

    if semantic_type == "status" and any(filter_data.get("type") == "status" for filter_data in plan.get("filters", [])):
        score += 1.0
        reasons.append("status filter needs a status column")

    for term, term_data in glossary_matches:
        for mapping in term_data.get("mapped_columns", []):
            if mapping.get("table") == table_name and mapping.get("column") == column.get("name"):
                score += 1.2
                reasons.append(f"glossary term '{term}' mapped to this column")
                break

    if vector_results:
        for vector_column in vector_results.get("columns") or []:
            if vector_column.get("table_name") == table_name and vector_column.get("column_name") == column.get("name"):
                score += 1.4
                reasons.append("vector retrieval nominated this column")
                break

    return score, reasons


def _select_columns_for_table(
    plan: dict[str, Any],
    table_name: str,
    table_data: dict[str, Any],
    glossary_matches: list[tuple[str, dict[str, Any]]],
    vector_results: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    scored_columns: list[tuple[str, float, list[str], str]] = []
    for column in table_data.get("columns", []):
        score, reasons = _column_score(plan, table_name, column, glossary_matches, vector_results)
        if score <= 0:
            continue
        scored_columns.append(
            (
                str(column.get("name", "")),
                score,
                reasons,
                resolved_semantic_type(column),
                str(column.get("semantic_type", "unknown")).strip().lower() or "unknown",
            )
        )

    scored_columns.sort(key=lambda item: (-item[1], item[0]))
    selected_columns = []
    for column_name, score, reasons, semantic_type, core_semantic_type in scored_columns[:6]:
        selected_columns.append(
            {
                "column": column_name,
                "semantic_type": semantic_type,
                "core_semantic_type": core_semantic_type,
                "confidence": round(min(max(score / 3.0, 0.45), 0.99), 2),
                "reason": "; ".join(dict.fromkeys(reasons)),
            }
        )
    return selected_columns


def _preferred_primary_tables_for_simple_question(
    plan: dict[str, Any],
    scored_tables: list[tuple[str, float, list[str]]],
    glossary_matches: list[tuple[str, dict[str, Any]]],
    vector_results: dict[str, Any] | None,
) -> list[str] | None:
    if not _is_simple_primary_table_question(plan) or not scored_tables:
        return None

    top_table, top_score, _ = scored_tables[0]
    second_table = scored_tables[1][0] if len(scored_tables) > 1 else None
    second_score = scored_tables[1][1] if len(scored_tables) > 1 else 0.0
    question = str(plan.get("question") or "")
    direct_match_tables = [
        table_name
        for table_name, _, _ in scored_tables
        if _table_name_matches_question(question, table_name)
    ]
    if len(direct_match_tables) == 1:
        return [direct_match_tables[0]]

    has_direct_match = _table_name_matches_question(question, top_table)
    vector_table_names = list((vector_results or {}).get("table_names") or [])
    has_top_vector_match = bool(vector_table_names and vector_table_names[0] == top_table)
    has_strong_glossary_match = _planner()._has_strong_glossary_table_match(question, top_table, glossary_matches)
    second_has_strong_glossary_match = bool(
        second_table
        and _planner()._has_strong_glossary_table_match(question, second_table, glossary_matches)
    )

    if len(scored_tables) == 1 and (has_direct_match or has_top_vector_match or has_strong_glossary_match):
        return [top_table]

    if second_score <= 0:
        return [top_table] if (has_direct_match or has_top_vector_match or has_strong_glossary_match) else None

    if str(plan.get("intent") or "") in {"total", "average"} and top_score > second_score:
        return [top_table]

    if (has_direct_match or has_top_vector_match) and top_score >= (second_score * 1.5):
        return [top_table]

    if has_strong_glossary_match and not second_has_strong_glossary_match and top_score > second_score:
        return [top_table]

    if (has_top_vector_match or has_strong_glossary_match) and top_score >= second_score + 0.35:
        return [top_table]

    return None


def _build_selected_table_entries(
    knowledge_base: dict,
    scored_tables: list[tuple[str, float, list[str]]],
    plan: dict[str, Any],
    glossary_matches: list[tuple[str, dict[str, Any]]],
    vector_results: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    if not scored_tables:
        return []

    top_tables = []
    preferred_primary_tables = _preferred_primary_tables_for_simple_question(
        plan,
        scored_tables,
        glossary_matches,
        vector_results,
    )
    if preferred_primary_tables:
        top_tables = [
            entry for entry in scored_tables
            if entry[0] in preferred_primary_tables
        ]

    if not top_tables:
        top_tables = [entry for entry in scored_tables if entry[1] > 0.6][:3]
    if not top_tables:
        top_tables = scored_tables[:3]

    selected_names = _planner()._expand_selected_tables(
        knowledge_base,
        [entry[0] for entry in top_tables],
        plan.get("dimension"),
        plan=plan,
    )
    entries = []
    score_lookup = {table_name: (score, reasons) for table_name, score, reasons in scored_tables}

    for table_name in selected_names:
        score, reasons = score_lookup.get(table_name, (0.75, ["selected as a relationship bridge"]))
        selected_columns = _select_columns_for_table(
            plan,
            table_name,
            knowledge_base.get(table_name, {}),
            glossary_matches,
            vector_results,
        )
        entries.append(
            {
                "table": table_name,
                "confidence": round(min(max(score / 4.0, 0.55), 0.99), 2),
                "reason": "; ".join(dict.fromkeys(reasons)) or "selected from semantic and vector context",
                "selected_columns": selected_columns,
            }
        )

    return entries


def _infer_metric_from_selected_columns(
    plan: dict[str, Any],
    selected_tables: list[dict[str, Any]],
) -> str | None:
    current_metric = plan.get("metric")
    if plan.get("intent") not in {"total", "average", "top_n", "trend", "comparison"}:
        return current_metric

    ranked_semantics: list[tuple[float, str]] = []
    for table_entry in selected_tables:
        for column_entry in table_entry.get("selected_columns", []):
            semantic_type = str(column_entry.get("semantic_type", "")).lower()
            if semantic_type not in {"money", "quantity", "percentage", "date", "status"}:
                continue
            ranked_semantics.append((float(column_entry.get("confidence") or 0.0), semantic_type))

    ranked_semantics.sort(key=lambda item: (-item[0], item[1]))
    for _, semantic_type in ranked_semantics:
        if semantic_type in {"money", "quantity", "percentage"}:
            return semantic_type

    return current_metric


def _infer_metric_from_glossary_matches(
    glossary_matches: list[tuple[str, dict[str, Any]]],
    knowledge_base: dict[str, Any],
) -> str | None:
    ranked_semantics: list[tuple[float, str]] = []
    confidence_rank = {"high": 1.0, "medium": 0.75, "low": 0.5}

    for _, term_data in glossary_matches:
        for mapping in term_data.get("mapped_columns", []):
            table_name = str(mapping.get("table", "") or "")
            column_name = str(mapping.get("column", "") or "")
            if not table_name or not column_name:
                continue

            semantic_type = ""
            for column in knowledge_base.get(table_name, {}).get("columns", []):
                if str(column.get("name", "")) == column_name:
                    semantic_type = resolved_semantic_type(column)
                    break

            if semantic_type not in {"money", "quantity", "percentage"}:
                continue

            confidence = confidence_rank.get(str(mapping.get("confidence", "")).lower(), 0.6)
            ranked_semantics.append((confidence, semantic_type))

    ranked_semantics.sort(key=lambda item: (-item[0], item[1]))
    return ranked_semantics[0][1] if ranked_semantics else None


_DIMENSION_DISPLAY_TOKENS = {
    "name",
    "title",
    "label",
    "category",
    "type",
    "status",
    "segment",
    "city",
    "method",
    "region",
    "group",
}

_EXPLICIT_IDENTIFIER_TOKENS = {"id", "identifier", "code", "number", "key"}


def _owner_context_tokens(owner_context: str | list[str] | tuple[str, ...] | set[str] | None) -> set[str]:
    if owner_context is None:
        return set()
    values = owner_context if isinstance(owner_context, (list, tuple, set)) else [owner_context]
    tokens: set[str] = set()
    for value in values:
        tokens.update(_field_tokens(str(value or "")))
    return tokens


def _selected_path_tables(selected_join_path: dict[str, Any] | None) -> set[str]:
    if not isinstance(selected_join_path, dict):
        return set()
    if selected_join_path.get("path_source") != "relationship_graph":
        return set()
    tables = {
        str(selected_join_path.get("base_table") or "").strip(),
        *[str(table).strip() for table in (selected_join_path.get("joined_tables") or [])],
    }
    for edge in selected_join_path.get("edges") or []:
        if not isinstance(edge, dict):
            continue
        tables.add(str(edge.get("from_table") or "").strip())
        tables.add(str(edge.get("to_table") or "").strip())
    tables.discard("")
    return tables


@dataclass
class RoleCandidateScore:
    table: str
    column: str
    role: str
    score: float
    score_reasons: list[str] = field(default_factory=list)
    penalties: list[str] = field(default_factory=list)
    evidence_tier: str = ""
    source: str = ""
    candidate_score: float = 0.0
    ambiguity_group_key: str = ""
    candidate: dict[str, Any] = field(default_factory=dict)

    def as_ranked_entry(self) -> dict[str, Any]:
        return {
            "candidate": dict(self.candidate),
            "table": self.table,
            "column": self.column,
            "role": self.role,
            "tier": self.evidence_tier,
            "evidence_tier": self.evidence_tier,
            "score": round(float(self.score or 0.0), 4),
            "candidate_score": round(float(self.candidate_score or 0.0), 4),
            "reasons": list(self.score_reasons),
            "score_reasons": list(self.score_reasons),
            "penalties": list(self.penalties),
            "source": self.source,
            "ambiguity_group_key": self.ambiguity_group_key,
        }


def _role_candidate_match_score(phrase: str, entry: dict[str, Any]) -> float:
    phrase_tokens = {_singularize_token(token) for token in _tokenize(phrase)}
    column_name = str(entry.get("column") or "")
    column_tokens = {_singularize_token(token) for token in _tokenize(column_name)}
    qualified_tokens = {
        _singularize_token(token)
        for token in _tokenize(f"{entry.get('table') or ''} {column_name}")
    }
    if not phrase_tokens or not column_tokens:
        return 0.0
    if phrase_tokens == qualified_tokens:
        lexical_score = 1.0
    elif phrase_tokens == column_tokens:
        lexical_score = 0.98
    elif phrase_tokens <= column_tokens:
        lexical_score = 0.9
    elif phrase_tokens <= qualified_tokens:
        lexical_score = 0.86
    else:
        lexical_score = (len(phrase_tokens & qualified_tokens) / len(phrase_tokens)) * 0.6
    for term in entry.get("matched_terms") or []:
        term_tokens = {_singularize_token(token) for token in _tokenize(str(term))}
        if term_tokens == phrase_tokens:
            lexical_score = max(lexical_score, 0.94)
        elif phrase_tokens <= term_tokens:
            lexical_score = max(lexical_score, 0.82)
    return round(lexical_score, 4)


def _candidate_is_numeric_metric(entry: dict[str, Any]) -> bool:
    semantic_type = _candidate_semantic_type(entry)
    data_type = str(entry.get("data_type") or entry.get("type") or "").strip().lower()
    if semantic_type in _planner()._NON_METRIC_SEMANTIC_TYPES:
        return False
    if _is_textual_sql_type(data_type) and not _is_numeric_sql_type(data_type):
        return False
    return bool(
        entry.get("is_measure")
        or semantic_type in _planner()._NUMERIC_METRIC_SEMANTIC_TYPES
        or _is_numeric_sql_type(data_type)
    )


def _candidate_is_dimension(entry: dict[str, Any], phrase: str) -> bool:
    semantic_type = _candidate_semantic_type(entry)
    data_type = str(entry.get("data_type") or entry.get("type") or "").strip().lower()
    column_tokens = _field_tokens(str(entry.get("column") or ""))
    phrase_tokens = _field_tokens(phrase)
    exact_column_request = bool(phrase_tokens and phrase_tokens == column_tokens)
    if bool(entry.get("is_measure")) and _candidate_is_numeric_metric(entry) and not exact_column_request:
        return False
    return bool(
        entry.get("is_dimension")
        or entry.get("is_date")
        or semantic_type in _planner()._DIMENSION_SEMANTIC_TYPES
        or _is_textual_sql_type(data_type)
        or exact_column_request
    )


def _dimension_identifier_explicitly_requested(phrase_tokens: set[str]) -> bool:
    return bool(phrase_tokens & _EXPLICIT_IDENTIFIER_TOKENS)


def _dimension_display_tokens(column_name: str) -> set[str]:
    return _field_tokens(column_name) & _DIMENSION_DISPLAY_TOKENS


def _dimension_candidate_is_join_key(entry: dict[str, Any], column_tokens: set[str]) -> bool:
    semantic_type = _candidate_semantic_type(entry)
    planner_roles = entry.get("planner_roles") if isinstance(entry.get("planner_roles"), dict) else {}
    column_name = str(entry.get("column") or "").strip().lower()
    return bool(
        semantic_type == "id"
        or entry.get("primary_key")
        or entry.get("is_primary_key")
        or entry.get("foreign_key")
        or entry.get("is_foreign_key")
        or planner_roles.get("join_candidate")
        or column_name.endswith("_id")
        or column_tokens == {"id"}
        or "id" in column_tokens
    )


def _apply_dimension_display_preferences(
    *,
    phrase_tokens: set[str],
    column_name: str,
    column_tokens: set[str],
    entry: dict[str, Any],
    score: float,
    reasons: list[str],
) -> tuple[float, list[str]]:
    penalties: list[str] = []
    explicit_identifier = _dimension_identifier_explicitly_requested(phrase_tokens)
    display_tokens = _dimension_display_tokens(column_name)
    if display_tokens and not explicit_identifier:
        score += 0.08
        reasons.append(f"display-friendly dimension column matched: {', '.join(sorted(display_tokens))}")

    if _dimension_candidate_is_join_key(entry, column_tokens) and not explicit_identifier:
        score = max(0.0, score - 0.25)
        penalties.append("dimension ID/join-key penalty")

    return score, penalties


def _apply_owner_preferences(
    *,
    phrase_tokens: set[str],
    table_tokens: set[str],
    column_tokens: set[str],
    owner_tokens: set[str],
    score: float,
    reasons: list[str],
) -> float:
    if owner_tokens and table_tokens and (table_tokens <= owner_tokens or owner_tokens <= table_tokens):
        score += 0.14
        reasons.append("owner context matched candidate table")
        return score
    if table_tokens and table_tokens <= phrase_tokens and phrase_tokens != column_tokens:
        score += 0.08
        reasons.append("owner-qualified phrase matched candidate table")
    return score


def _apply_selected_path_preferences(
    *,
    role: str,
    table_name: str,
    base_table: str,
    path_tables: set[str],
    score: float,
    reasons: list[str],
) -> float:
    if not table_name or table_name not in path_tables:
        return score
    score += 0.06
    reasons.append("selected join path contains candidate table")
    if role == "metric" and base_table and table_name == base_table:
        score += 0.04
        reasons.append("metric candidate agrees with selected join path base table")
    return score


def score_role_candidate(
    phrase: str,
    entry: dict[str, Any],
    *,
    role: str = "generic",
    owner_context: str | list[str] | tuple[str, ...] | set[str] | None = None,
    selected_join_path: dict[str, Any] | None = None,
) -> RoleCandidateScore | None:
    phrase_tokens = _field_tokens(phrase)
    table_name = str(entry.get("table") or "").strip()
    column_name = str(entry.get("column") or "").strip()
    column_tokens = _field_tokens(column_name)
    table_tokens = {_singularize_token(token) for token in _tokenize(table_name)}
    owner_tokens = _owner_context_tokens(owner_context)
    path_tables = _selected_path_tables(selected_join_path)
    path_base_table = str((selected_join_path or {}).get("base_table") or "").strip()
    qualified_tokens = table_tokens | column_tokens
    if not phrase_tokens or not column_tokens:
        return None

    if role == "metric" and not _candidate_is_numeric_metric(entry):
        return None
    if role == "dimension" and not _candidate_is_dimension(entry, phrase):
        return None

    normalized_phrase = _humanize(phrase)
    normalized_column = _humanize(column_name)
    generic_single_token = len(phrase_tokens) == 1 and next(iter(phrase_tokens), "") in _planner()._GENERIC_ROLE_TERMS
    matched_terms = [
        str(term).strip()
        for term in (entry.get("matched_terms") or [])
        if str(term).strip()
    ]
    matched_term_tokens = [
        {_singularize_token(token) for token in _tokenize(term)}
        for term in matched_terms
        if _tokenize(term)
    ]

    tier = ""
    score = 0.0
    reasons: list[str] = []

    if normalized_column == normalized_phrase or phrase_tokens == column_tokens:
        tier = "exact_normalized_column"
        score = _planner()._SCORING_TIERS[tier]
        reasons.append("exact normalized column phrase match")
    elif phrase_tokens == qualified_tokens:
        tier = "owner_qualified_exact"
        score = _planner()._SCORING_TIERS[tier]
        reasons.append("owner-qualified exact column phrase match")
    elif not generic_single_token and any(phrase_tokens == tokens for tokens in matched_term_tokens):
        tier = "kb_glossary_semantic"
        score = _planner()._SCORING_TIERS[tier]
        reasons.append("KB glossary or semantic term matched exactly")
    elif not generic_single_token and any(phrase_tokens == table_tokens | tokens for tokens in matched_term_tokens):
        tier = "owner_qualified_exact"
        score = _planner()._SCORING_TIERS[tier]
        reasons.append("owner-qualified semantic term matched exactly")
    else:
        lexical_score = _role_candidate_match_score(phrase, entry)
        if lexical_score <= 0:
            return None
        if role == "metric":
            tier = "numeric_metric_eligible"
        elif role == "dimension":
            tier = "dimension_type_eligible"
        elif role == "filter":
            tier = "sample_value_filter_match"
        else:
            tier = "kb_glossary_semantic"
        score = round(max(lexical_score, _planner()._SCORING_TIERS[tier]), 4)
        reasons.append(f"{tier.replace('_', ' ')} supported by candidate evidence")

    score = _apply_owner_preferences(
        phrase_tokens=phrase_tokens,
        table_tokens=table_tokens,
        column_tokens=column_tokens,
        owner_tokens=owner_tokens,
        score=score,
        reasons=reasons,
    )
    score = _apply_selected_path_preferences(
        role=role,
        table_name=table_name,
        base_table=path_base_table,
        path_tables=path_tables,
        score=score,
        reasons=reasons,
    )

    penalties: list[str] = []
    if role == "dimension":
        score, penalties = _apply_dimension_display_preferences(
            phrase_tokens=phrase_tokens,
            column_name=column_name,
            column_tokens=column_tokens,
            entry=entry,
            score=score,
            reasons=reasons,
        )

    evidence_score = _safe_float(entry.get("score") or entry.get("confidence"), 0.0)
    return RoleCandidateScore(
        table=table_name,
        column=column_name,
        role=role,
        score=round(score, 4),
        score_reasons=reasons,
        penalties=penalties,
        evidence_tier=tier,
        source=str(entry.get("source") or ""),
        candidate_score=round(evidence_score, 4),
        ambiguity_group_key=f"{role}:{tier}:{round(score, 4)}",
        candidate=dict(entry),
    )


def _role_candidate_scoring_entry(
    phrase: str,
    entry: dict[str, Any],
    *,
    role: str = "generic",
    owner_context: str | list[str] | tuple[str, ...] | set[str] | None = None,
    selected_join_path: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    scored = score_role_candidate(
        phrase,
        entry,
        role=role,
        owner_context=owner_context,
        selected_join_path=selected_join_path,
    )
    return scored.as_ranked_entry() if scored else None


def _role_rank_sort_key(item: dict[str, Any]) -> tuple[Any, ...]:
    return (
        -_safe_float(item.get("score")),
        -_safe_float(item.get("candidate_score")),
        str(item.get("candidate", {}).get("table") or item.get("table") or ""),
        str(item.get("candidate", {}).get("column") or item.get("column") or ""),
    )


def rank_role_candidates(
    phrase: str,
    candidates: list[dict[str, Any]],
    *,
    role: str = "generic",
    allowed_tables: set[str] | None = None,
    owner_context: str | list[str] | tuple[str, ...] | set[str] | None = None,
    selected_join_path: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ranked: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for candidate in candidates or []:
        table_name = str(candidate.get("table") or "").strip()
        column_name = str(candidate.get("column") or "").strip()
        signature = (table_name, column_name)
        if not all(signature) or signature in seen:
            continue
        if allowed_tables is not None and table_name not in allowed_tables:
            continue
        seen.add(signature)
        scored = score_role_candidate(
            phrase,
            candidate,
            role=role,
            owner_context=owner_context,
            selected_join_path=selected_join_path,
        )
        if scored:
            ranked.append(scored.as_ranked_entry())

    ranked.sort(key=_role_rank_sort_key)
    if not ranked:
        return {"status": "missing", "selected": None, "ranked": [], "tie_reason": ""}

    phrase_tokens = {_singularize_token(token) for token in _tokenize(phrase)}
    generic_single_token = len(phrase_tokens) == 1 and next(iter(phrase_tokens), "") in _planner()._GENERIC_ROLE_TERMS
    top = ranked[0]
    ties = [
        item for item in ranked
        if item.get("tier") == top.get("tier")
        and abs(_safe_float(item.get("score")) - _safe_float(top.get("score"))) < 0.0001
    ]
    if generic_single_token and len(ranked) > 1:
        path_reasons = list(top.get("score_reasons") or top.get("reasons") or [])
        selected_path_winner = (
            selected_join_path
            and len(ties) == 1
            and any("selected join path" in reason for reason in path_reasons)
        )
        owner_context_winner = (
            owner_context
            and len(ties) == 1
            and any("owner context" in reason for reason in path_reasons)
        )
        if not selected_path_winner and not owner_context_winner:
            return {
                "status": "ambiguous",
                "selected": None,
                "ranked": ranked,
                "tie_reason": "generic single-token phrase matched multiple safe candidates",
            }
    if len(ties) > 1:
        return {
            "status": "ambiguous",
            "selected": None,
            "ranked": ranked,
            "tie_reason": f"multiple candidates tied at tier {top.get('tier')}",
        }
    return {"status": "resolved", "selected": top, "ranked": ranked, "tie_reason": ""}


def _rank_role_candidates(
    phrase: str,
    candidates: list[dict[str, Any]],
    *,
    role: str = "generic",
    allowed_tables: set[str] | None = None,
    owner_context: str | list[str] | tuple[str, ...] | set[str] | None = None,
    selected_join_path: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return rank_role_candidates(
        phrase,
        candidates,
        role=role,
        allowed_tables=allowed_tables,
        owner_context=owner_context,
        selected_join_path=selected_join_path,
    )


def build_role_candidate_debug(result: dict[str, Any]) -> dict[str, Any]:
    selected = result.get("selected") or {}
    return {
        "status": result.get("status"),
        "selected": {
            "table": selected.get("table") or selected.get("candidate", {}).get("table"),
            "column": selected.get("column") or selected.get("candidate", {}).get("column"),
            "role": selected.get("role"),
            "score": selected.get("score"),
            "evidence_tier": selected.get("evidence_tier") or selected.get("tier"),
            "score_reasons": list(selected.get("score_reasons") or selected.get("reasons") or []),
            "penalties": list(selected.get("penalties") or []),
        } if selected else None,
        "ranked": [
            {
                "table": item.get("table") or item.get("candidate", {}).get("table"),
                "column": item.get("column") or item.get("candidate", {}).get("column"),
                "role": item.get("role"),
                "score": item.get("score"),
                "evidence_tier": item.get("evidence_tier") or item.get("tier"),
                "score_reasons": list(item.get("score_reasons") or item.get("reasons") or []),
                "penalties": list(item.get("penalties") or []),
                "ambiguity_group_key": item.get("ambiguity_group_key"),
            }
            for item in (result.get("ranked") or [])
        ],
        "tie_reason": result.get("tie_reason", ""),
    }


def build_metric_decision_contract(
    *,
    metric_phrase: str,
    metric_candidates: list[dict[str, Any]],
    aggregate_function: str,
    selected_metric: dict[str, Any] | None = None,
    selected_join_path: dict[str, Any] | None = None,
    owner_context: str | list[str] | tuple[str, ...] | set[str] | None = None,
    allowed_tables: set[str] | None = None,
    count_base_table: str = "",
) -> dict[str, Any]:
    aggregate = str(aggregate_function or "").strip().lower()
    path_tables = _selected_path_tables(selected_join_path)

    def _path_compatible(table_name: str) -> bool:
        return not path_tables or table_name in path_tables

    if aggregate not in {"count", "sum", "avg", "min", "max"}:
        return {
            "status": "unsupported",
            "metric_phrase": metric_phrase,
            "owner_entity": "",
            "table": "",
            "column": "",
            "aggregate_function": aggregate,
            "metric_mode": "numeric_metric",
            "numeric_eligible": False,
            "selected_path_compatible": False,
            "evidence_tier": "",
            "score": 0.0,
            "score_reasons": [],
            "penalties": [],
            "ambiguity_group_key": "metric:unsupported_aggregate",
            "rejected_candidates": [],
            "reason_code": "unsupported_aggregate_function",
        }

    if aggregate == "count":
        base_table = str(count_base_table or "").strip()
        path_ok = _path_compatible(base_table) if base_table else True
        return {
            "status": "resolved" if path_ok else "unsupported",
            "metric_phrase": metric_phrase,
            "owner_entity": base_table,
            "table": base_table,
            "column": "",
            "aggregate_function": aggregate,
            "metric_mode": "entity_count" if base_table else "row_count",
            "numeric_eligible": True,
            "selected_path_compatible": path_ok,
            "evidence_tier": "count_entity" if base_table else "count_rows",
            "score": 1.0,
            "score_reasons": ["COUNT does not require a numeric metric column"],
            "penalties": [],
            "ambiguity_group_key": "",
            "rejected_candidates": [],
            "reason_code": "" if path_ok else "count_base_table_outside_selected_path",
        }

    result = (
        {"status": "resolved", "selected": {"candidate": dict(selected_metric), "tier": "preserved_selected_metric", "score": 1.0, "reasons": ["metric preserved from upstream planner decision"]}, "ranked": [], "tie_reason": ""}
        if isinstance(selected_metric, dict) and selected_metric
        else rank_role_candidates(
            metric_phrase,
            metric_candidates,
            role="metric",
            allowed_tables=allowed_tables,
            owner_context=owner_context,
            selected_join_path=selected_join_path,
        )
    )
    if result.get("status") != "resolved":
        return {
            "status": str(result.get("status") or "missing"),
            "metric_phrase": metric_phrase,
            "owner_entity": "",
            "table": "",
            "column": "",
            "aggregate_function": aggregate,
            "metric_mode": "numeric_metric",
            "numeric_eligible": False,
            "selected_path_compatible": False,
            "evidence_tier": "",
            "score": 0.0,
            "score_reasons": [],
            "penalties": [],
            "ambiguity_group_key": f"metric:{result.get('status') or 'missing'}",
            "rejected_candidates": list(result.get("ranked") or []),
            "reason_code": f"metric_evidence_{result.get('status') or 'missing'}",
        }

    selected = result.get("selected") or {}
    candidate = dict(selected.get("candidate") or {})
    table_name = str(candidate.get("table") or "").strip()
    column_name = str(candidate.get("column") or "").strip()
    numeric_ok = _candidate_is_numeric_metric(candidate)
    table_allowed = allowed_tables is None or table_name in allowed_tables
    path_ok = _path_compatible(table_name)
    reason = ""
    if not numeric_ok:
        reason = "metric_not_numeric_eligible"
    elif not table_allowed:
        reason = "metric_table_not_allowed"
    elif not path_ok:
        reason = "metric_table_outside_selected_path"

    return {
        "status": "resolved" if not reason else "unsupported",
        "metric_phrase": metric_phrase,
        "owner_entity": table_name,
        "table": table_name,
        "column": column_name,
        "aggregate_function": aggregate,
        "metric_mode": "numeric_metric",
        "numeric_eligible": numeric_ok,
        "selected_path_compatible": path_ok,
        "evidence_tier": str(selected.get("tier") or selected.get("evidence_tier") or ""),
        "score": _safe_float(selected.get("score"), 0.0),
        "score_reasons": list(selected.get("reasons") or selected.get("score_reasons") or []),
        "penalties": list(selected.get("penalties") or []),
        "ambiguity_group_key": "" if not reason else f"metric:{table_name}.{column_name}",
        "rejected_candidates": [
            item for item in (result.get("ranked") or [])
            if (item.get("candidate") or {}) != candidate
        ],
        "reason_code": reason,
    }


def build_dimension_decision_contract(
    *,
    dimension_phrase: str,
    dimension_candidates: list[dict[str, Any]],
    dimension_mode: str = "grouping_dimension",
    selected_dimension: dict[str, Any] | None = None,
    selected_join_path: dict[str, Any] | None = None,
    owner_context: str | list[str] | tuple[str, ...] | set[str] | None = None,
    allowed_tables: set[str] | None = None,
) -> dict[str, Any]:
    mode = str(dimension_mode or "grouping_dimension").strip()
    if mode not in {"grouping_dimension", "display_dimension", "entity_label"}:
        mode = "grouping_dimension"
    path_tables = _selected_path_tables(selected_join_path)

    def _path_compatible(table_name: str) -> bool:
        return not path_tables or table_name in path_tables

    result = (
        {"status": "resolved", "selected": {"candidate": dict(selected_dimension), "tier": "preserved_selected_dimension", "score": 1.0, "reasons": ["dimension preserved from upstream planner decision"]}, "ranked": [], "tie_reason": ""}
        if isinstance(selected_dimension, dict) and selected_dimension
        else rank_role_candidates(
            dimension_phrase,
            dimension_candidates,
            role="dimension",
            allowed_tables=allowed_tables,
            owner_context=owner_context,
            selected_join_path=selected_join_path,
        )
    )
    if result.get("status") != "resolved":
        return {
            "status": str(result.get("status") or "missing"),
            "dimension_phrase": dimension_phrase,
            "dimension_mode": mode,
            "owner_entity": "",
            "table": "",
            "column": "",
            "semantic_type": "",
            "display_eligible": False,
            "grouping_eligible": False,
            "selected_path_compatible": False,
            "evidence_tier": "",
            "score": 0.0,
            "score_reasons": [],
            "penalties": [],
            "ambiguity_group_key": f"dimension:{result.get('status') or 'missing'}",
            "rejected_candidates": list(result.get("ranked") or []),
            "reason_code": f"dimension_evidence_{result.get('status') or 'missing'}",
        }

    selected = result.get("selected") or {}
    candidate = dict(selected.get("candidate") or {})
    table_name = str(candidate.get("table") or "").strip()
    column_name = str(candidate.get("column") or "").strip()
    semantic_type = _candidate_semantic_type(candidate)
    display_ok = _candidate_is_dimension(candidate, dimension_phrase)
    grouping_ok = display_ok and semantic_type not in {"money", "numeric", "numeric_candidate", "date"}
    table_allowed = allowed_tables is None or table_name in allowed_tables
    path_ok = _path_compatible(table_name)
    reason = ""
    if not display_ok:
        reason = "dimension_not_display_eligible"
    elif mode == "grouping_dimension" and not grouping_ok:
        reason = "dimension_not_grouping_eligible"
    elif not table_allowed:
        reason = "dimension_table_not_allowed"
    elif not path_ok:
        reason = "dimension_table_outside_selected_path"

    return {
        "status": "resolved" if not reason else "unsupported",
        "dimension_phrase": dimension_phrase,
        "dimension_mode": mode,
        "owner_entity": table_name,
        "table": table_name,
        "column": column_name,
        "semantic_type": semantic_type,
        "display_eligible": display_ok,
        "grouping_eligible": grouping_ok,
        "selected_path_compatible": path_ok,
        "evidence_tier": str(selected.get("tier") or selected.get("evidence_tier") or ""),
        "score": _safe_float(selected.get("score"), 0.0),
        "score_reasons": list(selected.get("reasons") or selected.get("score_reasons") or []),
        "penalties": list(selected.get("penalties") or []),
        "ambiguity_group_key": "" if not reason else f"dimension:{table_name}.{column_name}",
        "rejected_candidates": [
            item for item in (result.get("ranked") or [])
            if (item.get("candidate") or {}) != candidate
        ],
        "reason_code": reason,
    }


def _resolve_role_candidate(
    phrase: str,
    candidates: list[dict[str, Any]],
    *,
    allowed_tables: set[str] | None = None,
    role: str = "generic",
    owner_context: str | list[str] | tuple[str, ...] | set[str] | None = None,
    selected_join_path: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], str]:
    result = rank_role_candidates(
        phrase,
        candidates,
        role=role,
        allowed_tables=allowed_tables,
        owner_context=owner_context,
        selected_join_path=selected_join_path,
    )
    if result.get("status") != "resolved":
        return [], str(result.get("status") or "missing")
    selected = result.get("selected") or {}
    candidate = dict(selected.get("candidate") or {})
    return [candidate], "resolved"


def _selected_evidence_entry(result: dict[str, Any], *, selected: dict[str, Any] | None = None) -> dict[str, Any]:
    selected_item = selected or result.get("selected") or {}
    ranked = list(result.get("ranked") or [])
    candidate = dict(selected_item.get("candidate") or selected_item or {})
    losers = [
        {
            "table": item.get("candidate", {}).get("table"),
            "column": item.get("candidate", {}).get("column"),
            "tier": item.get("tier"),
            "score": item.get("score"),
            "reasons": item.get("reasons", []),
        }
        for item in ranked
        if item is not selected_item
    ][:5]
    return {
        "status": result.get("status", "resolved" if candidate else "missing"),
        "selected": candidate or None,
        "tier": selected_item.get("tier"),
        "score": selected_item.get("score"),
        "reasons": list(selected_item.get("reasons") or []),
        "losing_candidates": losers,
        "tie_reason": result.get("tie_reason", ""),
    }


def _graph_selected_evidence_entry(edge: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": "resolved",
        "selected": dict(edge),
        "tier": "selected_join_path_agreement",
        "score": _planner()._SCORING_TIERS["selected_join_path_agreement"],
        "reasons": ["selected_join_path agrees with one direct Relationship Graph edge"],
        "losing_candidates": [],
        "tie_reason": "",
    }


def _source_selected_evidence_entry(table_name: str, phrase: str, status: str) -> dict[str, Any]:
    tier = "source_phrase_agreement" if status == "resolved" and phrase else None
    return {
        "status": status,
        "selected": {"table": table_name} if table_name else None,
        "tier": tier,
        "score": _planner()._SCORING_TIERS[tier] if tier else None,
        "reasons": [f"source phrase '{phrase}' resolved to base table"] if tier else [],
        "losing_candidates": [],
        "tie_reason": "",
    }


def _exact_table_column_candidates(
    table_name: str,
    field_phrase: str,
    knowledge_base: dict[str, Any],
) -> list[dict[str, Any]]:
    if not table_name or not field_phrase:
        return []
    table_data = knowledge_base.get(table_name) or {}
    matches: list[dict[str, Any]] = []
    for column in table_data.get("columns", []) or []:
        column_name = str(column.get("name") or "").strip()
        if not column_name or _field_tokens(column_name) != _field_tokens(field_phrase):
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
                "confidence": 1.0,
                "reason": "exact column phrase in selected table",
            }
        )
    return matches


def _resolve_owned_monetary_metric_from_schema(
    metric_phrase: str,
    knowledge_base: dict[str, Any],
) -> tuple[dict[str, Any] | None, str]:
    phrase_tokens = {_singularize_token(token) for token in _tokenize(metric_phrase)}
    if len(phrase_tokens) < 2:
        return None, "missing"
    owner_matches: list[tuple[float, str]] = []
    for table_name in knowledge_base:
        table_tokens = {_singularize_token(token) for token in _tokenize(table_name)}
        if not table_tokens:
            continue
        overlap = phrase_tokens & table_tokens
        if not overlap:
            continue
        score = len(overlap) / max(len(table_tokens), 1)
        if score > 0:
            owner_matches.append((score, table_name))
    owner_matches.sort(key=lambda item: (-item[0], item[1]))
    if not owner_matches:
        return None, "missing"
    if len(owner_matches) > 1 and abs(owner_matches[0][0] - owner_matches[1][0]) < 0.08:
        return None, "ambiguous"
    owner_table = owner_matches[0][1]
    monetary_candidates: list[dict[str, Any]] = []
    for column in knowledge_base.get(owner_table, {}).get("columns", []) or []:
        column_name = str(column.get("name") or "").strip()
        if not column_name:
            continue
        semantic_type = str(column.get("semantic_type") or "").strip().lower()
        data_type = str(column.get("type") or "").strip().lower()
        planner_roles = column.get("planner_roles") if isinstance(column.get("planner_roles"), dict) else {}
        is_measure = bool(
            column.get("is_measure")
            or planner_roles.get("measure_candidate")
            or semantic_type in {"money", "numeric_candidate", "quantity", "percentage"}
        )
        is_monetary = semantic_type == "money" or any(
            token in data_type for token in ("decimal", "numeric", "money")
        )
        if not is_measure or not is_monetary:
            continue
        monetary_candidates.append(
            {
                "table": owner_table,
                "column": column_name,
                "semantic_type": semantic_type,
                "core_semantic_type": semantic_type,
                "data_type": data_type,
                "is_measure": True,
                "is_dimension": False,
                "is_date": False,
                "score": 0.74,
                "matched_terms": [metric_phrase],
                "evidence_sources": ["schema_owner_phrase", "kb_numeric_profile"],
                "source": "kb_schema_profile",
                "reason": "unique owner-table monetary measure resolved from schema/profile evidence",
            }
        )
    if len(monetary_candidates) == 1:
        return monetary_candidates[0], "resolved"
    if len(monetary_candidates) > 1:
        return None, "ambiguous"
    return None, "missing"


def _resolve_related_sales_amount_metric(
    metric_phrase: str,
    *,
    dimension_table: str,
    knowledge_base: dict[str, Any],
) -> tuple[dict[str, Any] | None, str]:
    phrase_tokens = {_singularize_token(token) for token in _tokenize(metric_phrase)}
    if "sale" not in phrase_tokens or not dimension_table:
        return None, "missing"

    graph = build_relationship_graph(knowledge_base, infer_relationships=False)
    candidates: list[dict[str, Any]] = []
    for table_name, table_data in (knowledge_base or {}).items():
        if table_name == dimension_table or not isinstance(table_data, dict):
            continue
        graph_edges = find_safe_direct_join_relationships(graph, table_name, dimension_table)
        if len(graph_edges) != 1:
            continue
        for column in table_data.get("columns", []) or []:
            column_name = str(column.get("name") or "").strip()
            if not column_name:
                continue
            planner_roles = column.get("planner_roles") if isinstance(column.get("planner_roles"), dict) else {}
            candidate = {
                "table": table_name,
                "column": column_name,
                "semantic_type": str(column.get("semantic_type") or "").strip().lower(),
                "core_semantic_type": str(column.get("semantic_type") or "").strip().lower(),
                "data_type": column.get("type") or column.get("data_type") or "",
                "type": column.get("type") or column.get("data_type") or "",
                "is_measure": bool(column.get("is_measure") or planner_roles.get("measure_candidate")),
                "is_dimension": False,
                "is_date": False,
                "score": 0.74,
                "matched_terms": [metric_phrase],
                "evidence_sources": ["relationship_graph", "schema_numeric_profile"],
                "source": "relationship_graph_schema_profile",
                "reason": "unique graph-related amount-like numeric measure resolved for generic sales wording",
            }
            column_tokens = {_singularize_token(token) for token in _tokenize(column_name)}
            if not _candidate_is_numeric_metric(candidate):
                continue
            if not (column_tokens & {"amount", "value", "total"}):
                continue
            if column_tokens & {"price", "cost", "discount", "quantity", "qty"}:
                continue
            candidates.append(candidate)

    unique: dict[tuple[str, str], dict[str, Any]] = {}
    for candidate in candidates:
        unique[(str(candidate["table"]), str(candidate["column"]))] = candidate
    candidates = list(unique.values())
    if len(candidates) == 1:
        return candidates[0], "resolved"
    if len(candidates) > 1:
        return None, "ambiguous"
    return None, "missing"


def _resolve_entity_display_dimension(
    entity_phrase: str,
    dimension_candidates: list[dict[str, Any]],
    knowledge_base: dict[str, Any],
) -> tuple[list[dict[str, Any]], str]:
    table_name, table_status = _planner()._resolve_join_table(entity_phrase, knowledge_base, [])
    if table_status != "resolved" or not table_name:
        return [], table_status
    phrase_tokens = {_singularize_token(token) for token in _tokenize(entity_phrase)}
    table_tokens = {_singularize_token(token) for token in _tokenize(table_name)}
    residual_tokens = phrase_tokens - table_tokens
    display_candidates: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for candidate in dimension_candidates:
        if str(candidate.get("table") or "") != table_name:
            continue
        column_name = str(candidate.get("column") or "").strip()
        if not column_name:
            continue
        semantic_type = str(candidate.get("semantic_type") or "").strip().lower()
        column_tokens = {_singularize_token(token) for token in _tokenize(column_name)}
        is_display = semantic_type == "name" or "name" in column_tokens
        if residual_tokens and not residual_tokens <= column_tokens:
            continue
        if not is_display:
            continue
        signature = (table_name, column_name)
        if signature in seen:
            continue
        seen.add(signature)
        display_candidates.append(dict(candidate))
    if len(display_candidates) == 1:
        return display_candidates, "resolved"
    if len(display_candidates) > 1:
        return [], "ambiguous"
    return [], "missing"


def _resolve_count_base_table(
    source_phrase: str,
    dimension_table: str,
    knowledge_base: dict[str, Any],
) -> tuple[str | None, str]:
    explicit_base, base_status = _planner()._resolve_join_table(source_phrase, knowledge_base, [])
    if base_status == "resolved" and explicit_base:
        return explicit_base, "resolved"
    if base_status != "ambiguous":
        return explicit_base, base_status

    ranked = sorted(
        (
            (_planner()._table_phrase_score(source_phrase, table_name), table_name)
            for table_name in knowledge_base
            if table_name != dimension_table
        ),
        key=lambda item: (-item[0], item[1]),
    )
    ranked = [item for item in ranked if item[0] > 0]
    if not ranked:
        return None, "missing"
    top_score = ranked[0][0]
    tied_candidates = [table_name for score, table_name in ranked if abs(score - top_score) < 0.08]
    graph = build_relationship_graph(knowledge_base, infer_relationships=False)
    graph_backed = [
        table_name
        for table_name in tied_candidates
        if len(find_safe_direct_join_relationships(graph, table_name, dimension_table)) == 1
    ]
    if len(graph_backed) == 1:
        return graph_backed[0], "resolved"
    if len(graph_backed) > 1:
        return None, "ambiguous"
    return None, "ambiguous"


def _fallback_metric_candidates_from_selected_columns(
    selected_columns: list[dict[str, Any]],
    question: str,
) -> list[dict[str, Any]]:
    if not _aggregate_function_hint(question):
        return []

    candidates: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    question_terms = set(_content_terms(question))
    for entry in selected_columns or []:
        table_name = str(entry.get("table") or "").strip()
        column_name = str(entry.get("column") or "").strip()
        semantic_type = str(entry.get("semantic_type") or "").strip().lower()
        core_semantic_type = str(entry.get("core_semantic_type") or "").strip().lower()
        if not table_name or not column_name:
            continue
        if semantic_type in {"id", "date", "boolean"} or core_semantic_type in {"id", "date", "boolean"}:
            continue
        if semantic_type not in {"numeric_candidate", "money", "quantity", "percentage"} and core_semantic_type != "numeric_candidate":
            continue
        key = (table_name, column_name)
        if key in seen:
            continue
        seen.add(key)
        column_terms = [term for term in _tokenize(column_name) if term not in {"id", "total", "amount", "value"}]
        matched_terms = [_humanize(column_name)] if column_terms and set(column_terms) <= question_terms else []
        candidates.append(
            {
                "table": table_name,
                "column": column_name,
                "semantic_type": semantic_type or core_semantic_type or "numeric_candidate",
                "core_semantic_type": core_semantic_type or semantic_type or "numeric_candidate",
                "score": float(entry.get("confidence") or 0.55),
                "matched_terms": matched_terms,
                "source": "selected_columns_fallback",
                "reason": "metric candidate derived from numeric selected column aggregate fallback",
            }
        )
    return candidates


def _prune_single_table_aggregate_context(
    selected_tables: list[dict[str, Any]],
    selected_columns: list[dict[str, Any]],
    selected_table_names: list[str],
    measure_candidates: list[dict[str, Any]],
    dimension_candidates: list[dict[str, Any]],
    filter_candidates: list[dict[str, Any]],
    join_paths: list[dict[str, Any]],
    matched_relationships: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    if measure_candidates:
        primary_table = str(measure_candidates[0].get("table") or "").strip()
    elif selected_table_names:
        primary_table = str(selected_table_names[0] or "").strip()
    else:
        primary_table = ""
    if not primary_table:
        return (
            selected_tables,
            selected_columns,
            selected_table_names,
            measure_candidates,
            [],
            filter_candidates,
            join_paths,
            matched_relationships,
        )

    pruned_tables = [entry for entry in selected_tables if str(entry.get("table") or "").strip() == primary_table]
    pruned_table_names = [primary_table]
    pruned_columns = [entry for entry in selected_columns if str(entry.get("table") or "").strip() == primary_table]
    pruned_measures = [entry for entry in measure_candidates if str(entry.get("table") or "").strip() == primary_table]
    pruned_filters = [entry for entry in filter_candidates if str(entry.get("table") or "").strip() == primary_table]
    return (
        pruned_tables,
        pruned_columns,
        pruned_table_names,
        pruned_measures,
        [],
        pruned_filters,
        [],
        [],
    )
