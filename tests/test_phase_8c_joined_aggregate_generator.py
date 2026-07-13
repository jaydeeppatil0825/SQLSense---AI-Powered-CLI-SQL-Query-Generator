from copy import deepcopy

from sql_pipeline.deterministic_sql_generator import generate_deterministic_sql
from sql_pipeline.sql_validator import validate_sql_structure


def _column(name, type_="VARCHAR"):
    return {"name": name, "type": type_}


def _kb():
    return {
        "payments": {
            "columns": [
                _column("payment_id", "INTEGER"),
                _column("order_id", "INTEGER"),
                _column("payment_amount", "DECIMAL(12,2)"),
                _column("payment_status", "VARCHAR(30)"),
            ],
            "primary_keys": ["payment_id"],
            "foreign_keys": [{"column": "order_id", "referenced_table": "orders", "referenced_column": "order_id"}],
            "relationships": [],
        },
        "orders": {
            "columns": [
                _column("order_id", "INTEGER"),
                _column("customer_id", "INTEGER"),
                _column("order_status", "VARCHAR(30)"),
            ],
            "primary_keys": ["order_id"],
            "foreign_keys": [{"column": "customer_id", "referenced_table": "customers", "referenced_column": "customer_id"}],
            "relationships": [],
        },
        "customers": {
            "columns": [
                _column("customer_id", "INTEGER"),
                _column("city", "VARCHAR(100)"),
                _column("customer_status", "VARCHAR(30)"),
            ],
            "primary_keys": ["customer_id"],
            "foreign_keys": [],
            "relationships": [],
        },
    }


def _edge(left, left_col, right, right_col, *, safe=True):
    return {
        "from_table": left,
        "from_column": left_col,
        "to_table": right,
        "to_column": right_col,
        "authoritative_from_table": left,
        "authoritative_from_column": left_col,
        "authoritative_to_table": right,
        "authoritative_to_column": right_col,
        "relationship_type": "foreign_key",
        "source": "database_metadata",
        "confidence": 1.0,
        "safe_for_planner": safe,
        "evidence": ["fk"],
        "evidence_reasons": ["metadata"],
    }


def _path():
    return {
        "base_table": "payments",
        "joined_tables": ["orders", "customers"],
        "edges": [
            _edge("payments", "order_id", "orders", "order_id"),
            _edge("orders", "customer_id", "customers", "customer_id"),
        ],
        "path_source": "relationship_graph",
        "ambiguity_status": "resolved",
    }


def _context(*, aggregate="sum", metric_column="payment_amount", dimension_column="city", filters=None, having=None, order=False, limit=None):
    metric = {"table": "payments", "column": metric_column}
    dimension = {"table": "customers", "column": dimension_column}
    aggregate_expr = "COUNT(*)" if aggregate == "count" else f"{aggregate.upper()}(payments.{metric_column})"
    aggregate_alias = f"count__payments__rows" if aggregate == "count" else f"{aggregate}__payments__{metric_column}"
    selected_path = _path()
    context = {
        "query_shape": "joined_aggregate",
        "route_recommendation": "deterministic_sql_required",
        "aggregate_function": aggregate,
        "selected_tables": [{"table": table} for table in ["payments", "orders", "customers"]],
        "selected_join_path": selected_path,
        "phase8a_grain_analysis": {"grain_preserved": True, "status": "grain_preserved"},
        "selected_metric": None if aggregate == "count" else metric,
        "selected_dimensions": [dimension],
        "selected_filters": filters or [],
        "selected_having": having or [],
        "selected_order_by": (
            {
                "target_type": "aggregate_expression",
                "table": "payments",
                "column": "" if aggregate == "count" else metric_column,
                "aggregate_function": aggregate,
                "direction": "desc",
            }
            if order
            else None
        ),
        "limit": limit,
        "selected_output_columns": [
            {
                "kind": "dimension",
                "table": "customers",
                "column": dimension_column,
                "expression": f"customers.{dimension_column}",
                "alias": f"customers__{dimension_column}",
            },
            {
                "kind": "aggregate",
                "table": "payments",
                "column": "" if aggregate == "count" else metric_column,
                "aggregate_function": aggregate,
                "expression": aggregate_expr,
                "alias": aggregate_alias,
            },
        ],
    }
    context["clause_plan"] = {
        "clause_shape": (
            "where_group_by_having" if context["selected_filters"] and context["selected_having"]
            else "where_group_by" if context["selected_filters"]
            else "group_by_having" if context["selected_having"]
            else "group_by"
        ),
        "selected_join_path": selected_path,
        "selected_order_by": dict(context["selected_order_by"] or {}),
        "limit": limit,
        "requires": {
            "aggregate": True,
            "metric": aggregate != "count",
            "dimension": True,
            "where": bool(context["selected_filters"]),
            "having": bool(context["selected_having"]),
            "order_by": bool(context["selected_order_by"]),
            "limit": limit is not None,
            "join": True,
        },
        "decision_path": [],
    }
    return context


def _sql(context):
    result = generate_deterministic_sql(query_context=context, knowledge_base=_kb())
    assert result.status == "generated", result.reason
    return result.sql


def test_generates_safe_two_edge_sum_avg_and_count():
    sum_sql = _sql(_context())
    avg_sql = _sql(_context(aggregate="avg"))
    count_sql = _sql(_context(aggregate="count"))

    assert "SUM(payments.payment_amount) AS sum__payments__payment_amount" in sum_sql
    assert "AVG(payments.payment_amount) AS avg__payments__payment_amount" in avg_sql
    assert "COUNT(*) AS count__payments__rows" in count_sql
    assert "COUNT DISTINCT" not in count_sql
    assert "FROM payments INNER JOIN orders ON payments.order_id = orders.order_id INNER JOIN customers ON orders.customer_id = customers.customer_id" in sum_sql
    assert "GROUP BY customers.city" in sum_sql


def test_renders_filters_having_order_and_limit_from_existing_clause_logic():
    context = _context(
        filters=[
            {"table": "payments", "column": "payment_status", "operator": "eq", "value": "Paid"},
            {"table": "orders", "column": "order_status", "operator": "eq", "value": "Delivered"},
            {"table": "customers", "column": "customer_status", "operator": "eq", "value": "Active"},
        ],
        having=[{"aggregate_function": "sum", "table": "payments", "column": "payment_amount", "operator": "gt", "value": 1000}],
        order=True,
        limit=3,
    )
    sql = _sql(context)

    assert "WHERE payments.payment_status = 'Paid' AND orders.order_status = 'Delivered' AND customers.customer_status = 'Active'" in sql
    assert "HAVING SUM(payments.payment_amount) > 1000" in sql
    assert "ORDER BY sum__payments__payment_amount DESC LIMIT 3" in sql


def test_rejects_missing_false_or_inconsistent_phase8a_evidence():
    missing = _context()
    missing.pop("phase8a_grain_analysis")
    false = _context()
    false["phase8a_grain_analysis"] = {"grain_preserved": False, "status": "row_multiplication_risk"}

    assert generate_deterministic_sql(query_context=missing, knowledge_base=_kb()).status == "cannot_plan_safely"
    assert generate_deterministic_sql(query_context=false, knowledge_base=_kb()).status == "cannot_plan_safely"


def test_rejects_bad_two_edge_paths_and_unknown_tables():
    cases = []
    reordered = _context()
    reordered["selected_join_path"] = deepcopy(reordered["selected_join_path"])
    reordered["selected_join_path"]["edges"] = list(reversed(reordered["selected_join_path"]["edges"]))
    cases.append(reordered)

    cyclic = _context()
    cyclic["selected_join_path"] = deepcopy(cyclic["selected_join_path"])
    cyclic["selected_join_path"]["joined_tables"] = ["orders", "payments"]
    cyclic["selected_join_path"]["edges"][1] = _edge("orders", "order_id", "payments", "order_id")
    cases.append(cyclic)

    extra = _context()
    extra["selected_join_path"] = deepcopy(extra["selected_join_path"])
    extra["selected_join_path"]["joined_tables"].append("regions")
    extra["selected_join_path"]["edges"].append(_edge("customers", "region_id", "regions", "region_id"))
    cases.append(extra)

    unknown = _context()
    unknown["selected_join_path"] = deepcopy(unknown["selected_join_path"])
    unknown["selected_join_path"]["edges"][1]["to_table"] = "missing"
    unknown["selected_join_path"]["joined_tables"][1] = "missing"
    cases.append(unknown)

    for context in cases:
        assert generate_deterministic_sql(query_context=context, knowledge_base=_kb()).status == "cannot_plan_safely"


def test_rejects_metric_and_dimension_outside_path():
    bad_metric = _context()
    bad_metric["selected_metric"] = {"table": "orders", "column": "order_id"}
    bad_dimension = _context()
    bad_dimension["selected_dimensions"] = [{"table": "regions", "column": "name"}]

    assert generate_deterministic_sql(query_context=bad_metric, knowledge_base=_kb()).status == "cannot_plan_safely"
    assert generate_deterministic_sql(query_context=bad_dimension, knowledge_base=_kb()).status == "cannot_plan_safely"


def test_direct_one_edge_joined_aggregate_generation_unchanged():
    kb = {
        "orders": {
            "columns": [_column("order_id", "INTEGER"), _column("customer_id", "INTEGER"), _column("order_amount", "DECIMAL")],
            "primary_keys": ["order_id"],
            "foreign_keys": [{"column": "customer_id", "referenced_table": "customers", "referenced_column": "customer_id"}],
        },
        "customers": {
            "columns": [_column("customer_id", "INTEGER"), _column("city")],
            "primary_keys": ["customer_id"],
            "foreign_keys": [],
        },
    }
    path = {
        "base_table": "orders",
        "joined_tables": ["customers"],
        "edges": [_edge("orders", "customer_id", "customers", "customer_id")],
        "path_source": "relationship_graph",
        "ambiguity_status": "resolved",
    }
    context = {
        "query_shape": "joined_aggregate",
        "aggregate_function": "sum",
        "selected_tables": [{"table": "orders"}, {"table": "customers"}],
        "selected_join_path": path,
        "selected_metric": {"table": "orders", "column": "order_amount"},
        "selected_dimensions": [{"table": "customers", "column": "city"}],
        "selected_output_columns": [
            {"kind": "dimension", "table": "customers", "column": "city", "expression": "customers.city", "alias": "customers__city"},
            {"kind": "aggregate", "table": "orders", "column": "order_amount", "aggregate_function": "sum", "expression": "SUM(orders.order_amount)", "alias": "sum__orders__order_amount"},
        ],
        "clause_plan": {
            "clause_shape": "group_by",
            "selected_join_path": path,
            "selected_order_by": {},
            "limit": None,
            "requires": {"aggregate": True, "metric": True, "dimension": True, "where": False, "having": False, "order_by": False, "limit": False, "join": True},
            "decision_path": [],
        },
    }

    result = generate_deterministic_sql(query_context=context, knowledge_base=kb)

    assert result.status == "generated"
    assert "FROM orders INNER JOIN customers ON orders.customer_id = customers.customer_id" in result.sql


def test_validator_still_blocks_two_edge_joined_aggregate_until_phase8d():
    context = _context()
    sql = _sql(context)

    assert validate_sql_structure(sql, _kb(), selected_join_path=context["selected_join_path"])[0] is False
