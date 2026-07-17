import { createFileRoute, Navigate } from "@tanstack/react-router";
import { useAppStore } from "@/stores/app-store";

export const Route = createFileRoute("/")({
  component: Index,
});

function Index() {
  const connected = useAppStore((s) => s.connected);
  return <Navigate to={connected ? "/app/ask" : "/connect"} replace />;
}
