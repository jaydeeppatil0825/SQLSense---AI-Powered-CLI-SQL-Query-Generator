import type { ChartEligibility, ChartType, ResultRow } from "../visualization.types";
import { BarResultChart } from "./BarResultChart";
import { ChartControls } from "./ChartControls";
import { KpiResult } from "./KpiResult";
import { LineResultChart } from "./LineResultChart";

export function ChartPanel({
  rows,
  eligibility,
  type,
  categoryColumn,
  valueColumn,
  onTypeChange,
  onCategoryChange,
  onValueChange,
  onReset,
}: {
  rows: ResultRow[];
  eligibility: ChartEligibility;
  type: ChartType;
  categoryColumn: string;
  valueColumn: string;
  onTypeChange: (type: ChartType) => void;
  onCategoryChange: (column: string) => void;
  onValueChange: (column: string) => void;
  onReset: () => void;
}) {
  return (
    <>
      <p className="text-sm text-slate-300">
        Chart view of {valueColumn}
        {type === "bar" ? ` by ${categoryColumn}` : null}
        {type === "line" ? ` over ${categoryColumn}` : null}. The table remains the source of truth.
      </p>
      <ChartControls
        eligibility={eligibility}
        type={type}
        categoryColumn={categoryColumn}
        valueColumn={valueColumn}
        onTypeChange={onTypeChange}
        onCategoryChange={onCategoryChange}
        onValueChange={onValueChange}
        onReset={onReset}
      />
      {type === "bar" ? <BarResultChart rows={rows} categoryColumn={categoryColumn} valueColumn={valueColumn} /> : null}
      {type === "line" ? <LineResultChart rows={rows} dateColumn={categoryColumn} valueColumn={valueColumn} /> : null}
      {type === "kpi" ? <KpiResult row={rows[0]} valueColumn={valueColumn} /> : null}
    </>
  );
}
