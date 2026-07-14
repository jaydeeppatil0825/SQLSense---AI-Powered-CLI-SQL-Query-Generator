import { GatewayClientError } from "../../gateway/errors";

const messages: Record<string, string> = {
  AMBIGUOUS_TABLE: "More detail is needed to choose the right data.",
  AMBIGUOUS_COLUMN: "More detail is needed to choose the right field.",
  AMBIGUOUS_METRIC: "More detail is needed to choose the right metric.",
  AMBIGUOUS_JOIN_PATH: "More detail is needed to connect the right data.",
  UNSUPPORTED_QUERY_SHAPE: "This question is not supported yet.",
  UNSAFE_SQL: "Query stopped for safety. SQLSense only runs read-only questions.",
  DATABASE_NOT_CONNECTED: "Connect your business data first.",
  KB_NOT_READY: "Your data is still being prepared.",
  STALE_QUERY_CONTEXT: "The saved query context is no longer valid.",
  VALIDATION_FAILED: "Query stopped because validation failed.",
  EXECUTION_REVALIDATION_FAILED: "Query stopped because the result could not be revalidated.",
  blocked_unsafe: "Query stopped for safety. SQLSense only runs read-only questions.",
  cannot_plan_safely: "More detail is needed before SQLSense can answer safely.",
  execution_failed: "The query could not be executed safely.",
  network_unavailable: "Service unavailable. Check that SQLSense is running.",
};

export function businessMessage(error: unknown): { code: string; message: string; details?: unknown } {
  if (error instanceof GatewayClientError) {
    return {
      code: error.code,
      message: messages[error.code] ?? error.message,
      details: error.details,
    };
  }
  return { code: "client_error", message: "Something went wrong while asking your data." };
}

export function clarificationOptions(details: unknown): string[] {
  if (!details || typeof details !== "object") {
    return [];
  }
  const record = details as Record<string, unknown>;
  const direct = record.options;
  if (Array.isArray(direct)) {
    return direct.filter((item): item is string => typeof item === "string");
  }
  const query = record.query;
  if (query && typeof query === "object") {
    const nested = (query as Record<string, unknown>).options;
    if (Array.isArray(nested)) {
      return nested.filter((item): item is string => typeof item === "string");
    }
  }
  return [];
}
