import { useMutation, useQueryClient } from "@tanstack/react-query";
import { PageHeader } from "../../components/shared/PageHeader";
import { Button } from "../../components/ui/Button";
import { Card } from "../../components/ui/Card";
import { rebuildKnowledge } from "../../gateway/actions";
import { normalizeGatewayError } from "../../gateway/errors";
import { useSystemStatus } from "../system-status/useSystemStatus";

export function KnowledgeBasePage() {
  const queryClient = useQueryClient();
  const { knowledge } = useSystemStatus();
  const rebuild = useMutation({
    mutationFn: rebuildKnowledge,
    onSettled: () => queryClient.invalidateQueries({ queryKey: ["knowledge-status"] }),
  });
  const error = rebuild.error ? normalizeGatewayError(rebuild.error) : null;
  const ready = Boolean(knowledge.data?.knowledge_base_loaded);

  return (
    <>
      <PageHeader
        eyebrow="Data Knowledge"
        title="Prepare your data for questions"
        description="SQLSense prepares schema, glossary, and relationship evidence before users ask questions."
      />
      <Card>
        <h2 className="text-xl font-bold text-white">{ready ? "Your data is ready." : "Data setup required."}</h2>
        <p className="mt-3 text-sm leading-6 text-slate-300">
          {ready
            ? "Business questions can use the prepared data knowledge."
            : "Prepare data knowledge after connecting a database."}
        </p>
        <Button className="mt-6" disabled={rebuild.isPending} onClick={() => rebuild.mutate()} variant="primary">
          {rebuild.isPending ? "Preparing your data" : "Prepare data knowledge"}
        </Button>
        {error ? <p className="mt-4 text-sm text-rose-200">{error.message}</p> : null}
      </Card>
    </>
  );
}
