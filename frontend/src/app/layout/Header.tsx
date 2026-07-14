import { useTheme } from "../providers/theme";
import { ConnectionBadge } from "../../components/shared/ConnectionBadge";
import { KnowledgeStatusBadge } from "../../components/shared/KnowledgeStatusBadge";
import { Badge } from "../../components/ui/Badge";
import { Button } from "../../components/ui/Button";
import { useSystemStatus } from "../../features/system-status/useSystemStatus";
import { MobileNavigation } from "./MobileNavigation";

export function Header() {
  const { theme, toggleTheme } = useTheme();
  const { health, session, database, knowledge } = useSystemStatus();
  const gatewayReady = health.isSuccess;

  return (
    <header className="sticky top-0 z-30 border-b border-white/10 bg-ink-950/70 px-4 py-4 backdrop-blur md:px-8">
      <div className="flex items-center justify-between gap-4">
        <MobileNavigation />
        <div className="hidden text-sm text-slate-400 lg:block">Secure business data workspace</div>
        <div className="flex flex-wrap items-center justify-end gap-2">
          <Badge tone={gatewayReady ? "good" : health.isError ? "bad" : "neutral"}>
            {gatewayReady ? "Service online" : health.isError ? "Service unavailable" : "Checking service"}
          </Badge>
          {session.isError ? <Badge tone="bad">Session expired</Badge> : null}
          <ConnectionBadge connected={database.data?.connected} />
          <KnowledgeStatusBadge ready={knowledge.data?.knowledge_base_loaded} />
          <Button variant="ghost" onClick={toggleTheme}>
            {theme === "dark" ? "Light" : "Dark"}
          </Button>
        </div>
      </div>
    </header>
  );
}
