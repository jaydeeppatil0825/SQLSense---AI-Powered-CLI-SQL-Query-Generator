import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { DatabasePage } from "./DatabasePage";

function envelope(action: string, data: unknown) {
  return new Response(JSON.stringify({ ok: true, request_id: "req", action, data, error: null }), {
    status: 200,
    headers: { "content-type": "application/json" },
  });
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <DatabasePage />
    </QueryClientProvider>,
  );
}

describe("database onboarding", () => {
  beforeEach(() => {
    vi.spyOn(Storage.prototype, "setItem");
    global.fetch = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      const action = JSON.parse(String(init?.body)).action;
      if (action === "database.status") {
        return envelope(action, { connected: false, ready: false, database: {} });
      }
      if (action === "knowledge.status") {
        return envelope(action, {
          database_ready: false,
          database: {},
          knowledge_base_loaded: false,
          kb_status: "not_ready",
          vector_status: "unknown",
        });
      }
      if (action === "database.connect") {
        return envelope(action, { message: "connected", database: { database: "company" } });
      }
      return envelope(action, { message: "ready", knowledge: {} });
    });
  });

  it("submits connection through the gateway and clears the password", async () => {
    renderPage();

    const password = screen.getByLabelText("Password");
    expect(password).toHaveAttribute("type", "password");

    await userEvent.clear(screen.getByLabelText("Database"));
    await userEvent.type(screen.getByLabelText("Database"), "company");
    await userEvent.clear(screen.getByLabelText("Username"));
    await userEvent.type(screen.getByLabelText("Username"), "owner");
    await userEvent.type(password, "secret");
    await userEvent.click(screen.getByRole("button", { name: "Connect database" }));

    await waitFor(() => expect(password).toHaveValue(""));
    const calls = vi.mocked(global.fetch).mock.calls.map((call) => JSON.parse(String(call[1]?.body ?? "{}")).action);
    expect(calls).toContain("database.connect");
    expect(Storage.prototype.setItem).not.toHaveBeenCalled();
  });
});
