import { createFileRoute } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { Search, Table2 } from "lucide-react";
import { Input } from "@/components/ui/input";
import { api } from "@/api/client";
import { cn } from "@/lib/utils";
import { Skeleton } from "@/components/ui/skeleton";

export const Route = createFileRoute("/app/schema")({
  head: () => ({ meta: [{ title: "Schema Explorer — SQLSense" }] }),
  component: SchemaPage,
});

function SchemaPage() {
  const { data, isLoading, isError } = useQuery({ queryKey: ["schema"], queryFn: api.listTables });
  const [q, setQ] = useState("");
  const [active, setActive] = useState<string | null>(null);
  const tables = useMemo(
    () => (data ?? []).filter((t) => t.name.toLowerCase().includes(q.toLowerCase())),
    [data, q],
  );
  const current = tables.find((t) => t.name === active) ?? tables[0];

  return (
    <div className="mx-auto max-w-6xl p-4 md:p-6 lg:p-8 space-y-5">
      <div>
        <h1 className="display text-xl md:text-2xl">Schema explorer</h1>
        <p className="text-xs md:text-sm text-muted-foreground">
          Browse tables, columns, keys, and data types in your connected database.
        </p>
      </div>

      <div className="grid gap-4 md:grid-cols-[280px_minmax(0,1fr)]">
        <div className="rounded-2xl border border-border bg-card p-3 space-y-2 md:max-h-[calc(100vh-220px)] md:overflow-auto">
          <div className="relative">
            <Search className="pointer-events-none absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
            <Input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Search tables"
              className="pl-8"
            />
          </div>
          {isLoading ? (
            <div className="space-y-2">
              {Array.from({ length: 5 }).map((_, i) => (
                <Skeleton key={i} className="h-9 w-full" />
              ))}
            </div>
          ) : isError || tables.length === 0 ? (
            <div className="rounded-lg border border-dashed border-border p-4 text-sm text-muted-foreground">
              Schema table listing is not exposed by the current gateway contract.
            </div>
          ) : (
            <ul className="space-y-1">
              {tables.map((t) => (
                <li key={t.name}>
                  <button
                    onClick={() => setActive(t.name)}
                    className={cn(
                      "flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-sm",
                      current?.name === t.name
                        ? "bg-primary/10 text-foreground"
                        : "text-muted-foreground hover:text-foreground hover:bg-secondary",
                    )}
                  >
                    <Table2 className="h-4 w-4 shrink-0" />
                    <span className="mono truncate">{t.name}</span>
                    <span className="ml-auto text-[11px] text-muted-foreground">
                      {t.columnCount}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>

        {current && (
          <div className="rounded-2xl border border-border bg-card p-4 md:p-6 space-y-4">
            <div>
              <div className="text-[11px] uppercase tracking-widest text-primary/80">
                {current.type}
              </div>
              <h2 className="display text-xl mono mt-1">{current.name}</h2>
              {current.description && (
                <p className="text-sm text-muted-foreground mt-1">{current.description}</p>
              )}
            </div>
            <div className="overflow-auto rounded-xl border border-border">
              <table className="w-full text-sm">
                <thead className="bg-surface-2 text-[11px] uppercase tracking-wider text-muted-foreground">
                  <tr>
                    <th className="px-3 py-2 text-left">Column</th>
                    <th className="px-3 py-2 text-left">Type</th>
                    <th className="px-3 py-2 text-left">Details</th>
                  </tr>
                </thead>
                <tbody>
                  {current.columns.map((c) => (
                    <tr key={c.name} className="border-t border-divider">
                      <td className="px-3 py-2 mono">{c.name}</td>
                      <td className="px-3 py-2 mono text-muted-foreground">{c.dataType}</td>
                      <td className="px-3 py-2">
                        <div className="flex flex-wrap gap-1">
                          {c.primaryKey && <Badge tone="primary">PK</Badge>}
                          {c.foreignKey && (
                            <Badge tone="soft">
                              FK → {c.foreignKey.table}.{c.foreignKey.column}
                            </Badge>
                          )}
                          {c.unique && <Badge tone="soft">unique</Badge>}
                          {!c.nullable && <Badge tone="soft">not null</Badge>}
                          {c.isDate && <Badge tone="soft">date</Badge>}
                          {c.isNumeric && <Badge tone="soft">numeric</Badge>}
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function Badge({ children, tone }: { children: React.ReactNode; tone: "primary" | "soft" }) {
  return (
    <span
      className={
        "inline-flex items-center rounded-full px-2 py-0.5 text-[10px] " +
        (tone === "primary" ? "bg-primary/15 text-primary" : "bg-secondary text-muted-foreground")
      }
    >
      {children}
    </span>
  );
}
