import { Outlet } from "@tanstack/react-router";
import { Header } from "./Header";
import { Sidebar } from "./Sidebar";

export function AppShell() {
  return (
    <div className="min-h-screen text-slate-100">
      <a href="#main-content" className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-50 focus:rounded-xl focus:bg-signal-500 focus:px-4 focus:py-2 focus:font-bold focus:text-ink-950">
        Skip to content
      </a>
      <div className="flex">
        <Sidebar />
        <div className="min-w-0 flex-1">
          <Header />
          <main id="main-content" className="mx-auto w-full max-w-7xl px-4 py-8 md:px-8" tabIndex={-1}>
            <Outlet />
          </main>
        </div>
      </div>
    </div>
  );
}
