"""
ai/prompt_builder.py
====================
Build the structured prompt sent to the AI backend for SQL generation.

The prompt is fully dynamic:
- Knowledge base is the source of truth
- Business glossary is derived from the active KB
- Relationships come only from the active schema context
- No fixed demo or ERP-specific table guidance is injected

This module belongs to the SQL Generation Pipeline and must reflect only
runtime planning evidence plus trusted KB/glossary/relationship context.
"""

from __future__ import annotations

import json
import os
import re
from copy import deepcopy
from typing import Any

from kb_pipeline.schema_facts import column_profile_facts


_QUERY_RULES = """
SQL query construction rules:
  - The first non-whitespace characters in the output must be SELECT.
  - SELECT must include at least one valid column, expression, or aggregate.
  - FROM must name a real table from the schema context.
  - Every JOIN must name a real table and include an ON predicate unless it is an explicit CROSS JOIN.
  - JOIN ON predicates must use existing columns on both joined tables.
  - Use aggregate functions only when the structured plan or evidence requires them.
  - Always add GROUP BY when mixing aggregate functions with non-aggregate columns.
  - Add ORDER BY only when the structured plan or explicit user request requires it.
  - ORDER BY must reference a selected column or alias.
  - WHERE, GROUP BY, HAVING, and ORDER BY must use only schema columns or selected aliases.
  - Use WHERE only for filters explicitly supported by the supplied context.
  - Use JOIN only when a supplied relationship or join path supports it.
  - Never invent table names or column names.
  - Never invent formulas. Use derived expressions only when formula evidence is supplied.
  - Prefer fully qualified column references when multiple tables are involved.
  - When using aliases in multi-table SQL, qualify every non-aggregate column with its alias.
  - Do not include explanations or comments in the SQL output.
""".strip()

_SAFETY_RULES = """
Safety rules:
  - Return ONLY a single SELECT statement.
  - Do NOT use: INSERT, UPDATE, DELETE, DROP, ALTER, TRUNCATE, CREATE, REPLACE, GRANT, REVOKE.
  - Do NOT include markdown fences, comments, or explanations.
  - Use MySQL identifier quoting only when an actual schema identifier requires it.
  - Do NOT include multiple statements separated by semicolons.
  - A trailing semicolon on the final statement is allowed.
""".strip()

_AI_PLAN_RULES = """
Structured-plan execution rules:
  - Treat the structured query plan as authoritative.
  - Prefer the selected tables first.
  - Prefer the selected columns and runtime candidates when choosing measures, dimensions, filters, and grouping columns.
  - If formula evidence is missing for a derived metric, do not invent one.
  - If no safe join path is supplied for a multi-table query, do not invent joins.
  - When the plan includes filters, apply only the supplied filters instead of returning a raw table dump.
  - Use computed join predicates exactly when they are supplied.
  - Do NOT use SELECT * for totals, counts, balances, grouped analysis, or other business-style questions unless the plan clearly indicates a raw record listing.
""".strip()

_SEMANTIC_GUIDANCE = """
Semantic type guidance:
  - semantic_type=money: valid numeric measure when runtime evidence selects it.
  - semantic_type=quantity: valid numeric measure when runtime evidence selects it.
  - semantic_type=date: valid date dimension or filter when runtime evidence selects it.
  - semantic_type=status: valid filter or grouping column when runtime evidence selects it.
  - semantic_type=name: valid display or grouping column when runtime evidence selects it.
  - semantic_type=code or semantic_type=id: valid identifier or join column when runtime evidence selects it.
  - semantic_type=percentage: valid numeric measure when runtime evidence selects it.
""".strip()


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip().lower())


def _query_shape(
    query_plan: dict | None,
    intent: dict | None,
    selected_tables: list[dict] | None,
    join_paths: list[dict] | None,
    formula_evidence: list[dict] | None,
) -> str:
    """Classify the SQL shape generically from pipeline evidence."""
    plan = query_plan or {}
    intent_payload = intent or {}
    planner_intent = str(plan.get("intent") or "").strip().lower()
    intent_type = str(intent_payload.get("intent_type") or "").strip().lower()
    table_count = len([entry for entry in (selected_tables or []) if entry.get("table")])
    has_join = bool(join_paths)
    has_grouping = bool(plan.get("grouping") or plan.get("dimension") or intent_payload.get("needs_grouping"))
    has_aggregation = bool(
        planner_intent in {"count", "total", "average", "top_n"}
        or intent_payload.get("needs_aggregation")
    )
    if has_grouping and plan.get("metric"):
        has_aggregation = True
    has_ranking = bool(planner_intent == "top_n" or intent_type == "ranking")
    has_formula = bool(formula_evidence)
    has_filters = bool(plan.get("filters"))

    if planner_intent == "count" and table_count <= 1 and not has_join:
        return "single_table_count"
    if planner_intent == "list" and table_count <= 1 and not has_join and not has_grouping and not has_aggregation:
        return "single_table_list"
    if has_formula and has_grouping:
        return "derived_grouped_aggregate"
    if has_ranking and (has_grouping or has_aggregation):
        return "ranking_grouped_aggregate"
    if has_grouping and has_aggregation:
        return "grouped_aggregate"
    if has_join and has_aggregation:
        return "joined_aggregate"
    if has_join and has_filters:
        return "joined_filtered_select"
    if has_join:
        return "joined_select"
    if has_aggregation:
        return "single_table_aggregate"
    if has_filters:
        return "single_table_filtered_list"
    return "generic_select"


def _extract_limit(user_question: str) -> int | None:
    match = re.search(
        r"\b(?:top|first|last|latest|recent|limit|show|get|return|fetch)\s+(\d+)\b"
        r"|\b(\d+)\s+(?:rows?|records?|results?|items?)\b",
        user_question,
        re.IGNORECASE,
    )
    if match:
        return int(match.group(1) or match.group(2))
    return None


def _build_retrieved_glossary_section(retrieved_context: dict | None) -> list[str]:
    matched_terms = list((retrieved_context or {}).get("matched_glossary_terms") or [])
    if not matched_terms:
        return ["Retrieved glossary evidence: no matched glossary terms were supplied by the pipeline.", ""]

    lines = ["Retrieved glossary evidence from pipeline:"]
    for entry in matched_terms[:8]:
        if not isinstance(entry, dict):
            continue
        term = str(entry.get("term", "")).strip()
        if term:
            lines.append(f"  term: {term}")
        description = str(entry.get("description", "")).strip()
        if description:
            lines.append(f"    description: {description}")
        mappings = []
        for mapping in entry.get("mapped_columns", [])[:5]:
            if not isinstance(mapping, dict):
                continue
            table_name = str(mapping.get("table", "")).strip()
            column_name = str(mapping.get("column", "")).strip()
            if table_name and column_name:
                mappings.append(f"{table_name}.{column_name}")
        if mappings:
            lines.append(f"    mapped_columns: {', '.join(mappings)}")
    lines.append("")
    return lines


def _get_relevant_glossary_terms(
    user_question: str,
    knowledge_base: dict,
    glossary_path: str = "semantic/business_glossary.json",
    glossary: dict | None = None,
) -> str:
    """
    Backward-compatible glossary section builder used by legacy prompt tests.

    Returns a human-readable glossary block derived only from the supplied
    glossary or runtime schema-backed glossary file when available.
    """
    loaded_glossary = glossary
    if loaded_glossary is None:
        try:
            with open(glossary_path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
            if isinstance(payload, dict):
                loaded_glossary = payload
            else:
                loaded_glossary = {}
        except Exception:
            loaded_glossary = {}

    matches = []
    question_normalized = _normalize(user_question)
    question_terms = set(re.split(r"[^a-z0-9]+", question_normalized))
    for term, entry in (loaded_glossary or {}).items():
        if not isinstance(entry, dict):
            continue
        normalized_term = _normalize(term)
        aliases = entry.get("business_terms", []) or []
        alias_hit = any(set(re.split(r"[^a-z0-9]+", _normalize(alias))) <= question_terms for alias in aliases if _normalize(alias))
        if normalized_term and normalized_term in question_normalized or alias_hit:
            matches.append((term, entry))

    lines = ["Business glossary:"]
    if not matches:
        lines.append("  No relevant glossary terms matched. Use only runtime schema identifiers and selected evidence.")
        return "\n".join(lines)

    for term, entry in matches[:8]:
        lines.append(f"  TERM: {term}")
        description = str(entry.get("description", "")).strip()
        if description:
            lines.append(f"    Description: {description}")
        mappings = []
        for mapping in entry.get("mapped_columns", [])[:8]:
            if not isinstance(mapping, dict):
                continue
            table_name = str(mapping.get("table", "")).strip()
            column_name = str(mapping.get("column", "")).strip()
            if table_name and column_name and table_name in knowledge_base:
                mappings.append(f"{table_name}.{column_name}")
        if mappings:
            lines.append(f"    Mapped columns: {', '.join(mappings)}")
    return "\n".join(lines)


def _join_skeletons(join_paths: list[dict] | None) -> list[str]:
    skeletons: list[str] = []
    for join_path in join_paths or []:
        if not isinstance(join_path, dict):
            continue
        parts: list[str] = []
        current_table = str(join_path.get("from_table", "")).strip()
        if current_table:
            parts.append(f"FROM {current_table}")
        for edge in join_path.get("path", []) or []:
            if not isinstance(edge, dict):
                continue
            join_table = str(edge.get("to_table", "")).strip()
            join_condition = str(edge.get("join_condition", "")).strip()
            if join_table and join_condition:
                parts.append(f"JOIN {join_table} ON {join_condition}")
        skeleton = " ".join(parts).strip()
        if skeleton and skeleton not in skeletons:
            skeletons.append(skeleton)
    return skeletons


def _scoped_prompt_knowledge_base(
    knowledge_base: dict,
    selected_tables: list[dict] | None,
    selected_columns: list[dict] | None,
    join_paths: list[dict] | None,
) -> dict:
    table_names: set[str] = set()
    for entry in selected_tables or []:
        table_name = str(entry.get("table", "")).strip()
        if table_name:
            table_names.add(table_name)
    for entry in selected_columns or []:
        table_name = str(entry.get("table", "")).strip()
        if table_name:
            table_names.add(table_name)
    for path in join_paths or []:
        if not isinstance(path, dict):
            continue
        for key in ("from_table", "to_table"):
            table_name = str(path.get(key, "")).strip()
            if table_name:
                table_names.add(table_name)
        for edge in path.get("path", []) or []:
            if not isinstance(edge, dict):
                continue
            for key in ("from_table", "to_table"):
                table_name = str(edge.get(key, "")).strip()
                if table_name:
                    table_names.add(table_name)
    if not table_names:
        return knowledge_base
    scoped = {
        table_name: deepcopy(table_data)
        for table_name, table_data in knowledge_base.items()
        if table_name in table_names
    }
    return scoped or knowledge_base


def _build_schema_section(knowledge_base: dict) -> list[str]:
    lines: list[str] = []
    lines.append("Database schema (use ONLY these tables and columns):")
    lines.append("")

    for table_name, table_data in knowledge_base.items():
        row_count = table_data.get("row_count", "unknown")
        lines.append(f"TABLE: {table_name}  (approx. {row_count} rows)")

        primary_keys = table_data.get("primary_keys", [])
        if primary_keys:
            lines.append(f"  Primary key(s): {', '.join(str(k) for k in primary_keys)}")

        for col in table_data.get("columns", []):
            name = col.get("name", "")
            col_type = col.get("type", "")
            nullable = "nullable" if col.get("nullable") else "not null"
            sem_type = col.get("semantic_type", "general")
            profile_facts = column_profile_facts(col)
            lines.append(
                f"  COLUMN: {name}  type={col_type}  {nullable}  semantic_type={sem_type}"
            )
            samples = [str(v) for v in (profile_facts.get("sample_values") or [])[:5] if v is not None]
            if samples:
                lines.append(f"    sample_values: {', '.join(samples)}")
            if profile_facts.get("min") is not None:
                lines.append(f"    range: {profile_facts.get('min')} .. {profile_facts.get('max')}")

        foreign_keys = table_data.get("foreign_keys", [])
        if foreign_keys:
            lines.append(f"  Relationships (JOIN hints for {table_name}):")
            for fk in foreign_keys:
                local_col = fk.get("column", "")
                ref_table = fk.get("referenced_table", "")
                ref_col = fk.get("referenced_column", "")
                lines.append(
                    f"    {table_name}.{local_col} references {ref_table}.{ref_col}"
                    f"  ->  JOIN {ref_table} ON {table_name}.{local_col} = {ref_table}.{ref_col}"
                )

        lines.append("")

    return lines


def _build_plan_section(query_plan: dict | None, selected_tables: list[dict] | None, join_paths: list[dict] | None = None) -> list[str]:
    if not query_plan and not selected_tables:
        return []

    lines: list[str] = []
    lines.append("Structured query plan:")
    if query_plan:
        lines.append(f"  intent: {query_plan.get('intent')}")
        lines.append(f"  metric: {query_plan.get('metric')}")
        lines.append(f"  dimension: {query_plan.get('dimension')}")
        lines.append(f"  filters: {query_plan.get('filters')}")
        lines.append(f"  date_range: {query_plan.get('date_range')}")
        lines.append(f"  grouping: {query_plan.get('grouping')}")
        lines.append(f"  sorting: {query_plan.get('sorting')}")
        lines.append(f"  limit: {query_plan.get('limit')}")
        lines.append(f"  semantic_hints: {sorted(query_plan.get('semantic_hints') or [])}")
        lines.append(f"  matched_glossary_terms: {query_plan.get('matched_glossary_terms')}")

    if selected_tables:
        lines.append("Relevant tables selected before SQL generation:")
        for table_entry in selected_tables:
            table_name = table_entry.get("table", "")
            confidence = table_entry.get("confidence", "unknown")
            reason = table_entry.get("reason", "")
            lines.append(f"  - {table_name} (confidence={confidence}): {reason}")
            selected_columns = table_entry.get("selected_columns", [])
            if selected_columns:
                column_parts = [
                    f"{column_entry.get('column')}[{column_entry.get('semantic_type', 'general')}]"
                    for column_entry in selected_columns[:6]
                ]
                lines.append(f"    selected columns: {', '.join(column_parts)}")

    if join_paths:
        lines.append("Computed join paths between selected tables:")
        for jp in join_paths:
            predicate_parts = []
            skeleton_parts = []
            current_table = jp.get("from_table")
            if current_table:
                skeleton_parts.append(f"FROM {current_table}")
            for edge in jp.get("path", []):
                join_condition = edge.get("join_condition")
                if join_condition:
                    predicate_parts.append(str(join_condition))
                from_table = edge.get("from_table", jp.get("from_table", ""))
                from_column = edge.get("from_column", "")
                to_table = edge.get("to_table", "")
                to_column = edge.get("to_column", "")
                if not join_condition and from_table and from_column and to_table and to_column:
                    join_condition = f"{from_table}.{from_column} = {to_table}.{to_column}"
                    predicate_parts.append(f"{from_table}.{from_column} = {to_table}.{to_column}")
                if to_table and join_condition:
                    skeleton_parts.append(f"JOIN {to_table} ON {join_condition}")

            path_str = " AND ".join(predicate_parts) if predicate_parts else "no join predicate available"
            lines.append(f"  - {jp.get('from_table')} -> {jp.get('to_table')}: {path_str}")
            if len(skeleton_parts) > 1:
                lines.append(f"    usable FROM/JOIN skeleton: {' '.join(skeleton_parts)}")

    lines.append("")
    return lines


def _build_runtime_evidence_section(
    selected_columns: list[dict] | None,
    measure_candidates: list[dict] | None,
    dimension_candidates: list[dict] | None,
    filter_candidates: list[dict] | None,
    formula_evidence: list[dict] | None,
    evidence_sources: list[str] | None,
) -> list[str]:
    lines: list[str] = []
    if not any((selected_columns, measure_candidates, dimension_candidates, filter_candidates, formula_evidence, evidence_sources)):
        return lines

    lines.append("Runtime evidence from pipeline context:")
    if selected_columns:
        rendered = [
            f"{entry.get('table')}.{entry.get('column')}[{entry.get('semantic_type', 'general')}]"
            for entry in selected_columns[:12]
            if entry.get("table") and entry.get("column")
        ]
        if rendered:
            lines.append(f"  selected_columns: {', '.join(rendered)}")
    if measure_candidates:
        rendered = [
            f"{entry.get('table')}.{entry.get('column')}[{entry.get('semantic_type', 'general')}]"
            for entry in measure_candidates[:10]
            if entry.get("table") and entry.get("column")
        ]
        if rendered:
            lines.append(f"  measure_candidates: {', '.join(rendered)}")
    if dimension_candidates:
        rendered = [
            f"{entry.get('table')}.{entry.get('column')}[{entry.get('semantic_type', 'general')}]"
            for entry in dimension_candidates[:10]
            if entry.get("table") and entry.get("column")
        ]
        if rendered:
            lines.append(f"  dimension_candidates: {', '.join(rendered)}")
    if filter_candidates:
        rendered = []
        for entry in filter_candidates[:10]:
            if entry.get("table") and entry.get("column"):
                value = entry.get("value")
                suffix = f"={value}" if value is not None else ""
                rendered.append(f"{entry.get('table')}.{entry.get('column')}{suffix}")
        if rendered:
            lines.append(f"  filter_candidates: {', '.join(rendered)}")
    if formula_evidence:
        rendered = []
        for entry in formula_evidence[:10]:
            table_name = entry.get("table")
            column_name = entry.get("primary_column") or entry.get("column")
            operation = entry.get("operation") or entry.get("formula_operation")
            secondary = entry.get("secondary_column") or entry.get("secondary")
            if table_name and column_name and operation:
                detail = f"{table_name}.{column_name} operation={operation}"
                if secondary:
                    detail += f" secondary_column={secondary}"
                if entry.get("alias"):
                    detail += f" alias={entry.get('alias')}"
                rendered.append(detail)
        if rendered:
            lines.append("  formula_evidence:")
            for item in rendered:
                lines.append(f"    - {item}")
    if evidence_sources:
        lines.append(f"  evidence_sources: {', '.join(str(value) for value in evidence_sources[:10])}")
    lines.append("")
    return lines


def _build_allowed_context_section(
    selected_tables: list[dict] | None,
    selected_columns: list[dict] | None,
    filter_candidates: list[dict] | None,
    join_paths: list[dict] | None,
    formula_evidence: list[dict] | None,
) -> list[str]:
    lines: list[str] = ["Allowed SQL generation context:"]
    table_names = [
        str(entry.get("table", "")).strip()
        for entry in (selected_tables or [])
        if str(entry.get("table", "")).strip()
    ]
    if table_names:
        lines.append("  allowed_tables:")
        for table_name in table_names[:10]:
            lines.append(f"    - {table_name}")

    allowed_columns = []
    for entry in selected_columns or []:
        table_name = str(entry.get("table", "")).strip()
        column_name = str(entry.get("column", "")).strip()
        if table_name and column_name:
            allowed_columns.append(f"{table_name}.{column_name}")
    if not allowed_columns:
        for table_entry in selected_tables or []:
            table_name = str(table_entry.get("table", "")).strip()
            if not table_name:
                continue
            for column_entry in table_entry.get("selected_columns", []) or []:
                column_name = str(column_entry.get("column", "")).strip()
                if table_name and column_name:
                    allowed_columns.append(f"{table_name}.{column_name}")
    if allowed_columns:
        lines.append("  allowed_columns:")
        for value in list(dict.fromkeys(allowed_columns))[:20]:
            lines.append(f"    - {value}")

    if filter_candidates:
        lines.append("  allowed_filter_columns:")
        for entry in filter_candidates[:10]:
            table_name = str(entry.get("table", "")).strip()
            column_name = str(entry.get("column", "")).strip()
            if table_name and column_name:
                lines.append(f"    - {table_name}.{column_name}")

    join_conditions = []
    for join_path in join_paths or []:
        for edge in join_path.get("path", []) or []:
            join_condition = str(edge.get("join_condition", "")).strip()
            if join_condition and join_condition not in join_conditions:
                join_conditions.append(join_condition)
    if join_conditions:
        lines.append("  allowed_joins:")
        for value in join_conditions[:12]:
            lines.append(f"    - {value}")

    skeletons = _join_skeletons(join_paths)
    if skeletons:
        lines.append("  allowed_from_join_skeletons:")
        for value in skeletons[:6]:
            lines.append(f"    - {value}")

    if formula_evidence:
        lines.append("  allowed_formulas:")
        for entry in formula_evidence[:10]:
            table_name = str(entry.get("table", "")).strip()
            primary = str(entry.get("primary_column") or entry.get("column") or "").strip()
            operation = str(entry.get("operation") or entry.get("formula_operation") or "").strip()
            secondary = str(entry.get("secondary_column") or entry.get("secondary") or "").strip()
            alias = str(entry.get("alias") or "").strip()
            if table_name and primary and operation:
                detail = f"{table_name}.{primary} operation={operation}"
                if secondary:
                    detail += f" secondary_column={secondary}"
                if alias:
                    detail += f" alias={alias}"
                lines.append(f"    - {detail}")

    lines.append("")
    return lines


def _build_sql_skeleton_section(
    query_shape: str,
    query_plan: dict | None,
    join_paths: list[dict] | None,
    explicit_limit: int | None,
) -> list[str]:
    lines = ["Generic SQL skeleton guidance:"]
    skeletons = _join_skeletons(join_paths)
    from_join_line = skeletons[0] if skeletons else "FROM <allowed_table>"
    limit_line = f"LIMIT {explicit_limit}" if explicit_limit else "<omit LIMIT unless explicitly required>"

    if query_shape == "ranking_grouped_aggregate":
        lines.extend(
            [
                "  Query shape: ranking_grouped_aggregate",
                "  Fill this shape using ONLY allowed evidence:",
                "  SELECT <dimension_column>, SUM(<measure_column>) AS <result_alias>",
                f"  {from_join_line}",
                "  GROUP BY <dimension_column>",
                "  ORDER BY <result_alias> DESC",
                f"  {limit_line};",
            ]
        )
    elif query_shape in {"grouped_aggregate", "derived_grouped_aggregate"}:
        aggregate_expression = "<derived_expression_from_formula_evidence>" if query_shape == "derived_grouped_aggregate" else "<measure_column>"
        lines.extend(
            [
                f"  Query shape: {query_shape}",
                "  Fill this shape using ONLY allowed evidence:",
                f"  SELECT <dimension_column>, SUM({aggregate_expression}) AS <result_alias>",
                f"  {from_join_line}",
                "  GROUP BY <dimension_column>",
                "  ORDER BY <result_alias> DESC or omit ORDER BY if not requested",
                f"  {limit_line};",
            ]
        )
    elif query_shape == "joined_aggregate":
        lines.extend(
            [
                "  Query shape: joined_aggregate",
                "  Fill this shape using ONLY allowed evidence:",
                "  SELECT SUM(<measure_column>) AS <result_alias>",
                f"  {from_join_line}",
                "  WHERE <allowed_filter_predicates_if_any>",
                f"  {limit_line};",
            ]
        )
    elif query_shape in {"joined_select", "joined_filtered_select"}:
        lines.extend(
            [
                f"  Query shape: {query_shape}",
                "  Fill this shape using ONLY allowed evidence:",
                "  SELECT <allowed_display_columns>",
                f"  {from_join_line}",
                "  WHERE <allowed_filter_predicates_if_any>",
                "  ORDER BY <allowed_sort_column_if_requested>",
                f"  {limit_line};",
            ]
        )
    else:
        lines.extend(
            [
                f"  Query shape: {query_shape}",
                "  Use only the allowed tables, columns, joins, and filters above.",
            ]
        )

    lines.append("")
    return lines


def _build_ai_target_section(query_plan: dict | None, selected_tables: list[dict] | None) -> list[str]:
    if not query_plan:
        return []

    lines = ["AI target for this question:"]
    intent = query_plan.get("intent")
    metric = query_plan.get("metric")
    dimension = query_plan.get("dimension")
    filters = query_plan.get("filters") or []
    date_range = query_plan.get("date_range") or {}
    grouping = query_plan.get("grouping") or []
    sorting = query_plan.get("sorting") or {}

    if metric:
        lines.append(f"  - Use the metric '{metric}' as the main measure hint.")
    if intent == "total":
        lines.append("  - Use SUM() for the final measure.")
    elif intent == "count":
        lines.append("  - Use COUNT() for the final measure.")
    elif intent == "average":
        lines.append("  - Use AVG() for the final measure.")
    elif intent == "top_n":
        lines.append("  - Rank results with ORDER BY DESC.")
    elif intent == "trend":
        lines.append("  - Aggregate over time and include GROUP BY for the time bucket.")
    if dimension:
        lines.append(f"  - Break down results by '{dimension}'.")
    if grouping:
        lines.append(f"  - Required grouping: {grouping}.")
    if sorting:
        lines.append(f"  - Preferred sorting: {sorting}.")
    if filters:
        lines.append(f"  - Required filters: {filters}.")
    if date_range:
        lines.append(f"  - Required date range: {date_range}.")

    if selected_tables:
        table_names = [table_entry.get("table", "") for table_entry in selected_tables if table_entry.get("table")]
        if table_names:
            lines.append(f"  - Prefer these tables: {', '.join(table_names)}.")
        ranked_columns = []
        for table_entry in selected_tables:
            for column_entry in table_entry.get("selected_columns", [])[:3]:
                ranked_columns.append(
                    f"{table_entry.get('table')}.{column_entry.get('column')} "
                    f"[{column_entry.get('semantic_type', 'general')}]"
                )
        if ranked_columns:
            lines.append(f"  - Prefer these columns first: {', '.join(ranked_columns[:8])}.")

    lines.append("  - Do not answer this with a generic SELECT * unless the plan clearly indicates a raw record listing.")
    lines.append("")
    return lines


def _build_relationship_section(knowledge_base: dict, selected_tables: list[dict] | None) -> list[str]:
    table_names = {entry.get("table", "") for entry in (selected_tables or []) if entry.get("table")}
    if not table_names:
        table_names = set(knowledge_base.keys())

    relationship_lines: list[str] = []
    seen = set()
    for table_name in sorted(table_names):
        for fk in knowledge_base.get(table_name, {}).get("foreign_keys", []):
            from_table = table_name
            from_column = fk.get("column")
            to_table = fk.get("referenced_table")
            to_column = fk.get("referenced_column")
            if not from_column or not to_table or not to_column:
                continue
            if to_table not in knowledge_base:
                continue
            signature = (from_table, from_column, to_table, to_column)
            if signature in seen:
                continue
            seen.add(signature)
            relationship_lines.append(
                f"  - {from_table}.{from_column} = {to_table}.{to_column} (confidence=1.0, source=foreign_key)"
            )

        for relationship in knowledge_base.get(table_name, {}).get("relationships", []):
            from_table = relationship.get("from_table")
            to_table = relationship.get("to_table")
            if not from_table or not to_table:
                continue
            if from_table not in table_names and to_table not in table_names:
                continue
            signature = (
                from_table,
                relationship.get("from_column"),
                to_table,
                relationship.get("to_column"),
            )
            if signature in seen:
                continue
            seen.add(signature)
            relationship_lines.append(
                f"  - {from_table}.{relationship.get('from_column')} = "
                f"{to_table}.{relationship.get('to_column')} "
                f"(confidence={relationship.get('confidence')}, source={relationship.get('source', 'rule')})"
            )

    if not relationship_lines:
        return []

    return ["Detected schema relationships to prefer for JOINs:"] + relationship_lines + [""]


def _build_pipeline_evidence_section(
    normalized_question: str | None,
    intent: dict | None,
    retrieved_context: dict | None,
    route_recommendation: str | None,
) -> list[str]:
    lines: list[str] = []
    if normalized_question:
        lines.append(f"Normalized question: {normalized_question}")
    if route_recommendation:
        lines.append(f"Route recommendation: {route_recommendation}")
    if intent:
        lines.append("Structured intent from pipeline:")
        for key in (
            "user_goal",
            "intent_type",
            "business_operation",
            "requested_metrics",
            "requested_dimensions",
            "requested_filters",
            "requested_sort",
            "limit",
            "needs_grouping",
            "needs_aggregation",
            "needs_join",
            "raw_business_terms",
            "confidence",
        ):
            if key in intent:
                lines.append(f"  {key}: {intent.get(key)}")
    if retrieved_context:
        lines.append("Retrieved dynamic context from pipeline:")
        for label, key in (
            ("matched_tables", "matched_tables"),
            ("matched_columns", "matched_columns"),
            ("matched_glossary_terms", "matched_glossary_terms"),
            ("matched_relationships", "matched_relationships"),
            ("possible_join_paths", "possible_join_paths"),
            ("measure_candidates", "measure_candidates"),
            ("dimension_candidates", "dimension_candidates"),
            ("filter_candidates", "filter_candidates"),
            ("retrieval_sources", "retrieval_sources"),
            ("confidence", "confidence"),
        ):
            if key in retrieved_context and retrieved_context.get(key) not in (None, [], {}, ""):
                lines.append(f"  {label}: {retrieved_context.get(key)}")
    if lines:
        lines.append("")
    return lines


def build_sql_prompt(
    user_question: str,
    knowledge_base: dict,
    normalized_question: str | None = None,
    intent: dict | None = None,
    retrieved_context: dict | None = None,
    query_plan: dict | None = None,
    selected_tables: list[dict] | None = None,
    selected_columns: list[dict] | None = None,
    measure_candidates: list[dict] | None = None,
    dimension_candidates: list[dict] | None = None,
    filter_candidates: list[dict] | None = None,
    business_glossary: dict | None = None,
    join_paths: list[dict] | None = None,
    formula_evidence: list[dict] | None = None,
    evidence_sources: list[str] | None = None,
    route_recommendation: str | None = None,
) -> list[dict]:
    """Build an OpenAI-compatible message list for SQL generation."""
    if not knowledge_base:
        raise ValueError(
            "Knowledge base is missing or empty. "
            "Please run option 2 (Build Knowledge Base) first."
        )

    explicit_limit = _extract_limit(user_question)
    query_shape = _query_shape(
        query_plan,
        intent,
        selected_tables,
        join_paths,
        formula_evidence,
    )
    if explicit_limit:
        limit_instruction = (
            f"The user asked for {explicit_limit} rows. "
            f"Use LIMIT {explicit_limit} in your query."
        )
    else:
        limit_instruction = (
            "The user did not specify a row count. "
            "Do not add LIMIT unless the question explicitly requests a row count."
        )
    scoped_knowledge_base = _scoped_prompt_knowledge_base(
        knowledge_base,
        selected_tables,
        selected_columns,
        join_paths,
    )

    system_parts: list[str] = []
    system_parts.append(
        "You are a MySQL SQL expert. "
        "Your only job is to write a single SELECT SQL statement. "
        "Return ONLY the SQL with no explanations."
    )
    system_parts.append("")
    system_parts.append(_SAFETY_RULES)
    system_parts.append("")
    system_parts.append(_QUERY_RULES)
    system_parts.append("")
    system_parts.append(_AI_PLAN_RULES)
    system_parts.append("")
    system_parts.append(
        "Pipeline execution rule: Use ONLY the provided selected tables, selected columns, runtime candidates, "
        "and supplied join paths. If safe evidence is missing, do not invent schema details."
    )
    system_parts.append("")
    system_parts.append(f"LIMIT rule: {limit_instruction}")
    system_parts.append("")
    system_parts.extend(
        _build_pipeline_evidence_section(
            normalized_question,
            intent,
            retrieved_context,
            route_recommendation,
        )
    )
    system_parts.extend(_build_retrieved_glossary_section(retrieved_context))
    system_parts.extend(_build_plan_section(query_plan, selected_tables, join_paths))
    system_parts.extend(
        _build_runtime_evidence_section(
            selected_columns,
            measure_candidates,
            dimension_candidates,
            filter_candidates,
            formula_evidence,
            evidence_sources,
        )
    )
    system_parts.extend(
        _build_allowed_context_section(
            selected_tables,
            selected_columns,
            filter_candidates,
            join_paths,
            formula_evidence,
        )
    )
    system_parts.extend(
        _build_sql_skeleton_section(
            query_shape,
            query_plan,
            join_paths,
            explicit_limit,
        )
    )
    system_parts.extend(_build_ai_target_section(query_plan, selected_tables))
    system_parts.extend(_build_relationship_section(scoped_knowledge_base, selected_tables))
    system_parts.append(_SEMANTIC_GUIDANCE)
    system_parts.append("")
    system_parts.extend(_build_schema_section(scoped_knowledge_base))

    system_content = "\n".join(system_parts).strip()

    if os.getenv("DEBUG_PROMPT", "").strip().lower() == "true":
        print("\n" + "=" * 60)
        print("  DEBUG: Full system prompt being sent to AI")
        print("=" * 60)
        print(system_content)
        print("=" * 60 + "\n")

    return [
        {"role": "system", "content": system_content},
        {"role": "user", "content": user_question},
    ]
