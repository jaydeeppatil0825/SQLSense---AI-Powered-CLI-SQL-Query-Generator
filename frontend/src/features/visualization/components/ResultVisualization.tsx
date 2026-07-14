import { lazy, Suspense, useEffect, useMemo, useState } from "react";
import { ResultTable } from "../../results/ResultTable";
import { analyzeChartEligibility } from "../eligibility/analyzeChartEligibility";
import type { ChartType, ResultRow } from "../visualization.types";
import { ChartErrorBoundary } from "./ChartErrorBoundary";
import { ChartUnavailable } from "./ChartUnavailable";
import { VisualizationTabs } from "./VisualizationTabs";

const ChartPanel = lazy(() => import("./ChartPanel").then((module) => ({ default: module.ChartPanel })));

export function ResultVisualization({ columns, rows }: { columns: string[]; rows: ResultRow[] }) {
  const eligibility = useMemo(() => analyzeChartEligibility(columns, rows), [columns, rows]);
  const [mode, setMode] = useState<"table" | "chart">("table");
  const defaultOption = eligibility.eligible[0];
  const [type, setType] = useState<ChartType>(defaultOption?.type ?? "bar");
  const [categoryColumn, setCategoryColumn] = useState(defaultOption?.categoryColumn ?? defaultOption?.dateColumn ?? "");
  const [valueColumn, setValueColumn] = useState(defaultOption?.valueColumn ?? "");
  const canChart = eligibility.eligible.length > 0;

  const reset = () => {
    const option = eligibility.eligible[0];
    setType(option?.type ?? "bar");
    setCategoryColumn(option?.categoryColumn ?? option?.dateColumn ?? "");
    setValueColumn(option?.valueColumn ?? "");
  };

  const changeType = (nextType: ChartType) => {
    const option = eligibility.eligible.find((item) => item.type === nextType);
    if (!option) return;
    setType(option.type);
    setCategoryColumn(option.categoryColumn ?? option.dateColumn ?? "");
    setValueColumn(option.valueColumn);
  };

  useEffect(() => {
    if (!canChart) {
      setMode("table");
      return;
    }
    reset();
  }, [canChart, columns, rows]);

  if (rows.length === 0) {
    return <ResultTable columns={columns} rows={rows} />;
  }

  return (
    <div className="grid gap-4">
      <VisualizationTabs mode={mode} canChart={canChart} onModeChange={setMode} />
      {mode === "table" ? (
        <>
          <ResultTable columns={columns} rows={rows} />
          {!canChart && eligibility.unavailableReason ? <ChartUnavailable reason={eligibility.unavailableReason} /> : null}
        </>
      ) : (
        <ChartErrorBoundary>
          <Suspense fallback={<ChartUnavailable reason="Loading chart view." />}>
            <ChartPanel
              rows={rows}
              eligibility={eligibility}
              type={type}
              categoryColumn={categoryColumn}
              valueColumn={valueColumn}
              onTypeChange={changeType}
              onCategoryChange={setCategoryColumn}
              onValueChange={setValueColumn}
              onReset={reset}
            />
          </Suspense>
        </ChartErrorBoundary>
      )}
    </div>
  );
}
