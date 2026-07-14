export type RegisteredGatewayAction =
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

export type GatewayErrorPayload = {
  code: string;
  message: string;
  details?: unknown;
};

export type GatewayEnvelope<TData> = {
  ok: boolean;
  request_id: string;
  action: RegisteredGatewayAction | string;
  data: TData | null;
  error: GatewayErrorPayload | null;
};

export type HealthResponse = {
  ok: true;
  service: string;
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
  database: Record<string, unknown>;
};

export type DatabaseConnectPayload = {
  db_type: "mysql";
  host: string;
  port: number;
  database: string;
  username: string;
  password: string;
};

export type DatabaseConnectResult = {
  message: string;
  database: Record<string, unknown>;
  prepare?: unknown;
};

export type DatabaseDisconnectResult = {
  disconnected: boolean;
  message: string;
};

export type KnowledgeStatus = {
  database_ready: boolean;
  database: Record<string, unknown>;
  knowledge_base_loaded: boolean;
  knowledge_base_origin?: string;
  schema_hash?: string;
  kb_status: string;
  vector_status: string;
  vector?: {
    index_status?: string;
    document_count?: number;
    chroma_ready?: boolean;
  };
  last_build?: unknown;
  last_prepare?: unknown;
};

export type KnowledgeRebuildResult = {
  message: string;
  knowledge: KnowledgeStatus;
};

export type QueryRow = Record<string, unknown>;

export type QueryExecution = {
  executed: boolean;
  message: string;
  duration_ms?: number;
};

export type QueryAskPayload = {
  question: string;
};

export type QueryResult = {
  request_id?: string;
  route?: string;
  query_shape?: string;
  sql: string | null;
  columns: string[];
  rows: QueryRow[];
  row_count: number;
  execution?: QueryExecution;
};

export type HistoryStatus = "success" | "rejected";

export type HistoryEntry = {
  id: string;
  request_id: string;
  question: string;
  created_at: string;
  status: HistoryStatus;
  route?: string | null;
  query_shape?: string | null;
  sql?: string | null;
  row_count?: number | null;
  duration_ms?: number | null;
  error?: GatewayErrorPayload;
};

export type HistoryListResult = {
  entries: HistoryEntry[];
  count: number;
  limit: number;
};

export type HistoryClearResult = {
  cleared: number;
  count: number;
};
