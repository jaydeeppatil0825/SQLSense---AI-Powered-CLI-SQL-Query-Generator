import type { GatewayEnvelope, HealthResponse, RegisteredGatewayAction } from "./contracts";
import { GatewayClientError } from "./errors";

const gatewayPath = "/api/v1/gateway";
const healthPath = "/health";

function apiUrl(path: string): string {
  const baseUrl = import.meta.env.VITE_API_BASE_URL ?? "";
  return `${baseUrl}${path}`;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function assertEnvelope<TData>(
  value: unknown,
  expectedAction: RegisteredGatewayAction,
): GatewayEnvelope<TData> {
  if (!isRecord(value) || typeof value.ok !== "boolean" || typeof value.action !== "string") {
    throw new GatewayClientError("malformed_response", "Gateway returned an invalid response");
  }
  if (value.action !== expectedAction) {
    throw new GatewayClientError("action_mismatch", "Gateway returned a mismatched action");
  }
  return value as GatewayEnvelope<TData>;
}

async function parseJson(response: Response): Promise<unknown> {
  try {
    return await response.json();
  } catch {
    throw new GatewayClientError("malformed_response", "Gateway returned invalid JSON");
  }
}

export async function getHealth(): Promise<HealthResponse> {
  let response: Response;
  try {
    response = await fetch(apiUrl(healthPath), { credentials: "include" });
  } catch {
    throw new GatewayClientError("network_unavailable", "Gateway is unavailable");
  }
  const body = await parseJson(response);
  if (!response.ok || !isRecord(body) || body.ok !== true || typeof body.service !== "string") {
    throw new GatewayClientError("health_check_failed", "Gateway health check failed");
  }
  return body as HealthResponse;
}

export async function gatewayEnvelopeRequest<TData>(
  action: RegisteredGatewayAction,
  payload: Record<string, unknown> = {},
): Promise<GatewayEnvelope<TData>> {
  let response: Response;
  try {
    response = await fetch(apiUrl(gatewayPath), {
      method: "POST",
      credentials: "include",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ action, payload }),
    });
  } catch {
    throw new GatewayClientError("network_unavailable", "Gateway is unavailable");
  }

  const envelope = assertEnvelope<TData>(await parseJson(response), action);
  if (!response.ok || !envelope.ok) {
    const error = envelope.error;
    throw new GatewayClientError(
      error?.code ?? "gateway_rejected",
      error?.message ?? "Gateway request was rejected",
      error?.details,
    );
  }
  if (envelope.data === null) {
    throw new GatewayClientError("empty_response", "Gateway returned no data");
  }
  return envelope;
}

export async function gatewayRequest<TData>(
  action: RegisteredGatewayAction,
  payload: Record<string, unknown> = {},
): Promise<TData> {
  return (await gatewayEnvelopeRequest<TData>(action, payload)).data as TData;
}
