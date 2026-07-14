import { describe, expect, it } from "vitest";
import { formatDisplayValue } from "./formatDisplayValue";

describe("formatDisplayValue", () => {
  it("formats common display values without changing source semantics", () => {
    expect(formatDisplayValue(null)).toBe("—");
    expect(formatDisplayValue(true)).toBe("Yes");
    expect(formatDisplayValue(1234567)).toBe("1,234,567");
    expect(formatDisplayValue(-12.5)).toBe("-12.5");
    expect(formatDisplayValue("2026-01-02")).toBe("2026-01-02");
    expect(formatDisplayValue({ nested: true })).toBe("Unsupported value");
  });

  it("shortens long strings for display only", () => {
    expect(formatDisplayValue("a".repeat(150))).toHaveLength(140);
  });
});
