import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  connectDatabase,
  clearHistory,
  disconnectDatabase,
  fetchDatabaseStatus,
  fetchKnowledgeStatus,
  fetchSessionStatus,
  listHistory,
  rebuildKnowledge,
} from "./actions";

function envelope(action: string, data: unknown) {
  return new Response(JSON.stringify({ ok: true, request_id: "req", action, data, error: null }), {
    status: 200,
    headers: { "content-type": "application/json" },
  });
}

describe("gateway actions", () => {
  beforeEach(() => {
    global.fetch = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      const action = JSON.parse(String(init?.body)).action;
      if (action === "database.status") {
        return envelope(action, { connected: false, ready: false, database: {} });
      }
      if (action === "knowledge.status") {
        return envelope(action, { database_ready: false, database: {}, knowledge_base_loaded: false, kb_status: "not_ready", vector_status: "unknown" });
      }
      if (action === "database.connect") {
        return envelope(action, { message: "connected", database: {} });
      }
      if (action === "database.disconnect") {
        return envelope(action, { disconnected: true, message: "disconnected" });
      }
      if (action === "knowledge.rebuild") {
        return envelope(action, { message: "ready", knowledge: {} });
      }
      if (action === "history.list") {
        return envelope(action, { entries: [], count: 0, limit: 50 });
      }
      if (action === "history.clear") {
        return envelope(action, { cleared: 1, count: 0 });
      }
      return envelope(action, { session_id: "session", database_connected: false, database_ready: false, has_last_sql: false });
    });
  });

  it("wraps session status", async () => {
    await expect(fetchSessionStatus()).resolves.toMatchObject({ session_id: "session" });
  });

  it("wraps database status", async () => {
    await expect(fetchDatabaseStatus()).resolves.toMatchObject({ connected: false });
  });

  it("wraps knowledge status", async () => {
    await expect(fetchKnowledgeStatus()).resolves.toMatchObject({ kb_status: "not_ready" });
  });

  it("wraps database connect and disconnect", async () => {
    await expect(
      connectDatabase({
        db_type: "mysql",
        host: "localhost",
        port: 3306,
        database: "company",
        username: "owner",
        password: "secret",
      }),
    ).resolves.toMatchObject({ message: "connected" });
    await expect(disconnectDatabase()).resolves.toMatchObject({ disconnected: true });
  });

  it("wraps knowledge rebuild", async () => {
    await expect(rebuildKnowledge()).resolves.toMatchObject({ message: "ready" });
  });

  it("wraps query history actions", async () => {
    await expect(listHistory()).resolves.toMatchObject({ count: 0, limit: 50 });
    await expect(clearHistory()).resolves.toMatchObject({ cleared: 1 });
  });
});
