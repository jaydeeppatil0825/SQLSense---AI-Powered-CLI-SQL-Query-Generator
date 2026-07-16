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
    <div className="grid gap-3 sm:grid-cols-2 sm:gap-4">
      {examples.map((example, index) => (
        <button
          key={example}
          type="button"
          onClick={() => onPick(example)}
          className="query-example-card group flex min-h-20 items-center gap-4 rounded-2xl border border-slate-200/80 bg-white/80 px-5 py-4 text-left text-sm font-semibold leading-6 text-slate-700 shadow-[0_10px_35px_rgba(15,23,42,0.07)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-signal-500 dark:border-white/10 dark:bg-white/[0.045] dark:text-slate-200 dark:shadow-none"
        >
          <span className="grid h-8 w-8 shrink-0 place-items-center rounded-xl border border-signal-500/20 bg-signal-500/10 text-xs font-black text-signal-700 transition-colors group-hover:bg-signal-500 group-hover:text-ink-950 dark:text-signal-300">
            {String(index + 1).padStart(2, "0")}
          </span>
          <span>{example}</span>
        </button>
      ))}
    </div>
  );
}
