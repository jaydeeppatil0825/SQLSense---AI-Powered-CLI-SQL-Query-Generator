import { Check, Loader2, X } from "lucide-react";
import type { QueryResponse } from "@/api/types";

export function ProgressStages({ stages }: { stages?: QueryResponse["stages"] }) {
  if (!stages || stages.length === 0) return null;
  return (
    <div className="flex flex-wrap gap-1.5">
      {stages.map((s) => (
        <span
          key={s.name}
          className={
            "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11px] " +
            (s.status === "done"
              ? "border-positive/30 bg-positive-soft text-positive"
              : s.status === "pending"
                ? "border-border bg-secondary text-muted-foreground"
                : "border-negative/30 bg-negative-soft text-negative")
          }
        >
          {s.status === "done" ? (
            <Check className="h-3 w-3" />
          ) : s.status === "pending" ? (
            <Loader2 className="h-3 w-3 animate-spin" />
          ) : (
            <X className="h-3 w-3" />
          )}
          {s.name}
          {s.detail && <span className="text-muted-foreground">· {s.detail}</span>}
        </span>
      ))}
    </div>
  );
}

export function LoadingStages() {
  const stages = [
    "Understanding question",
    "Finding database fields",
    "Preparing query",
    "Validating query",
    "Running query",
  ];
  return (
    <div className="rounded-xl border border-border bg-card p-4 space-y-3">
      <div className="flex items-center gap-2 text-sm text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin text-primary" />
        Working on your question…
      </div>
      <div className="flex flex-wrap gap-1.5">
        {stages.map((s) => (
          <span
            key={s}
            className="inline-flex items-center gap-1.5 rounded-full border border-border bg-secondary px-2.5 py-1 text-[11px] text-muted-foreground"
          >
            {s}
          </span>
        ))}
      </div>
    </div>
  );
}
