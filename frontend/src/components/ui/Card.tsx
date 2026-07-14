import type { ReactNode } from "react";

export function Card({ children, className = "" }: { children: ReactNode; className?: string }) {
  return (
    <section className={`rounded-3xl border border-white/10 bg-slate-950/55 p-6 shadow-glow backdrop-blur ${className}`}>
      {children}
    </section>
  );
}
