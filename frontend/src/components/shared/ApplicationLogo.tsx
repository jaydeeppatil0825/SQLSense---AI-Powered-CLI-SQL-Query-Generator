export function ApplicationLogo() {
  return (
    <div className="flex items-center gap-3">
      <div className="grid h-10 w-10 place-items-center rounded-2xl bg-signal-500 text-lg font-black text-ink-950">
        SQL
      </div>
      <div>
        <div className="text-sm font-black uppercase tracking-[0.28em] text-white">SQLSense</div>
        <div className="text-xs text-slate-400">Business data answers</div>
      </div>
    </div>
  );
}
