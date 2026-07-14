import { Link } from "@tanstack/react-router";
import { ApplicationLogo } from "../../components/shared/ApplicationLogo";
import { NAV_ITEMS } from "./navigation";

export function Sidebar() {
  return (
    <aside className="hidden min-h-screen w-72 border-r border-white/10 bg-ink-950/80 p-5 backdrop-blur lg:block">
      <ApplicationLogo />
      <nav aria-label="Primary" className="mt-10 space-y-2">
        {NAV_ITEMS.map((item) => (
          <Link
            key={item.to}
            to={item.to}
            className="block rounded-2xl border border-transparent px-4 py-3 text-sm text-slate-300 transition hover:border-slate-700 hover:bg-slate-900/70 hover:text-white focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-signal-400"
            activeProps={{ className: "border-signal-500/30 bg-signal-500/10 text-white" }}
          >
            <span className="font-bold">{item.label}</span>
            <span className="mt-1 block text-xs text-slate-500">{item.description}</span>
          </Link>
        ))}
      </nav>
    </aside>
  );
}
