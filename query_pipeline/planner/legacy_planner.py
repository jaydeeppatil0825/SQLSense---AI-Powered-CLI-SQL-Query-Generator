"""Legacy direct-planner compatibility path.

Active runtime planning uses intent plus retrieved context in
``query_pipeline.query_planner``. This module keeps the old direct planner
branch separate without changing its behavior.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from query_pipeline.planner import filter_resolver as _filters
from query_pipeline.planner import phase7_bfs_join_resolver as _bfs
from query_pipeline.planner import role_resolver as _roles


def build_legacy_query_context(
    question: str,
    knowledge_base: dict,
    business_glossary: dict | None = None,
    use_vector_retrieval: bool = True,
    vector_retriever: Any | None = None,
) -> dict:
    from query_pipeline import query_planner as _qp

    normalized_question = _qp._normalize(question)
    enriched_kb = _qp._enriched_kb(knowledge_base)

    intent = _qp._detect_intent(normalized_question)
    dimension = _qp._detect_dimension(normalized_question, intent)
    date_range = _qp._detect_date_range(normalized_question)
    limit = _qp._extract_limit(normalized_question) or _qp._default_limit_for_intent(intent)
    sorting = _qp._detect_sorting(normalized_question)
    semantic_hints = _qp._semantic_hints(intent, date_range, sorting)
    glossary_matches = _qp._glossary_matches(question, business_glossary)

    _qp.logger.debug(f"[DEBUG] Question: {question}")
    _qp.logger.debug(f"[DEBUG] Intent: {intent}, Dimension: {dimension}, Semantic hints: {semantic_hints}")

    vector_results: dict[str, Any] = {}
    if use_vector_retrieval:
        vector_results = _qp._retrieve_with_vector(
            question,
            enriched_kb,
            business_glossary,
            retriever=vector_retriever,
        ) or {}

    _qp.logger.debug(f"[DEBUG] Vector results: {vector_results.get('used_vector') if vector_results else False}")
    if vector_results:
        _qp.logger.debug(f"[DEBUG] Vector table candidates: {vector_results.get('table_names', [])}")
        _qp.logger.debug(f"[DEBUG] Vector column candidates: {[col.get('column_name') for col in vector_results.get('columns', [])[:5]]}")

    if intent == "top_n":
        sorting = {"direction": "desc", "by": "metric"}
    elif intent == "trend":
        sorting = {"direction": "asc", "by": "date"}

    grouping = []
    if dimension:
        grouping.append(dimension)
    if intent == "trend" and "month" not in grouping:
        grouping.append("month")

    plan = {
        "question": question,
        "intent": intent,
        "metric": _qp._primary_metric_hint(semantic_hints),
        "dimension": dimension,
        "filters": [],
        "date_range": date_range,
        "grouping": grouping,
        "sorting": sorting,
        "limit": limit,
        "question_terms": _qp._content_terms(normalized_question),
        "semantic_hints": semantic_hints,
        "matched_glossary_terms": [term for term, _ in glossary_matches],
    }

    scored_by_name: dict[str, tuple[float, list[str]]] = {}
    for table_name, table_data in enriched_kb.items():
        score, reasons = _roles._table_score(plan, table_name, table_data, glossary_matches, vector_results)
        if score > 0:
            scored_by_name[table_name] = (score, reasons)

    if vector_results:
        for table_name in vector_results.get("table_names") or []:
            if table_name not in enriched_kb:
                continue
            if table_name in scored_by_name:
                continue
            scored_by_name[table_name] = (1.0, ["vector retrieval match"])

        for column_meta in vector_results.get("columns") or []:
            table_name = column_meta.get("table_name")
            if not table_name or table_name not in enriched_kb:
                continue
            score, reasons = scored_by_name.get(table_name, (0.0, []))
            reasons = list(reasons)
            score += 0.7
            reasons.append(f"vector column match: {column_meta.get('column_name')}")
            scored_by_name[table_name] = (score, reasons)

    scored_tables = [
        (table_name, score, reasons)
        for table_name, (score, reasons) in scored_by_name.items()
    ]
    scored_tables.sort(key=lambda item: (-item[1], item[0]))

    filters = _filters._detect_runtime_filters(
        question,
        _qp._candidate_tables_for_filters(enriched_kb, scored_tables, vector_results),
    )
    if not filters:
        filters = _filters._detect_generic_value_filters(
            question,
            _qp._candidate_tables_for_filters(enriched_kb, scored_tables, vector_results),
        )
    if filters:
        plan["filters"] = filters
        if any(filter_data.get("type") == "status" for filter_data in filters):
            plan["semantic_hints"].add("status")
        plan["metric"] = _qp._primary_metric_hint(plan["semantic_hints"])
        rescored_by_name: dict[str, tuple[float, list[str]]] = {}
        for table_name, table_data in enriched_kb.items():
            score, reasons = _roles._table_score(plan, table_name, table_data, glossary_matches, vector_results)
            if score > 0:
                rescored_by_name[table_name] = (score, reasons)
        if vector_results:
            for table_name in vector_results.get("table_names") or []:
                if table_name not in enriched_kb or table_name in rescored_by_name:
                    continue
                rescored_by_name[table_name] = (1.0, ["vector retrieval match"])

            for column_meta in vector_results.get("columns") or []:
                table_name = column_meta.get("table_name")
                if not table_name or table_name not in enriched_kb:
                    continue
                score, reasons = rescored_by_name.get(table_name, (0.0, []))
                reasons = list(reasons)
                score += 0.7
                reasons.append(f"vector column match: {column_meta.get('column_name')}")
                rescored_by_name[table_name] = (score, reasons)

        scored_tables = [
            (table_name, score, reasons)
            for table_name, (score, reasons) in rescored_by_name.items()
        ]
        scored_tables.sort(key=lambda item: (-item[1], item[0]))

    selected_tables = _roles._build_selected_table_entries(
        enriched_kb,
        scored_tables,
        plan,
        glossary_matches,
        vector_results,
    )
    simple_primary_table = _qp._primary_table_for_simple_question_from_entries(
        question,
        plan,
        [
            {
                "table": entry.get("table"),
                "score": float(entry.get("confidence") or 0.0),
                "confidence": float(entry.get("confidence") or 0.0),
            }
            for entry in selected_tables
        ],
    )
    if simple_primary_table:
        selected_tables = [
            entry
            for entry in selected_tables
            if str(entry.get("table", "")).strip() == simple_primary_table
        ]
    selected_names = [entry["table"] for entry in selected_tables if entry.get("table") in enriched_kb]
    if len(selected_names) != len(selected_tables):
        selected_tables = [entry for entry in selected_tables if entry.get("table") in enriched_kb]

    _qp.logger.debug(f"[DEBUG] Selected tables before join path computation: {selected_names}")

    fk_graph = _bfs._build_fk_relationship_graph(enriched_kb)
    _qp.logger.debug(f"[DEBUG] FK relationships loaded: {len(fk_graph)} tables with relationships")
    for table_name, edges in list(fk_graph.items())[:3]:
        _qp.logger.debug(f"[DEBUG]   {table_name}: {len(edges['outgoing'])} outgoing, {len(edges['incoming'])} incoming")

    join_paths = [] if simple_primary_table else _bfs._compute_join_paths(selected_names, enriched_kb)
    _qp.logger.debug(f"[DEBUG] Computed {len(join_paths)} join paths between selected tables")
    for jp in join_paths[:3]:
        _qp.logger.debug(f"[DEBUG]   {jp['from_table']} -> {jp['to_table']} (length: {jp['length']})")

    if not simple_primary_table:
        selected_names, selected_tables = _bfs._promote_join_path_tables(
            selected_names,
            selected_tables,
            enriched_kb,
            plan,
            join_paths,
        )
        join_paths = _bfs._compute_join_paths(selected_names, enriched_kb)
    _qp.logger.debug(f"[DEBUG] Selected tables after join-path promotion: {selected_names}")
    _qp.logger.debug(f"[DEBUG] Recomputed {len(join_paths)} join paths after promotion")

    reduced_kb = {table_name: deepcopy(enriched_kb[table_name]) for table_name in selected_names}
    warnings = []
    if len(reduced_kb) < len(enriched_kb):
        warnings.append(f"Using {len(reduced_kb)} relevant table(s) instead of the full schema.")
    if intent in {"list", "top_n"}:
        warnings.append("Read-only row limits stay enabled for list-style questions.")
    if vector_results and not vector_results.get("used_vector"):
        warnings.append("Vector retrieval was unavailable; using KB and glossary rules only.")
    if not selected_names:
        warnings.append("Planner could not isolate a table safely from the available schema evidence.")

    overall_confidence = round(
        sum(entry["confidence"] for entry in selected_tables) / max(len(selected_tables), 1),
        2,
    )
    selected_columns = [
        {
            "table": entry["table"],
            **column_entry,
        }
        for entry in selected_tables
        for column_entry in entry.get("selected_columns", [])
    ]

    _qp.logger.debug(f"[DEBUG] Selected columns before missing table addition: {[(col['table'], col['column']) for col in selected_columns[:10]]}")
    _qp.logger.debug(f"[DEBUG] Selected columns: {[(col['table'], col['column']) for col in selected_columns[:10]]}")

    metric_from_selected_columns = _roles._infer_metric_from_selected_columns(plan, selected_tables)
    metric_from_glossary = _roles._infer_metric_from_glossary_matches(glossary_matches, enriched_kb)
    if _roles._should_preserve_simple_list_metric(plan):
        if metric_from_selected_columns in {"money", "quantity", "percentage"}:
            plan["metric"] = metric_from_selected_columns
        elif metric_from_glossary in {"money", "quantity", "percentage"}:
            plan["metric"] = metric_from_glossary
        else:
            plan["metric"] = metric_from_selected_columns
    else:
        plan["metric"] = None

    measure_candidates: list[dict[str, Any]] = []
    dimension_candidates: list[dict[str, Any]] = []
    filter_candidates = list(plan.get("filters") or [])
    formula_evidence = list(plan.get("formula_evidence") or [])
    legacy_query_shape = _qp._derive_complex_query_shape(
        plan,
        None,
        selected_tables,
        measure_candidates,
        dimension_candidates,
        filter_candidates,
        join_paths,
        formula_evidence,
    )

    missing_evidence_flags = _qp._detect_missing_evidence(
        plan,
        selected_tables,
        selected_columns,
        join_paths,
        plan.get("requested_metrics", []),
        plan.get("requested_dimensions", []),
        plan.get("requested_filters", []),
        measure_candidates,
        dimension_candidates,
        filter_candidates,
        formula_evidence,
        legacy_query_shape,
    )

    legacy_route_recommendation = _qp._compute_route_recommendation(
        plan,
        missing_evidence_flags,
        overall_confidence,
        selected_tables,
        legacy_query_shape,
    )

    complex_sql_plan = _qp._build_complex_sql_plan(
        None,
        plan,
        selected_tables,
        selected_columns,
        measure_candidates,
        dimension_candidates,
        filter_candidates,
        join_paths,
        formula_evidence,
        missing_evidence_flags,
        legacy_route_recommendation,
        legacy_query_shape,
    )

    debug_trace_details = _qp._build_debug_trace(
        question,
        plan,
        selected_tables,
        selected_columns,
        join_paths,
        missing_evidence_flags,
        overall_confidence,
        legacy_route_recommendation,
    )

    return _qp._normalize_planner_output(
        question=question,
        normalized_question=normalized_question,
        intent=intent,
        retrieved_context={},
        plan=plan,
        selected_tables=selected_tables,
        selected_columns=selected_columns,
        selected_table_names=selected_names,
        selected_knowledge_base=reduced_kb,
        knowledge_base=enriched_kb,
        warnings=warnings,
        confidence=overall_confidence,
        vector_results=vector_results,
        vector_used=bool(vector_results and vector_results.get("used_vector")),
        join_paths=join_paths,
        fk_relationships=fk_graph,
        matched_relationships=[],
        measure_candidates=measure_candidates,
        dimension_candidates=dimension_candidates,
        filter_candidates=filter_candidates,
        formula_evidence=formula_evidence,
        missing_evidence_flags=missing_evidence_flags,
        complex_sql_plan=complex_sql_plan,
        legacy_route_recommendation=legacy_route_recommendation,
        debug_trace_details=debug_trace_details,
    )
