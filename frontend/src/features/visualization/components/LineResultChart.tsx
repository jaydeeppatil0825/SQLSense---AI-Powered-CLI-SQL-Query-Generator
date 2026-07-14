import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { toFiniteNumber } from "../eligibility/columnTypes";
import { formatAxisLabel } from "../formatting/formatAxisLabel";
import { formatChartValue } from "../formatting/formatChartValue";
import type { ResultRow } from "../visualization.types";

export function LineResultChart({ rows, dateColumn, valueColumn }: { rows: ResultRow[]; dateColumn: string; valueColumn: string }) {
  const data = rows
    .map((row, index) => ({
      label: String(row[dateColumn]),
      value: toFiniteNumber(row[valueColumn]),
      timestamp: Date.parse(String(row[dateColumn])),
      sourceIndex: index,
    }))
    .sort((a, b) => a.timestamp - b.timestamp || a.sourceIndex - b.sourceIndex);

  return (
    <div className="h-80 w-full" aria-label="View trend chart">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 12, right: 16, bottom: 24, left: 8 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#334155" />
          <XAxis dataKey="label" tickFormatter={formatAxisLabel} stroke="#cbd5e1" />
          <YAxis stroke="#cbd5e1" tickFormatter={formatChartValue} />
          <Tooltip formatter={(value) => formatChartValue(value)} labelFormatter={(label) => `${dateColumn}: ${label}`} />
          <Line type="monotone" dataKey="value" name={valueColumn} stroke="#22c55e" strokeWidth={3} dot />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
