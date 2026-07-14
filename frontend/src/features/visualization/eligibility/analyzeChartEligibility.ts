import { BAR_CATEGORY_LIMIT, LINE_POINT_LIMIT } from "./visualizationLimits";
import { isDateColumn, isNumericColumn, isTextColumn } from "./columnTypes";
import type { ChartEligibility, ChartOption, ResultRow } from "../visualization.types";

function hasDuplicateValues(column: string, rows: ResultRow[]): boolean {
  const seen = new Set<string>();
  for (const row of rows) {
    const key = String(row[column]);
    if (seen.has(key)) return true;
    seen.add(key);
  }
  return false;
}

export function analyzeChartEligibility(columns: string[], rows: ResultRow[]): ChartEligibility {
  if (rows.length === 0) {
    return { eligible: [], textColumns: [], numericColumns: [], dateColumns: [], unavailableReason: "This result is best viewed as a table." };
  }

  const numericColumns = columns.filter((name) => isNumericColumn(name, rows)).map((name) => ({ name, kind: "number" as const }));
  const dateColumns = columns.filter((name) => isDateColumn(name, rows)).map((name) => ({ name, kind: "date" as const }));
  const rawTextColumns = columns
    .filter((name) => !numericColumns.some((column) => column.name === name) && !dateColumns.some((column) => column.name === name) && isTextColumn(name, rows))
    .map((name) => ({ name, kind: "text" as const }));
  const textColumns = rawTextColumns.filter((column) => !hasDuplicateValues(column.name, rows));

  const eligible: ChartOption[] = [];
  const numeric = numericColumns[0]?.name;

  if (numeric && textColumns.length > 0 && rows.length <= BAR_CATEGORY_LIMIT) {
    const category = textColumns.find((column) => !hasDuplicateValues(column.name, rows))?.name;
    if (category) {
      eligible.push({ type: "bar", label: "Compare values", categoryColumn: category, valueColumn: numeric });
    }
  }

  if (numeric && dateColumns.length > 0 && rows.length <= LINE_POINT_LIMIT) {
    eligible.push({ type: "line", label: "View trend", dateColumn: dateColumns[0].name, valueColumn: numeric });
  }

  if (rows.length === 1 && numeric) {
    eligible.push({ type: "kpi", label: "Key result", valueColumn: numeric });
  }

  let unavailableReason = eligible.length ? null : "This result is best viewed as a table.";
  if (!numeric) unavailableReason = "This result has no numeric value to chart.";
  if (textColumns.length > 0 && numeric && rows.length > BAR_CATEGORY_LIMIT) {
    unavailableReason = "Too many values to chart clearly. Refine your question to return fewer categories or a grouped time period.";
  }
  if (dateColumns.length > 0 && numeric && rows.length > LINE_POINT_LIMIT) {
    unavailableReason = "Too many values to chart clearly. Refine your question to return fewer categories or a grouped time period.";
  }
  if (rawTextColumns.length > 0 && textColumns.length === 0 && numeric && eligible.length === 0) {
    unavailableReason = "Duplicate categories need a grouped question before they can be charted.";
  }

  return { eligible, textColumns, numericColumns, dateColumns, unavailableReason };
}
