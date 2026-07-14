import { useState } from "react";
import { ReadonlySqlBlock } from "../../components/shared/ReadonlySqlBlock";
import { Button } from "../../components/ui/Button";
import type { QueryResult } from "../../gateway/contracts";

export function QueryDetails({ result }: { result: QueryResult }) {
  const [copied, setCopied] = useState(false);
  const sql = result.sql ?? "";

  const copySql = async () => {
    await navigator.clipboard?.writeText(sql);
    setCopied(true);
  };

  return (
    <details className="rounded-2xl border border-white/10 bg-slate-950/50 p-4">
      <summary className="cursor-pointer text-sm font-semibold text-slate-200">View validated SQL</summary>
      <div className="mt-4 grid gap-3">
        <ReadonlySqlBlock sql={sql} />
        <div className="flex flex-wrap items-center gap-3 text-xs text-slate-500">
          {result.request_id ? <span>Request ID: {result.request_id}</span> : null}
          {result.query_shape ? <span>Query type: {result.query_shape}</span> : null}
          <Button onClick={copySql} type="button">
            {copied ? "Copied" : "Copy SQL"}
          </Button>
        </div>
      </div>
    </details>
  );
}
