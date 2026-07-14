import { Badge } from "../ui/Badge";

export function ConnectionBadge({ connected }: { connected?: boolean }) {
  if (connected) {
    return <Badge tone="good">Database connected</Badge>;
  }
  return <Badge tone="warn">Database not connected</Badge>;
}
