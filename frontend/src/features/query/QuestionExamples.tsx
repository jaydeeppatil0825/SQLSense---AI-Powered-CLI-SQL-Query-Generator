const examples = [
  "Show paid payments",
  "Top 5 customers by total amount",
  "Count orders by customer city",
  "Show active customer records",
  "Total sales by product category",
  "Show delivered orders from last 30 days",
];

export function QuestionExamples({ onPick }: { onPick: (question: string) => void }) {
  return (
    <div className="grid gap-4 sm:grid-cols-2">
      {examples.map((example) => (
        <button
          key={example}
          type="button"
          onClick={() => onPick(example)}
          className="rounded-2xl border border-slate-200 bg-white/85 p-5 text-left text-sm font-semibold text-slate-700 shadow-[0_10px_30px_rgba(15,23,42,0.10)] transition hover:-translate-y-0.5 hover:border-signal-400/60 hover:text-slate-950 dark:border-white/10 dark:bg-white/5 dark:text-slate-300 dark:shadow-none dark:hover:text-white"
        >
          {example}
        </button>
      ))}
    </div>
  );
}
