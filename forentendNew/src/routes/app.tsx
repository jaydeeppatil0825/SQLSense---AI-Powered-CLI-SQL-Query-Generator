import { createFileRoute } from "@tanstack/react-router";
import { AppShell } from "@/features/shell/AppShell";
import { ConnectionGuard } from "@/features/shell/ConnectionGuard";

export const Route = createFileRoute("/app")({
  component: AppLayout,
});

function AppLayout() {
  return (
    <ConnectionGuard>
      <AppShell />
    </ConnectionGuard>
  );
}
