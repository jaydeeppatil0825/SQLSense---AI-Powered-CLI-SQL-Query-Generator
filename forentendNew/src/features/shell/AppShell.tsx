import { Link, Outlet, useRouterState } from "@tanstack/react-router";
import {
  Database,
  History,
  MessageSquare,
  Settings as SettingsIcon,
  Table2,
  Sun,
  Moon,
  Laptop,
  CircleDot,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { useAppStore } from "@/stores/app-store";

const NAV = [
  { to: "/app/ask", label: "Ask", icon: MessageSquare },
  { to: "/app/schema", label: "Schema", icon: Table2 },
  { to: "/app/history", label: "History", icon: History },
  { to: "/app/settings", label: "Settings", icon: SettingsIcon },
] as const;

const MOBILE_NAV = NAV;

export function AppShell() {
  const profile = useAppStore((s) => s.profile);
  const pathname = useRouterState({ select: (s) => s.location.pathname });

  return (
    <div className="flex min-h-screen w-full bg-background text-foreground">
      <aside className="hidden lg:flex w-60 shrink-0 flex-col border-r border-border bg-card">
        <div className="flex items-center gap-2 px-4 py-4 border-b border-divider">
          <div className="grid h-9 w-9 place-items-center rounded-xl bg-primary text-primary-foreground display font-semibold">
            S
          </div>
          <div className="min-w-0">
            <div className="display text-[15px] leading-tight">SQLSense</div>
            <div className="text-[11px] text-muted-foreground">deterministic NL → SQL</div>
          </div>
        </div>
        <nav className="flex-1 overflow-auto p-2">
          {NAV.map((item) => {
            const active = pathname.startsWith(item.to);
            const Icon = item.icon;
            return (
              <Link
                key={item.to}
                to={item.to}
                className={cn(
                  "flex items-center gap-3 rounded-lg px-3 py-2 text-sm transition-colors",
                  active
                    ? "bg-primary text-primary-foreground"
                    : "text-muted-foreground hover:bg-secondary hover:text-foreground",
                )}
              >
                <Icon className="h-4 w-4 shrink-0" />
                <span className="truncate">{item.label}</span>
              </Link>
            );
          })}
        </nav>
        <div className="border-t border-divider p-3 space-y-2">
          <div className="flex items-center gap-2 rounded-lg bg-secondary px-3 py-2">
            <Database className="h-4 w-4 text-primary shrink-0" />
            <div className="min-w-0 flex-1">
              <div className="text-[11px] uppercase tracking-wider text-muted-foreground">
                Connected
              </div>
              <div className="text-xs truncate mono">
                {profile ? `${profile.database}@${profile.host}` : "—"}
              </div>
            </div>
            <span className="grid h-2 w-2 rounded-full bg-positive" />
          </div>
          <ThemeToggle />
        </div>
      </aside>

      <div className="flex min-h-screen w-full min-w-0 flex-1 flex-col">
        <TopBar />
        <main className="flex-1 min-w-0 pb-20 lg:pb-0">
          <Outlet />
        </main>
        <nav className="fixed bottom-0 inset-x-0 z-40 border-t border-border bg-card lg:hidden">
          <div className="grid grid-cols-4">
            {MOBILE_NAV.map((item) => {
              const active = pathname.startsWith(item.to);
              const Icon = item.icon;
              return (
                <Link
                  key={item.to}
                  to={item.to}
                  className={cn(
                    "flex flex-col items-center justify-center gap-1 py-2.5 min-h-[52px] text-[11px]",
                    active ? "text-primary" : "text-muted-foreground",
                  )}
                >
                  <Icon className="h-5 w-5" />
                  <span>{item.label}</span>
                </Link>
              );
            })}
          </div>
        </nav>
      </div>
    </div>
  );
}

function TopBar() {
  const profile = useAppStore((s) => s.profile);
  return (
    <header className="sticky top-0 z-30 flex h-14 items-center gap-3 border-b border-border bg-background/85 backdrop-blur px-4 lg:px-6">
      <div className="lg:hidden flex items-center gap-2">
        <div className="grid h-8 w-8 place-items-center rounded-lg bg-primary text-primary-foreground display font-semibold text-sm">
          S
        </div>
        <span className="display text-[15px]">SQLSense</span>
      </div>
      <div className="hidden lg:flex items-center gap-2 text-sm text-muted-foreground min-w-0">
        <Database className="h-4 w-4" />
        <span className="mono truncate">
          {profile
            ? `${profile.engine} · ${profile.database} @ ${profile.host}:${profile.port}`
            : "not connected"}
        </span>
      </div>
      <div className="ml-auto flex items-center gap-2">
        <span className="hidden md:inline-flex items-center gap-1.5 rounded-full bg-positive-soft px-2.5 py-1 text-[11px] text-positive">
          <CircleDot className="h-3 w-3" /> Database ready
        </span>
        <div className="lg:hidden">
          <ThemeToggle compact />
        </div>
      </div>
    </header>
  );
}

export function ThemeToggle({ compact = false }: { compact?: boolean }) {
  const theme = useAppStore((s) => s.theme);
  const setTheme = useAppStore((s) => s.setTheme);
  const options = [
    { value: "light" as const, icon: Sun, label: "Light" },
    { value: "dark" as const, icon: Moon, label: "Dark" },
    { value: "system" as const, icon: Laptop, label: "System" },
  ];
  return (
    <div
      className={cn(
        "inline-flex items-center gap-0.5 rounded-full border border-border bg-secondary p-0.5",
        compact && "text-xs",
      )}
    >
      {options.map((o) => (
        <button
          key={o.value}
          onClick={() => setTheme(o.value)}
          aria-label={`${o.label} theme`}
          aria-pressed={theme === o.value}
          className={cn(
            "grid h-8 w-8 place-items-center rounded-full transition-colors",
            theme === o.value
              ? "bg-primary text-primary-foreground"
              : "text-muted-foreground hover:text-foreground",
          )}
        >
          <o.icon className="h-3.5 w-3.5" />
        </button>
      ))}
    </div>
  );
}
