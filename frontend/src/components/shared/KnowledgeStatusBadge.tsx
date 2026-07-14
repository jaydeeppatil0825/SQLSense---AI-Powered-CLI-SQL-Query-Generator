import { Badge } from "../ui/Badge";

export function KnowledgeStatusBadge({ ready }: { ready?: boolean }) {
  if (ready) {
    return <Badge tone="good">Data ready</Badge>;
  }
  return <Badge tone="neutral">Data setup required</Badge>;
}
