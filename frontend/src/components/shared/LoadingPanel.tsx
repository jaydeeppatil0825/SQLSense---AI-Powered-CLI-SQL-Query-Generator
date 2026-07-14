import { Card } from "../ui/Card";

export function LoadingPanel({ label = "Loading status" }: { label?: string }) {
  return (
    <Card>
      <div className="flex items-center gap-3 text-sm text-slate-300">
        <span className="h-3 w-3 animate-pulse rounded-full bg-signal-400" />
        {label}
      </div>
    </Card>
  );
}
