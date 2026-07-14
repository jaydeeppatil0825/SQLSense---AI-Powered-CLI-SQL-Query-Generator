export function formatDisplayValue(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (typeof value === "number") return new Intl.NumberFormat("en-US", { maximumFractionDigits: 6 }).format(value);
  if (typeof value === "string") {
    const trimmed = value.trim();
    if (/^\d{4}-\d{2}-\d{2}$/.test(trimmed)) return trimmed;
    if (/^\d{4}-\d{2}-\d{2}[T\s]\d{2}:\d{2}/.test(trimmed) && !Number.isNaN(Date.parse(trimmed))) {
      return new Date(trimmed).toLocaleString();
    }
    return trimmed.length > 140 ? `${trimmed.slice(0, 137)}...` : trimmed;
  }
  return "Unsupported value";
}
