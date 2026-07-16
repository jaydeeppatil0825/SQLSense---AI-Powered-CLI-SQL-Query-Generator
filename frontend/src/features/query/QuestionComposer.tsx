import type { KeyboardEvent } from "react";
import { Button } from "../../components/ui/Button";

export function QuestionComposer({
  question,
  onQuestionChange,
  onSubmit,
  onClear,
  disabled,
  error,
  variant = "panel",
}: {
  question: string;
  onQuestionChange: (value: string) => void;
  onSubmit: () => void;
  onClear: () => void;
  disabled: boolean;
  error?: string | null;
  variant?: "panel" | "hero";
}) {
  const submitFromKeyboard = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
      event.preventDefault();
      onSubmit();
    }
  };
  const errorId = "business-question-error";

  if (variant === "hero") {
    return (
      <div className="grid gap-3">
        <div className="query-input-shell flex items-center rounded-[1.75rem] border border-slate-200/80 bg-white p-2 pl-5 dark:border-white/10 dark:bg-slate-950/90 sm:rounded-full sm:pl-7">
          <label className="sr-only" htmlFor="business-question">
            Business question
          </label>
          <textarea
            id="business-question"
            value={question}
            onChange={(event) => onQuestionChange(event.target.value)}
            onKeyDown={submitFromKeyboard}
            rows={1}
            placeholder="Ask anything about your business data..."
            aria-invalid={Boolean(error)}
            aria-describedby={error ? errorId : undefined}
            className="max-h-28 min-h-12 flex-1 resize-none bg-transparent py-3 text-base font-medium leading-6 text-slate-900 outline-none placeholder:font-normal placeholder:text-slate-400 dark:text-white sm:text-lg"
          />
          <button
            type="button"
            aria-label="Ask your data"
            disabled={disabled}
            onClick={onSubmit}
            className="query-submit-button ml-3 grid h-14 w-14 shrink-0 place-items-center rounded-full bg-signal-500 text-xs font-black uppercase tracking-wide text-ink-950 shadow-[0_12px_30px_rgba(6,182,212,0.32)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-signal-500 disabled:cursor-not-allowed disabled:opacity-60 sm:h-16 sm:w-16"
          >
            {disabled ? "Wait" : "Ask"}
          </button>
        </div>
        {error ? (
          <p id={errorId} className="text-center text-sm font-semibold text-rose-600 dark:text-rose-300">
            {error}
          </p>
        ) : null}
        {question ? (
          <button
            type="button"
            onClick={onClear}
            disabled={disabled}
            className="mx-auto rounded-lg px-3 py-1 text-sm font-semibold text-slate-500 transition hover:bg-slate-900/5 hover:text-slate-900 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-signal-500 dark:text-slate-400 dark:hover:bg-white/5 dark:hover:text-white"
          >
            Clear question
          </button>
        ) : null}
      </div>
    );
  }

  return (
    <div className="grid gap-3">
      <label className="grid gap-2 text-sm font-semibold text-slate-200">
        Business question
        <textarea
          value={question}
          onChange={(event) => onQuestionChange(event.target.value)}
          onKeyDown={submitFromKeyboard}
          rows={5}
          placeholder="What would you like to know about your business?"
          aria-invalid={Boolean(error)}
          aria-describedby={error ? errorId : undefined}
          className="resize-y rounded-2xl border border-slate-700 bg-slate-950 px-4 py-3 text-base text-white outline-none focus:border-signal-400"
        />
      </label>
      {error ? (
        <p id={errorId} className="text-sm text-rose-200">
          {error}
        </p>
      ) : null}
      <div className="flex flex-wrap gap-3">
        <Button disabled={disabled} onClick={onSubmit} variant="primary">
          {disabled ? "Asking" : "Ask your data"}
        </Button>
        <Button disabled={disabled && !question} onClick={onClear} type="button">
          Clear
        </Button>
      </div>
      <p className="text-xs text-slate-500">Tip: press Ctrl+Enter to submit.</p>
    </div>
  );
}
