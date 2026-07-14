import type { KeyboardEvent } from "react";
import { Button } from "../../components/ui/Button";

export function QuestionComposer({
  question,
  onQuestionChange,
  onSubmit,
  onClear,
  disabled,
  error,
}: {
  question: string;
  onQuestionChange: (value: string) => void;
  onSubmit: () => void;
  onClear: () => void;
  disabled: boolean;
  error?: string | null;
}) {
  const submitFromKeyboard = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
      event.preventDefault();
      onSubmit();
    }
  };
  const errorId = "business-question-error";

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
