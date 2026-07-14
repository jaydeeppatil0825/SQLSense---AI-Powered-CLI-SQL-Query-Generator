import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { toFiniteNumber } from "../eligibility/columnTypes";
import { formatAxisLabel } from "../formatting/formatAxisLabel";
import { formatChartValue } from "../formatting/formatChartValue";
import type { ResultRow } from "../visualization.types";

export function BarResultChart({ rows, categoryColumn, valueColumn }: { rows: ResultRow[]; categoryColumn: string; valueColumn: string }) {
  const data = rows.map((row, index) => ({
    label: String(row[categoryColumn]),
    value: toFiniteNumber(row[valueColumn]),
    sourceIndex: index + 1,
  }));

  return (
    <div className="h-80 w-full" aria-label="Compare values chart">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} margin={{ top: 12, right: 16, bottom: 24, left: 8 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#334155" />
          <XAxis dataKey="label" tickFormatter={formatAxisLabel} stroke="#cbd5e1" />
          <YAxis stroke="#cbd5e1" tickFormatter={formatChartValue} />
          <Tooltip formatter={(value) => formatChartValue(value)} labelFormatter={(label) => `${categoryColumn}: ${label}`} />
          <Bar dataKey="value" name={valueColumn} fill="#38bdf8" radius={[8, 8, 0, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
