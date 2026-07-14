import { expect, test, type Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

type MockState = {
  connected: boolean;
  ready: boolean;
  history: any[];
  askPayloads: any[];
};

async function mockGateway(page: Page, state: MockState) {
  await page.route("**/health", async (route) => {
    await route.fulfill({ json: { ok: true, service: "sqlsense-gateway" } });
  });
  await page.route("**/api/v1/gateway", async (route) => {
    const body = route.request().postDataJSON();
    const action = body.action;
    const payload = body.payload ?? {};
    const ok = (data: unknown) => route.fulfill({ json: { ok: true, request_id: "req-e2e", action, data, error: null } });
    const rejected = (code: string, message: string) => route.fulfill({ json: { ok: false, request_id: "req-e2e", action, data: null, error: { code, message } } });

    if (action === "session.status") return ok({ session_id: "s1", database_connected: state.connected, database_ready: state.ready, has_last_sql: false });
    if (action === "database.status") return ok({ connected: state.connected, ready: state.connected, database: state.connected ? { database: "demo" } : {} });
    if (action === "knowledge.status") return ok({ database_ready: state.connected, database: {}, knowledge_base_loaded: state.ready, kb_status: state.ready ? "ready" : "not_ready", vector_status: "ready" });
    if (action === "database.connect") {
      state.connected = true;
      state.ready = false;
      return ok({ message: "connected", database: { database: payload.database }, prepare: {} });
    }
    if (action === "knowledge.rebuild") {
      state.ready = true;
      return ok({ message: "ready", knowledge: { database_ready: true, database: {}, knowledge_base_loaded: true, kb_status: "ready", vector_status: "ready" } });
    }
    if (action === "history.list") return ok({ entries: state.history, count: state.history.length, limit: 50 });
    if (action === "history.clear") {
      const cleared = state.history.length;
      state.history = [];
      return ok({ cleared, count: 0 });
    }
    if (action === "query.ask") {
      state.askPayloads.push(payload);
      if (Object.keys(payload).join(",") !== "question") return rejected("invalid_payload", "Only question is allowed");
      if (String(payload.question).includes("delete")) {
        state.history.unshift({ id: "h-reject", request_id: "req-e2e", question: payload.question, created_at: new Date().toISOString(), status: "rejected", route: "UNSAFE_SQL", error: { code: "UNSAFE_SQL", message: "Unsafe request" } });
        return rejected("UNSAFE_SQL", "Unsafe request");
      }
      const zero = String(payload.question).includes("zero");
      const data = {
        route: "deterministic_sql_required",
        query_shape: "grouped_aggregate",
        sql: "SELECT city, amount, order_date FROM demo",
        columns: zero ? [] : ["city", "amount", "order_date"],
        rows: zero ? [] : [
          { city: "Mumbai", amount: 10, order_date: "2026-01-01" },
          { city: "Pune", amount: 20, order_date: "2026-01-02" },
        ],
        row_count: zero ? 0 : 2,
        execution: { executed: true, message: "ok", duration_ms: 5 },
      };
      state.history.unshift({ id: `h-${state.history.length}`, request_id: "req-e2e", question: payload.question, created_at: new Date().toISOString(), status: "success", route: data.route, query_shape: data.query_shape, sql: data.sql, row_count: data.row_count, duration_ms: 5 });
      return ok(data);
    }
    return rejected("unknown_action", "Unknown action");
  });
}

test("complete mocked web app journey", async ({ page }) => {
  const state: MockState = { connected: false, ready: false, history: [], askPayloads: [] };
  await mockGateway(page, state);

  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Ask questions. Understand your business data." })).toBeVisible();
  await page.getByRole("link", { name: "Open SQLSense" }).first().click();
  await expect(page.getByRole("heading", { name: "Understand your business data" })).toBeVisible();

  await page.goto("/app/database");
  await expect(page.getByText("Disconnected")).toBeVisible();
  await page.getByLabel("Database").fill("demo");
  await page.getByLabel("Username").fill("root");
  await page.getByLabel("Password").fill("secret");
  await page.getByRole("button", { name: "Connect database" }).click();
  await expect(page.getByText("Database connected. Checking data readiness.")).toBeVisible();
  await page.getByRole("button", { name: "Prepare data knowledge" }).click();
  await expect(page.getByText("Your data is ready.")).toBeVisible();

  await page.goto("/app/query");
  await page.getByLabel("Business question").fill("show sales by city");
  await page.getByRole("button", { name: "Ask your data" }).click();
  await expect(page.getByText("Mumbai")).toBeVisible();
  await expect(page.getByText("Pune")).toBeVisible();
  await page.getByRole("tab", { name: "Chart" }).click();
  await expect(page.getByText("Select category")).toBeVisible();
  expect(state.askPayloads[0]).toEqual({ question: "show sales by city" });

  await page.getByRole("button", { name: "Export CSV" }).click();
  await page.getByRole("button", { name: "Export JSON" }).click();
  await page.getByText("View validated SQL").click();
  await expect(page.getByText("SELECT city, amount, order_date FROM demo")).toBeVisible();

  await page.getByRole("button", { name: "Clear" }).click();
  await page.getByLabel("Business question").fill("zero rows");
  await page.getByRole("button", { name: "Ask your data" }).click();
  await expect(page.getByText("No matching records were found.")).toBeVisible();

  await page.getByLabel("Business question").fill("delete customers");
  await page.getByRole("button", { name: "Ask your data" }).click();
  await expect(page.getByText("Query stopped for safety. SQLSense only runs read-only questions.")).toBeVisible();

  await page.goto("/app/history");
  await expect(page.getByText("show sales by city")).toBeVisible();
  await page.getByRole("button", { name: "Ask Again" }).first().click();
  await expect(page.getByLabel("Business question")).toHaveValue("delete customers");
  expect(state.askPayloads).toHaveLength(3);

  await page.goto("/app/history");
  await page.getByRole("button", { name: "Clear History" }).click();
  await page.getByRole("button", { name: "Confirm clear" }).click();
  await expect(page.getByText("No query history yet")).toBeVisible();

  await page.goto("/app/query");
  await page.reload();
  await expect(page.getByRole("heading", { name: "Ask Your Data" })).toBeVisible();

  const results = await new AxeBuilder({ page }).exclude("pre").analyze();
  expect(results.violations.filter((violation) => ["critical", "serious"].includes(violation.impact ?? ""))).toEqual([]);
});

test("offline and deep routes are controlled", async ({ page }) => {
  await page.route("**/health", async (route) => route.abort());
  await page.route("**/api/v1/gateway", async (route) => route.abort());
  await page.goto("/app/query");
  await expect(page.getByRole("heading", { name: "Service unavailable", level: 1 })).toBeVisible();
  await page.setViewportSize({ width: 390, height: 800 });
  await page.goto("/app/status");
  await expect(page.getByRole("button", { name: "Menu" })).toBeVisible();
});

test("session-expired state is visible", async ({ page }) => {
  await page.route("**/health", async (route) => {
    await route.fulfill({ json: { ok: true, service: "sqlsense-gateway" } });
  });
  await page.route("**/api/v1/gateway", async (route) => {
    const body = route.request().postDataJSON();
    if (body.action === "session.status") {
      await route.fulfill({ json: { ok: false, request_id: "expired", action: "session.status", data: null, error: { code: "session_expired", message: "Session expired" } } });
      return;
    }
    await route.fulfill({ json: { ok: true, request_id: "req", action: body.action, data: { connected: false, ready: false, database: {}, database_ready: false, knowledge_base_loaded: false, kb_status: "not_ready", vector_status: "unknown" }, error: null } });
  });
  await page.goto("/app");
  await expect(page.getByText("Session expired")).toBeVisible();
});
