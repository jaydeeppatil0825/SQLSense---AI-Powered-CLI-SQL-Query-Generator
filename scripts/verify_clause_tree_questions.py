from decimal import Decimal
import os
from pathlib import Path
import sys
from typing import Any


TEST_DB_NAME = "sqlsense_clause_tree_lab"
_APP: Any = None


TEST_CASES: list[dict[str, Any]] = [
    # ------------------------------------------------------------
    # A. Basic list / count / aggregate
    # ------------------------------------------------------------
    {
        "id": "BASIC_COUNT_001",
        "question": "count project receipts",
        "expected_type": "scalar",
        "expected": 16,
    },
    {
        "id": "BASIC_SUM_002",
        "question": "show sum invoice amount from project receipts",
        "expected_type": "scalar_decimal",
        "expected": Decimal("226500.00"),
    },
    {
        "id": "BASIC_SUM_003",
        "question": "show sum collected amount from project receipts",
        "expected_type": "scalar_decimal",
        "expected": Decimal("169000.00"),
    },
    {
        "id": "BASIC_AVG_004",
        "question": "average invoice amount from project receipts",
        "expected_type": "scalar_decimal",
        "expected": Decimal("14156.25"),
    },

    # ------------------------------------------------------------
    # B. Phase 2 advanced filters
    # ------------------------------------------------------------
    {
        "id": "FILTER_AND_005",
        "question": "show project receipts where payment state is open and collected amount equals 0",
        "expected_type": "row_count",
        "expected": 4,
    },
    {
        "id": "FILTER_BETWEEN_006",
        "question": "show project receipts where invoice amount between 5000 and 12000",
        "expected_type": "row_count",
        "expected": 8,
    },
    {
        "id": "FILTER_NOT_EQUAL_007",
        "question": "show project receipts where payment state is not settled",
        "expected_type": "row_count",
        "expected": 8,
    },
    {
        "id": "FILTER_CONTAINS_008",
        "question": "show project receipts where client name contains Retail",
        "expected_type": "row_count",
        "expected": 3,
    },
    {
        "id": "FILTER_DATE_009",
        "question": "show project receipts where service date is after 2026-03-01",
        "expected_type": "row_count",
        "expected": 9,
    },

    # ------------------------------------------------------------
    # C. Phase 3 GROUP BY / HAVING
    # ------------------------------------------------------------
    {
        "id": "GROUP_SUM_010",
        "question": "show sum invoice amount by payment state from project receipts",
        "expected_type": "grouped_decimal",
        "expected": {
            "settled": Decimal("151000.00"),
            "partial": Decimal("48000.00"),
            "open": Decimal("27500.00"),
        },
    },
    {
        "id": "GROUP_SUM_011",
        "question": "show sum collected amount by department from project receipts",
        "expected_type": "grouped_decimal",
        "expected": {
            "cloud": Decimal("53000.00"),
            "support": Decimal("23000.00"),
            "automation": Decimal("58000.00"),
            "analytics": Decimal("35000.00"),
        },
    },
    {
        "id": "HAVING_012",
        "question": "show department where sum invoice amount is greater than 40000 from project receipts",
        "expected_type": "grouped_decimal",
        "expected": {
            "cloud": Decimal("69500.00"),
            "support": Decimal("50000.00"),
            "automation": Decimal("67000.00"),
        },
    },
    {
        "id": "WHERE_GROUP_HAVING_013",
        "question": "show sum invoice amount from project receipts where payment state is settled group by client name having sum invoice amount greater than 20000",
        "expected_type": "grouped_decimal",
        "expected": {
            "Apex Motors": Decimal("48000.00"),
            "Orion Textiles": Decimal("52000.00"),
            "Zen Labs": Decimal("32000.00"),
        },
    },

    # ------------------------------------------------------------
    # D. Phase 4 ORDER BY / LIMIT ranking
    # ------------------------------------------------------------
    {
        "id": "RANK_TOP_ROWS_014",
        "question": "top 5 project receipts by invoice amount",
        "expected_type": "ordered_codes",
        "expected": ["PR-007", "PR-012", "PR-004", "PR-016", "PR-001"],
    },
    {
        "id": "RANK_LOWEST_ROWS_015",
        "question": "lowest 4 project receipts by invoice amount",
        "expected_type": "ordered_codes",
        "expected": ["PR-006", "PR-010", "PR-002", "PR-011"],
    },
    {
        "id": "RANK_GROUPED_016",
        "question": "top 3 client name by sum collected amount from project receipts",
        "expected_type": "ordered_grouped_decimal",
        "expected": [
            ("Orion Textiles", Decimal("58000.00")),
            ("Apex Motors", Decimal("53000.00")),
            ("Zen Labs", Decimal("35000.00")),
        ],
    },
    {
        "id": "FULL_CLAUSE_TREE_017",
        "question": "show sum invoice amount from project receipts where payment state is settled group by client name having sum invoice amount greater than 20000 order by sum invoice amount descending limit 3",
        "expected_type": "ordered_grouped_decimal",
        "expected": [
            ("Orion Textiles", Decimal("52000.00")),
            ("Apex Motors", Decimal("48000.00")),
            ("Zen Labs", Decimal("32000.00")),
        ],
    },

    # ------------------------------------------------------------
    # E. Fail-closed safety checks
    # ------------------------------------------------------------
    {
        "id": "SAFE_AMBIGUOUS_METRIC_018",
        "question": "show sum amount from project receipts",
        "expected_type": "blocked",
        "expected_route": "cannot_plan_safely",
    },
    {
        "id": "SAFE_AMBIGUOUS_RANK_019",
        "question": "top 5 project receipts by amount",
        "expected_type": "blocked",
        "expected_route": "cannot_plan_safely",
    },
    {
        "id": "SAFE_UNKNOWN_ORDER_020",
        "question": "show project receipts ordered by unknown field",
        "expected_type": "blocked",
        "expected_route": "cannot_plan_safely",
    },
    {
        "id": "SAFE_MULTI_METRIC_021",
        "question": "show sum invoice amount and collected amount by payment state from project receipts",
        "expected_type": "blocked",
        "expected_route": "cannot_plan_safely",
    },
    {
        "id": "SAFE_UNSAFE_022",
        "question": "delete project receipts",
        "expected_type": "blocked",
        "expected_route": "blocked_unsafe",
    },
]


def ask_sqlsense_question(question: str) -> dict[str, Any]:
    """Run one question through SQLSense and execute only validated SQL."""
    if _APP is None:
        raise RuntimeError("SQLSense application is not initialized.")

    result = _APP.process_question(question)
    context = result.get("query_context") or {}
    route = str(
        result.get("route")
        or result.get("route_used")
        or context.get("route")
        or context.get("route_used")
        or ""
    )
    sql = result.get("generated_sql") or result.get("sql")
    actual: dict[str, Any] = {
        "route": route,
        "sql": sql,
        "executed": False,
        "rows": [],
        "scalar": None,
        "error": result.get("error"),
        "message": result.get("message"),
        "intent": context.get("intent"),
        "planner": context,
        "clause_plan": context.get("clause_plan"),
        "validation": result.get("validation_result"),
    }
    if not result.get("success") or not sql:
        return actual

    validation = result.get("validation_result") or {}
    if validation.get("is_valid") is not True:
        actual["error"] = validation.get("reason") or "SQLSense validation failed."
        return actual

    executed, message, rows = _APP.execute_sql(sql, revalidate=True)
    actual["executed"] = bool(executed)
    actual["rows"] = list(rows or [])
    actual["error"] = None if executed else message
    if actual["rows"] and isinstance(actual["rows"][0], dict) and actual["rows"][0]:
        actual["scalar"] = next(iter(actual["rows"][0].values()))
    return actual


def initialize_sqlsense() -> None:
    """Connect through the normal app flow using environment-only credentials."""
    global _APP

    required = ("DB_HOST", "DB_PORT", "DB_USER", "DB_PASSWORD", "SQLSENSE_TEST_DB")
    missing = [name for name in required if not str(os.getenv(name) or "").strip()]
    if missing:
        raise RuntimeError(f"Missing required environment variables: {', '.join(missing)}")

    project_root = Path(__file__).resolve().parents[1]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")
    os.environ.setdefault("CHROMA_ANONYMIZED_TELEMETRY", "False")

    from core.app_service import AppService

    app = AppService()
    success, message, report = app.connect_database_and_prepare(
        db_type="mysql",
        host=os.environ["DB_HOST"],
        port=int(os.environ["DB_PORT"]),
        username=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
        database=os.environ["SQLSENSE_TEST_DB"],
        use_ai_enrichment=False,
    )
    if not success or not app.is_database_ready():
        raise RuntimeError(
            f"SQLSense database preparation failed: {message}; "
            f"connected={report.get('connected')} kb_built={report.get('kb_built')} "
            f"vector_status={report.get('vector_status')}"
        )
    _APP = app


def normalize_decimal(value: Any) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.01"))


def assert_case(case: dict[str, Any], actual: dict[str, Any]) -> tuple[bool, str]:
    expected_type = case["expected_type"]

    if expected_type == "blocked":
        expected_route = case["expected_route"]
        if actual.get("route") != expected_route:
            return False, f"Expected route {expected_route}, got {actual.get('route')}"
        if actual.get("executed") is True:
            return False, "Blocked query executed, which is unsafe"
        if actual.get("sql"):
            return False, f"Blocked query should not return SQL, got {actual.get('sql')}"
        return True, "PASS"

    if actual.get("executed") is not True:
        return False, f"Safe query did not execute. Route={actual.get('route')} Error={actual.get('error')}"

    rows = actual.get("rows") or []

    if expected_type == "scalar":
        got = actual.get("scalar")
        return got == case["expected"], f"Expected {case['expected']}, got {got}"

    if expected_type == "scalar_decimal":
        got = normalize_decimal(actual.get("scalar"))
        return got == case["expected"], f"Expected {case['expected']}, got {got}"

    if expected_type == "row_count":
        got = len(rows)
        return got == case["expected"], f"Expected {case['expected']} rows, got {got}"

    if expected_type == "ordered_codes":
        got = [row.get("receipt_code") for row in rows]
        return got == case["expected"], f"Expected order {case['expected']}, got {got}"

    if expected_type == "grouped_decimal":
        got = {}
        for row in rows:
            values = list(row.values())
            group_key = values[0]
            metric_value = normalize_decimal(values[1])
            got[str(group_key)] = metric_value
        return got == case["expected"], f"Expected {case['expected']}, got {got}"

    if expected_type == "ordered_grouped_decimal":
        got = []
        for row in rows:
            values = list(row.values())
            got.append((str(values[0]), normalize_decimal(values[1])))
        return got == case["expected"], f"Expected {case['expected']}, got {got}"

    return False, f"Unknown expected_type: {expected_type}"


def main() -> int:
    try:
        initialize_sqlsense()
    except Exception as exc:
        print(f"SQLSense initialization failed: {exc}")
        return 1

    failures = []

    print("\nSQLSense Clause Tree Verification")
    print("=" * 120)
    print(f"{'ID':<28} {'RESULT':<8} {'ROUTE':<28} {'QUESTION'}")
    print("-" * 120)

    for case in TEST_CASES:
        actual = ask_sqlsense_question(case["question"])
        passed, message = assert_case(case, actual)

        result = "PASS" if passed else "FAIL"
        print(f"{case['id']:<28} {result:<8} {str(actual.get('route')):<28} {case['question']}")

        if not passed:
            failures.append({
                "case": case,
                "actual": actual,
                "message": message,
            })

    if failures:
        print("\nFailures")
        print("=" * 120)
        for failure in failures:
            case = failure["case"]
            actual = failure["actual"]

            print(f"\nID: {case['id']}")
            print(f"Question: {case['question']}")
            print(f"Expected: {case.get('expected', case.get('expected_route'))}")
            print(f"Actual rows: {actual.get('rows')}")
            print(f"Actual scalar: {actual.get('scalar')}")
            print(f"Route: {actual.get('route')}")
            print(f"SQL: {actual.get('sql')}")
            print(f"Error: {actual.get('error')}")
            print(f"Reason: {failure['message']}")

        return 1

    print("\nAll verification cases passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
