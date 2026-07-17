import { useMemo, useState } from "react";
import {
  ChevronLeft,
  ChevronRight,
  ChevronsUpDown,
  ArrowUp,
  ArrowDown,
  Download,
  Maximize2,
  Search,
  X,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import type { QueryResponse } from "@/api/types";
import { cn } from "@/lib/utils";
import { useAppStore } from "@/stores/app-store";

type SortState = { key: string; dir: "asc" | "desc" } | null;

export function ResultTable({ result }: { result: QueryResponse }) {
  const pageSize = useAppStore((s) => s.defaultPageSize) || 25;
  const [page, setPage] = useState(0);
  const [expanded, setExpanded] = useState(false);
  const [q, setQ] = useState("");
  const [sort, setSort] = useState<SortState>(null);

  const rows = result.rows ?? [];
  const columns = result.columns ?? [];

  const filtered = useMemo(() => {
    const needle = q.trim().toLowerCase();
    if (!needle) return rows;
    return rows.filter((r) =>
      columns.some((c) =>
        String(r[c.key] ?? "")
          .toLowerCase()
          .includes(needle),
      ),
    );
  }, [rows, q, columns]);

  const sorted = useMemo(() => {
    if (!sort) return filtered;
    const dir = sort.dir === "asc" ? 1 : -1;
    return [...filtered].sort((a, b) => {
      const av = a[sort.key];
      const bv = b[sort.key];
      const an = Number(av);
      const bn = Number(bv);
      if (!Number.isNaN(an) && !Number.isNaN(bn)) return (an - bn) * dir;
      return String(av ?? "").localeCompare(String(bv ?? "")) * dir;
    });
  }, [filtered, sort]);

  const totalPages = Math.max(1, Math.ceil(sorted.length / pageSize));
  const slice = useMemo(
    () => sorted.slice(page * pageSize, (page + 1) * pageSize),
    [sorted, page, pageSize],
  );

  if (columns.length === 0) return null;

  if (rows.length === 0) {
    return (
      <div className="rounded-xl border border-dashed border-border bg-card p-10 text-center animate-fade-in">
        <div className="mx-auto grid h-12 w-12 place-items-center rounded-full bg-secondary text-muted-foreground text-lg">
          ∅
        </div>
        <div className="mt-3 text-sm font-medium">Query completed — no matching rows</div>
        <div className="text-xs text-muted-foreground">Try adjusting your question or filters.</div>
      </div>
    );
  }

  function toggleSort(key: string) {
    setSort((s) =>
      s?.key === key ? (s.dir === "asc" ? { key, dir: "desc" } : null) : { key, dir: "asc" },
    );
    setPage(0);
  }

  function exportCsv() {
    const header = columns.map((c) => JSON.stringify(c.label)).join(",");
    const body = sorted
      .map((r) => columns.map((c) => JSON.stringify(r[c.key] ?? "")).join(","))
      .join("\n");
    const blob = new Blob([header + "\n" + body], { type: "text/csv" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "sqlsense-result.csv";
    a.click();
    URL.revokeObjectURL(url);
  }

  return (
    <div
      className={cn(
        "rounded-xl border border-border bg-card overflow-hidden animate-fade-in",
        expanded && "fixed inset-4 z-50 flex flex-col shadow-2xl",
      )}
    >
      <div className="flex flex-wrap items-center gap-2 border-b border-divider px-3 py-2">
        <div className="relative">
          <Search className="pointer-events-none absolute left-2 top-2 h-3.5 w-3.5 text-muted-foreground" />
          <Input
            value={q}
            onChange={(e) => {
              setQ(e.target.value);
              setPage(0);
            }}
            placeholder="Search results"
            className="h-8 w-44 pl-7 text-xs"
          />
        </div>
        <span className="mono text-[11px] text-muted-foreground">{sorted.length} rows</span>
        <span className="text-border">·</span>
        <span className="mono text-[11px] text-muted-foreground">{columns.length} cols</span>
        <div className="ml-auto flex items-center gap-1">
          <Button size="sm" variant="ghost" onClick={exportCsv}>
            <Download className="h-3.5 w-3.5" /> CSV
          </Button>
          <Button size="sm" variant="ghost" onClick={() => setExpanded((v) => !v)}>
            {expanded ? <X className="h-3.5 w-3.5" /> : <Maximize2 className="h-3.5 w-3.5" />}
          </Button>
        </div>
      </div>
      <div className={cn("overflow-auto", expanded ? "flex-1" : "max-h-[520px]")}>
        <table className="w-full text-sm">
          <thead className="sticky top-0 bg-surface-2 text-[11px] uppercase tracking-wider text-muted-foreground z-10">
            <tr>
              {columns.map((c) => {
                const active = sort?.key === c.key;
                const Icon = active ? (sort!.dir === "asc" ? ArrowUp : ArrowDown) : ChevronsUpDown;
                return (
                  <th
                    key={c.key}
                    className={cn(
                      "px-3 py-2 text-left font-medium border-b border-divider whitespace-nowrap select-none",
                      c.numeric && "text-right",
                    )}
                  >
                    <button
                      onClick={() => toggleSort(c.key)}
                      className={cn(
                        "inline-flex items-center gap-1 hover:text-foreground transition-colors",
                        c.numeric && "flex-row-reverse",
                        active && "text-foreground",
                      )}
                    >
                      <Icon className="h-3 w-3 opacity-70" />
                      {c.label}
                    </button>
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {slice.map((row, i) => (
              <tr
                key={i}
                className="border-b border-divider/60 hover:bg-secondary/70 transition-colors animate-fade-in"
                style={{ animationDelay: `${Math.min(i, 12) * 20}ms` }}
              >
                {columns.map((c) => (
                  <td
                    key={c.key}
                    className={cn(
                      "px-3 py-2 whitespace-nowrap",
                      c.numeric ? "text-right mono tabular-nums" : "font-normal",
                    )}
                    onDoubleClick={() => navigator.clipboard.writeText(String(row[c.key] ?? ""))}
                    title="Double-click to copy"
                  >
                    {String(row[c.key] ?? "")}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="flex items-center justify-between border-t border-divider px-3 py-2 text-xs text-muted-foreground">
        <span>
          Page {page + 1} of {totalPages}
        </span>
        <div className="flex items-center gap-1">
          <Button
            size="sm"
            variant="ghost"
            disabled={page === 0}
            onClick={() => setPage((p) => Math.max(0, p - 1))}
          >
            <ChevronLeft className="h-3.5 w-3.5" />
          </Button>
          <Button
            size="sm"
            variant="ghost"
            disabled={page >= totalPages - 1}
            onClick={() => setPage((p) => p + 1)}
          >
            <ChevronRight className="h-3.5 w-3.5" />
          </Button>
        </div>
      </div>
    </div>
  );
}
