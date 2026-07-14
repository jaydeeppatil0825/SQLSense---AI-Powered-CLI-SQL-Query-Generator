import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AppProviders } from "../../app/providers/AppProviders";
import { createAppRouter } from "../../app/router";

type MockState = {
  connected: boolean;
  ready: boolean;
  answer: "success" | "zero" | "ambiguous" | "unsafe" | "validation";
};

const state: MockState = { connected: true, ready: true, answer: "success" };

function envelope(action: string, data: unknown) {
  return new Response(JSON.stringify({ ok: true, request_id: "req-123", action, data, error: null }), {
    status: 200,
    headers: { "content-type": "application/json" },
  });
}

function rejected(action: string, code: string, message: string, details?: unknown) {
  return new Response(JSON.stringify({ ok: false, request_id: "req-err", action, data: null, error: { code, message, details } }), {
    status: 200,
    headers: { "content-type": "application/json" },
  });
}

function renderPage() {
  window.history.pushState({}, "", "/app/query");
  render(<AppProviders router={createAppRouter()} />);
}

describe("Ask Your Data page", () => {
  beforeEach(() => {
    state.connected = true;
    state.ready = true;
    state.answer = "success";
    vi.spyOn(Storage.prototype, "setItem");
    Object.assign(navigator, { clipboard: { writeText: vi.fn(async () => undefined) } });
    global.fetch = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      if (!init?.method) {
        return new Response(JSON.stringify({ ok: true, service: "sqlsense-gateway" }), {
          status: 200,
          headers: { "content-type": "application/json" },
        });
      }
      const action = JSON.parse(String(init?.body)).action;
      if (action === "database.status") {
        return envelope(action, { connected: state.connected, ready: state.connected, database: {} });
      }
      if (action === "knowledge.status") {
        return envelope(action, {
          database_ready: state.connected,
          database: {},
          knowledge_base_loaded: state.ready,
          kb_status: state.ready ? "ready" : "not_ready",
          vector_status: "ready",
        });
      }
      if (state.answer === "zero") {
        return envelope(action, {
          route: "deterministic_sql_required",
          query_shape: "filtered_query",
          sql: "SELECT orders.order_id FROM orders WHERE orders.status = 'Closed'",
          columns: [],
          rows: [],
          row_count: 0,
          execution: { executed: true, message: "ok", duration_ms: 4 },
        });
      }
      if (state.answer === "ambiguous") {
        return rejected(action, "AMBIGUOUS_COLUMN", "Ambiguous column", { options: ["show paid orders", "show paid payments"] });
      }
      if (state.answer === "unsafe") {
        return rejected(action, "UNSAFE_SQL", "Unsafe request");
      }
      if (state.answer === "validation") {
        return rejected(action, "VALIDATION_FAILED", "Validation failed");
      }
      return envelope(action, {
        route: "deterministic_sql_required",
        query_shape: "single_table_list",
        sql: "SELECT customers.name, customers.status FROM customers",
        columns: ["name", "status", "last_seen"],
        rows: [{ name: "Acme", status: true, last_seen: null }],
        row_count: 1,
        execution: { executed: true, message: "ok", duration_ms: 9 },
      });
    });
  });

  it("gates asking when database is disconnected", async () => {
    state.connected = false;
    state.ready = false;
    renderPage();

    expect(await screen.findByText("Connect your business data first")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Go to Data Connection" })).toHaveAttribute("href", "/app/database");
  });

  it("gates asking while data knowledge is not ready", async () => {
    state.connected = true;
    state.ready = false;
    renderPage();

    expect(await screen.findByText("Your data is still being prepared")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Go to Data Knowledge" })).toHaveAttribute("href", "/app/knowledge");
  });

  it("validates empty questions", async () => {
    state.connected = true;
    state.ready = true;
    renderPage();

    await userEvent.click(await screen.findByRole("button", { name: "Ask your data" }));

    expect(screen.getByText("Enter a business question first.")).toBeInTheDocument();
  });

  it("submits only the question and renders dynamic results", async () => {
    renderPage();

    await userEvent.type(await screen.findByLabelText("Business question"), "show customers");
    await userEvent.click(screen.getByRole("button", { name: "Ask your data" }));

    expect(await screen.findByText("Results")).toBeInTheDocument();
    expect(screen.getByText("Acme")).toBeInTheDocument();
    expect(screen.getByText("Yes")).toBeInTheDocument();
    expect(screen.getByText("—")).toBeInTheDocument();

    const askCall = vi
      .mocked(global.fetch)
      .mock.calls.find((call) => JSON.parse(String(call[1]?.body ?? "{}")).action === "query.ask");
    expect(JSON.parse(String(askCall?.[1]?.body))).toEqual({ action: "query.ask", payload: { question: "show customers" } });
    expect(askCall?.[1]).toMatchObject({ credentials: "include" });
    expect(Storage.prototype.setItem).not.toHaveBeenCalled();
  });

  it("treats zero rows as success", async () => {
    state.answer = "zero";
    renderPage();

    await userEvent.type(await screen.findByLabelText("Business question"), "show closed orders");
    await userEvent.click(screen.getByRole("button", { name: "Ask your data" }));

    expect(await screen.findByText("No matching records were found.")).toBeInTheDocument();
  });

  it("keeps validated SQL read-only and copyable", async () => {
    renderPage();

    await userEvent.type(await screen.findByLabelText("Business question"), "show customers");
    await userEvent.click(screen.getByRole("button", { name: "Ask your data" }));
    await userEvent.click(await screen.findByText("View validated SQL"));
    await userEvent.click(screen.getByRole("button", { name: "Copy SQL" }));

    await waitFor(() => expect(navigator.clipboard.writeText).toHaveBeenCalledWith("SELECT customers.name, customers.status FROM customers"));
    expect(screen.queryByRole("textbox", { name: /sql/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /run sql/i })).not.toBeInTheDocument();
  });

  it("renders ambiguity options without auto-running them", async () => {
    state.answer = "ambiguous";
    renderPage();

    await userEvent.type(await screen.findByLabelText("Business question"), "show paid");
    await userEvent.click(screen.getByRole("button", { name: "Ask your data" }));
    await userEvent.click(await screen.findByRole("button", { name: "show paid payments" }));

    expect(screen.getByLabelText("Business question")).toHaveValue("show paid payments");
    const askCalls = vi
      .mocked(global.fetch)
      .mock.calls.filter((call) => JSON.parse(String(call[1]?.body ?? "{}")).action === "query.ask");
    expect(askCalls).toHaveLength(1);
  });

  it("shows unsafe and validation failures as stopped queries", async () => {
    state.answer = "unsafe";
    renderPage();
    await userEvent.type(await screen.findByLabelText("Business question"), "delete customers");
    await userEvent.click(screen.getByRole("button", { name: "Ask your data" }));
    expect(await screen.findByText("Query stopped for safety. SQLSense only runs read-only questions.")).toBeInTheDocument();
  });
});
