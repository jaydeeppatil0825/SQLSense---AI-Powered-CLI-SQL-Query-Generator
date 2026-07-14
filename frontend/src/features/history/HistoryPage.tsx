import { useMemo, useState } from "react";
import { GatewayErrorPanel } from "../../components/shared/GatewayErrorPanel";
import { LoadingPanel } from "../../components/shared/LoadingPanel";
import { PageHeader } from "../../components/shared/PageHeader";
import { Card } from "../../components/ui/Card";
import { normalizeGatewayError } from "../../gateway/errors";
import { ClearHistoryDialog } from "./ClearHistoryDialog";
import { HistoryEmptyState } from "./HistoryEmptyState";
import { HistoryFilters, type HistoryFilter } from "./HistoryFilters";
import { HistoryList } from "./HistoryList";
import { useClearHistory, useQueryHistory } from "./useQueryHistory";

export function HistoryPage() {
  const history = useQueryHistory();
  const clear = useClearHistory();
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState<HistoryFilter>("all");

  const entries = history.data?.entries ?? [];
  const filtered = useMemo(() => {
    const term = search.trim().toLowerCase();
    return entries.filter((entry) => {
      const matchesText = !term || entry.question.toLowerCase().includes(term);
      const matchesStatus = status === "all" || entry.status === status;
      return matchesText && matchesStatus;
    });
  }, [entries, search, status]);

  const error = history.error ? normalizeGatewayError(history.error) : clear.error ? normalizeGatewayError(clear.error) : null;

  return (
    <>
      <PageHeader
        eyebrow="Query History"
        title="Review past answers"
        description="This session history stores safe summaries only, not result rows or internal planning details."
        actions={<ClearHistoryDialog disabled={!entries.length || clear.isPending} onConfirm={() => clear.mutate()} />}
      />
      <Card className="mb-5">
        <HistoryFilters search={search} status={status} onSearch={setSearch} onStatus={setStatus} />
      </Card>
      {history.isLoading ? <LoadingPanel label="Loading query history" /> : null}
      {error ? <GatewayErrorPanel title={error.code} message={error.message} /> : null}
      {!history.isLoading && !filtered.length ? <HistoryEmptyState /> : <HistoryList entries={filtered} />}
    </>
  );
}
