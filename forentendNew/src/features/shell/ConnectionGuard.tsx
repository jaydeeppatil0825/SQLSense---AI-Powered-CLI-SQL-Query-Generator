import { Navigate } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import { useEffect } from "react";
import { api } from "@/api/client";
import { useAppStore, type ConnectionProfile } from "@/stores/app-store";
import type { ReactNode } from "react";

export function ConnectionGuard({ children }: { children: ReactNode }) {
  const connect = useAppStore((s) => s.connect);
  const disconnect = useAppStore((s) => s.disconnect);
  const status = useQuery({
    queryKey: ["session.status"],
    queryFn: api.sessionStatus,
    retry: false,
  });
  const db = useQuery({
    queryKey: ["database.status"],
    queryFn: api.databaseStatus,
    enabled: status.data?.database_connected === true,
    retry: false,
  });

  useEffect(() => {
    if (status.data && !status.data.database_connected) disconnect();
  }, [disconnect, status.data]);

  useEffect(() => {
    const raw = db.data?.database;
    if (!raw || !db.data?.connected) return;
    const profile: ConnectionProfile = {
      engine:
        String(raw.db_type ?? raw.database_type ?? "mysql") === "postgresql"
          ? "postgresql"
          : "mysql",
      host: String(raw.host ?? "localhost"),
      port: Number(raw.port ?? 0),
      database: String(raw.database ?? raw.database_name ?? ""),
      username: String(raw.username ?? ""),
      ssl: false,
    };
    connect(profile);
  }, [connect, db.data]);

  if (status.isLoading) {
    return (
      <div className="grid min-h-screen place-items-center bg-background text-muted-foreground">
        <div className="flex items-center gap-2 text-sm">
          <Loader2 className="h-4 w-4 animate-spin" /> Checking session
        </div>
      </div>
    );
  }

  if (status.isError || !status.data?.database_connected) return <Navigate to="/connect" replace />;
  return <>{children}</>;
}
