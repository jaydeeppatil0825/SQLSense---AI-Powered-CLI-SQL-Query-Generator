"""
core/context_retriever.py
=========================
Dynamic context retrieval for the query pipeline.

This module collects schema-driven evidence for planning without choosing
final tables, columns, formulas, or SQL.
"""

from __future__ import annotations

from collections import deque
import re
from typing import Any, Dict, Optional

from kb_pipeline.schema_facts import (
    column_ai_metadata,
    column_business_description,
    column_business_terms,
    column_is_date,
    column_is_dimension,
    column_is_measure,
    column_planner_roles,
    column_sample_values,
    resolved_semantic_type,
)
from utils.logger import get_logger
from kb_pipeline.relationship_graph import build_relationship_graph, find_all_possible_join_paths

logger = get_logger()


def retrieve_context(
    normalized_question: str,
    intent: Dict[str, Any],
    knowledge_base: Dict[str, Any],
    business_glossary: Optional[Dict[str, Any]] = None,
    vector_retriever: Optional[Any] = None,
) -> Dict[str, Any]:
    """Collect dynamic schema/glossary/vector evidence for the planner."""
    normalized_question = _normalize_text(normalized_question)
    query_terms = _query_terms(normalized_question, intent)

    glossary = business_glossary or {}
    kb_table_matches = _match_tables(query_terms, knowledge_base)
    matched_tables = list(kb_table_matches)
    matched_columns = _match_columns(query_terms, knowledge_base)
    matched_glossary_terms = _match_glossary_terms(query_terms, glossary)
    glossary_table_boosts, glossary_column_boosts = _glossary_mappings(matched_glossary_terms)
    vector_context = _vector_context(normalized_question, vector_retriever)

    matched_tables = _merge_table_candidates(
        matched_tables,
        glossary_table_boosts,
        vector_context.get("matched_tables", []),
    )
    matched_columns = _merge_column_candidates(
        matched_columns,
        glossary_column_boosts,
        vector_context.get("matched_columns", []),
    )

    strong_primary_table = _strong_direct_primary_table_match(
        normalized_question,
        intent,
        query_terms,
        kb_table_matches,
    )
    if strong_primary_table:
        matched_tables = [
            entry for entry in matched_tables
            if str(entry.get("table") or "").strip() == strong_primary_table
        ]
        matched_columns = [
            entry for entry in matched_columns
            if str(entry.get("table") or "").strip() == strong_primary_table
        ]
        matched_glossary_terms = [
            entry for entry in matched_glossary_terms
            if any(
                str(mapping.get("table") or "").strip() == strong_primary_table
                for mapping in (entry.get("mapped_columns") or [])
            )
        ]

    matched_relationships = _match_relationships(
        knowledge_base,
        matched_tables,
        vector_context.get("matched_relationships", []),
    )
    possible_join_paths = _possible_join_paths(knowledge_base, matched_tables)

    measure_candidates = _candidate_columns(
        intent.get("requested_metrics") or [],
        matched_columns,
        require_measure=True,
    )
    dimension_candidates = _candidate_columns(
        intent.get("requested_dimensions") or [],
        matched_columns,
        require_dimension=True,
    )
    filter_candidates = _candidate_columns(
        intent.get("requested_filters") or [],
        matched_columns,
        require_filter=True,
    )

    retrieval_sources = _unique(
        [entry.get("source") for entry in matched_tables]
        + [entry.get("source") for entry in matched_columns]
        + [entry.get("source") for entry in matched_glossary_terms]
        + [entry.get("source") for entry in matched_relationships]
        + vector_context.get("retrieval_sources", [])
    )
    confidence = _overall_confidence(
        matched_tables=matched_tables,
        matched_columns=matched_columns,
        matched_glossary_terms=matched_glossary_terms,
        matched_relationships=matched_relationships,
    )

    return {
        "query_terms": query_terms,
        "matched_tables": matched_tables,
        "matched_columns": matched_columns,
        "matched_glossary_terms": matched_glossary_terms,
        "matched_relationships": matched_relationships,
        "possible_join_paths": possible_join_paths,
        "measure_candidates": measure_candidates,
        "dimension_candidates": dimension_candidates,
        "filter_candidates": filter_candidates,
        "retrieval_sources": retrieval_sources,
        "confidence": confidence,
    }


def _is_simple_single_table_question(intent: Dict[str, Any], normalized_question: str) -> bool:
    intent_type = str((intent or {}).get("intent_type") or "").strip().lower()
    requested_metrics = list((intent or {}).get("requested_metrics") or [])
    requested_filters = list((intent or {}).get("requested_filters") or [])
    needs_grouping = bool((intent or {}).get("needs_grouping"))
    needs_join = (intent or {}).get("needs_join")

    return (
        intent_type in {"list", "count", "sorted_list"}
        and not requested_metrics
        and not requested_filters
        and not needs_grouping
        and not needs_join
        and " by " not in normalized_question
        and " per " not in normalized_question
        and not re.search(r"\b[a-z0-9_ ]+\s+wise\b", normalized_question)
    )


def _strong_direct_primary_table_match(
    normalized_question: str,
    intent: Dict[str, Any],
    query_terms: list[str],
    kb_table_matches: list[Dict[str, Any]],
) -> str | None:
    if not _is_simple_single_table_question(intent, normalized_question):
        return None
    if not kb_table_matches:
        return None

    direct_matches = []
    for entry in kb_table_matches:
        table_name = str(entry.get("table") or "").strip()
        if not table_name:
            continue
        identifier_score = 0.0
        identifier_evidence: list[str] = []
        for term in query_terms:
            score, evidence = _score_identifier_match(term, table_name, _humanize(table_name))
            if score > identifier_score:
                identifier_score = score
                identifier_evidence = evidence
        if identifier_score < 0.84:
            continue
        if not set(identifier_evidence) & {
            "exact_phrase",
            "exact_token_match",
            "all_term_tokens_present",
            "identifier_token_match",
        }:
            continue
        direct_matches.append({**entry, "score": identifier_score})

    if not direct_matches:
        return None

    direct_matches.sort(key=lambda item: (-float(item.get("score") or 0.0), str(item.get("table") or "")))
    top = direct_matches[0]
    second_score = float(direct_matches[1].get("score") or 0.0) if len(direct_matches) > 1 else 0.0
    top_table = str(top.get("table") or "").strip()
    top_score = float(top.get("score") or 0.0)

    if len(direct_matches) == 1:
        return top_table
    if top_score >= second_score + 0.15:
        return top_table
    return None


def _score_identifier_match(term: str, *texts: str) -> tuple[float, list[str]]:
    """
    Score identifier-only matches more strongly than generic substring matches.

    This keeps simple singular/plural entity queries like "partner" -> "partners"
    on the direct-table path instead of falling through to glossary-expanded
    related tables.
    """
    normalized_term = _normalize_text(term)
    term_tokens = set(_tokenize(term))
    if not normalized_term or not term_tokens:
        return 0.0, []

    best_score = 0.0
    evidences: list[str] = []

    for text in texts:
        normalized_text = _normalize_text(text)
        text_tokens = set(_tokenize(text))
        if not normalized_text or not text_tokens:
            continue

        if normalized_text == normalized_term:
            return 1.0, ["exact_phrase"]
        if term_tokens == text_tokens:
            return 0.92, ["exact_token_match"]
        if term_tokens <= text_tokens:
            if len(term_tokens) == 1:
                local_score = 0.91
                local_evidence = ["identifier_token_match"]
            else:
                local_score = 0.88
                local_evidence = ["all_term_tokens_present"]
        else:
            local_score, local_evidence = _score_match(term, text)
        if local_score > best_score:
            best_score = local_score
            evidences = local_evidence

    return round(best_score, 4), _unique(evidences)


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def _humanize(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", str(value or "").lower())).strip()


def _tokenize(value: str) -> list[str]:
    tokens = [token for token in re.split(r"[^a-z0-9_]+", _normalize_text(value)) if token]
    expanded = []
    for token in tokens:
        expanded.append(token)
        if token.endswith("ies") and len(token) > 3:
            expanded.append(token[:-3] + "y")
        elif token.endswith("ses") and len(token) > 3:
            expanded.append(token[:-2])
        elif token.endswith("s") and not token.endswith("ss") and len(token) > 1:
            expanded.append(token[:-1])
    return _unique(expanded)


def _unique(values: list[Any]) -> list[Any]:
    seen = set()
    result = []
    for value in values:
        if value in (None, "", [], {}):
            continue
        key = str(value).lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(value)
    return result


def _score_text(term_tokens: list[str], *texts: str) -> float:
    score = 0.0
    for text in texts:
        text_tokens = set(_tokenize(text))
        if not text_tokens:
            continue
        overlap = set(term_tokens) & text_tokens
        if not overlap:
            continue
        score = max(score, len(overlap) / max(len(set(term_tokens)), 1))
    return round(score, 4)


def _score_match(term: str, *texts: str) -> tuple[float, list[str]]:
    normalized_term = _normalize_text(term)
    term_tokens = set(_tokenize(term))
    if not normalized_term or not term_tokens:
        return 0.0, []

    best_score = 0.0
    evidences: list[str] = []

    for text in texts:
        normalized_text = _normalize_text(text)
        if not normalized_text:
            continue
        text_tokens = set(_tokenize(text))
        if not text_tokens:
            continue

        local_score = 0.0
        local_evidence: list[str] = []

        if normalized_text == normalized_term:
            local_score = 1.0
            local_evidence.append("exact_phrase")
        elif normalized_term in normalized_text:
            local_score = max(local_score, 0.9 if len(term_tokens) > 1 else 0.82)
            local_evidence.append("phrase_in_text")
        elif text_tokens == term_tokens:
            local_score = max(local_score, 0.92)
            local_evidence.append("exact_token_match")
        elif term_tokens <= text_tokens:
            local_score = max(local_score, 0.84)
            local_evidence.append("all_term_tokens_present")
        else:
            overlap = term_tokens & text_tokens
            if overlap:
                overlap_ratio = len(overlap) / max(len(term_tokens), 1)
                if overlap_ratio >= 0.75:
                    local_score = max(local_score, 0.72)
                    local_evidence.append("strong_token_overlap")
                elif overlap_ratio >= 0.5:
                    local_score = max(local_score, 0.52)
                    local_evidence.append("partial_token_overlap")
                elif len(overlap) == 1:
                    local_score = max(local_score, 0.28)
                    local_evidence.append("single_token_overlap")

        if local_score > best_score:
            best_score = local_score
            evidences = local_evidence

    return round(best_score, 4), _unique(evidences)


def _query_terms(normalized_question: str, intent: Dict[str, Any]) -> list[str]:
    terms = []
    for key in ("raw_business_terms", "requested_metrics", "requested_dimensions", "requested_filters"):
        values = intent.get(key) or []
        if isinstance(values, str):
            values = [values]
        for value in values:
            cleaned = _humanize(value)
            if cleaned:
                terms.append(cleaned)
    if not terms:
        terms.append(_humanize(normalized_question))
    return _unique(terms)


def _match_tables(query_terms: list[str], knowledge_base: Dict[str, Any]) -> list[Dict[str, Any]]:
    matches = []
    for table_name, table_data in knowledge_base.items():
        related_tables = []
        for foreign_key in table_data.get("foreign_keys", []) or []:
            related_tables.append(str(foreign_key.get("referenced_table", "") or ""))
        for relationship in table_data.get("relationships", []) or []:
            related_tables.extend(
                [
                    str(relationship.get("from_table", "") or ""),
                    str(relationship.get("to_table", "") or ""),
                ]
            )
        search_texts = [
            table_name,
            _humanize(table_name),
            table_data.get("business_description", ""),
            table_data.get("business_purpose", ""),
            *list(table_data.get("business_terms", []) or []),
            *related_tables,
        ]
        best_score = 0.0
        matched_terms = []
        evidence_sources: list[str] = []
        for term in query_terms:
            score, term_evidence = _score_match(term, *search_texts)
            if score <= 0:
                continue
            best_score = max(best_score, score)
            matched_terms.append(term)
            evidence_sources.extend(term_evidence)
        if best_score <= 0:
            continue
        matches.append(
            {
                "table": table_name,
                "score": round(best_score, 4),
                "matched_terms": _unique(matched_terms),
                "evidence_sources": _unique(["runtime_table_name", *evidence_sources]),
                "source": "kb_identifier",
            }
        )
    matches.sort(key=lambda item: (-item["score"], item["table"]))
    return matches[:10]


def _match_columns(query_terms: list[str], knowledge_base: Dict[str, Any]) -> list[Dict[str, Any]]:
    matches = []
    for table_name, table_data in knowledge_base.items():
        for column in table_data.get("columns", []):
            column_name = str(column.get("name", "")).strip()
            if not column_name:
                continue
            ai_metadata = column_ai_metadata(column)
            planner_roles = column_planner_roles(column)
            search_texts = [
                column_name,
                _humanize(column_name),
                table_name,
                _humanize(table_name),
                column_business_description(column),
                *column_business_terms(column),
                *column_sample_values(column),
                ai_metadata.get("ai_semantic_type", ""),
                *[role_name.replace("_", " ") for role_name, enabled in planner_roles.items() if enabled],
            ]
            best_score = 0.0
            matched_terms = []
            evidence_sources: list[str] = []
            for term in query_terms:
                score, term_evidence = _score_match(term, *search_texts)
                if score <= 0:
                    continue
                best_score = max(best_score, score)
                matched_terms.append(term)
                evidence_sources.extend(term_evidence)
            if best_score <= 0:
                continue
            matches.append(
                {
                    "table": table_name,
                    "column": column_name,
                    "semantic_type": resolved_semantic_type(column),
                    "core_semantic_type": str(column.get("semantic_type", "")).strip().lower(),
                    "is_measure": column_is_measure(column),
                    "is_dimension": column_is_dimension(column),
                    "is_date": column_is_date(column),
                    "score": round(best_score, 4),
                    "matched_terms": _unique(matched_terms),
                    "evidence_sources": _unique(["runtime_column_name", *evidence_sources]),
                    "source": "kb_identifier",
                }
            )
    matches.sort(key=lambda item: (-item["score"], item["table"], item["column"]))
    return matches[:20]


def _match_glossary_terms(query_terms: list[str], glossary: Dict[str, Any]) -> list[Dict[str, Any]]:
    matches = []
    for term, entry in glossary.items():
        primary_terms = list(entry.get("primary_terms", []) or entry.get("business_terms", []) or [])
        search_texts = [
            term,
            entry.get("description", ""),
            *primary_terms,
        ]
        best_score = 0.0
        matched_terms = []
        evidence_sources: list[str] = []
        for query_term in query_terms:
            score, term_evidence = _score_match(query_term, *search_texts)
            if score <= 0:
                continue
            best_score = max(best_score, score)
            matched_terms.append(query_term)
            evidence_sources.extend(term_evidence)
        if best_score <= 0:
            continue
        matches.append(
            {
                "term": term,
                "score": round(best_score, 4),
                "matched_terms": _unique(matched_terms),
                "mapped_tables": list(entry.get("mapped_tables", []) or []),
                "mapped_columns": list(entry.get("mapped_columns", []) or []),
                "related_tables": list(entry.get("related_tables", []) or []),
                "evidence_sources": _unique(["dynamic_glossary", *evidence_sources]),
                "source": "glossary",
            }
        )
    matches.sort(key=lambda item: (-item["score"], item["term"]))
    return matches[:10]


def _glossary_mappings(glossary_matches: list[Dict[str, Any]]) -> tuple[list[Dict[str, Any]], list[Dict[str, Any]]]:
    table_boosts = []
    column_boosts = []
    for entry in glossary_matches:
        term = entry.get("term")
        score = float(entry.get("score") or 0.0)
        for mapping in entry.get("mapped_columns", []) or []:
            table_name = mapping.get("table")
            column_name = mapping.get("column")
            if table_name:
                table_boosts.append(
                    {
                        "table": table_name,
                        "score": round(min(score + 0.15, 1.0), 4),
                        "matched_terms": [term] if term else [],
                        "evidence_sources": ["dynamic_glossary", "glossary_table_mapping"],
                        "source": "glossary",
                    }
                )
            if table_name and column_name:
                column_boosts.append(
                    {
                        "table": table_name,
                        "column": column_name,
                        "score": round(min(score + 0.15, 1.0), 4),
                        "matched_terms": [term] if term else [],
                        "evidence_sources": ["dynamic_glossary", "glossary_column_mapping"],
                        "source": "glossary",
                    }
                )
    return table_boosts, column_boosts


def _vector_context(normalized_question: str, vector_retriever: Optional[Any]) -> Dict[str, Any]:
    if vector_retriever is None:
        return {"retrieval_sources": []}

    try:
        table_details = vector_retriever.get_relevant_table_details(normalized_question, top_k=6)
        columns = vector_retriever.get_relevant_columns(normalized_question, top_k=10)
        glossary_terms = vector_retriever.get_relevant_glossary_terms(normalized_question, top_k=6)
        relationships = vector_retriever.get_relevant_relationships(normalized_question, top_k=6)
    except Exception as exc:
        logger.debug(f"Context retriever vector fallback activated: {exc}")
        return {"retrieval_sources": []}

    return {
        "matched_tables": [
            {
                "table": entry.get("table_name"),
                "score": round(float(entry.get("score") or 0.0), 4),
                "matched_terms": [],
                "evidence_sources": ["vector_retrieval"],
                "source": "vector",
            }
            for entry in table_details
            if entry.get("table_name")
        ],
        "matched_columns": [
            {
                "table": entry.get("table_name"),
                "column": entry.get("column_name"),
                "semantic_type": entry.get("semantic_type"),
                "core_semantic_type": entry.get("core_semantic_type"),
                "is_measure": bool(entry.get("is_measure")),
                "is_dimension": bool(entry.get("is_dimension")),
                "is_date": bool(entry.get("is_date")),
                "score": 0.75,
                "matched_terms": [],
                "evidence_sources": ["vector_retrieval"],
                "source": "vector",
            }
            for entry in columns
            if entry.get("table_name") and entry.get("column_name")
        ],
        "matched_glossary_terms": glossary_terms,
        "matched_relationships": [
            {
                **entry,
                "evidence_sources": _unique(["vector_retrieval", *(entry.get("evidence_sources") or [])]),
                "source": "vector",
            }
            for entry in relationships
            if entry.get("from_table") and entry.get("to_table")
        ],
        "retrieval_sources": ["vector"] if any([table_details, columns, glossary_terms, relationships]) else [],
    }


def _merge_table_candidates(*candidate_groups: list[Dict[str, Any]]) -> list[Dict[str, Any]]:
    merged: dict[str, Dict[str, Any]] = {}
    for group in candidate_groups:
        for candidate in group:
            table_name = str(candidate.get("table", "")).strip()
            if not table_name:
                continue
            existing = merged.get(table_name)
            if not existing:
                merged[table_name] = dict(candidate)
                merged[table_name]["matched_terms"] = _unique(candidate.get("matched_terms", []))
                merged[table_name]["evidence_sources"] = _unique(candidate.get("evidence_sources", []))
                continue
            existing["score"] = max(float(existing.get("score") or 0.0), float(candidate.get("score") or 0.0))
            existing["matched_terms"] = _unique(list(existing.get("matched_terms", [])) + list(candidate.get("matched_terms", [])))
            existing["evidence_sources"] = _unique(list(existing.get("evidence_sources", [])) + list(candidate.get("evidence_sources", [])))
            existing["source"] = existing.get("source") if existing.get("source") == "vector" else candidate.get("source", existing.get("source"))
    results = list(merged.values())
    results.sort(key=lambda item: (-float(item.get("score") or 0.0), item["table"]))
    return results[:10]


def _merge_column_candidates(*candidate_groups: list[Dict[str, Any]]) -> list[Dict[str, Any]]:
    merged: dict[tuple[str, str], Dict[str, Any]] = {}
    for group in candidate_groups:
        for candidate in group:
            table_name = str(candidate.get("table", "")).strip()
            column_name = str(candidate.get("column", "")).strip()
            if not table_name or not column_name:
                continue
            key = (table_name, column_name)
            existing = merged.get(key)
            if not existing:
                merged[key] = dict(candidate)
                merged[key]["matched_terms"] = _unique(candidate.get("matched_terms", []))
                merged[key]["evidence_sources"] = _unique(candidate.get("evidence_sources", []))
                continue
            existing["score"] = max(float(existing.get("score") or 0.0), float(candidate.get("score") or 0.0))
            existing["matched_terms"] = _unique(list(existing.get("matched_terms", [])) + list(candidate.get("matched_terms", [])))
            existing["evidence_sources"] = _unique(list(existing.get("evidence_sources", [])) + list(candidate.get("evidence_sources", [])))
            existing["is_measure"] = bool(existing.get("is_measure")) or bool(candidate.get("is_measure"))
            existing["is_dimension"] = bool(existing.get("is_dimension")) or bool(candidate.get("is_dimension"))
            existing["is_date"] = bool(existing.get("is_date")) or bool(candidate.get("is_date"))
            existing["source"] = existing.get("source") if existing.get("source") == "vector" else candidate.get("source", existing.get("source"))
    results = list(merged.values())
    results.sort(key=lambda item: (-float(item.get("score") or 0.0), item["table"], item["column"]))
    return results[:20]


def _relationship_edges(knowledge_base: Dict[str, Any]) -> list[Dict[str, Any]]:
    edges = []
    seen = set()
    for table_name, table_data in knowledge_base.items():
        for foreign_key in table_data.get("foreign_keys", []) or []:
            from_column = foreign_key.get("column") or foreign_key.get("from_column")
            to_table = foreign_key.get("referenced_table") or foreign_key.get("to_table")
            to_column = foreign_key.get("referenced_column") or foreign_key.get("to_column")
            signature = (table_name, from_column, to_table, to_column)
            if not from_column or not to_table or not to_column or signature in seen:
                continue
            seen.add(signature)
            edges.append(
                {
                    "from_table": table_name,
                    "from_column": from_column,
                    "to_table": to_table,
                    "to_column": to_column,
                    "join_condition": f"{table_name}.{from_column} = {to_table}.{to_column}",
                    "source": "fk_relationship",
                }
            )
        for relationship in table_data.get("relationships", []) or []:
            from_table = relationship.get("from_table") or table_name
            from_column = relationship.get("from_column")
            to_table = relationship.get("to_table")
            to_column = relationship.get("to_column")
            signature = (from_table, from_column, to_table, to_column)
            if not from_column or not to_table or not to_column or signature in seen:
                continue
            seen.add(signature)
            edges.append(
                {
                    "from_table": from_table,
                    "from_column": from_column,
                    "to_table": to_table,
                    "to_column": to_column,
                    "join_condition": relationship.get("join_condition") or f"{from_table}.{from_column} = {to_table}.{to_column}",
                    "source": "relationship_metadata",
                }
            )
    return edges


def _match_relationships(
    knowledge_base: Dict[str, Any],
    matched_tables: list[Dict[str, Any]],
    vector_relationships: list[Dict[str, Any]],
) -> list[Dict[str, Any]]:
    matched_names = {entry.get("table") for entry in matched_tables if entry.get("table")}
    score_by_table = {
        str(entry.get("table")): float(entry.get("score") or 0.0)
        for entry in matched_tables
        if entry.get("table")
    }
    relationships = []
    for edge in _relationship_edges(knowledge_base):
        if edge["from_table"] in matched_names or edge["to_table"] in matched_names:
            support = round(score_by_table.get(edge["from_table"], 0.0) + score_by_table.get(edge["to_table"], 0.0), 4)
            relationships.append(
                {
                    **edge,
                    "score": support,
                    "evidence_sources": _unique(
                        [
                            "fk_relationship_context" if edge.get("source") == "fk_relationship" else "relationship_context",
                            "matched_table_support",
                        ]
                    ),
                }
            )
    for edge in vector_relationships:
        if not edge.get("from_table") or not edge.get("to_table"):
            continue
        support = round(score_by_table.get(str(edge.get("from_table")), 0.0) + score_by_table.get(str(edge.get("to_table")), 0.0), 4)
        relationships.append(
            {
                **edge,
                "score": max(float(edge.get("score") or 0.0), support),
                "evidence_sources": _unique(list(edge.get("evidence_sources", [])) + ["matched_table_support"]),
            }
        )
    deduped = []
    seen = set()
    for edge in relationships:
        signature = (edge.get("from_table"), edge.get("from_column"), edge.get("to_table"), edge.get("to_column"))
        if signature in seen:
            continue
        seen.add(signature)
        deduped.append(edge)
    deduped.sort(
        key=lambda edge: (
            -float(edge.get("score") or 0.0),
            str(edge.get("from_table") or ""),
            str(edge.get("to_table") or ""),
        )
    )
    return deduped[:12]


def _possible_join_paths(knowledge_base: Dict[str, Any], matched_tables: list[Dict[str, Any]]) -> list[Dict[str, Any]]:
    """Find possible join paths between matched tables using dynamic relationship graph."""
    selected_tables = [entry.get("table") for entry in matched_tables[:4] if entry.get("table")]
    support_by_table = {
        str(entry.get("table")): float(entry.get("score") or 0.0)
        for entry in matched_tables
        if entry.get("table")
    }
    if len(selected_tables) < 2:
        return []

    # Build relationship graph dynamically from knowledge base
    graph = build_relationship_graph(knowledge_base)
    
    # Find join paths between all pairs of selected tables
    paths = []
    seen = set()
    for index, start_table in enumerate(selected_tables):
        for target_table in selected_tables[index + 1:]:
            # Find all possible paths between the two tables
            table_paths = find_all_possible_join_paths(
                graph, start_table, target_table, max_paths=3, max_hops=4
            )
            
            for path_result in table_paths:
                if not path_result["resolved"]:
                    continue
                
                # Convert to expected format
                signature = (start_table, target_table, tuple(path_result["path"]))
                if signature in seen:
                    continue
                seen.add(signature)
                
                # Build edge list in the expected format
                edges = []
                for i, (from_col, to_col) in enumerate(path_result["join_columns"]):
                    from_table = path_result["path"][i]
                    to_table = path_result["path"][i + 1]
                    edges.append({
                        "from_table": from_table,
                        "from_column": from_col,
                        "to_table": to_table,
                        "to_column": to_col,
                        "join_condition": f"{from_table}.{from_col} = {to_table}.{to_col}",
                        "source": path_result["edge_sources"][i],
                        "confidence": path_result["confidences"][i],
                    })
                
                paths.append({
                    "from_table": start_table,
                    "to_table": target_table,
                    "path": edges,
                    "length": path_result["path_length"],
                    "total_confidence": path_result["total_confidence"],
                    "support_score": round(
                        sum(support_by_table.get(table_name, 0.0) for table_name in path_result["path"]),
                        4,
                    ),
                    "edge_sources": path_result["edge_sources"],
                    "evidence_sources": _unique(["join_path", "relationship_graph", *path_result["edge_sources"]]),
                })
    
    # Sort by: length (shorter first), total_confidence (higher first), FK preference
    def path_sort_key(item: Dict[str, Any]) -> tuple:
        fk_count = sum(1 for source in item.get("edge_sources", []) if source == "foreign_key")
        return (
            item["length"],
            -item.get("support_score", 0.0),
            -item.get("total_confidence", 0.0),
            -fk_count,
        )
    
    paths.sort(key=path_sort_key)
    return paths[:8]


def _candidate_columns(
    requested_terms: list[str],
    matched_columns: list[Dict[str, Any]],
    *,
    require_measure: bool = False,
    require_dimension: bool = False,
    require_filter: bool = False,
) -> list[Dict[str, Any]]:
    candidates = []
    requested_terms = [_humanize(term) for term in requested_terms if _humanize(term)]
    if require_filter and not requested_terms:
        return []
    for entry in matched_columns:
        if require_measure and not bool(entry.get("is_measure")):
            continue
        if require_dimension and not (
            bool(entry.get("is_dimension"))
            or str(entry.get("semantic_type") or "") in {"name", "text", "category_candidate", "text_candidate", "date"}
        ):
            continue
        if require_filter and requested_terms:
            matched_terms = {str(term).lower() for term in entry.get("matched_terms", [])}
            if not any(term.lower() in matched_terms for term in requested_terms):
                continue
        candidates.append(entry)
    if not candidates and requested_terms:
        for entry in matched_columns:
            matched_terms = {str(term).lower() for term in entry.get("matched_terms", [])}
            if any(term.lower() in matched_terms for term in requested_terms):
                candidates.append(entry)
    deduped = []
    seen = set()
    for entry in candidates:
        key = (entry.get("table"), entry.get("column"))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(entry)
    return deduped[:10]


def _overall_confidence(
    *,
    matched_tables: list[Dict[str, Any]],
    matched_columns: list[Dict[str, Any]],
    matched_glossary_terms: list[Dict[str, Any]],
    matched_relationships: list[Dict[str, Any]],
) -> float:
    components = []
    if matched_tables:
        components.append(float(matched_tables[0].get("score") or 0.0))
    if matched_columns:
        components.append(float(matched_columns[0].get("score") or 0.0))
    if matched_glossary_terms:
        components.append(float(matched_glossary_terms[0].get("score") or 0.0))
    if matched_relationships:
        components.append(min(0.85, float(matched_relationships[0].get("score") or 0.0) or 0.5))
    if not components:
        return 0.25
    return round(sum(components) / len(components), 2)
