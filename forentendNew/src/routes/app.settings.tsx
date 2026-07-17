import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import { api } from "@/api/client";
import { useAppStore } from "@/stores/app-store";
import { applyAccentVars } from "@/lib/theme";

export const Route = createFileRoute("/app/settings")({
  head: () => ({ meta: [{ title: "Settings — SQLSense" }] }),
  component: SettingsPage,
});

function SettingsPage() {
  const navigate = useNavigate();
  const s = useAppStore();
  return (
    <div className="mx-auto max-w-3xl p-4 md:p-6 lg:p-8 space-y-6">
      <div>
        <h1 className="display text-xl md:text-2xl">Settings</h1>
        <p className="text-xs md:text-sm text-muted-foreground">
          Appearance, database profile, and query preferences.
        </p>
      </div>

      <Section title="Appearance">
        <Row label="Theme">
          <Group
            value={s.theme}
            options={["dark", "light", "system"]}
            onChange={(v) => s.setTheme(v as never)}
          />
        </Row>
        <Row label="Accent">
          <Group
            value={s.accent}
            options={["violet", "oceanic", "amber"]}
            onChange={(v) => {
              s.setAccent(v as never);
              setTimeout(applyAccentVars, 0);
            }}
          />
        </Row>
        <Row label="Reduced motion">
          <Switch checked={s.reducedMotion} onCheckedChange={s.setReducedMotion} />
        </Row>
      </Section>

      <Section title="Database profile">
        {s.profile ? (
          <>
            <KV label="Engine" value={s.profile.engine} />
            <KV label="Host" value={s.profile.host} />
            <KV label="Port" value={String(s.profile.port)} />
            <KV label="Database" value={s.profile.database} />
            <KV label="Username" value={s.profile.username} />
            <div className="flex flex-wrap gap-2 pt-2">
              <Button variant="outline" onClick={() => navigate({ to: "/connect" })}>
                Change database
              </Button>
              <Button
                variant="ghost"
                className="text-negative hover:text-negative"
                onClick={async () => {
                  try {
                    await api.disconnect();
                  } finally {
                    s.disconnect();
                  }
                  navigate({ to: "/connect" });
                }}
              >
                Disconnect
              </Button>
            </div>
          </>
        ) : (
          <div className="text-sm text-muted-foreground">Not connected.</div>
        )}
      </Section>

      <Section title="Query experience">
        <Row label="Default result page size">
          <Input
            type="number"
            className="w-24"
            value={s.defaultPageSize}
            onChange={(e) => s.setPageSize(Number(e.target.value) || 25)}
          />
        </Row>
        <Row label="Show generated SQL automatically">
          <Switch checked={s.showPlanByDefault} onCheckedChange={s.setShowPlan} />
        </Row>
        <Row label="Confirm CSV export">
          <Switch checked={s.confirmExport} onCheckedChange={s.setConfirmExport} />
        </Row>
      </Section>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="rounded-2xl border border-border bg-card p-4 md:p-6 space-y-3">
      <div className="text-[11px] uppercase tracking-wider text-muted-foreground">{title}</div>
      <div className="space-y-3">{children}</div>
    </div>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-3">
      <Label className="text-sm">{label}</Label>
      <div>{children}</div>
    </div>
  );
}

function Group({
  value,
  options,
  onChange,
}: {
  value: string;
  options: string[];
  onChange: (v: string) => void;
}) {
  return (
    <div className="inline-flex items-center gap-0.5 rounded-full border border-border bg-secondary p-0.5">
      {options.map((o) => (
        <button
          key={o}
          onClick={() => onChange(o)}
          className={
            "rounded-full px-3 py-1 text-xs capitalize " +
            (value === o ? "bg-primary text-primary-foreground" : "text-muted-foreground")
          }
        >
          {o}
        </button>
      ))}
    </div>
  );
}

function KV({ label, value }: { label: string; value: string }) {
  return (
    <div className="grid grid-cols-[140px_1fr] text-sm">
      <div className="text-muted-foreground">{label}</div>
      <div className="mono">{value}</div>
    </div>
  );
}
