import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ResultVisualization } from "./ResultVisualization";

describe("ResultVisualization", () => {
  it("shows table by default and chart only when eligible", async () => {
    global.fetch = vi.fn();
    render(<ResultVisualization columns={["city", "amount"]} rows={[{ city: "Mumbai", amount: 10 }]} />);

    expect(screen.getByRole("tab", { name: "Table" })).toHaveAttribute("aria-selected", "true");
    await userEvent.click(screen.getByRole("tab", { name: "Chart" }));
    expect(screen.getByText("Select category")).toBeInTheDocument();
    expect(global.fetch).not.toHaveBeenCalled();
  });

  it("keeps chart tab hidden when unavailable", () => {
    render(<ResultVisualization columns={["city"]} rows={[{ city: "Mumbai" }]} />);

    expect(screen.queryByRole("tab", { name: "Chart" })).not.toBeInTheDocument();
    expect(screen.getByText("This result has no numeric value to chart.")).toBeInTheDocument();
  });

  it("supports KPI values", async () => {
    render(<ResultVisualization columns={["total"]} rows={[{ total: 99 }]} />);

    await userEvent.click(screen.getByRole("tab", { name: "Chart" }));
    await userEvent.selectOptions(screen.getByLabelText("Chart"), "kpi");
    expect(screen.getByText("99")).toBeInTheDocument();
  });
});
