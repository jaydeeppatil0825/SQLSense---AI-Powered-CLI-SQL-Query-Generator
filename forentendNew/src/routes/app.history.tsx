import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { Trash2, Copy, RotateCcw } from "lucide-react";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { api } from "@/api/client";
import { RouteBadge } from "@/features/query/RouteBadge";

export const Route = createFileRoute("/app/history")({
  head: () => ({ meta: [{ title: "Query History — SQLSense" }] }),
  component: HistoryPage,
});

function HistoryPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { data: entries = [], isLoading } = useQuery({
    queryKey: ["history.list"],
    queryFn: api.listHistory,
  });
  const clearMutation = useMutation({
    mutationFn: api.clearHistory,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["history.list"] }),
  });
  const [q, setQ] = useState("");
  const [filter, setFilter] = useState<"all" | "ok" | "ambiguous" | "blocked">("all");

  const filtered = useMemo(
    () =>
      entries.filter(
        (e) =>
          (filter === "all" || e.status === filter) &&
          e.question.toLowerCase().includes(q.toLowerCase()),
      ),
    [entries, q, filter],
  );

  return (
    <div className="mx-auto max-w-5xl p-4 md:p-6 lg:p-8 space-y-5">
      <div className="flex flex-wrap items-center gap-3">
        <div>
          <h1 className="display text-xl md:text-2xl">Query history</h1>
          <p className="text-xs md:text-sm text-muted-foreground">
            Server session history. Re-running always re-plans and re-validates through the backend.
          </p>
        </div>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => clearMutation.mutate()}
          disabled={entries.length === 0 || clearMutation.isPending}
          className="ml-auto"
        >
          <Trash2 className="h-3.5 w-3.5" /> Clear
        </Button>
      </div>

      <div className="flex flex-wrap gap-2">
        <Input
          placeholder="Search questions…"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          className="max-w-xs"
        />
        <div className="inline-flex items-center gap-0.5 rounded-full border border-border bg-secondary p-0.5">
          {(["all", "ok", "ambiguous", "blocked"] as const).map((f) => (
            <button
              key={f}
              onClick={() => setFilter(f)}
              className={
                "rounded-full px-3 py-1 text-xs capitalize " +
                (filter === f
                  ? "bg-primary text-primary-foreground"
                  : "text-muted-foreground hover:text-foreground")
              }
            >
              {f}
            </button>
          ))}
        </div>
      </div>

      {isLoading ? (
        <div className="rounded-2xl border border-dashed border-border bg-card p-10 text-center text-sm text-muted-foreground">
          Loading history...
        </div>
      ) : filtered.length === 0 ? (
        <div className="rounded-2xl border border-dashed border-border bg-card p-10 text-center text-sm text-muted-foreground">
          No queries yet. Ask something on the Ask page to get started.
        </div>
      ) : (
        <ul className="rounded-2xl border border-border bg-card divide-y divide-divider overflow-hidden">
          {filtered.map((e) => (
            <li
              key={e.id}
              className="p-4 grid gap-2 md:grid-cols-[minmax(0,1fr)_auto] md:items-center"
            >
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                  <RouteBadge route={e.route} />
                  {e.queryShape && (
                    <span className="mono rounded-full bg-secondary px-2 py-0.5">
                      {e.queryShape}
                    </span>
                  )}
                  <span className="mono">{e.database}</span>
                  {typeof e.rowCount === "number" && <span>{e.rowCount} rows</span>}
                  {typeof e.executionTimeMs === "number" && <span>{e.executionTimeMs} ms</span>}
                  <span>{new Date(e.timestamp).toLocaleString()}</span>
                </div>
                <div className="mt-1 text-sm font-medium truncate">{e.question}</div>
              </div>
              <div className="flex items-center gap-1 justify-end">
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => {
                    try {
                      sessionStorage.setItem("sqlsense:pending-question", e.question);
                    } catch {
                      // Ignore browsers that block sessionStorage.
                    }
                    navigate({ to: "/app/ask" });
                  }}
                  title="Re-run question"
                >
                  <RotateCcw className="h-3.5 w-3.5" />
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => navigator.clipboard.writeText(e.question)}
                  title="Copy question"
                >
                  <Copy className="h-3.5 w-3.5" />
                </Button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
