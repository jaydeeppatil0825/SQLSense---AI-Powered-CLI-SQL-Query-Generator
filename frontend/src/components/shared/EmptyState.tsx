import { Card } from "../ui/Card";

export function EmptyState({ title, message }: { title: string; message: string }) {
  return (
    <Card>
      <div className="py-10 text-center">
        <h2 className="text-xl font-bold text-white">{title}</h2>
        <p className="mx-auto mt-3 max-w-lg text-sm leading-6 text-slate-300">{message}</p>
      </div>
    </Card>
  );
}
