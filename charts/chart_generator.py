"""
charts/chart_generator.py
==========================
Analyses SQL query results and generates the most appropriate chart
using matplotlib.  No seaborn or other charting libraries are used.

Public API
----------
  detect_chart_type(rows)
      → Returns a chart-type string or None when a chart is not suitable.

  generate_chart(rows, chart_type=None, output_path="output/chart.png")
      → Draws the chart and saves it to *output_path*.
      → Returns the final output path on success.
      → Raises ValueError when the data is not chartable.

Supported chart types
---------------------
  "bar"           — 1 text/category column + 1 numeric column
  "line"          — 1 date/time column + 1 numeric column
  "grouped_bar"   — 1 category column + 2+ numeric columns
  None            — result not suitable for a chart
"""

from __future__ import annotations

import os
from decimal import Decimal
from pathlib import Path


# ── Type detection helpers ────────────────────────────────────────────────────

# Words in a column name that suggest it holds date/time values.
_DATE_HINTS = {
    "date", "month", "year", "week", "day", "time",
    "quarter", "period", "created", "updated", "at",
}

# Python numeric types recognised for chart axis values.
# Decimal is included because MySQL aggregate functions (SUM, AVG) return it.
# bool is excluded explicitly — True/False are subclasses of int in Python
# but should not be treated as numeric data for charting purposes.
_NUMERIC_TYPES = (int, float, Decimal)


def _is_numeric_value(value) -> bool:
    """
    Return True when *value* is a real number suitable for a chart axis.

    Accepts int, float, and decimal.Decimal (returned by MySQL aggregates).
    Explicitly rejects bool even though bool is a subclass of int in Python.
    """
    return isinstance(value, _NUMERIC_TYPES) and not isinstance(value, bool)


def _is_numeric_column(values: list) -> bool:
    """
    Return True if the majority of non-None values in *values* are numeric.

    A column is considered numeric when more than half its non-null values
    pass _is_numeric_value().  This tolerates a few None/null entries.
    """
    non_null = [v for v in values if v is not None]
    if not non_null:
        return False
    numeric_count = sum(1 for v in non_null if _is_numeric_value(v))
    return numeric_count / len(non_null) >= 0.5


def _is_date_column(col_name: str) -> bool:
    """
    Return True when the column name contains a date/time hint word.

    For example: "order_month", "created_at", "payment_date" all match.
    """
    name_lower = col_name.lower()
    return any(hint in name_lower for hint in _DATE_HINTS)


def _column_values(rows: list[dict], col: str) -> list:
    """Extract all values for *col* from the list of row dicts."""
    return [row.get(col) for row in rows]


def _coerce_numeric(values: list) -> list[float]:
    """
    Convert a list of values to floats, replacing non-convertible entries
    with 0.0 so the chart always has something to plot.
    """
    result = []
    for v in values:
        try:
            result.append(float(v))
        except (TypeError, ValueError):
            result.append(0.0)
    return result


# ── Chart type detection ──────────────────────────────────────────────────────

def detect_chart_type(rows: list[dict]) -> str | None:
    """
    Inspect *rows* and decide which chart type best represents the data.

    Decision logic
    --------------
    1. Need at least 2 rows and 2 columns to draw anything meaningful.
    2. Classify each column as numeric or text/category.
    3. If a text column looks like a date (name contains date hints), prefer
       a line chart over a bar chart.
    4. Exactly 1 text column + 1 numeric column  → bar (or line if date column)
    5. Exactly 1 text column + 2+ numeric columns → grouped bar
    6. All columns numeric (summary row)         → not suitable
    7. No numeric columns at all                 → not suitable

    Returns
    -------
    "bar", "line", "grouped_bar", or None.
    """
    if not rows or len(rows) < 2:
        # A single row is a scalar result — not meaningful as a chart.
        return None

    columns = list(rows[0].keys())
    if len(columns) < 2:
        return None

    # Classify each column.
    numeric_cols = [c for c in columns if _is_numeric_column(_column_values(rows, c))]
    text_cols    = [c for c in columns if c not in numeric_cols]

    num_numeric = len(numeric_cols)
    num_text    = len(text_cols)

    # No numeric data → cannot draw a chart.
    if num_numeric == 0:
        return None

    # All numeric (e.g. a single summary row with totals) → not suitable.
    if num_text == 0:
        return None

    # Exactly 1 text + 1 numeric → bar or line
    if num_text == 1 and num_numeric == 1:
        # Prefer line chart when the x-axis is a date/time column.
        if _is_date_column(text_cols[0]):
            return "line"
        return "bar"

    # 1 text + 2 or more numeric → grouped bar
    if num_text == 1 and num_numeric >= 2:
        return "grouped_bar"

    # Fallback: more than 1 text column with some numeric → plain bar on first pair.
    if num_text >= 1 and num_numeric >= 1:
        return "bar"

    return None


# ── Chart generation ──────────────────────────────────────────────────────────

def generate_chart(
    rows: list[dict],
    chart_type: str | None = None,
    output_path: str = "output/chart.png",
) -> str:
    """
    Generate a chart from *rows* and save it to *output_path*.

    Parameters
    ----------
    rows        : List of row dicts returned by execute_query().
    chart_type  : Override the auto-detected type ("bar", "line",
                  "grouped_bar").  Pass None to auto-detect.
    output_path : Where to save the PNG file.  Parent directories are
                  created automatically.

    Returns
    -------
    The absolute path where the chart was saved.

    Raises
    ------
    ValueError  : If the data is not suitable for any chart type.
    RuntimeError: If matplotlib fails to render or save the file.
    """
    # Lazy import so the rest of the app works even if matplotlib is not
    # installed (the chart feature simply won't be available).
    try:
        import matplotlib
        matplotlib.use("Agg")        # non-interactive backend — no display needed
        import matplotlib.pyplot as plt
    except ImportError:
        raise RuntimeError(
            "matplotlib is not installed. Run: pip install matplotlib"
        )

    # Auto-detect chart type if not provided.
    resolved_type = chart_type or detect_chart_type(rows)
    if resolved_type is None:
        raise ValueError("Chart not suitable for this result.")

    # Identify columns.
    columns  = list(rows[0].keys())
    num_cols = [c for c in columns if _is_numeric_column(_column_values(rows, c))]
    txt_cols = [c for c in columns if c not in num_cols]

    if not txt_cols or not num_cols:
        raise ValueError("Chart not suitable for this result.")

    x_col = txt_cols[0]                       # category / date axis
    x_labels = [str(row.get(x_col, "")) for row in rows]

    # ── Ensure output directory exists ───────────────────────────────────────
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    # ── Draw the chart ────────────────────────────────────────────────────────
    fig, ax = plt.subplots(figsize=(10, 6))

    if resolved_type in ("bar", "line"):
        y_col    = num_cols[0]
        y_values = _coerce_numeric(_column_values(rows, y_col))

        if resolved_type == "bar":
            x_pos = range(len(x_labels))
            ax.bar(x_pos, y_values, color="#4C72B0", edgecolor="white")
            ax.set_xticks(list(x_pos))
            ax.set_xticklabels(x_labels, rotation=45, ha="right")
        else:  # line
            ax.plot(x_labels, y_values, marker="o", color="#4C72B0", linewidth=2)
            ax.tick_params(axis="x", rotation=45)

        # Labels derived from column names (replace underscores for readability).
        ax.set_xlabel(_col_label(x_col))
        ax.set_ylabel(_col_label(y_col))
        ax.set_title(f"{_col_label(y_col)} by {_col_label(x_col)}")

    elif resolved_type == "grouped_bar":
        # Draw one bar group per numeric column, side by side.
        import numpy as np

        n_groups   = len(rows)
        n_series   = len(num_cols)
        bar_width  = 0.8 / n_series           # divide the slot evenly
        x_base     = np.arange(n_groups)

        colors = ["#4C72B0", "#DD8452", "#55A868", "#C44E52", "#8172B3"]
        for i, y_col in enumerate(num_cols):
            y_values = _coerce_numeric(_column_values(rows, y_col))
            offset   = (i - n_series / 2 + 0.5) * bar_width
            ax.bar(
                x_base + offset,
                y_values,
                width=bar_width,
                label=_col_label(y_col),
                color=colors[i % len(colors)],
                edgecolor="white",
            )

        ax.set_xticks(list(x_base))
        ax.set_xticklabels(x_labels, rotation=45, ha="right")
        ax.set_xlabel(_col_label(x_col))
        ax.set_ylabel("Value")
        ax.set_title(f"Comparison by {_col_label(x_col)}")
        ax.legend()

    else:
        plt.close(fig)
        raise ValueError(f"Unknown chart type: {resolved_type}")

    # ── Final formatting and save ─────────────────────────────────────────────
    ax.grid(axis="y", linestyle="--", alpha=0.5)
    plt.tight_layout()

    try:
        fig.savefig(output_path, dpi=150)
    except Exception as exc:
        raise RuntimeError(f"Failed to save chart to '{output_path}': {exc}") from exc
    finally:
        plt.close(fig)   # always release memory

    return str(Path(output_path).resolve())


def _col_label(col_name: str) -> str:
    """Convert a snake_case column name to a Title Case human-readable label."""
    return col_name.replace("_", " ").title()
