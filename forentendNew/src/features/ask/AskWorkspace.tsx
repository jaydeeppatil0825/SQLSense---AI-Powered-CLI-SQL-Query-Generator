import { useMutation } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Database, Lightbulb, RotateCcw, Send, ShieldAlert, Sparkles, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { api } from "@/api/client";
import type { QueryResponse } from "@/api/types";
import { useAppStore } from "@/stores/app-store";
import { LoadingStages } from "@/features/query/ProgressStages";
import { RouteBadge } from "@/features/query/RouteBadge";
import { ResultTable } from "@/features/query/ResultTable";
import { SqlView } from "@/features/query/SqlView";
import { ChartView } from "@/features/query/ChartView";
import { cn } from "@/lib/utils";

const SUGGESTIONS = [
  "Show all active customers",
  "Show orders from last month",
  "Total payment amount by customer",
  "Top five products by total sales",
  "Show payments with customer details",
];

type Tab = "results" | "sql" | "chart";

export function AskWorkspace() {
  const profile = useAppStore((s) => s.profile);
  const [question, setQuestion] = useState("");
  const [tab, setTab] = useState<Tab>("results");
  const [result, setResult] = useState<QueryResponse | null>(null);

  useEffect(() => {
    try {
      const pending = sessionStorage.getItem("sqlsense:pending-question");
      if (pending) {
        sessionStorage.removeItem("sqlsense:pending-question");
        setQuestion(pending);
        setResult(null);
        mutation.mutate(pending);
      }
    } catch {
      // Ignore browsers that block sessionStorage.
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const mutation = useMutation({
    mutationFn: (q: string) => api.askQuery(q),
    onSuccess: (r) => {
      setResult(r);
      setTab("results");
    },
  });

  const clarifyMutation = useMutation({
    mutationFn: ({ q }: { q: string; id: string }) => api.clarify(q),
    onSuccess: (r) => {
      setResult(r);
      setTab("results");
    },
  });

  function submit(qOverride?: string) {
    const q = (qOverride ?? question).trim();
    if (!q) return;
    if (qOverride) setQuestion(qOverride);
    setResult(null);
    mutation.mutate(q);
  }

  function onKeyDown(e: React.KeyboardEvent) {
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
      e.preventDefault();
      submit();
    }
  }

  return (
    <div className="mx-auto flex min-h-[calc(100vh-56px)] max-w-6xl flex-col gap-6 p-4 md:p-6 lg:p-8">
      <div className="flex flex-wrap items-center gap-3 animate-fade-in">
        <div className="grid h-11 w-11 place-items-center rounded-2xl bg-gradient-to-br from-primary/25 to-primary/5 text-primary ring-1 ring-primary/20 shadow-lg shadow-primary/10">
          <Database className="h-5 w-5" />
        </div>
        <div className="min-w-0">
          <h1 className="display text-xl md:text-2xl">Ask your database</h1>
          <p className="text-xs md:text-sm text-muted-foreground">
            Ask a question about
            <span className="mono ml-1">{profile?.database ?? "your database"}</span>
          </p>
        </div>
      </div>

      {mutation.isPending && <LoadingStages />}

      {!mutation.isPending && !result && (
        <div className="relative overflow-hidden rounded-2xl border border-border bg-card p-6 md:p-10 animate-fade-in">
          <div className="pointer-events-none absolute -top-24 -right-24 h-64 w-64 rounded-full bg-primary/20 blur-3xl" />
          <div className="pointer-events-none absolute -bottom-24 -left-24 h-64 w-64 rounded-full bg-primary/10 blur-3xl" />
          <div className="relative mx-auto max-w-xl text-center">
            <div className="mx-auto grid h-14 w-14 place-items-center rounded-2xl bg-primary/15 text-primary ring-1 ring-primary/30 shadow-lg shadow-primary/20 animate-scale-in">
              <Sparkles className="h-6 w-6" />
            </div>
            <h2 className="display text-lg md:text-xl mt-4">Ask a database question</h2>
            <p className="mt-2 text-sm text-muted-foreground">
              Type a question in plain English. SQLSense builds a safe, read-only query and shows
              you the results.
            </p>
          </div>
          <div className="relative mt-6 grid gap-2 sm:grid-cols-2 md:grid-cols-3">
            {SUGGESTIONS.map((s) => (
              <button
                key={s}
                onClick={() => submit(s)}
                className="group flex items-start gap-2 rounded-xl border border-border bg-secondary/60 backdrop-blur p-3 text-left text-sm transition-all hover:border-primary/40 hover:-translate-y-0.5 hover:shadow-lg hover:shadow-primary/10"
              >
                <Lightbulb className="h-4 w-4 text-primary mt-0.5 shrink-0" />
                <span className="text-muted-foreground group-hover:text-foreground">{s}</span>
              </button>
            ))}
          </div>
        </div>
      )}

      {result && (
        <ResultCard
          result={result}
          tab={tab}
          onTab={setTab}
          onRerun={() => mutation.mutate(result.question)}
          onClarify={(id) => clarifyMutation.mutate({ q: result.question, id })}
          clarifying={clarifyMutation.isPending}
        />
      )}

      <div className="mt-auto sticky bottom-20 lg:bottom-4 z-20">
        <div className="rounded-2xl border border-border bg-card/95 backdrop-blur p-2 shadow-xl shadow-black/10 ring-1 ring-primary/10 focus-within:ring-primary/40 transition-all">
          <Textarea
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={onKeyDown}
            placeholder="Ask a question about the connected database…"
            className="min-h-[76px] resize-none border-0 bg-transparent focus-visible:ring-0 text-[15px]"
          />
          <div className="flex items-center gap-2 px-2 pb-1">
            <span className="text-[11px] text-muted-foreground hidden sm:inline">
              <kbd className="mono rounded border border-border bg-secondary px-1 py-0.5 text-[10px]">
                Ctrl/⌘
              </kbd>
              <span className="mx-1">+</span>
              <kbd className="mono rounded border border-border bg-secondary px-1 py-0.5 text-[10px]">
                Enter
              </kbd>
              &nbsp;to submit
            </span>
            <div className="ml-auto flex items-center gap-1">
              {question && (
                <Button size="sm" variant="ghost" onClick={() => setQuestion("")}>
                  <X className="h-3.5 w-3.5" /> Clear
                </Button>
              )}
              <Button
                size="sm"
                onClick={() => submit()}
                disabled={!question.trim() || mutation.isPending}
              >
                <Send className="h-3.5 w-3.5" /> Ask
              </Button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function ResultCard({
  result,
  tab,
  onTab,
  onRerun,
  onClarify,
  clarifying,
}: {
  result: QueryResponse;
  tab: Tab;
  onTab: (t: Tab) => void;
  onRerun: () => void;
  onClarify: (id: string) => void;
  clarifying: boolean;
}) {
  const isBlocked = result.route === "blocked_unsafe";
  const isAmbiguous = result.route === "cannot_plan_safely";

  return (
    <div className="rounded-2xl border border-border bg-card overflow-hidden animate-fade-in shadow-lg shadow-black/5">
      <div className="border-b border-divider p-4 md:p-5 space-y-3">
        <div className="flex flex-wrap items-start gap-3">
          <RouteBadge route={result.route} />
          {typeof result.rowCount === "number" && (
            <span className="text-[11px] text-muted-foreground">
              <span className="mono">{result.rowCount}</span> rows
            </span>
          )}
          {typeof result.executionTimeMs === "number" && (
            <span className="text-[11px] text-muted-foreground">
              <span className="mono">{result.executionTimeMs} ms</span>
            </span>
          )}
          <span className="text-[11px] text-muted-foreground">
            {new Date().toLocaleTimeString()}
          </span>
          <div className="ml-auto flex items-center gap-1">
            <Button size="sm" variant="ghost" onClick={onRerun}>
              <RotateCcw className="h-3.5 w-3.5" /> Re-run
            </Button>
          </div>
        </div>
        <div className="text-sm">
          <span className="text-muted-foreground">Question · </span>
          <span className="font-medium">{result.question}</span>
        </div>
      </div>

      {isBlocked && <BlockedState reason={result.reason} />}
      {isAmbiguous && (
        <AmbiguityState
          reason={result.reason}
          choices={result.ambiguityChoices ?? []}
          onPick={onClarify}
          loading={clarifying}
        />
      )}

      {result.route === "deterministic_sql_required" && (
        <>
          <div className="border-b border-divider bg-surface-2 px-2 md:px-4">
            <div className="flex gap-1">
              {(["results", "sql", "chart"] as const).map((t) => (
                <button
                  key={t}
                  onClick={() => onTab(t)}
                  className={cn(
                    "relative px-3 py-2.5 text-sm capitalize transition-colors",
                    tab === t ? "text-foreground" : "text-muted-foreground hover:text-foreground",
                  )}
                >
                  {t === "sql" ? "Generated SQL" : t === "chart" ? "Chart" : "Results"}
                  {tab === t && (
                    <span className="absolute bottom-0 inset-x-2 h-0.5 rounded-full bg-primary" />
                  )}
                </button>
              ))}
            </div>
          </div>
          <div className="p-3 md:p-4 animate-fade-in">
            {tab === "results" && <ResultTable result={result} />}
            {tab === "sql" && <SqlView result={result} />}
            {tab === "chart" && <ChartView result={result} />}
          </div>
        </>
      )}
    </div>
  );
}

function BlockedState({ reason }: { reason: string }) {
  return (
    <div className="p-6 md:p-8">
      <div className="rounded-xl border border-negative/30 bg-negative-soft p-5 md:p-6 animate-fade-in">
        <div className="flex items-center gap-2">
          <div className="grid h-9 w-9 place-items-center rounded-full bg-negative text-white">
            <ShieldAlert className="h-4 w-4" />
          </div>
          <div>
            <div className="font-semibold">Request blocked</div>
            <div className="text-sm text-muted-foreground">
              SQLSense supports safe, read-only database questions. This request cannot be executed.
            </div>
          </div>
        </div>
        <div className="mt-3 text-sm">{reason}</div>
      </div>
    </div>
  );
}

function AmbiguityState({
  reason,
  choices,
  onPick,
  loading,
}: {
  reason: string;
  choices: { id: string; label: string; description?: string }[];
  onPick: (id: string) => void;
  loading: boolean;
}) {
  return (
    <div className="p-6 md:p-8 space-y-4">
      <div className="rounded-xl border border-warning/30 bg-warning-soft p-5 animate-fade-in">
        <div className="font-semibold">Clarification needed</div>
        <div className="mt-1 text-sm">{reason}</div>
      </div>
      <div className="grid gap-2 md:grid-cols-3">
        {choices.map((c) => (
          <button
            key={c.id}
            disabled={loading}
            onClick={() => onPick(c.id)}
            className="rounded-xl border border-border bg-secondary p-3 text-left transition-all hover:border-primary/40 hover:-translate-y-0.5 hover:shadow-md disabled:opacity-60"
          >
            <div className="mono text-xs text-foreground">{c.label}</div>
            {c.description && (
              <div className="mt-1 text-[11px] text-muted-foreground">{c.description}</div>
            )}
          </button>
        ))}
      </div>
      <div className="text-[11px] text-muted-foreground">
        Pick the option you meant — SQLSense won't guess when it changes the result.
      </div>
    </div>
  );
}
