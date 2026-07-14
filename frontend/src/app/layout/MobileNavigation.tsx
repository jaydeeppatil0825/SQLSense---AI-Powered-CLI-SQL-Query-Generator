import { Link } from "@tanstack/react-router";
import { useState } from "react";
import { ApplicationLogo } from "../../components/shared/ApplicationLogo";
import { Button } from "../../components/ui/Button";
import { NAV_ITEMS } from "./navigation";

export function MobileNavigation() {
  const [open, setOpen] = useState(false);

  return (
    <div className="lg:hidden">
      <Button aria-expanded={open} aria-controls="mobile-navigation" onClick={() => setOpen(true)}>
        Menu
      </Button>
      {open ? (
        <div className="fixed inset-0 z-40 bg-ink-950/90 p-4 backdrop-blur" role="dialog" aria-modal="true" aria-label="Mobile navigation">
          <div className="rounded-3xl border border-white/10 bg-slate-950 p-5">
            <div className="flex items-center justify-between gap-4">
              <ApplicationLogo />
              <Button onClick={() => setOpen(false)} variant="ghost">
                Close
              </Button>
            </div>
            <nav id="mobile-navigation" aria-label="Mobile primary" className="mt-8 grid gap-2">
              {NAV_ITEMS.map((item) => (
                <Link
                  key={item.to}
                  to={item.to}
                  onClick={() => setOpen(false)}
                  className="rounded-2xl border border-slate-800 px-4 py-3 text-sm text-slate-200 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-signal-400"
                >
                  {item.label}
                </Link>
              ))}
            </nav>
          </div>
        </div>
      ) : null}
    </div>
  );
}
