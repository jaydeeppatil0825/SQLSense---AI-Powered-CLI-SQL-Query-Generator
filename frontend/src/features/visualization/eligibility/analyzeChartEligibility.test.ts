import { describe, expect, it } from "vitest";
import { analyzeChartEligibility } from "./analyzeChartEligibility";

describe("analyzeChartEligibility", () => {
  it("allows text plus numeric bar charts", () => {
    const result = analyzeChartEligibility(["city", "amount"], [{ city: "Mumbai", amount: 10 }]);
    expect(result.eligible.some((option) => option.type === "bar")).toBe(true);
  });

  it("allows date plus numeric line charts", () => {
    const result = analyzeChartEligibility(["date", "amount"], [{ date: "2026-01-01", amount: 10 }]);
    expect(result.eligible.some((option) => option.type === "line")).toBe(true);
  });

  it("allows one-row numeric KPI", () => {
    const result = analyzeChartEligibility(["total"], [{ total: 42 }]);
    expect(result.eligible.some((option) => option.type === "kpi")).toBe(true);
  });

  it("disables empty and non-numeric results", () => {
    expect(analyzeChartEligibility(["city"], []).eligible).toHaveLength(0);
    expect(analyzeChartEligibility(["city"], [{ city: "Mumbai" }]).unavailableReason).toBe("This result has no numeric value to chart.");
  });

  it("does not blindly accept invalid numeric strings", () => {
    const result = analyzeChartEligibility(["city", "amount"], [{ city: "Mumbai", amount: "10x" }]);
    expect(result.numericColumns).toHaveLength(0);
  });

  it("rejects duplicate category values instead of aggregating", () => {
    const result = analyzeChartEligibility(["city", "amount"], [{ city: "Mumbai", amount: 10 }, { city: "Mumbai", amount: 20 }]);
    expect(result.eligible.some((option) => option.type === "bar")).toBe(false);
    expect(result.unavailableReason).toContain("Duplicate categories");
  });

  it("returns controlled reasons for excessive result sizes", () => {
    const rows = Array.from({ length: 51 }, (_, index) => ({ city: `C${index}`, amount: index }));
    expect(analyzeChartEligibility(["city", "amount"], rows).unavailableReason).toContain("Too many values");
  });

  it("handles null-heavy and unsupported object values safely", () => {
    const result = analyzeChartEligibility(["city", "amount", "meta"], [{ city: null, amount: 10, meta: { x: 1 } }]);
    expect(result.numericColumns.map((column) => column.name)).toEqual(["amount"]);
  });
});
