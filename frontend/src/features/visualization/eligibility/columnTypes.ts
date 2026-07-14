import type { ResultRow } from "../visualization.types";

const numberPattern = /^[+-]?(?:\d+\.?\d*|\.\d+)$/;

export function toFiniteNumber(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string" && numberPattern.test(value.trim())) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

export function isNumericColumn(column: string, rows: ResultRow[]): boolean {
  const values = rows.map((row) => row[column]).filter((value) => value !== null && value !== undefined);
  return values.length > 0 && values.every((value) => toFiniteNumber(value) !== null);
}

export function isDateColumn(column: string, rows: ResultRow[]): boolean {
  const values = rows.map((row) => row[column]).filter((value) => value !== null && value !== undefined);
  return (
    values.length > 0 &&
    values.every((value) => {
      if (value instanceof Date) return !Number.isNaN(value.getTime());
      if (typeof value !== "string") return false;
      const trimmed = value.trim();
      if (!/^\d{4}-\d{2}-\d{2}(?:[T\s]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2})?)?$/.test(trimmed)) return false;
      return !Number.isNaN(Date.parse(trimmed));
    })
  );
}

export function isTextColumn(column: string, rows: ResultRow[]): boolean {
  const values = rows.map((row) => row[column]).filter((value) => value !== null && value !== undefined);
  return values.length > 0 && values.every((value) => ["string", "boolean"].includes(typeof value));
}
