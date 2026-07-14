import type { ReactNode } from "react";

type BadgeTone = "neutral" | "good" | "warn" | "bad";

const tones: Record<BadgeTone, string> = {
  neutral: "border-slate-500/30 bg-slate-500/10 text-slate-200",
  good: "border-emerald-400/30 bg-emerald-400/10 text-emerald-200",
  warn: "border-amber-400/30 bg-amber-400/10 text-amber-100",
  bad: "border-rose-400/30 bg-rose-400/10 text-rose-100",
};

export function Badge({ children, tone = "neutral" }: { children: ReactNode; tone?: BadgeTone }) {
  return (
    <span className={`inline-flex items-center rounded-full border px-2.5 py-1 text-xs font-medium ${tones[tone]}`}>
      {children}
    </span>
  );
}
