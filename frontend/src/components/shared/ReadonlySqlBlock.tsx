export function ReadonlySqlBlock({ sql }: { sql: string }) {
  return (
    <pre className="overflow-x-auto rounded-2xl border border-slate-700 bg-slate-950 p-4 text-sm text-slate-100">
      <code>{sql}</code>
    </pre>
  );
}
