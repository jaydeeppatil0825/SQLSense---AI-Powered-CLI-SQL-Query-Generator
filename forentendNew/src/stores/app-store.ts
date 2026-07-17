import { create } from "zustand";
import { persist } from "zustand/middleware";

export type Theme = "dark" | "light" | "system";
export type Accent = "violet" | "oceanic" | "amber";

export type ConnectionProfile = {
  engine: "mysql" | "postgresql";
  host: string;
  port: number;
  database: string;
  username: string;
  ssl: boolean;
};

type AppState = {
  theme: Theme;
  accent: Accent;
  reducedMotion: boolean;
  connected: boolean;
  profile: ConnectionProfile | null;
  savedProfiles: ConnectionProfile[];
  defaultPageSize: number;
  showPlanByDefault: boolean;
  confirmExport: boolean;
  showDeveloperDetail: boolean;
  setTheme: (t: Theme) => void;
  setAccent: (a: Accent) => void;
  setReducedMotion: (v: boolean) => void;
  connect: (p: ConnectionProfile) => void;
  disconnect: () => void;
  saveProfile: (p: ConnectionProfile) => void;
  removeProfile: (host: string, database: string) => void;
  setPageSize: (n: number) => void;
  setShowPlan: (v: boolean) => void;
  setConfirmExport: (v: boolean) => void;
  setShowDeveloperDetail: (v: boolean) => void;
};

export const useAppStore = create<AppState>()(
  persist(
    (set) => ({
      theme: "dark",
      accent: "violet",
      reducedMotion: false,
      connected: false,
      profile: null,
      savedProfiles: [],
      defaultPageSize: 25,
      showPlanByDefault: false,
      confirmExport: true,
      showDeveloperDetail: false,
      setTheme: (theme) => set({ theme }),
      setAccent: (accent) => set({ accent }),
      setReducedMotion: (reducedMotion) => set({ reducedMotion }),
      connect: (profile) => set({ connected: true, profile }),
      disconnect: () => set({ connected: false, profile: null }),
      saveProfile: (p) =>
        set((s) => {
          const key = (x: ConnectionProfile) => `${x.host}:${x.port}/${x.database}`;
          const next = s.savedProfiles.filter((x) => key(x) !== key(p));
          return { savedProfiles: [p, ...next].slice(0, 8) };
        }),
      removeProfile: (host, database) =>
        set((s) => ({
          savedProfiles: s.savedProfiles.filter(
            (x) => !(x.host === host && x.database === database),
          ),
        })),
      setPageSize: (defaultPageSize) => set({ defaultPageSize }),
      setShowPlan: (showPlanByDefault) => set({ showPlanByDefault }),
      setConfirmExport: (confirmExport) => set({ confirmExport }),
      setShowDeveloperDetail: (showDeveloperDetail) => set({ showDeveloperDetail }),
    }),
    {
      name: "sqlsense-app",
      partialize: (s) => ({
        theme: s.theme,
        accent: s.accent,
        reducedMotion: s.reducedMotion,
        savedProfiles: s.savedProfiles,
        defaultPageSize: s.defaultPageSize,
        showPlanByDefault: s.showPlanByDefault,
        confirmExport: s.confirmExport,
        showDeveloperDetail: s.showDeveloperDetail,
      }),
    },
  ),
);

export type HistoryEntry = {
  id: string;
  question: string;
  route: "deterministic_sql_required" | "cannot_plan_safely" | "blocked_unsafe";
  status: "ok" | "blocked" | "ambiguous" | "error";
  queryShape?: string;
  database: string;
  rowCount?: number;
  executionTimeMs?: number;
  timestamp: number;
};

type HistoryState = {
  entries: HistoryEntry[];
  add: (e: HistoryEntry) => void;
  remove: (id: string) => void;
  clear: () => void;
};

export const useHistoryStore = create<HistoryState>()(
  persist(
    (set) => ({
      entries: [],
      add: (e) => set((s) => ({ entries: [e, ...s.entries].slice(0, 200) })),
      remove: (id) => set((s) => ({ entries: s.entries.filter((x) => x.id !== id) })),
      clear: () => set({ entries: [] }),
    }),
    { name: "sqlsense-history" },
  ),
);
