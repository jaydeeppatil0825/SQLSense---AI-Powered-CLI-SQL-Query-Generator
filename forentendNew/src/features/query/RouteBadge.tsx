import { AlertTriangle, ShieldAlert, Check } from "lucide-react";
import type { QueryRoute } from "@/api/types";
import { cn } from "@/lib/utils";

export function RouteBadge({ route }: { route: QueryRoute }) {
  if (route === "deterministic_sql_required")
    return <Chip icon={Check} label="Executed" tone="positive" />;
  if (route === "cannot_plan_safely")
    return <Chip icon={AlertTriangle} label="Needs clarification" tone="warning" />;
  return <Chip icon={ShieldAlert} label="Blocked" tone="negative" />;
}

function Chip({
  icon: Icon,
  label,
  tone,
}: {
  icon: React.ComponentType<{ className?: string }>;
  label: string;
  tone: "positive" | "warning" | "negative";
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[11px] font-medium",
        tone === "positive" && "bg-positive-soft text-positive",
        tone === "warning" && "bg-warning-soft text-warning",
        tone === "negative" && "bg-negative-soft text-negative",
      )}
    >
      <Icon className="h-3 w-3" />
      {label}
    </span>
  );
}
