export function ClarificationOptions({
  options,
  onSelect,
}: {
  options: string[];
  onSelect: (option: string) => void;
}) {
  if (!options.length) {
    return null;
  }
  return (
    <div className="mt-4">
      <p className="text-sm font-semibold text-slate-200">Try adding more detail:</p>
      <div className="mt-2 flex flex-wrap gap-2">
        {options.map((option) => (
          <button
            key={option}
            type="button"
            onClick={() => onSelect(option)}
            className="rounded-full border border-slate-700 px-3 py-1 text-sm text-slate-300 hover:border-signal-400 hover:text-white"
          >
            {option}
          </button>
        ))}
      </div>
    </div>
  );
}
