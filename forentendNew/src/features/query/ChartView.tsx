import { useMemo } from "react";
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid } from "recharts";
import type { QueryResponse } from "@/api/types";

export function ChartView({ result }: { result: QueryResponse }) {
  const { data, labelKey, valueKey } = useMemo(() => {
    const cols = result.columns ?? [];
    const rows = result.rows ?? [];
    const num = cols.find((c) => c.numeric);
    const dim = cols.find((c) => !c.numeric);
    if (!num || !dim || rows.length === 0)
      return { data: [] as Array<Record<string, unknown>>, labelKey: "", valueKey: "" };
    return {
      labelKey: dim.key,
      valueKey: num.key,
      data: rows.slice(0, 20).map((r) => ({
        ...r,
        [num.key]: typeof r[num.key] === "string" ? Number(r[num.key]) : r[num.key],
      })),
    };
  }, [result]);

  if (!data.length) {
    return (
      <div className="rounded-xl border border-dashed border-border bg-card p-10 text-center text-sm text-muted-foreground">
        No chart-friendly columns detected in these results.
      </div>
    );
  }

  return (
    <div className="rounded-xl border border-border bg-card p-4 animate-fade-in">
      <div className="text-[11px] uppercase tracking-wider text-muted-foreground mb-3">
        {valueKey} by {labelKey}
      </div>
      <div className="h-72 w-full">
        <ResponsiveContainer>
          <BarChart data={data} margin={{ top: 8, right: 12, left: 4, bottom: 8 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="var(--divider)" />
            <XAxis dataKey={labelKey} tick={{ fontSize: 11, fill: "var(--text-2)" }} />
            <YAxis tick={{ fontSize: 11, fill: "var(--text-2)" }} />
            <Tooltip
              contentStyle={{
                background: "var(--surface)",
                border: "1px solid var(--border-c)",
                borderRadius: 10,
                fontSize: 12,
              }}
            />
            <Bar dataKey={valueKey} fill="var(--accent-c)" radius={[6, 6, 0, 0]} />
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
