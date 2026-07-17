import { Copy, Download, ShieldCheck, AlertTriangle } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { QueryResponse } from "@/api/types";

export function SqlView({ result }: { result: QueryResponse }) {
  if (!result.sql)
    return (
      <div className="rounded-xl border border-dashed border-border bg-card p-8 text-center text-sm text-muted-foreground">
        No SQL generated for this route.
      </div>
    );

  const copy = () => navigator.clipboard.writeText(result.sql ?? "");
  const download = () => {
    const blob = new Blob([result.sql ?? ""], { type: "application/sql" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "sqlsense.sql";
    a.click();
    URL.revokeObjectURL(url);
  };

  const valid = result.validation?.valid !== false;

  return (
    <div className="rounded-xl border border-border bg-card overflow-hidden">
      <div className="flex flex-wrap items-center gap-2 border-b border-divider px-3 py-2">
        <span className="inline-flex items-center gap-1.5 rounded-full bg-secondary px-2.5 py-1 text-[11px] text-muted-foreground">
          <ShieldCheck className="h-3 w-3" /> Read-only · SELECT
        </span>
        <span
          className={
            "inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[11px] " +
            (valid ? "bg-positive-soft text-positive" : "bg-negative-soft text-negative")
          }
        >
          {valid ? "Validated" : "Validation failed"}
        </span>
        {result.validation?.message && (
          <span className="text-[11px] text-muted-foreground truncate">
            {result.validation.message}
          </span>
        )}
        <div className="ml-auto flex items-center gap-1">
          <Button size="sm" variant="ghost" onClick={copy}>
            <Copy className="h-3.5 w-3.5" /> Copy
          </Button>
          <Button size="sm" variant="ghost" onClick={download}>
            <Download className="h-3.5 w-3.5" /> .sql
          </Button>
        </div>
      </div>
      <pre className="mono text-[12.5px] leading-relaxed p-4 overflow-auto bg-inset text-foreground whitespace-pre">
        {result.sql}
      </pre>
      <div className="border-t border-divider bg-surface-2 px-3 py-2 text-[11px] text-muted-foreground flex items-center gap-2">
        <AlertTriangle className="h-3 w-3 text-warning" />
        Manually modified SQL has not been re-validated by SQLSense.
      </div>
    </div>
  );
}
