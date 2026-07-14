import { Card } from "../ui/Card";

export function GatewayErrorPanel({ title, message }: { title: string; message: string }) {
  return (
    <Card className="border-rose-400/30 bg-rose-950/20">
      <h2 className="text-lg font-bold text-rose-100">{title}</h2>
      <p className="mt-2 text-sm leading-6 text-rose-100/80">{message}</p>
    </Card>
  );
}
