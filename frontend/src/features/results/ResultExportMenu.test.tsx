import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ResultExportMenu } from "./ResultExportMenu";

describe("ResultExportMenu", () => {
  beforeEach(() => {
    global.fetch = vi.fn();
    URL.createObjectURL = vi.fn(() => "blob:test");
    URL.revokeObjectURL = vi.fn();
    HTMLAnchorElement.prototype.click = vi.fn();
  });

  it("exports current browser rows without calling the gateway", async () => {
    render(
      <ResultExportMenu
        result={{
          sql: "SELECT 1",
          columns: ["name"],
          rows: [{ name: "Acme" }],
          row_count: 1,
        }}
      />,
    );

    await userEvent.click(screen.getByRole("button", { name: "Export CSV" }));
    await userEvent.click(screen.getByRole("button", { name: "Export JSON" }));

    expect(global.fetch).not.toHaveBeenCalled();
    expect(URL.createObjectURL).toHaveBeenCalledTimes(2);
  });
});
