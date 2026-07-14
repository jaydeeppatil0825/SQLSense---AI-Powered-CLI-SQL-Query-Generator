import type { ReactNode } from "react";

export function VisualizationTabs({
  mode,
  canChart,
  onModeChange,
}: {
  mode: "table" | "chart";
  canChart: boolean;
  onModeChange: (mode: "table" | "chart") => void;
}) {
  return (
    <div className="flex gap-2" role="tablist" aria-label="Result view">
      <button type="button" role="tab" aria-selected={mode === "table"} className={tabClass(mode === "table")} onClick={() => onModeChange("table")}>
        Table
      </button>
      {canChart ? (
        <button type="button" role="tab" aria-selected={mode === "chart"} className={tabClass(mode === "chart")} onClick={() => onModeChange("chart")}>
          Chart
        </button>
      ) : null}
    </div>
  );
}

function tabClass(active: boolean): string {
  return `rounded-xl px-3 py-2 text-sm font-semibold transition ${
    active ? "bg-signal-500 text-ink-950" : "border border-slate-600/70 bg-slate-900/60 text-slate-100 hover:border-slate-400"
  }`;
}
