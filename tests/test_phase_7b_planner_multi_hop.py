from query_pipeline.intent_builder import build_intent
from query_pipeline.query_planner import build_query_context


def _kb(*, direct_region=False, unsafe_middle=False, alternate_path=False):
    orders_fks = [{"column": "customer_id", "referenced_table": "customers", "referenced_column": "customer_id"}]
    if direct_region:
        orders_fks.append({"column": "region_id", "referenced_table": "regions", "referenced_column": "region_id"})
    kb = {
        "orders": {
            "columns": [
                {"name": "order_id", "type": "INTEGER", "semantic_type": "id"},
                {"name": "customer_id", "type": "INTEGER", "semantic_type": "id"},
                {"name": "region_id", "type": "INTEGER", "semantic_type": "id"},
                {"name": "order_amount", "type": "DECIMAL", "semantic_type": "money"},
            ],
            "primary_keys": ["order_id"],
            "foreign_keys": orders_fks,
            "relationships": [],
        },
        "customers": {
            "columns": [
                {"name": "customer_id", "type": "INTEGER", "semantic_type": "id"},
                {"name": "region_id", "type": "INTEGER", "semantic_type": "id"},
                {"name": "customer_segment", "type": "VARCHAR", "semantic_type": "text"},
            ],
            "primary_keys": ["customer_id"],
            "foreign_keys": ([] if unsafe_middle else [{"column": "region_id", "referenced_table": "regions", "referenced_column": "region_id"}]),
            "relationships": [],
        },
        "regions": {
            "columns": [
                {"name": "region_id", "type": "INTEGER", "semantic_type": "id"},
                {"name": "region_name", "type": "VARCHAR", "semantic_type": "name"},
            ],
            "primary_keys": ["region_id"],
            "foreign_keys": [],
            "relationships": [],
        },
    }
    if unsafe_middle:
        kb["customers"]["relationships"] = [
            {
                "from_table": "customers",
                "from_column": "region_id",
                "to_table": "regions",
                "to_column": "region_id",
                "relationship_type": "inferred",
                "source": "kb_build_inference",
                "confidence": 0.9,
                "safe_for_planner": False,
                "is_fallback": True,
                "evidence": ["name"],
                "evidence_reasons": ["unsafe test edge"],
                "reason": "unsafe test edge",
            }
        ]
    if alternate_path:
        kb["stores"] = {
            "columns": [
                {"name": "store_id", "type": "INTEGER", "semantic_type": "id"},
                {"name": "region_id", "type": "INTEGER", "semantic_type": "id"},
            ],
            "primary_keys": ["store_id"],
            "foreign_keys": [{"column": "region_id", "referenced_table": "regions", "referenced_column": "region_id"}],
            "relationships": [],
        }
        kb["orders"]["columns"].append({"name": "store_id", "type": "INTEGER", "semantic_type": "id"})
        kb["orders"]["foreign_keys"].append({"column": "store_id", "referenced_table": "stores", "referenced_column": "store_id"})
    return kb


def _evidence():
    return {
        "query_terms": [],
        "matched_tables": [
            {"table": "orders", "score": 0.99, "matched_terms": ["orders"]},
            {"table": "regions", "score": 0.98, "matched_terms": ["regions", "region details"]},
        ],
        "matched_columns": [],
        "matched_glossary_terms": [],
        "matched_relationships": [],
        "possible_join_paths": [],
        "measure_candidates": [],
        "dimension_candidates": [],
        "filter_candidates": [],
        "date_candidates": [],
        "retrieval_sources": ["test"],
        "ambiguity_candidates": {},
        "missing_evidence_indicators": {},
        "confidence": 0.98,
    }


def _aggregate_evidence():
    evidence = _evidence()
    evidence["matched_tables"] = [
        {"table": "orders", "score": 0.99, "matched_terms": ["orders"]},
        {"table": "customers", "score": 0.98, "matched_terms": ["customers"]},
    ]
    evidence["measure_candidates"] = [
        {"table": "orders", "column": "order_amount", "semantic_type": "money", "score": 0.99, "matched_terms": ["order amount"]}
    ]
    evidence["dimension_candidates"] = [
        {"table": "customers", "column": "customer_segment", "semantic_type": "text", "score": 0.99, "matched_terms": ["customer segment"]}
    ]
    return evidence


def _context(question, kb):
    return build_query_context(question, kb, intent=build_intent(question), retrieved_context=_evidence())


def _aggregate_context(question, kb):
    return build_query_context(question, kb, intent=build_intent(question), retrieved_context=_aggregate_evidence())


def test_direct_lookup_still_uses_direct_resolver():
    context = _context("show orders with region details", _kb(direct_region=True))

    assert context["route_recommendation"] == "deterministic_sql_required"
    assert len(context["selected_join_path"]["edges"]) == 1
    assert context["selected_join_path"].get("non_executable_reason") is None


def test_unique_two_edge_lookup_path_is_routed_to_deterministic_generation():
    context = _context("show orders with region details", _kb())

    assert context["route_recommendation"] == "deterministic_sql_required"
    assert context["selected_join_path"]["joined_tables"] == ["customers", "regions"]
    assert len(context["selected_join_path"]["edges"]) == 2
    assert "non_executable_reason" not in context["selected_join_path"]


def test_direct_joined_aggregate_stays_direct():
    context = _aggregate_context("show total order amount by customer segment", _kb())

    assert context["route_recommendation"] == "deterministic_sql_required"
    assert context["query_shape"] == "joined_aggregate"
    assert len(context["selected_join_path"]["edges"]) == 1


def test_ambiguous_multi_hop_lookup_fails_closed():
    context = _context("show orders with region details", _kb(alternate_path=True))

    assert context["route_recommendation"] == "cannot_plan_safely"
    assert context["selected_join_path"] is None
    assert "ambiguous_path" in context["route_reason"]


def test_missing_multi_hop_path_fails_closed():
    kb = _kb()
    kb["customers"]["foreign_keys"] = []
    context = _context("show orders with region details", kb)

    assert context["route_recommendation"] == "cannot_plan_safely"
    assert context["selected_join_path"] is None
    assert "no_safe_path" in context["route_reason"]


def test_multi_hop_aggregate_fails_closed_without_bfs_path():
    context = _context("show total order amount by region name", _kb())

    assert context["route_recommendation"] == "cannot_plan_safely"
    assert context.get("selected_join_path") is None


def test_deterministic_path_ordering_when_graph_order_changes():
    first = _context("show orders with region details", _kb())
    second_kb = _kb()
    second_kb = {key: second_kb[key] for key in reversed(list(second_kb))}
    second = _context("show orders with region details", second_kb)

    assert first["selected_join_path"] == second["selected_join_path"]
