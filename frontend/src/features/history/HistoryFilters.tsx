import type { HistoryStatus } from "../../gateway/contracts";

export type HistoryFilter = "all" | HistoryStatus;

export function HistoryFilters({
  search,
  status,
  onSearch,
  onStatus,
}: {
  search: string;
  status: HistoryFilter;
  onSearch: (value: string) => void;
  onStatus: (value: HistoryFilter) => void;
}) {
  return (
    <div className="grid gap-3 md:grid-cols-[1fr_12rem]">
      <input
        aria-label="Search history"
        value={search}
        onChange={(event) => onSearch(event.target.value)}
        placeholder="Search questions"
        className="rounded-xl border border-slate-700 bg-slate-950 px-3 py-2 text-white outline-none focus:border-signal-400"
      />
      <select
        aria-label="Filter history"
        value={status}
        onChange={(event) => onStatus(event.target.value as HistoryFilter)}
        className="rounded-xl border border-slate-700 bg-slate-950 px-3 py-2 text-white"
      >
        <option value="all">All</option>
        <option value="success">Successful</option>
        <option value="rejected">Stopped safely</option>
      </select>
    </div>
  );
}
