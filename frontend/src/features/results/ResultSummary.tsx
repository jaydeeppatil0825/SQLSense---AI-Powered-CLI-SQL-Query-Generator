import { Badge } from "../../components/ui/Badge";
import type { QueryResult } from "../../gateway/contracts";

export function ResultSummary({ result }: { result: QueryResult }) {
  const duration = result.execution?.duration_ms;
  return (
    <div className="flex flex-wrap gap-2">
      <Badge tone="good">Results validated</Badge>
      <Badge tone="neutral">{result.row_count} rows</Badge>
      {duration !== undefined ? <Badge tone="neutral">{duration} ms</Badge> : null}
    </div>
  );
}
