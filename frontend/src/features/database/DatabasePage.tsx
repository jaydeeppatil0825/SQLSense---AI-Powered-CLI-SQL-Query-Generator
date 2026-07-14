import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { PageHeader } from "../../components/shared/PageHeader";
import { Badge } from "../../components/ui/Badge";
import { Button } from "../../components/ui/Button";
import { Card } from "../../components/ui/Card";
import { connectDatabase, disconnectDatabase, rebuildKnowledge } from "../../gateway/actions";
import { normalizeGatewayError } from "../../gateway/errors";
import { useSystemStatus } from "../system-status/useSystemStatus";

const emptyForm = {
  db_type: "mysql" as const,
  host: "localhost",
  port: 3306,
  database: "",
  username: "",
  password: "",
};

export function DatabasePage() {
  const queryClient = useQueryClient();
  const { database, knowledge } = useSystemStatus();
  const [form, setForm] = useState(emptyForm);
  const [message, setMessage] = useState<string | null>(null);
  const [connectPending, setConnectPending] = useState(false);
  const [connectFailed, setConnectFailed] = useState(false);

  const refreshStatus = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["database-status"] }),
      queryClient.invalidateQueries({ queryKey: ["knowledge-status"] }),
      queryClient.invalidateQueries({ queryKey: ["session-status"] }),
    ]);
  };

  const disconnect = useMutation({
    mutationFn: disconnectDatabase,
    onSettled: refreshStatus,
  });

  const rebuild = useMutation({
    mutationFn: rebuildKnowledge,
    onSuccess: async () => {
      setMessage("Your data is ready.");
      await refreshStatus();
    },
    onError: (error) => setMessage(normalizeGatewayError(error).message),
  });

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setMessage(null);
    setConnectFailed(false);
    setConnectPending(true);
    try {
      await connectDatabase(form);
      setMessage("Database connected. Checking data readiness.");
      await refreshStatus();
    } catch (error) {
      setConnectFailed(true);
      setMessage(normalizeGatewayError(error).message);
    } finally {
      setConnectPending(false);
      setForm((current) => ({ ...current, password: "" }));
    }
  };

  const connected = Boolean(database.data?.connected);
  const ready = Boolean(knowledge.data?.knowledge_base_loaded);
  const busy = connectPending || disconnect.isPending || rebuild.isPending;

  return (
    <>
      <PageHeader
        eyebrow="Data Connection"
        title="Connect your company database"
        description="Connect once, prepare data knowledge, then ask business questions in everyday language."
      />
      <div className="grid gap-6 xl:grid-cols-[1fr_0.85fr]">
        <Card>
          <form onSubmit={submit} className="grid gap-4">
            <label className="grid gap-2 text-sm font-semibold text-slate-200">
              Engine
              <select
                value={form.db_type}
                onChange={(event) => setForm((current) => ({ ...current, db_type: event.target.value as "mysql" }))}
                className="rounded-xl border border-slate-700 bg-slate-950 px-3 py-2 text-white"
              >
                <option value="mysql">MySQL</option>
              </select>
            </label>
            <div className="grid gap-4 md:grid-cols-[1fr_8rem]">
              <Field label="Host" value={form.host} onChange={(host) => setForm((current) => ({ ...current, host }))} />
              <Field
                label="Port"
                type="number"
                value={String(form.port)}
                onChange={(port) => setForm((current) => ({ ...current, port: Number(port) || 3306 }))}
              />
            </div>
            <Field
              label="Database"
              value={form.database}
              required
              onChange={(databaseName) => setForm((current) => ({ ...current, database: databaseName }))}
            />
            <Field
              label="Username"
              value={form.username}
              required
              onChange={(username) => setForm((current) => ({ ...current, username }))}
            />
            <Field
              label="Password"
              type="password"
              value={form.password}
              required
              onChange={(password) => setForm((current) => ({ ...current, password }))}
            />
            <div className="flex flex-wrap gap-3">
              <Button disabled={busy} variant="primary">
                {connectPending ? "Connecting" : "Connect database"}
              </Button>
              <Button type="button" disabled={!connected || busy} onClick={() => disconnect.mutate()}>
                {disconnect.isPending ? "Disconnecting" : "Disconnect"}
              </Button>
            </div>
          </form>
        </Card>

        <Card>
          <h2 className="text-xl font-bold">Setup progress</h2>
          <div className="mt-5 space-y-4">
            <Step title="1. Connect database" done={connected} active={connectPending} />
            <Step title="2. Prepare data knowledge" done={ready} active={rebuild.isPending || knowledge.isFetching} />
            <Step title="3. Ready to ask questions" done={connected && ready} />
          </div>
          <div className="mt-6 flex flex-wrap items-center gap-3">
            <Badge tone={connected ? "good" : connectFailed ? "bad" : "warn"}>
              {connected ? "Connected" : connectFailed ? "Connection failed" : "Disconnected"}
            </Badge>
            <Badge tone={ready ? "good" : rebuild.isError ? "bad" : "neutral"}>
              {ready ? "Data ready" : rebuild.isPending ? "Preparing your data" : "Data setup required"}
            </Badge>
          </div>
          {connected && !ready ? (
            <Button className="mt-6" disabled={busy} onClick={() => rebuild.mutate()} variant="primary">
              {rebuild.isPending ? "Preparing your data" : "Prepare data knowledge"}
            </Button>
          ) : null}
          {message ? <p className="mt-5 text-sm leading-6 text-slate-300">{message}</p> : null}
        </Card>
      </div>
    </>
  );
}

function Field({
  label,
  value,
  onChange,
  type = "text",
  required = false,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  type?: string;
  required?: boolean;
}) {
  return (
    <label className="grid gap-2 text-sm font-semibold text-slate-200">
      {label}
      <input
        required={required}
        type={type}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className="rounded-xl border border-slate-700 bg-slate-950 px-3 py-2 text-white outline-none focus:border-signal-400"
      />
    </label>
  );
}

function Step({ title, done, active = false }: { title: string; done: boolean; active?: boolean }) {
  return (
    <div className="flex items-center gap-3 rounded-2xl border border-white/10 bg-white/5 p-4">
      <span className={`h-3 w-3 rounded-full ${done ? "bg-emerald-400" : active ? "bg-signal-400" : "bg-slate-600"}`} />
      <span className="text-sm text-slate-200">{title}</span>
    </div>
  );
}
