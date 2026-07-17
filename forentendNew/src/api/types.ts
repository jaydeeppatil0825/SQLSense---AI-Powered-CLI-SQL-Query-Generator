export type QueryRoute = "deterministic_sql_required" | "cannot_plan_safely" | "blocked_unsafe";

export type QueryColumn = {
  key: string;
  label: string;
  dataType?: string;
  numeric?: boolean;
};

export type JoinEdge = {
  fromTable: string;
  fromColumn: string;
  toTable: string;
  toColumn: string;
  relationshipType: string;
};

export type AmbiguityChoice = {
  id: string;
  label: string;
  description?: string;
};

export type PlanDetail = {
  baseTable?: string;
  columns?: string[];
  metrics?: string[];
  dimensions?: string[];
  filters?: { column: string; op: string; value: string }[];
  dateInterval?: string;
  sort?: { column: string; direction: "asc" | "desc" }[];
  limit?: number;
  confidence?: number;
  detectedShape?: string;
};

export type EvidenceDetail = {
  schemaMatches?: { table: string; column?: string; score: number; source: string }[];
  glossaryMatches?: { term: string; score: number; source: string }[];
  vectorMatches?: { text: string; score: number }[];
};

export type ValidationDetail = {
  valid: boolean;
  message: string;
  checks?: { name: string; passed: boolean; detail?: string }[];
};

export type QueryResponse = {
  route: QueryRoute;
  reason: string;
  question: string;
  queryShape?: string;
  sql?: string;
  columns?: QueryColumn[];
  rows?: Record<string, unknown>[];
  rowCount?: number;
  executionTimeMs?: number;
  selectedJoinPath?: JoinEdge[];
  planner?: PlanDetail;
  evidence?: EvidenceDetail;
  validation?: ValidationDetail;
  ambiguityChoices?: AmbiguityChoice[];
  stages?: { name: string; status: "done" | "pending" | "failed"; detail?: string }[];
};

export type ConnectionTestResult = {
  ok: boolean;
  code?: "invalid_credentials" | "server_unreachable" | "database_not_found" | "unknown";
  message: string;
};

export type KnowledgeBaseStage = {
  name: string;
  status: "done" | "running" | "pending" | "failed";
  detail?: string;
};

export type KnowledgeBaseStatus = {
  database: string;
  schemaHash: string;
  lastBuildAt: number | null;
  tableCount: number;
  columnCount: number;
  relationshipCount: number;
  glossaryTermCount: number;
  vectorIndex: "ready" | "building" | "missing";
  semanticEnrichment: "disabled" | "local_ollama" | "external";
  stale: boolean;
  stages: KnowledgeBaseStage[];
  buildLog: { at: number; message: string; level: "info" | "warn" | "error" }[];
};

export type SchemaColumn = {
  name: string;
  dataType: string;
  nullable: boolean;
  primaryKey?: boolean;
  foreignKey?: { table: string; column: string };
  unique?: boolean;
  isDate?: boolean;
  isNumeric?: boolean;
  plannerRole?: "metric" | "dimension" | "date" | "identifier" | "status" | "text";
  samples?: string[];
};

export type SchemaTable = {
  name: string;
  type: "table" | "view";
  description?: string;
  columnCount: number;
  columns: SchemaColumn[];
};

export type GraphNode = { id: string; label: string; group?: string };
export type GraphEdge = {
  id: string;
  from: string;
  to: string;
  fromColumn: string;
  toColumn: string;
  kind: "fk" | "inferred" | "unsafe";
  cardinality: "one-to-one" | "one-to-many" | "many-to-many";
  confidence: number;
  source: string;
  safeForPlanner: boolean;
};

export type RelationshipGraph = {
  nodes: GraphNode[];
  edges: GraphEdge[];
};

export type GlossaryEntry = {
  id: string;
  term: string;
  description: string;
  candidateTables: string[];
  candidateColumns: string[];
  synonyms: string[];
  source: string;
  confidence: number;
  updatedAt: number;
};
