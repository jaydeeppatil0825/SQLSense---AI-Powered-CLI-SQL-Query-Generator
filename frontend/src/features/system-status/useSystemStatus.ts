import { useQuery } from "@tanstack/react-query";
import { fetchDatabaseStatus, fetchHealth, fetchKnowledgeStatus, fetchSessionStatus } from "../../gateway/actions";

export function useSystemStatus() {
  const health = useQuery({ queryKey: ["gateway-health"], queryFn: fetchHealth });
  const session = useQuery({
    queryKey: ["session-status"],
    queryFn: fetchSessionStatus,
    enabled: health.isSuccess,
  });
  const database = useQuery({
    queryKey: ["database-status"],
    queryFn: fetchDatabaseStatus,
    enabled: health.isSuccess,
  });
  const knowledge = useQuery({
    queryKey: ["knowledge-status"],
    queryFn: fetchKnowledgeStatus,
    enabled: health.isSuccess,
  });

  return { health, session, database, knowledge };
}
