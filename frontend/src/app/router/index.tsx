import { createRootRoute, createRoute, createRouter, Navigate, Outlet } from "@tanstack/react-router";
import { lazy, Suspense, type ComponentType } from "react";
import { FeatureErrorBoundary } from "../../components/shared/FeatureErrorBoundary";
import { AppShell } from "../layout/AppShell";
import { paths } from "./paths";

export { paths } from "./paths";

const rootRoute = createRootRoute({ component: Outlet });

function lazyPage(loader: () => Promise<{ default?: ComponentType; [key: string]: ComponentType | undefined }>, exportName: string) {
  const Component = lazy(() => loader().then((module) => ({ default: module[exportName] ?? module.default! })));
  return function LazyPage() {
    return (
      <FeatureErrorBoundary>
        <Suspense fallback={<div className="text-sm text-slate-300">Loading page.</div>}>
          <Component />
        </Suspense>
      </FeatureErrorBoundary>
    );
  };
}

const LandingPage = lazyPage(() => import("../../features/landing/LandingPage"), "LandingPage");
const ResultsPage = lazyPage(() => import("../../features/results/ResultsPage"), "ResultsPage");
const QueryPage = lazyPage(() => import("../../features/query/QueryPage"), "QueryPage");
const HistoryPage = lazyPage(() => import("../../features/history/HistoryPage"), "HistoryPage");
const DatabasePage = lazyPage(() => import("../../features/database/DatabasePage"), "DatabasePage");
const KnowledgeBasePage = lazyPage(() => import("../../features/knowledge-base/KnowledgeBasePage"), "KnowledgeBasePage");
const SettingsPage = lazyPage(() => import("../../features/settings/SettingsPage"), "SettingsPage");
const SystemStatusPage = lazyPage(() => import("../../features/system-status/SystemStatusPage"), "SystemStatusPage");

const indexRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: paths.home,
  component: LandingPage,
});

const appRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: paths.app,
  component: AppShell,
});

const appIndexRoute = createRoute({
  getParentRoute: () => appRoute,
  path: "/",
  component: ResultsPage,
});

const queryRoute = createRoute({
  getParentRoute: () => appRoute,
  path: "query",
  component: QueryPage,
});

const historyRoute = createRoute({
  getParentRoute: () => appRoute,
  path: "history",
  component: HistoryPage,
});

const databaseRoute = createRoute({
  getParentRoute: () => appRoute,
  path: "database",
  component: DatabasePage,
});

const knowledgeBaseRoute = createRoute({
  getParentRoute: () => appRoute,
  path: "knowledge",
  component: KnowledgeBasePage,
});

const settingsRoute = createRoute({
  getParentRoute: () => appRoute,
  path: "settings",
  component: SettingsPage,
});

const systemStatusRoute = createRoute({
  getParentRoute: () => appRoute,
  path: "status",
  component: SystemStatusPage,
});

function RedirectTo({ to }: { to: string }) {
  return <Navigate to={to} replace />;
}

const legacyRoutes = [
  [paths.legacyQuery, paths.query],
  [paths.legacyHistory, paths.history],
  [paths.legacyDatabase, paths.database],
  [paths.legacyKnowledgeBase, paths.knowledgeBase],
  [paths.legacySettings, paths.settings],
  [paths.legacySystemStatus, paths.systemStatus],
].map(([from, to]) =>
  createRoute({
    getParentRoute: () => rootRoute,
    path: from,
    component: () => <RedirectTo to={to} />,
  }),
);

export const routeTree = rootRoute.addChildren([
  indexRoute,
  appRoute.addChildren([
    appIndexRoute,
    queryRoute,
    historyRoute,
    databaseRoute,
    knowledgeBaseRoute,
    settingsRoute,
    systemStatusRoute,
  ]),
  ...legacyRoutes,
]);

export function createAppRouter() {
  return createRouter({ routeTree });
}

export const router = createAppRouter();

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}
