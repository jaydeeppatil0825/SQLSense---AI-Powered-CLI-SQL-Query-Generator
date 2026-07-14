export function formatAxisLabel(value: unknown): string {
  const label = value === null || value === undefined ? "No value" : String(value);
  return label.length > 24 ? `${label.slice(0, 21)}...` : label;
}
