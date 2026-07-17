import type {
  ConnectionTestResult,
  KnowledgeBaseStatus,
  QueryColumn,
  QueryResponse,
  QueryRoute,
  SchemaTable,
  ValidationDetail,
} from "./types";
import type { ConnectionProfile, HistoryEntry } from "@/stores/app-store";

const USE_MOCK = (import.meta.env.VITE_USE_MOCK_API ?? "false") === "true";
const API_ORIGIN = String(import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000")
  .replace(/\/api\/v1\/?$/, "")
  .replace(/\/$/, "");

type GatewayAction =
  | "session.status"
  | "session.reset"
  | "database.connect"
  | "database.disconnect"
  | "database.status"
  | "knowledge.status"
  | "knowledge.rebuild"
  | "query.ask"
  | "query.last"
  | "history.list"
  | "history.clear";

type GatewayEnvelope<T> = {
  ok: boolean;
  request_id: string;
  action: GatewayAction;
  data: T | null;
  error: { code: string; message: string; details?: unknown } | null;
};

export type SessionStatus = {
  session_id: string;
  database_connected: boolean;
  database_ready: boolean;
  has_last_sql: boolean;
};

export type DatabaseStatus = {
  connected: boolean;
  ready: boolean;
  database?: Record<string, unknown>;
};

class GatewayError extends Error {
  code: string;
  details: unknown;

  constructor(code: string, message: string, details?: unknown) {
    super(message);
    this.name = "GatewayError";
    this.code = code;
    this.details = details;
  }
}

async function gatewayEnvelope<T>(action: GatewayAction, payload: Record<string, unknown> = {}) {
  const res = await fetch(`${API_ORIGIN}/api/v1/gateway`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    credentials: "include",
    body: JSON.stringify({ action, payload }),
  });
  return (await res.json()) as GatewayEnvelope<T>;
}

async function gateway<T>(action: GatewayAction, payload: Record<string, unknown> = {}) {
  const envelope = await gatewayEnvelope<T>(action, payload);
  if (!envelope.ok || !envelope.data) {
    throw new GatewayError(
      envelope.error?.code ?? "gateway_error",
      envelope.error?.message ?? "Gateway request failed",
      envelope.error?.details,
    );
  }
  return envelope.data;
}

function connectionPayload(p: ConnectionProfile & { password: string }) {
  return {
    db_type: p.engine,
    host: p.host,
    port: p.port,
    username: p.username,
    password: p.password,
    database: p.database,
    use_ai_enrichment: false,
  };
}

function mapRoute(value: unknown): QueryRoute {
  return value === "blocked_unsafe" || value === "cannot_plan_safely"
    ? value
    : "deterministic_sql_required";
}

function mapColumns(rawColumns: unknown, rows: Record<string, unknown>[]): QueryColumn[] {
  const names =
    Array.isArray(rawColumns) && rawColumns.length
      ? rawColumns.map(String)
      : Object.keys(rows[0] ?? {});
  return names.map((name) => ({
    key: name,
    label: name,
    numeric: rows.some((row) => typeof row[name] === "number"),
  }));
}

function mapValidation(value: unknown): ValidationDetail | undefined {
  if (!value || typeof value !== "object") return undefined;
  const raw = value as Record<string, unknown>;
  const valid = raw.valid ?? raw.ok ?? raw.is_valid;
  return {
    valid: valid !== false,
    message: String(raw.message ?? raw.reason ?? "Validated by SQLSense"),
  };
}

function mapJoinPath(value: unknown) {
  const path = value && typeof value === "object" ? (value as Record<string, unknown>) : {};
  const edges = Array.isArray(path.edges) ? path.edges : [];
  return edges
    .map((edge) => {
      const e = edge as Record<string, unknown>;
      return {
        fromTable: String(e.from_table ?? ""),
        fromColumn: String(e.from_column ?? ""),
        toTable: String(e.to_table ?? ""),
        toColumn: String(e.to_column ?? ""),
        relationshipType: String(e.relationship_type ?? "relationship_graph"),
      };
    })
    .filter((edge) => edge.fromTable && edge.toTable);
}

function mapQuerySuccess(question: string, data: Record<string, unknown>): QueryResponse {
  const rows = Array.isArray(data.rows) ? (data.rows as Record<string, unknown>[]) : [];
  const query =
    data.query && typeof data.query === "object" ? (data.query as Record<string, unknown>) : {};
  const execution =
    data.execution && typeof data.execution === "object"
      ? (data.execution as Record<string, unknown>)
      : {};
  return {
    route: mapRoute(data.route),
    reason: String(
      query.route_reason ?? execution.message ?? "SQLSense generated and validated this query.",
    ),
    question,
    queryShape: String(data.query_shape ?? query.query_shape ?? ""),
    sql: typeof data.sql === "string" ? data.sql : undefined,
    columns: mapColumns(data.columns, rows),
    rows,
    rowCount: Number(data.row_count ?? rows.length),
    executionTimeMs: Number(execution.duration_ms ?? 0),
    selectedJoinPath: mapJoinPath(query.selected_join_path),
    validation: mapValidation(data.validation),
  };
}

function mapQueryError(question: string, envelope: GatewayEnvelope<unknown>): QueryResponse {
  const details = envelope.error?.details;
  const query =
    details && typeof details === "object" && "query" in details
      ? ((details as { query?: Record<string, unknown> }).query ?? {})
      : {};
  const code = envelope.error?.code ?? "cannot_plan_safely";
  return {
    route: mapRoute(code),
    reason: envelope.error?.message ?? "SQLSense could not safely answer this question.",
    question,
    queryShape: String(query.query_shape ?? ""),
    selectedJoinPath: mapJoinPath(query.selected_join_path),
    validation: mapValidation(
      details && typeof details === "object" && "validation" in details
        ? (details as { validation?: unknown }).validation
        : undefined,
    ),
  };
}

function mapKnowledgeStatus(data: Record<string, unknown>): KnowledgeBaseStatus {
  const database =
    data.database && typeof data.database === "object"
      ? (data.database as Record<string, unknown>)
      : {};
  const vector =
    data.vector && typeof data.vector === "object" ? (data.vector as Record<string, unknown>) : {};
  const lastBuild =
    data.last_build && typeof data.last_build === "object"
      ? (data.last_build as Record<string, unknown>)
      : {};
  return {
    database: String(database.database ?? database.database_name ?? ""),
    schemaHash: String(data.schema_hash ?? ""),
    lastBuildAt: null,
    tableCount: Number(lastBuild.table_count ?? 0),
    columnCount: Number(lastBuild.column_count ?? 0),
    relationshipCount: Number(lastBuild.relationship_count ?? 0),
    glossaryTermCount: Number(lastBuild.glossary_term_count ?? 0),
    vectorIndex:
      data.vector_status === "ready" || vector.index_status === "ready" ? "ready" : "missing",
    semanticEnrichment: "disabled",
    stale: data.kb_status !== "ready",
    stages: [
      { name: "Database ready", status: data.database_ready ? "done" : "pending" },
      { name: "Knowledge base", status: data.knowledge_base_loaded ? "done" : "pending" },
      { name: "Vector index", status: data.vector_status === "ready" ? "done" : "pending" },
    ],
    buildLog: [],
  };
}

function mapHistoryEntry(raw: Record<string, unknown>): HistoryEntry {
  const route = mapRoute(raw.route);
  return {
    id: String(raw.id ?? raw.request_id ?? Date.now()),
    question: String(raw.question ?? ""),
    route,
    status: raw.status === "success" ? "ok" : route === "blocked_unsafe" ? "blocked" : "ambiguous",
    queryShape: raw.query_shape ? String(raw.query_shape) : undefined,
    database: "",
    rowCount: typeof raw.row_count === "number" ? raw.row_count : undefined,
    executionTimeMs: typeof raw.duration_ms === "number" ? raw.duration_ms : undefined,
    timestamp: raw.created_at ? Date.parse(String(raw.created_at)) : Date.now(),
  };
}

export const api = {
  async health() {
    const res = await fetch(`${API_ORIGIN}/health`, { credentials: "include" });
    return (await res.json()) as { ok: boolean; service?: string };
  },

  gateway,

  sessionStatus() {
    return gateway<SessionStatus>("session.status");
  },

  databaseStatus() {
    return gateway<DatabaseStatus>("database.status");
  },

  async testConnection(p: ConnectionProfile & { password: string }): Promise<ConnectionTestResult> {
    if (USE_MOCK) return { ok: true, message: "Mock connection succeeded." };
    try {
      await gateway<Record<string, unknown>>("database.connect", connectionPayload(p));
      return { ok: true, message: "Connection succeeded." };
    } catch (error) {
      return {
        ok: false,
        code: "unknown",
        message: error instanceof Error ? error.message : "Connection failed.",
      };
    }
  },

  async connect(p: ConnectionProfile & { password: string }): Promise<ConnectionTestResult> {
    return this.testConnection(p);
  },

  async disconnect() {
    return gateway<Record<string, unknown>>("database.disconnect");
  },

  async askQuery(question: string): Promise<QueryResponse> {
    if (USE_MOCK) throw new GatewayError("mock_disabled", "Mock API is disabled by default.");
    const envelope = await gatewayEnvelope<Record<string, unknown>>("query.ask", { question });
    return envelope.ok && envelope.data
      ? mapQuerySuccess(question, envelope.data)
      : mapQueryError(question, envelope);
  },

  clarify(question: string): Promise<QueryResponse> {
    return this.askQuery(question);
  },

  async listTables(): Promise<SchemaTable[]> {
    await gateway<Record<string, unknown>>("knowledge.status");
    return [];
  },

  async knowledgeBaseStatus(): Promise<KnowledgeBaseStatus> {
    const data = await gateway<Record<string, unknown>>("knowledge.status");
    return mapKnowledgeStatus(data);
  },

  async rebuildKnowledgeBase(): Promise<KnowledgeBaseStatus> {
    const data = await gateway<Record<string, unknown>>("knowledge.rebuild", {
      use_ai_enrichment: false,
    });
    const knowledge =
      data.knowledge && typeof data.knowledge === "object"
        ? (data.knowledge as Record<string, unknown>)
        : data;
    return mapKnowledgeStatus(knowledge);
  },

  async listHistory(): Promise<HistoryEntry[]> {
    const data = await gateway<{ entries?: Record<string, unknown>[] }>("history.list");
    return (data.entries ?? []).map(mapHistoryEntry);
  },

  async clearHistory() {
    await gateway<Record<string, unknown>>("history.clear");
  },
};
