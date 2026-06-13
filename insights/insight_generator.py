"""
insights/insight_generator.py
==============================
Analyses SQL query results and generates human-readable business insights.

Two modes
---------
Rule-based (always available)
    Pure Python logic — no network calls, always fast.
    Covers the most common result shapes: single aggregate, one
    category + one numeric, and time-series (date/month column).

AI-powered (optional, requires ENABLE_AI_INSIGHTS=true in .env)
    Sends the result to the configured AI backend (Ollama or OpenAI).
    Falls back to rule-based automatically if the AI call fails.

Public API
----------
    generate_insights(user_question, sql, rows, knowledge_base=None,
                      backend="local")
        → list[str]   always returns a list, never raises

    generate_ai_insights(user_question, sql, rows, backend="local")
        → list[str]   may raise; caller wraps in try/except
"""

from __future__ import annotations

import os
from decimal import Decimal
from datetime import date, datetime


# ── Type helpers ──────────────────────────────────────────────────────────────

_NUMERIC_TYPES = (int, float, Decimal)

# Words in a column name that suggest it holds date / time values.
_DATE_HINTS = {
    "date", "month", "year", "week", "day", "period",
    "quarter", "time", "created", "updated",
}


def _is_numeric(value) -> bool:
    """
    Return True when *value* is a real number (int, float, or Decimal).

    Explicitly rejects bool even though bool is a subclass of int in Python,
    so that flag columns (True/False) are not misclassified as numeric data.
    Decimal is included because MySQL aggregate functions return it.
    """
    return isinstance(value, _NUMERIC_TYPES) and not isinstance(value, bool)


def _to_float(value) -> float | None:
    """Safely convert *value* to float; return None if not possible."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _is_date_col(col_name: str) -> bool:
    """Return True when the column name looks like a date/time column."""
    lower = col_name.lower()
    return any(hint in lower for hint in _DATE_HINTS)


def _col_values(rows: list[dict], col: str) -> list:
    """Extract all values for *col* from the list of row dicts."""
    return [row.get(col) for row in rows]


def _numeric_cols(rows: list[dict]) -> list[str]:
    """Return column names whose majority of non-null values are numeric."""
    if not rows:
        return []
    columns = list(rows[0].keys())
    result = []
    for col in columns:
        vals = [v for v in _col_values(rows, col) if v is not None]
        if vals and sum(1 for v in vals if _is_numeric(v)) / len(vals) >= 0.5:
            result.append(col)
    return result


def _text_cols(rows: list[dict]) -> list[str]:
    """Return column names that are not classified as numeric."""
    if not rows:
        return []
    all_cols = list(rows[0].keys())
    num = set(_numeric_cols(rows))
    return [c for c in all_cols if c not in num]


def _fmt(value) -> str:
    """Format a number for display: integers without decimals, floats rounded."""
    if isinstance(value, Decimal):
        value = float(value)
    if isinstance(value, float):
        # Show up to 2 decimal places but drop trailing zeros.
        formatted = f"{value:,.2f}"
        if "." in formatted:
            formatted = formatted.rstrip("0").rstrip(".")
        return formatted
    if isinstance(value, int):
        return f"{value:,}"
    return str(value)


# ── Rule-based insight engine ─────────────────────────────────────────────────

def _insights_empty() -> list[str]:
    return ["No data found for this query."]


def _insights_single_aggregate(rows: list[dict], num_col: str) -> list[str]:
    """
    Generate insights for a single-row, single-column aggregate result.
    Example: SELECT COUNT(*) AS total_orders FROM orders → [{"total_orders": 30}]
    """
    insights: list[str] = []
    value = rows[0].get(num_col)
    fval = _to_float(value)

    if fval is None:
        insights.append(f"The result for {num_col} could not be interpreted.")
        return insights

    if fval == 0:
        insights.append(f"The {num_col} is 0. No records match the criteria.")
    else:
        insights.append(f"The total {num_col} is {_fmt(value)}.")

    return insights


def _insights_category_numeric(
    rows: list[dict],
    cat_col: str,
    num_col: str,
) -> list[str]:
    """
    Generate insights for a category-vs-numeric result.
    Example: city | total_sales
    Finds highest, lowest, total, and average.
    """
    insights: list[str] = []

    # Collect (label, numeric_value) pairs, skipping rows where value is None.
    pairs = []
    for row in rows:
        label = str(row.get(cat_col, ""))
        fval  = _to_float(row.get(num_col))
        if fval is not None:
            pairs.append((label, fval, row.get(num_col)))

    if not pairs:
        insights.append(f"No numeric values found in {num_col}.")
        return insights

    # Sort by numeric value.
    pairs_sorted = sorted(pairs, key=lambda x: x[1], reverse=True)
    top_label,  _, top_raw  = pairs_sorted[0]
    bot_label,  _, bot_raw  = pairs_sorted[-1]

    total   = sum(p[1] for p in pairs)
    average = total / len(pairs)

    insights.append(
        f"{top_label} has the highest {num_col} with {_fmt(top_raw)}."
    )
    insights.append(
        f"{bot_label} has the lowest {num_col} with {_fmt(bot_raw)}."
    )
    insights.append(
        f"The total {num_col} across all rows is {_fmt(total)}."
    )
    insights.append(
        f"The average {num_col} is {_fmt(average)}."
    )

    # Extra: mention how many categories contributed.
    if len(pairs) > 2:
        insights.append(
            f"There are {len(pairs)} {cat_col} entries in this result."
        )

    return insights


def _insights_time_series(
    rows: list[dict],
    date_col: str,
    num_col: str,
) -> list[str]:
    """
    Generate insights for a date/month-vs-numeric time series result.
    Example: month | total_sales
    Identifies first period, last period, and trend direction.
    """
    insights: list[str] = []

    # Collect ordered (period_label, numeric_value) pairs.
    pairs = []
    for row in rows:
        label = str(row.get(date_col, ""))
        fval  = _to_float(row.get(num_col))
        if label and fval is not None:
            pairs.append((label, fval, row.get(num_col)))

    if len(pairs) < 2:
        # Fall back to plain category insights for very short series.
        return _insights_category_numeric(rows, date_col, num_col)

    first_label, first_val, first_raw = pairs[0]
    last_label,  last_val,  last_raw  = pairs[-1]
    total   = sum(p[1] for p in pairs)
    average = total / len(pairs)

    insights.append(
        f"The data spans from {first_label} to {last_label} "
        f"({len(pairs)} periods)."
    )
    insights.append(
        f"The total {num_col} over all periods is {_fmt(total)}."
    )
    insights.append(
        f"The average {num_col} per period is {_fmt(average)}."
    )

    # Trend: compare first vs last period.
    if last_val > first_val:
        change = last_val - first_val
        insights.append(
            f"{num_col} increased from {_fmt(first_raw)} ({first_label}) "
            f"to {_fmt(last_raw)} ({last_label}), "
            f"a rise of {_fmt(change)}."
        )
    elif last_val < first_val:
        change = first_val - last_val
        insights.append(
            f"{num_col} decreased from {_fmt(first_raw)} ({first_label}) "
            f"to {_fmt(last_raw)} ({last_label}), "
            f"a drop of {_fmt(change)}."
        )
    else:
        insights.append(
            f"{num_col} remained the same from {first_label} to {last_label}."
        )

    # Highlight peak period.
    peak = max(pairs, key=lambda x: x[1])
    insights.append(
        f"The peak {num_col} was {_fmt(peak[2])} in {peak[0]}."
    )

    return insights


def _rule_based_insights(rows: list[dict]) -> list[str]:
    """
    Dispatch to the appropriate insight function based on result shape.

    Decision tree
    -------------
    - Empty rows          → empty message
    - 1 row, 1 numeric    → single aggregate
    - date col + numeric  → time series
    - text col + numeric  → category vs numeric
    - only numeric cols   → summary stats
    - no numeric cols     → generic count message
    """
    if not rows:
        return _insights_empty()

    num_cols  = _numeric_cols(rows)
    text_cols = _text_cols(rows)

    # Single aggregate value (e.g. COUNT(*), SUM(amount))
    if len(rows) == 1 and len(num_cols) == 1 and not text_cols:
        return _insights_single_aggregate(rows, num_cols[0])

    # Time series: first text-like column looks like a date.
    if text_cols and num_cols:
        date_candidates = [c for c in text_cols if _is_date_col(c)]
        if date_candidates:
            return _insights_time_series(rows, date_candidates[0], num_cols[0])

    # Category vs numeric (most common case).
    if text_cols and num_cols:
        return _insights_category_numeric(rows, text_cols[0], num_cols[0])

    # All numeric — just summarise each column.
    if num_cols and not text_cols:
        insights = []
        for col in num_cols[:3]:  # limit to 3 columns to keep output clean
            vals = [_to_float(v) for v in _col_values(rows, col) if v is not None]
            if vals:
                insights.append(
                    f"{col}: total={_fmt(sum(vals))}, "
                    f"avg={_fmt(sum(vals)/len(vals))}, "
                    f"min={_fmt(min(vals))}, max={_fmt(max(vals))}"
                )
        return insights or [f"Result contains {len(rows)} rows."]

    # No numeric columns at all — just report the row count.
    return [f"Query returned {len(rows)} rows with no numeric columns to analyse."]


# ── AI-powered insights ───────────────────────────────────────────────────────

def _format_rows_for_prompt(rows: list[dict]) -> str:
    """
    Serialise up to 50 result rows into a plain-text table for the AI prompt.

    We use a simple pipe-delimited format that models read easily.
    Decimal values are converted to plain floats so they display cleanly.
    Passwords, API keys, and other secrets are never in rows — they come
    straight from the database query result, which is safe to share.
    """
    if not rows:
        return "(empty result)"

    # Cap at 50 rows to keep the prompt short.
    sample = rows[:50]
    headers = list(sample[0].keys())

    # Header line
    lines = ["| " + " | ".join(headers) + " |"]
    lines.append("| " + " | ".join("-" * len(h) for h in headers) + " |")

    for row in sample:
        cells = []
        for h in headers:
            val = row.get(h)
            # Convert Decimal to float for clean display in the prompt.
            if isinstance(val, Decimal):
                val = float(val)
            cells.append(str(val) if val is not None else "NULL")
        lines.append("| " + " | ".join(cells) + " |")

    if len(rows) > 50:
        lines.append(f"(... {len(rows) - 50} more rows not shown)")

    return "\n".join(lines)


def _pre_calculate_stats(rows: list[dict]) -> str:
    """
    Pre-calculate numeric statistics from the result rows in Python and
    return them as a plain-text block to include in the AI prompt.

    Purpose: LLMs are poor at arithmetic. By handing the model already-
    computed totals, averages, differences, and percentages we eliminate
    the main source of calculation errors (e.g. "approximately 934500"
    when the real total is 943750).

    Only columns where the majority of values are numeric are included.
    Decimal values are converted to float before arithmetic so there are
    no type errors.
    """
    if not rows:
        return ""

    columns = list(rows[0].keys())
    stats_lines: list[str] = []

    for col in columns:
        # Collect all non-None float values for this column.
        raw_vals = [row.get(col) for row in rows]
        float_vals: list[float] = []
        for v in raw_vals:
            if v is None:
                continue
            if isinstance(v, bool):
                continue
            try:
                float_vals.append(float(v))
            except (TypeError, ValueError):
                pass

        # Need at least half the rows to be numeric to count as a numeric col.
        if not float_vals or len(float_vals) / len(rows) < 0.5:
            continue

        total   = sum(float_vals)
        average = total / len(float_vals)
        maximum = max(float_vals)
        minimum = min(float_vals)

        # Format numbers cleanly: no unnecessary trailing zeros.
        def _f(n: float) -> str:
            if n == int(n):
                return f"{int(n):,}"
            return f"{n:,.2f}"

        stats_lines.append(
            f"  {col}: "
            f"total={_f(total)}, "
            f"average={_f(average)}, "
            f"max={_f(maximum)}, "
            f"min={_f(minimum)}, "
            f"rows={len(float_vals)}"
        )

        # For exactly 2 rows, include the difference so the model can quote it.
        if len(float_vals) == 2:
            diff = abs(float_vals[0] - float_vals[1])
            stats_lines.append(f"    difference between row 1 and row 2: {_f(diff)}")

        # Top-2 difference (useful for "close behind" insights).
        if len(float_vals) >= 2:
            sorted_vals = sorted(float_vals, reverse=True)
            gap = sorted_vals[0] - sorted_vals[1]
            stats_lines.append(
                f"    gap between highest and second-highest: {_f(gap)}"
            )

    if not stats_lines:
        return ""

    return "Pre-calculated statistics (USE THESE EXACT NUMBERS — do not recalculate):\n" + "\n".join(stats_lines)


def _build_insight_prompt(
    user_question: str,
    sql: str,
    rows: list[dict],
    knowledge_base: dict | None,
) -> list[dict]:
    """
    Build the AI message list for insight generation.

    Key improvement over the previous version
    ------------------------------------------
    Python pre-calculates all totals, averages, differences, and percentages
    and injects them as verified facts into the prompt.  The model is
    explicitly told to use those numbers instead of recalculating, which
    eliminates arithmetic estimation errors.

    System message: analyst role + strict numeric accuracy rules.
    User message:   question, SQL, row count, pre-computed stats,
                    formatted data table, and optional schema context
                    (table names only — no passwords, no API keys).

    Returns an OpenAI-compatible messages list.
    """
    system_content = (
        "You are a senior business analyst. "
        "You receive a user question, the SQL that answered it, and the query results. "
        "Your job is to write 3 to 5 short, accurate business insights.\n\n"

        "NUMERIC ACCURACY RULES (follow these strictly):\n"
        "- The prompt includes a 'Pre-calculated statistics' section with exact totals, "
        "averages, max, min, and gaps already computed in Python.\n"
        "- USE THOSE EXACT NUMBERS. Do NOT recalculate them yourself.\n"
        "- Do NOT round, estimate, or use the word 'approximately' for any value "
        "that appears in the pre-calculated statistics or result table.\n"
        "- If you are unsure of a calculation, DO NOT state the number — omit it.\n"
        "- Do NOT invent percentages or ratios unless you can derive them exactly "
        "from the numbers in the result table.\n"
        "- Copy numeric values exactly as they appear (e.g. 943750.00 not 934500).\n\n"

        "CONTENT RULES:\n"
        "- Use ONLY the values present in the result table and pre-calculated stats.\n"
        "- Do NOT invent names, facts, or data that are not in the result.\n"
        "- Do NOT mention columns that are not in the result.\n"
        "- Do NOT output SQL, markdown tables, or code blocks.\n"
        "- Each insight must be ONE sentence.\n"
        "- Start every insight line with '- ' (dash space).\n"
        "- Use business language: mention highest/lowest, totals, averages, "
        "trends, concentration, comparisons, or anomalies when visible.\n"
        "- If the result is empty, output exactly: - No data was found for this query.\n\n"

        "EXAMPLE (correct vs wrong):\n"
        "Result rows: Nikhil Gupta=265000.00, Karan Shah=249500.00, "
        "Sneha Iyer=153350.00, Aditya Verma=142400.00, Sahil Khan=133500.00\n"
        "Pre-calculated total: 943750.00\n"
        "CORRECT: - The top 5 customers together generated 943750.00 in total sales.\n"
        "WRONG:   - The top 5 customers generated approximately 934500.00 in total sales.\n"
        "WRONG:   - The top 5 customers generated around 940000 in sales."
    )

    # Pre-calculate stats in Python — model uses these exact numbers.
    stats_block = _pre_calculate_stats(rows)

    # Optional schema context: only table names (never full KB JSON).
    schema_hint = ""
    if knowledge_base:
        table_names = ", ".join(knowledge_base.keys())
        schema_hint = f"\nDatabase tables available: {table_names}\n"

    rows_text = _format_rows_for_prompt(rows)

    user_content = (
        f"User question: {user_question}\n"
        f"SQL executed:\n{sql}\n"
        f"Total rows returned: {len(rows)}\n"
        f"{schema_hint}"
        + (f"\n{stats_block}\n" if stats_block else "")
        + f"\nQuery result:\n{rows_text}\n\n"
        "Generate 3 to 5 business insights from the result above. "
        "Use the pre-calculated statistics for any totals or averages. "
        "Start each insight with '- '."
    )

    return [
        {"role": "system", "content": system_content},
        {"role": "user",   "content": user_content},
    ]


def generate_ai_insights(
    user_question: str,
    sql: str,
    rows: list[dict],
    knowledge_base: dict | None = None,
    backend: str = "local",
) -> list[str]:
    """
    Call the configured AI backend to generate business insights.

    This function may raise — the caller (generate_insights) wraps it in
    a try/except and falls back to rule-based insights on any failure.

    Safety rules enforced here
    --------------------------
    - Maximum 50 rows sent to the AI (prompt stays short and cheap).
    - No passwords or API keys are ever included in the prompt.
    - Knowledge base context is limited to table names only.

    Args:
        user_question:  Original user question.
        sql:            The SQL that was executed.
        rows:           Query results as a list of row dicts.
        knowledge_base: Optional knowledge base for schema context.
        backend:        "local" (Ollama) or "openai".

    Returns:
        List of 3–5 insight strings.

    Raises:
        RuntimeError / ConnectionError on backend failure.
        ValueError if the OpenAI key is missing.
    """
    messages = _build_insight_prompt(user_question, sql, rows, knowledge_base)

    raw_response = ""

    if backend == "nvidia":
        # ── NVIDIA path ───────────────────────────────────────────────────
        api_key = os.getenv("NVIDIA_API_KEY", "")
        if not api_key or not api_key.strip():
            raise ValueError("NVIDIA_API_KEY is missing.")
        try:
            import requests  # lazy import
            base_url = os.getenv("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1")
            model = os.getenv("NVIDIA_MODEL", "nvidia/nemotron-3-ultra-550b-a55b")
            headers = {
                "Authorization": f"Bearer {api_key.strip()}",
                "Content-Type": "application/json"
            }
            resp = requests.post(
                f"{base_url}/chat/completions",
                json={
                    "model": model,
                    "messages": messages,
                    "temperature": 0,
                },
                headers=headers,
                timeout=30
            )
            resp.raise_for_status()
            data = resp.json()
            raw_response = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        except Exception as exc:
            raise RuntimeError(f"NVIDIA insight generation failed: {exc}") from exc

    else:
        # ── Local Ollama path ─────────────────────────────────────────────
        try:
            import requests  # lazy import
        except ImportError:
            raise RuntimeError("The 'requests' package is required for the local backend.")

        model   = (os.getenv("LOCAL_MODEL") or "llama3").strip() or "llama3"
        payload = {
            "model":   model,
            "messages": messages,
            "stream":  False,
            "options": {"temperature": 0},  # deterministic output
        }
        try:
            resp = requests.post(
                "http://localhost:11434/api/chat",
                json=payload,
                timeout=60,
            )
            resp.raise_for_status()
            data = resp.json()
            raw_response = (data.get("message") or {}).get("content", "")
        except requests.exceptions.ConnectionError as exc:
            raise ConnectionError(
                "Local Ollama server is not running. Start it with: ollama serve"
            ) from exc
        except Exception as exc:
            raise RuntimeError(f"Ollama insight generation failed: {exc}") from exc

    # ── Parse response: extract lines that start with "- " ────────────────
    # The prompt instructs the model to prefix every insight with "- ".
    # We strip that prefix before returning so the CLI adds its own bullet.
    insights: list[str] = []
    for line in raw_response.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        # Remove leading "- " or "-" or "•" or "*" bullet markers.
        cleaned = stripped.lstrip("-•* ").strip()
        # Skip empty lines and lines that look like headers or markdown.
        if cleaned and not cleaned.startswith("#") and not cleaned.startswith("```"):
            insights.append(cleaned)
        if len(insights) >= 5:
            break

    return insights if insights else ["AI did not return any insights."]


# ── Public entry point ────────────────────────────────────────────────────────

def generate_insights(
    user_question: str,
    sql: str,
    rows: list[dict],
    knowledge_base: dict | None = None,
    backend: str = "local",
) -> list[str]:
    """
    Generate business insights for a SQL query result.

    Default behaviour (ENABLE_AI_INSIGHTS=true)
    --------------------------------------------
    1. Try AI-powered insights first using the configured backend.
    2. If AI fails for any reason, print a warning and fall back to
       rule-based insights automatically.

    Override (ENABLE_AI_INSIGHTS=false)
    ------------------------------------
    Skip AI entirely and use rule-based insights only.

    This function NEVER raises — any unexpected error is returned as
    an insight string so the CLI always has something to display.

    Args:
        user_question:  Original natural language question from the user.
        sql:            The SQL that was executed.
        rows:           Query result as a list of row dicts.
        knowledge_base: Optional knowledge base dict for schema context.
                        Only table names are sent to the AI — the full
                        JSON is never forwarded.
        backend:        AI backend to use ("local" or "openai").

    Returns:
        A list of insight strings (always at least one entry).
    """
    try:
        # Check env var — default is "true" so AI insights are primary.
        enable_ai = os.getenv("ENABLE_AI_INSIGHTS", "true").strip().lower() != "false"

        if enable_ai:
            try:
                # PRIMARY: AI-generated insights.
                return generate_ai_insights(
                    user_question=user_question,
                    sql=sql,
                    rows=rows,
                    knowledge_base=knowledge_base,
                    backend=backend,
                )
            except Exception as ai_err:
                # AI failed — warn the user and fall back to rules.
                print(f"  AI insight generation failed. Using rule-based insights.")

        # FALLBACK (or ENABLE_AI_INSIGHTS=false): rule-based insights.
        return _rule_based_insights(rows)

    except Exception as exc:
        # Last-resort catch — insight errors must never crash the CLI.
        return [f"Insight generation skipped due to error: {exc}"]
