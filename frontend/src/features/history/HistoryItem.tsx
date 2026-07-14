import { useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import { ReadonlySqlBlock } from "../../components/shared/ReadonlySqlBlock";
import { Badge } from "../../components/ui/Badge";
import { Button } from "../../components/ui/Button";
import { paths } from "../../app/router/paths";
import type { HistoryEntry } from "../../gateway/contracts";
import { setQuestionDraft } from "../query/composerDraft";

export function HistoryItem({ entry }: { entry: HistoryEntry }) {
  const navigate = useNavigate();
  const [copied, setCopied] = useState(false);
  const date = new Date(entry.created_at);

  const askAgain = () => {
    setQuestionDraft(entry.question);
    void navigate({ to: paths.query });
  };

  const copyQuestion = async () => {
    await navigator.clipboard?.writeText(entry.question);
    setCopied(true);
  };

  return (
    <article className="rounded-3xl border border-white/10 bg-slate-950/55 p-5">
      <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <Badge tone={entry.status === "success" ? "good" : "warn"}>
              {entry.status === "success" ? "Successful" : "Stopped safely"}
            </Badge>
            <span className="text-xs text-slate-500">{Number.isNaN(date.getTime()) ? entry.created_at : date.toLocaleString()}</span>
          </div>
          <h2 className="mt-3 text-lg font-bold text-white">{entry.question}</h2>
          <p className="mt-2 text-sm text-slate-400">
            {entry.status === "success"
              ? `${entry.row_count ?? 0} rows${entry.duration_ms ? `, ${entry.duration_ms} ms` : ""}`
              : entry.error?.message}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button onClick={copyQuestion} type="button">
            {copied ? "Copied" : "Copy Question"}
          </Button>
          <Button onClick={askAgain} type="button" variant="primary">
            Ask Again
          </Button>
        </div>
      </div>
      <details className="mt-4">
        <summary className="cursor-pointer text-sm font-semibold text-slate-300">Technical details</summary>
        <div className="mt-3 grid gap-3 text-xs text-slate-500">
          {entry.query_shape ? <span>Query type: {entry.query_shape}</span> : null}
          {entry.request_id ? <span>Request ID: {entry.request_id}</span> : null}
          {entry.sql ? <ReadonlySqlBlock sql={entry.sql} /> : null}
        </div>
      </details>
    </article>
  );
}
