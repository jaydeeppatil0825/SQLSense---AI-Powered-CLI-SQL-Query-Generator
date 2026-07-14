import { GatewayErrorPanel } from "../../components/shared/GatewayErrorPanel";
import { LoadingPanel } from "../../components/shared/LoadingPanel";
import { PageHeader } from "../../components/shared/PageHeader";
import { Card } from "../../components/ui/Card";
import { normalizeGatewayError } from "../../gateway/errors";
import { useSystemStatus } from "./useSystemStatus";

function JsonPanel({ title, value }: { title: string; value: unknown }) {
  return (
    <Card>
      <h2 className="mb-3 text-lg font-bold text-white">{title}</h2>
      <pre className="max-h-72 overflow-auto rounded-2xl bg-slate-950 p-4 text-xs text-slate-200">
        {JSON.stringify(value, null, 2)}
      </pre>
    </Card>
  );
}

export function SystemStatusPage() {
  const { health, session, database, knowledge } = useSystemStatus();
  const healthError = health.error ? normalizeGatewayError(health.error) : null;

  return (
    <>
      <PageHeader
        eyebrow="System"
        title="Gateway and session status"
        description="Detailed service diagnostics for administrators and support."
      />
      {health.isLoading ? <LoadingPanel label="Checking gateway" /> : null}
      {healthError ? <GatewayErrorPanel title={healthError.code} message={healthError.message} /> : null}
      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <JsonPanel title="Health" value={health.data ?? null} />
        <JsonPanel title="Session" value={session.data ?? null} />
        <JsonPanel title="Database" value={database.data ?? null} />
        <JsonPanel title="Knowledge Base" value={knowledge.data ?? null} />
      </div>
    </>
  );
}
