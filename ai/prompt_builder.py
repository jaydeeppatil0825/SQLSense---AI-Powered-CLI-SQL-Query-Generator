"""
ai/prompt_builder.py
====================
Build the structured prompt sent to the AI backend for SQL generation.

The prompt is fully dynamic:
- Knowledge base is the source of truth
- Business glossary is derived from the active KB
- Relationships come only from the active schema context
- No fixed demo or ERP-specific table guidance is injected
"""

from __future__ import annotations

import os
import re
from typing import Any

from semantic.business_glossary import load_business_glossary


_QUERY_RULES = """
SQL query construction rules:
  - The first non-whitespace characters in the output must be SELECT.
  - SELECT must include at least one valid column, expression, or aggregate.
  - FROM must name a real table from the schema context.
  - Every JOIN must name a real table and include an ON predicate unless it is an explicit CROSS JOIN.
  - JOIN ON predicates must use existing columns on both joined tables.
  - Use COUNT(*) or COUNT(column) when the user asks "how many".
  - Use SUM(column) when the user asks for total, amount, balance, or quantity.
  - Use AVG(column) when the user asks for average.
  - Use MAX/MIN when the user asks for highest/lowest/most/least.
  - Always add GROUP BY when mixing aggregate functions with non-aggregate columns.
  - Add ORDER BY <alias or aggregate> DESC when the user asks "top", "highest", or "most".
  - Add ORDER BY <alias or aggregate> ASC when the user asks "lowest", "least", or "fewest".
  - ORDER BY must reference a selected column or alias.
  - WHERE, GROUP BY, HAVING, and ORDER BY must use only schema columns or selected aliases.
  - Use WHERE to filter by status, date range, value, or any explicit condition from the prompt context.
  - Use JOIN only when a listed relationship supports it.
  - Never invent table names or column names.
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
  - Prefer the selected columns when choosing measures, dimensions, date filters, and status filters.
  - For intent=total use SUM() on the best numeric measure.
  - For intent=count use COUNT(*), COUNT(column), or COUNT(DISTINCT column) as appropriate.
  - For intent=top_n use GROUP BY + ORDER BY DESC, and add LIMIT only when the question explicitly requests a row count.
  - For intent=trend or grouping by month use a date expression and aggregate.
  - When the plan includes filters, apply them instead of returning a raw table dump.
  - Use computed join predicates exactly when they are supplied.
  - Do NOT use SELECT * for totals, counts, balances, grouped analysis, or other business-style questions unless the plan clearly indicates a raw record listing.
""".strip()

_SEMANTIC_GUIDANCE = """
Semantic type guidance:
  - semantic_type=money: prefer for totals, balances, amounts, costs, prices, and other monetary measures.
  - semantic_type=quantity: prefer for counts, units, and other measurable quantities.
  - semantic_type=date: prefer for latest/recent/date/month questions.
  - semantic_type=status: prefer for state-like filters when the plan or sample values indicate them.
  - semantic_type=name: prefer for user-facing labels in SELECT, GROUP BY, or ORDER BY.
  - semantic_type=code or semantic_type=id: prefer for identifiers and joins when needed.
  - semantic_type=percentage: prefer for ratio or percent questions.
""".strip()


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip().lower())


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


def _term_matches_question(term: str, term_data: dict[str, Any], question: str) -> bool:
    normalized_question = _normalize(question)
    normalized_term = _normalize(term)
    if normalized_term and normalized_term in normalized_question:
        return True

    question_terms = set(re.split(r"[^a-z0-9]+", normalized_question))
    term_terms = {token for token in re.split(r"[^a-z0-9]+", normalized_term) if token}
    if term_terms and term_terms <= question_terms:
        return True

    for alias in term_data.get("business_terms", []) or []:
        alias_terms = {token for token in re.split(r"[^a-z0-9]+", _normalize(alias)) if token}
        if alias_terms and alias_terms <= question_terms:
            return True
    return False


def _get_relevant_glossary_terms(
    user_question: str,
    knowledge_base: dict | None = None,
    glossary: dict | None = None,
    glossary_path: str | None = None,
) -> str:
    """Load the active glossary and return the section relevant to this question."""
    if glossary_path is None:
        glossary_path = "semantic/business_glossary.json"

    active_glossary = glossary if glossary is not None else load_business_glossary(glossary_path)
    if not active_glossary:
        return "Business glossary: no glossary terms are available for this session."

    relevant_terms = {
        term: term_data
        for term, term_data in active_glossary.items()
        if _term_matches_question(term, term_data, user_question)
    }

    if not relevant_terms and knowledge_base:
        relevant_terms = dict(list(active_glossary.items())[:4])

    if not relevant_terms:
        return "Business glossary: no directly matched glossary terms. Use the selected schema context only."

    lines = ["Business glossary for this question:"]
    lines.append("")
    for term, term_data in list(relevant_terms.items())[:8]:
        lines.append(f"  TERM: {term}")
        description = str(term_data.get("description", "")).strip()
        if description:
            lines.append(f"    description: {description}")
        mappings = []
        for mapping in term_data.get("mapped_columns", [])[:5]:
            table_name = mapping.get("table", "")
            column_name = mapping.get("column", "")
            if table_name and column_name:
                mappings.append(f"{table_name}.{column_name}")
        if mappings:
            lines.append(f"    mapped_columns: {', '.join(mappings)}")
        aliases = [str(alias) for alias in (term_data.get("business_terms") or [])[:5] if str(alias).strip()]
        if aliases:
            lines.append(f"    aliases: {', '.join(aliases)}")
        examples = [str(example) for example in (term_data.get("example_questions") or [])[:2] if str(example).strip()]
        if examples:
            lines.append(f"    examples: {', '.join(examples)}")
        lines.append("")

    return "\n".join(lines).strip()


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
            lines.append(
                f"  COLUMN: {name}  type={col_type}  {nullable}  semantic_type={sem_type}"
            )
            samples = [str(v) for v in (col.get("sample_values") or [])[:5] if v is not None]
            if samples:
                lines.append(f"    sample_values: {', '.join(samples)}")
            if "min_value" in col and col["min_value"] is not None:
                lines.append(f"    range: {col['min_value']} .. {col.get('max_value')}")

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


def build_sql_prompt(
    user_question: str,
    knowledge_base: dict,
    query_plan: dict | None = None,
    selected_tables: list[dict] | None = None,
    business_glossary: dict | None = None,
    join_paths: list[dict] | None = None,
) -> list[dict]:
    """Build an OpenAI-compatible message list for SQL generation."""
    if not knowledge_base:
        raise ValueError(
            "Knowledge base is missing or empty. "
            "Please run option 2 (Build Knowledge Base) first."
        )

    explicit_limit = _extract_limit(user_question)
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
    system_parts.append(f"LIMIT rule: {limit_instruction}")
    system_parts.append("")
    system_parts.append(_get_relevant_glossary_terms(user_question, knowledge_base, glossary=business_glossary))
    system_parts.append("")
    system_parts.extend(_build_plan_section(query_plan, selected_tables, join_paths))
    system_parts.extend(_build_ai_target_section(query_plan, selected_tables))
    system_parts.extend(_build_relationship_section(knowledge_base, selected_tables))
    system_parts.append(_SEMANTIC_GUIDANCE)
    system_parts.append("")
    system_parts.extend(_build_schema_section(knowledge_base))

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
