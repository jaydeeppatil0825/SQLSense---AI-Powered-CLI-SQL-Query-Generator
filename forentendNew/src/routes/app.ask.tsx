import { createFileRoute } from "@tanstack/react-router";
import { AskWorkspace } from "@/features/ask/AskWorkspace";

export const Route = createFileRoute("/app/ask")({
  head: () => ({ meta: [{ title: "Ask SQLSense" }] }),
  component: AskWorkspace,
});
