import type { GatewayErrorPayload } from "./contracts";

export class GatewayClientError extends Error {
  readonly code: string;
  readonly details?: unknown;

  constructor(code: string, message: string, details?: unknown) {
    super(message);
    this.name = "GatewayClientError";
    this.code = code;
    this.details = details;
  }
}

export function normalizeGatewayError(error: unknown): GatewayErrorPayload {
  if (error instanceof GatewayClientError) {
    return { code: error.code, message: error.message, details: error.details };
  }
  if (error instanceof Error) {
    return { code: "client_error", message: error.message };
  }
  return { code: "client_error", message: "Gateway request failed" };
}
