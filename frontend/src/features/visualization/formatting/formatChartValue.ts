export function formatChartValue(value: unknown): string {
  if (value === null || value === undefined) return "No value";
  if (typeof value === "number") return new Intl.NumberFormat().format(value);
  return String(value);
}
