import type { HistoryEntry } from "../../gateway/contracts";
import { HistoryItem } from "./HistoryItem";

export function HistoryList({ entries }: { entries: HistoryEntry[] }) {
  return (
    <div className="grid gap-4">
      {entries.map((entry) => (
        <HistoryItem key={entry.id} entry={entry} />
      ))}
    </div>
  );
}
