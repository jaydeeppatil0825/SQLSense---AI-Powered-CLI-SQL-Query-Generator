export function ChartUnavailable({ reason }: { reason: string }) {
  return (
    <div className="rounded-2xl border border-white/10 bg-slate-950/50 p-4 text-sm text-slate-300">
      {reason}
    </div>
  );
}
