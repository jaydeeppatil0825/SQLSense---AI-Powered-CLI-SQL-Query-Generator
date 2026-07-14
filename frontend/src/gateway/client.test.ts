import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { gatewayRequest, getHealth } from "./client";
import { GatewayClientError } from "./errors";

function jsonResponse(body: unknown, init: ResponseInit = {}) {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "content-type": "application/json" },
    ...init,
  });
}

describe("gateway client", () => {
  beforeEach(() => {
    global.fetch = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      const body = init?.body ? JSON.parse(String(init.body)) : null;
      if (!init?.method) {
        return jsonResponse({ ok: true, service: "sqlsense-gateway" });
      }
      return jsonResponse({
        ok: true,
        request_id: "req-1",
        action: body.action,
        data: { database_connected: false, database_ready: false, has_last_sql: false, session_id: "s1" },
        error: null,
      });
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("checks health with cookies included", async () => {
    await expect(getHealth()).resolves.toEqual({ ok: true, service: "sqlsense-gateway" });

    expect(global.fetch).toHaveBeenCalledWith("http://localhost:8000/health", { credentials: "include" });
  });

  it("posts gateway actions through the shared envelope transport", async () => {
    await expect(gatewayRequest("session.status")).resolves.toMatchObject({ session_id: "s1" });

    expect(global.fetch).toHaveBeenCalledWith(
      "http://localhost:8000/api/v1/gateway",
      expect.objectContaining({
        method: "POST",
        credentials: "include",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ action: "session.status", payload: {} }),
      }),
    );
  });

  it("rejects malformed gateway responses", async () => {
    global.fetch = vi.fn(async () => jsonResponse({ ok: true }));

    await expect(gatewayRequest("session.status")).rejects.toBeInstanceOf(GatewayClientError);
  });

  it("normalizes gateway rejections without stack details", async () => {
    global.fetch = vi.fn(async () =>
      jsonResponse({
        ok: false,
        request_id: "req-2",
        action: "session.status",
        data: null,
        error: { code: "invalid_payload", message: "Invalid action payload" },
      }),
    );

    await expect(gatewayRequest("session.status")).rejects.toMatchObject({
      code: "invalid_payload",
      message: "Invalid action payload",
    });
  });
});
