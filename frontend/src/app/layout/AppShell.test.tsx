import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AppProviders } from "../providers/AppProviders";
import { createAppRouter } from "../router";

function gatewayEnvelope(action: string, data: unknown) {
  return new Response(JSON.stringify({ ok: true, request_id: "req", action, data, error: null }), {
    status: 200,
    headers: { "content-type": "application/json" },
  });
}

function renderApp() {
  window.history.pushState({}, "", "/app");
  render(<AppProviders router={createAppRouter()} />);
}

describe("application shell", () => {
  beforeEach(() => {
    document.documentElement.className = "";
    global.fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/health")) {
        return new Response(JSON.stringify({ ok: true, service: "sqlsense-gateway" }), {
          status: 200,
          headers: { "content-type": "application/json" },
        });
      }
      const action = JSON.parse(String(init?.body)).action;
      if (action === "database.status") {
        return gatewayEnvelope(action, { connected: true, ready: true, database: { database: "demo" } });
      }
      if (action === "knowledge.status") {
        return gatewayEnvelope(action, {
          database_ready: true,
          database: {},
          knowledge_base_loaded: true,
          kb_status: "ready",
          vector_status: "ready",
        });
      }
      return gatewayEnvelope(action, { session_id: "session", database_connected: true, database_ready: true, has_last_sql: false });
    });
  });

  it("renders sidebar navigation", async () => {
    renderApp();

    expect(await screen.findByText("SQLSense")).toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "Primary" })).toBeInTheDocument();
    expect(screen.getByText("Data Knowledge")).toBeInTheDocument();
  });

  it("opens and closes mobile navigation", async () => {
    renderApp();

    await userEvent.click(await screen.findByRole("button", { name: "Menu" }));
    expect(screen.getByRole("navigation", { name: "Mobile primary" })).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(screen.queryByRole("navigation", { name: "Mobile primary" })).not.toBeInTheDocument();
  });

  it("toggles theme without browser persistence", async () => {
    renderApp();

    await waitFor(() => expect(document.documentElement).toHaveClass("dark"));
    await userEvent.click(screen.getByRole("button", { name: "Light" }));
    expect(document.documentElement).not.toHaveClass("dark");
  });

  it("shows gateway, database, and knowledge status", async () => {
    renderApp();

    expect(await screen.findByText("Service online")).toBeInTheDocument();
    expect(await screen.findByText("Database connected")).toBeInTheDocument();
    expect(await screen.findByText("Data ready")).toBeInTheDocument();
  });
});
