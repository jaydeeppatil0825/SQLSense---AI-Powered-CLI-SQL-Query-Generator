import { describe, expect, it } from "vitest";
import { csvCell, exportCsv, exportJson, safeResultFilename } from "./exportResult";

describe("result export helpers", () => {
  it("escapes CSV cells and protects formulas", () => {
    expect(csvCell('a,b"c')).toBe('"a,b""c"');
    expect(csvCell("line\r\nbreak")).toBe('"line\r\nbreak"');
    expect(csvCell("=SUM(A1:A2)")).toBe("'=SUM(A1:A2)");
    expect(csvCell("+1")).toBe("'+1");
    expect(csvCell("-1")).toBe("'-1");
    expect(csvCell("@cmd")).toBe("'@cmd");
    expect(csvCell(null)).toBe("");
  });

  it("preserves dynamic column order", () => {
    const csv = exportCsv(["b", "a"], [{ a: 1, b: "two" }]);
    expect(csv).toBe("b,a\r\ntwo,1");
  });

  it("exports valid JSON with result data only", () => {
    expect(JSON.parse(exportJson(["name"], [{ name: "Acme" }]))).toEqual({
      columns: ["name"],
      rows: [{ name: "Acme" }],
    });
  });

  it("uses a sanitized timestamp filename", () => {
    expect(safeResultFilename("csv", new Date("2026-01-02T03:04:05"))).toBe("sqlsense-result-2026-01-02-030405.csv");
  });
});
