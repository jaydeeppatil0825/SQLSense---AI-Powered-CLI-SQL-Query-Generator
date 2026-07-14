import { AppProviders } from "../../app/providers/AppProviders";
import { createAppRouter } from "../../app/router";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const entries = [
  {
    id: "2",
    request_id: "req-2",
    question: "delete customers",
    created_at: "2026-01-02T03:04:05Z",
    status: "rejected",
    route: "blocked_unsafe",
    error: { code: "blocked_unsafe", message: "Unsafe request" },
  },
  {
    id: "1",
    request_id: "req-1",
    question: "show customers",
    created_at: "2026-01-01T03:04:05Z",
    status: "success",
    route: "deterministic_sql_required",
    query_shape: "single_table_list",
    sql: "SELECT customers.name FROM customers",
    row_count: 2,
    duration_ms: 12,
  },
];

function renderPage() {
  window.history.pushState({}, "", "/app/history");
  render(<AppProviders router={createAppRouter()} />);
}

describe("Query History page", () => {
  beforeEach(() => {
    Object.assign(navigator, { clipboard: { writeText: vi.fn(async () => undefined) } });
    global.fetch = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      if (!init?.method) {
        return new Response(JSON.stringify({ ok: true, service: "sqlsense-gateway" }), { status: 200 });
      }
      const action = JSON.parse(String(init.body)).action;
      if (action === "history.list") {
        return new Response(JSON.stringify({ ok: true, request_id: "req", action, data: { entries, count: entries.length, limit: 50 }, error: null }), { status: 200 });
      }
      if (action === "history.clear") {
        return new Response(JSON.stringify({ ok: true, request_id: "req", action, data: { cleared: 2, count: 0 }, error: null }), { status: 200 });
      }
      if (action === "database.status") {
        return new Response(JSON.stringify({ ok: true, request_id: "req", action, data: { connected: true, ready: true, database: {} }, error: null }), { status: 200 });
      }
      if (action === "knowledge.status") {
        return new Response(
          JSON.stringify({
            ok: true,
            request_id: "req",
            action,
            data: { database_ready: true, database: {}, knowledge_base_loaded: true, kb_status: "ready", vector_status: "ready" },
            error: null,
          }),
          { status: 200 },
        );
      }
      return new Response(JSON.stringify({ ok: true, request_id: "req", action, data: {}, error: null }), { status: 200 });
    });
  });

  it("renders loaded history with search and status filters", async () => {
    renderPage();

    expect(await screen.findByText("show customers")).toBeInTheDocument();
    expect(screen.getByText("delete customers")).toBeInTheDocument();
    expect(screen.getAllByText("Successful").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Stopped safely").length).toBeGreaterThan(0);

    await userEvent.type(screen.getByLabelText("Search history"), "delete");
    expect(screen.queryByText("show customers")).not.toBeInTheDocument();

    await userEvent.selectOptions(screen.getByLabelText("Filter history"), "success");
    expect(screen.getByText("No query history yet")).toBeInTheDocument();
  });

  it("copies questions and transfers Ask Again without executing SQL", async () => {
    renderPage();

    await screen.findByText("delete customers");
    await userEvent.click(screen.getAllByRole("button", { name: "Copy Question" })[0]);
    expect(navigator.clipboard.writeText).toHaveBeenCalledWith("delete customers");

    await userEvent.click(screen.getAllByRole("button", { name: "Ask Again" })[1]);
    expect(await screen.findByDisplayValue("show customers")).toBeInTheDocument();
    const queryAskCalls = vi.mocked(global.fetch).mock.calls.filter((call) => String(call[1]?.body ?? "").includes("query.ask"));
    expect(queryAskCalls).toHaveLength(0);
  });

  it("clears history with confirmation", async () => {
    renderPage();

    await userEvent.click(await screen.findByRole("button", { name: "Clear History" }));
    await userEvent.click(screen.getByRole("button", { name: "Confirm clear" }));

    await waitFor(() => {
      expect(vi.mocked(global.fetch).mock.calls.some((call) => String(call[1]?.body ?? "").includes("history.clear"))).toBe(true);
    });
  });
});
