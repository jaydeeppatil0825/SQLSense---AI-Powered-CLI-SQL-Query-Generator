import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import { AlertCircle, Check, Database, Lock, Loader2, ShieldCheck } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { api } from "@/api/client";
import { useAppStore, type ConnectionProfile } from "@/stores/app-store";
import { ThemeToggle } from "@/features/shell/AppShell";

export const Route = createFileRoute("/connect")({
  head: () => ({ meta: [{ title: "Connect a database — SQLSense" }] }),
  component: ConnectPage,
});

type TestState =
  | { kind: "idle" }
  | { kind: "testing" }
  | { kind: "ok"; message: string }
  | { kind: "error"; message: string };

type BuildState = { kind: "idle" } | { kind: "building"; stage: number };

const KB_STAGES = [
  "Reading database structure",
  "Preparing table information",
  "Detecting relationships",
  "Finalizing setup",
  "Ready",
];

function ConnectPage() {
  const navigate = useNavigate();
  const connect = useAppStore((s) => s.connect);
  const savedProfiles = useAppStore((s) => s.savedProfiles);
  const saveProfile = useAppStore((s) => s.saveProfile);

  const [form, setForm] = useState<ConnectionProfile & { password: string }>({
    engine: "mysql",
    host: "localhost",
    port: 3306,
    database: "",
    username: "root",
    ssl: false,
    password: "",
  });
  const [test, setTest] = useState<TestState>({ kind: "idle" });
  const [build, setBuild] = useState<BuildState>({ kind: "idle" });

  function update<K extends keyof typeof form>(key: K, value: (typeof form)[K]) {
    setForm((f) => ({ ...f, [key]: value }));
    setTest({ kind: "idle" });
  }

  async function onTest() {
    setTest({ kind: "testing" });
    const r = await api.testConnection(form);
    setTest(r.ok ? { kind: "ok", message: r.message } : { kind: "error", message: r.message });
  }

  async function onConnect() {
    setTest({ kind: "testing" });
    const r = await api.connect(form);
    if (!r.ok) {
      setTest({ kind: "error", message: r.message });
      return;
    }
    setBuild({ kind: "building", stage: 0 });
    for (let i = 0; i < KB_STAGES.length; i++) {
      await new Promise((res) => setTimeout(res, 360));
      setBuild({ kind: "building", stage: i });
    }
    const profile: ConnectionProfile = {
      engine: form.engine,
      host: form.host,
      port: form.port,
      database: form.database,
      username: form.username,
      ssl: form.ssl,
    };
    saveProfile(profile);
    connect(profile);
    navigate({ to: "/app/ask" });
  }

  return (
    <div className="min-h-screen w-full bg-background text-foreground">
      <header className="flex items-center gap-3 px-6 py-4 border-b border-border">
        <div className="grid h-9 w-9 place-items-center rounded-xl bg-primary text-primary-foreground display font-semibold">
          S
        </div>
        <div>
          <div className="display text-[15px] leading-tight">SQLSense</div>
          <div className="text-[11px] text-muted-foreground">
            Deterministic natural-language to SQL
          </div>
        </div>
        <div className="ml-auto">
          <ThemeToggle />
        </div>
      </header>

      <div className="mx-auto grid max-w-5xl gap-6 px-4 py-8 md:grid-cols-[minmax(0,1fr)_320px] md:px-6 lg:py-14">
        <section className="rounded-2xl border border-border bg-card p-6 md:p-8">
          <div className="mb-6">
            <div className="text-[11px] uppercase tracking-widest text-primary/80">Step 1 of 2</div>
            <h1 className="display text-2xl mt-1">Connect a database</h1>
            <p className="mt-2 text-sm text-muted-foreground max-w-xl">
              Connect once and SQLSense prepares your database so you can ask questions in plain
              English and get validated, read-only results.
            </p>
          </div>

          {build.kind === "building" ? (
            <BuildProgress stage={build.stage} />
          ) : (
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="Engine">
                <div className="grid grid-cols-2 gap-2">
                  {(["postgresql", "mysql"] as const).map((e) => (
                    <button
                      key={e}
                      onClick={() => update("engine", e)}
                      className={
                        "h-11 rounded-lg border text-sm transition-colors " +
                        (form.engine === e
                          ? "border-primary bg-primary/10 text-foreground"
                          : "border-border bg-secondary text-muted-foreground hover:text-foreground")
                      }
                    >
                      {e === "postgresql" ? "PostgreSQL" : "MySQL"}
                    </button>
                  ))}
                </div>
              </Field>

              <Field label="Host">
                <Input
                  value={form.host}
                  onChange={(e) => update("host", e.target.value)}
                  placeholder="db.example.com"
                />
              </Field>
              <Field label="Port">
                <Input
                  type="number"
                  value={form.port}
                  onChange={(e) => update("port", Number(e.target.value))}
                />
              </Field>
              <Field label="Database">
                <Input value={form.database} onChange={(e) => update("database", e.target.value)} />
              </Field>
              <Field label="Username">
                <Input
                  value={form.username}
                  onChange={(e) => update("username", e.target.value)}
                  autoComplete="username"
                />
              </Field>
              <Field label="Password">
                <Input
                  type="password"
                  value={form.password}
                  onChange={(e) => update("password", e.target.value)}
                  autoComplete="current-password"
                  placeholder="Not stored on this device"
                />
              </Field>
              <div className="flex items-center justify-between rounded-lg border border-border bg-secondary px-3 py-2 sm:col-span-2">
                <div className="flex items-center gap-2 text-sm">
                  <ShieldCheck className="h-4 w-4 text-primary" />
                  Require SSL / TLS
                </div>
                <Switch checked={form.ssl} onCheckedChange={(v) => update("ssl", v)} />
              </div>

              <div className="sm:col-span-2 rounded-lg border border-warning/30 bg-warning-soft px-3 py-2 text-[12px] text-warning flex items-start gap-2">
                <Lock className="h-3.5 w-3.5 mt-0.5 shrink-0" />
                <span>
                  Your password is used only to open this connection and is never saved on this
                  device. Non-sensitive fields (engine, host, port, database, username) are stored
                  as a saved profile.
                </span>
              </div>

              <TestBanner state={test} />

              <div className="sm:col-span-2 flex flex-wrap gap-2 pt-2">
                <Button variant="outline" onClick={onTest} disabled={test.kind === "testing"}>
                  {test.kind === "testing" ? (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  ) : (
                    <Database className="h-4 w-4" />
                  )}
                  Test connection
                </Button>
                <Button onClick={onConnect} disabled={test.kind === "testing"}>
                  Connect database
                </Button>
              </div>
            </div>
          )}
        </section>

        <aside className="space-y-4">
          <div className="rounded-2xl border border-border bg-card p-5">
            <div className="text-[11px] uppercase tracking-wider text-muted-foreground">
              Saved profiles
            </div>
            {savedProfiles.length === 0 ? (
              <div className="mt-3 text-sm text-muted-foreground">No profiles saved yet.</div>
            ) : (
              <ul className="mt-3 space-y-2">
                {savedProfiles.map((p) => (
                  <li key={`${p.host}:${p.port}/${p.database}`}>
                    <button
                      className="w-full text-left rounded-lg border border-border bg-secondary p-3 hover:border-primary/40"
                      onClick={() => setForm({ ...p, password: "" })}
                    >
                      <div className="mono text-xs">
                        {p.engine} · {p.database}
                      </div>
                      <div className="text-[11px] text-muted-foreground truncate">
                        {p.username}@{p.host}:{p.port}
                      </div>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>

          <div className="rounded-2xl border border-border bg-card p-5 text-sm text-muted-foreground space-y-2">
            <div className="flex items-center gap-2 text-foreground">
              <ShieldCheck className="h-4 w-4 text-primary" /> Safety
            </div>
            <p>SQLSense only runs safe, read-only queries. Your password is never saved.</p>
          </div>
        </aside>
      </div>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="space-y-1.5">
      <Label className="text-[11px] uppercase tracking-wider text-muted-foreground">{label}</Label>
      {children}
    </div>
  );
}

function TestBanner({ state }: { state: TestState }) {
  if (state.kind === "idle") return null;
  if (state.kind === "testing")
    return (
      <div className="sm:col-span-2 flex items-center gap-2 rounded-lg border border-border bg-secondary px-3 py-2 text-sm text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" /> Testing connection…
      </div>
    );
  if (state.kind === "ok")
    return (
      <div className="sm:col-span-2 flex items-center gap-2 rounded-lg border border-positive/40 bg-positive-soft px-3 py-2 text-sm text-positive">
        <Check className="h-4 w-4" /> {state.message}
      </div>
    );
  return (
    <div className="sm:col-span-2 flex items-center gap-2 rounded-lg border border-negative/40 bg-negative-soft px-3 py-2 text-sm text-negative">
      <AlertCircle className="h-4 w-4" /> {state.message}
    </div>
  );
}

function BuildProgress({ stage }: { stage: number }) {
  return (
    <div className="space-y-3">
      <div className="text-sm text-muted-foreground">
        Preparing your database for questions. This only takes a moment.
      </div>
      <ol className="mt-2 space-y-1.5">
        {KB_STAGES.map((s, i) => {
          const state = i < stage ? "done" : i === stage ? "running" : "pending";
          return (
            <li
              key={s}
              className={
                "flex items-center gap-3 rounded-lg border px-3 py-2 text-sm transition-colors " +
                (state === "done"
                  ? "border-positive/30 bg-positive-soft text-foreground"
                  : state === "running"
                    ? "border-primary/40 bg-primary/10 text-foreground"
                    : "border-border bg-secondary text-muted-foreground")
              }
            >
              <span
                className={
                  "grid h-5 w-5 place-items-center rounded-full text-[10px] mono " +
                  (state === "done"
                    ? "bg-positive text-background"
                    : state === "running"
                      ? "bg-primary text-primary-foreground"
                      : "bg-border text-muted-foreground")
                }
              >
                {state === "done" ? (
                  <Check className="h-3 w-3" />
                ) : state === "running" ? (
                  <Loader2 className="h-3 w-3 animate-spin" />
                ) : (
                  i + 1
                )}
              </span>
              <span>{s}</span>
            </li>
          );
        })}
      </ol>
    </div>
  );
}
