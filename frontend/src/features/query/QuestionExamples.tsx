const examples = [
  "Show paid payments",
  "Top customers by total amount",
  "Count orders by customer city",
  "Show active customer records",
];

export function QuestionExamples({ onPick }: { onPick: (question: string) => void }) {
  return (
    <div className="grid gap-2 sm:grid-cols-2">
      {examples.map((example) => (
        <button
          key={example}
          type="button"
          onClick={() => onPick(example)}
          className="rounded-2xl border border-white/10 bg-white/5 p-3 text-left text-sm text-slate-300 transition hover:border-signal-400/40 hover:text-white"
        >
          {example}
        </button>
      ))}
    </div>
  );
}
