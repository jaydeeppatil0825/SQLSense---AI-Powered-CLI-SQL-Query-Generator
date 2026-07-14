import type { QueryRow } from "../../gateway/contracts";

function timestamp(date = new Date()): string {
  const pad = (value: number) => String(value).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}-${pad(date.getHours())}${pad(date.getMinutes())}${pad(date.getSeconds())}`;
}

export function safeResultFilename(extension: "csv" | "json", date = new Date()): string {
  return `sqlsense-result-${timestamp(date)}.${extension}`;
}

function protectFormula(value: string): string {
  return /^[=+\-@]/.test(value) ? `'${value}` : value;
}

export function csvCell(value: unknown): string {
  if (value === null || value === undefined) {
    return "";
  }
  const text = protectFormula(String(value));
  return /[",\r\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

export function exportCsv(columns: string[], rows: QueryRow[]): string {
  return [columns.map(csvCell).join(","), ...rows.map((row) => columns.map((column) => csvCell(row[column])).join(","))].join("\r\n");
}

export function exportJson(columns: string[], rows: QueryRow[]): string {
  return JSON.stringify({ columns, rows }, null, 2);
}

export function downloadText(filename: string, contents: string, type: string) {
  const blob = new Blob([contents], { type });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(url);
}
