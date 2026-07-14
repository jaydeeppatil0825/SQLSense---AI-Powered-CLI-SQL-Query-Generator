import { gatewayEnvelopeRequest, gatewayRequest, getHealth } from "./client";
import type {
  DatabaseConnectPayload,
  DatabaseConnectResult,
  DatabaseDisconnectResult,
  DatabaseStatus,
  HealthResponse,
  HistoryClearResult,
  HistoryListResult,
  KnowledgeRebuildResult,
  KnowledgeStatus,
  QueryAskPayload,
  QueryResult,
  SessionStatus,
} from "./contracts";

export { getHealth };

export function fetchHealth(): Promise<HealthResponse> {
  return getHealth();
}

export function fetchSessionStatus(): Promise<SessionStatus> {
  return gatewayRequest<SessionStatus>("session.status");
}

export function resetSession(): Promise<SessionStatus> {
  return gatewayRequest<SessionStatus>("session.reset");
}

export function fetchDatabaseStatus(): Promise<DatabaseStatus> {
  return gatewayRequest<DatabaseStatus>("database.status");
}

export function connectDatabase(payload: DatabaseConnectPayload): Promise<DatabaseConnectResult> {
  return gatewayRequest<DatabaseConnectResult>("database.connect", payload);
}

export function disconnectDatabase(): Promise<DatabaseDisconnectResult> {
  return gatewayRequest<DatabaseDisconnectResult>("database.disconnect");
}

export function fetchKnowledgeStatus(): Promise<KnowledgeStatus> {
  return gatewayRequest<KnowledgeStatus>("knowledge.status");
}

export function rebuildKnowledge(): Promise<KnowledgeRebuildResult> {
  return gatewayRequest<KnowledgeRebuildResult>("knowledge.rebuild");
}

export async function askQuestion(payload: QueryAskPayload): Promise<QueryResult> {
  const envelope = await gatewayEnvelopeRequest<QueryResult>("query.ask", payload);
  return { ...envelope.data, request_id: envelope.request_id } as QueryResult;
}

export function listHistory(): Promise<HistoryListResult> {
  return gatewayRequest<HistoryListResult>("history.list");
}

export function clearHistory(): Promise<HistoryClearResult> {
  return gatewayRequest<HistoryClearResult>("history.clear");
}
