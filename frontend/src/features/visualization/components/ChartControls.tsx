import type { ChartEligibility, ChartType } from "../visualization.types";

export function ChartControls({
  eligibility,
  type,
  categoryColumn,
  valueColumn,
  onTypeChange,
  onCategoryChange,
  onValueChange,
  onReset,
}: {
  eligibility: ChartEligibility;
  type: ChartType;
  categoryColumn: string;
  valueColumn: string;
  onTypeChange: (type: ChartType) => void;
  onCategoryChange: (column: string) => void;
  onValueChange: (column: string) => void;
  onReset: () => void;
}) {
  const categoryOptions = type === "line" ? eligibility.dateColumns : eligibility.textColumns;
  return (
    <div className="grid gap-3 rounded-2xl border border-white/10 bg-slate-950/40 p-4 md:grid-cols-4">
      <label className="grid gap-1 text-xs font-semibold text-slate-300">
        Chart
        <select className="rounded-xl border border-white/10 bg-slate-950 px-3 py-2 text-sm text-white" value={type} onChange={(event) => onTypeChange(event.target.value as ChartType)}>
          {eligibility.eligible.map((option) => (
            <option key={option.type} value={option.type}>
              {option.label}
            </option>
          ))}
        </select>
      </label>
      {type !== "kpi" ? (
        <label className="grid gap-1 text-xs font-semibold text-slate-300">
          {type === "line" ? "Select date" : "Select category"}
          <select className="rounded-xl border border-white/10 bg-slate-950 px-3 py-2 text-sm text-white" value={categoryColumn} onChange={(event) => onCategoryChange(event.target.value)}>
            {categoryOptions.map((column) => (
              <option key={column.name} value={column.name}>
                {column.name}
              </option>
            ))}
          </select>
        </label>
      ) : null}
      <label className="grid gap-1 text-xs font-semibold text-slate-300">
        Select value
        <select className="rounded-xl border border-white/10 bg-slate-950 px-3 py-2 text-sm text-white" value={valueColumn} onChange={(event) => onValueChange(event.target.value)}>
          {eligibility.numericColumns.map((column) => (
            <option key={column.name} value={column.name}>
              {column.name}
            </option>
          ))}
        </select>
      </label>
      <button type="button" className="self-end rounded-xl border border-slate-600/70 bg-slate-900/60 px-3 py-2 text-sm font-semibold text-slate-100" onClick={onReset}>
        Reset visualization
      </button>
    </div>
  );
}
