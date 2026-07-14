import { formatChartValue } from "../formatting/formatChartValue";
import type { ResultRow } from "../visualization.types";

export function KpiResult({ row, valueColumn }: { row: ResultRow; valueColumn: string }) {
  return (
    <div className="rounded-3xl border border-signal-500/30 bg-signal-500/10 p-8 text-center">
      <div className="text-sm font-bold uppercase tracking-[0.3em] text-signal-300">Key result</div>
      <div className="mt-4 text-5xl font-black text-white">{formatChartValue(row[valueColumn])}</div>
      <div className="mt-3 text-sm text-slate-300">{valueColumn}</div>
    </div>
  );
}
