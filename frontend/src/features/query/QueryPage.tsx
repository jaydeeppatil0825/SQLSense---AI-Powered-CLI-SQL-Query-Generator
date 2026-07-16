import { Link } from "@tanstack/react-router";
import { useState } from "react";
import { EmptyState } from "../../components/shared/EmptyState";
import { PageHeader } from "../../components/shared/PageHeader";
import { Button } from "../../components/ui/Button";
import { paths } from "../../app/router/paths";
import { EmptyResult } from "../results/EmptyResult";
import { QueryDetails } from "../results/QueryDetails";
import { ResultExportMenu } from "../results/ResultExportMenu";
import { ResultErrorBoundary } from "../results/ResultErrorBoundary";
import { ResultSummary } from "../results/ResultSummary";
import { ResultVisualization } from "../visualization/components/ResultVisualization";
import { useSystemStatus } from "../system-status/useSystemStatus";
import { QuestionComposer } from "./QuestionComposer";
import { QuestionExamples } from "./QuestionExamples";
import { QueryLoadingState } from "./QueryLoadingState";
import { QueryRejection } from "./QueryRejection";
import { useAskQuestion } from "./useAskQuestion";
import { businessMessage } from "./queryMessages";
import { takeQuestionDraft } from "./composerDraft";

export function QueryPage() {
  const { health, database, knowledge } = useSystemStatus();
  const [question, setQuestion] = useState(takeQuestionDraft);
  const [formError, setFormError] = useState<string | null>(null);
  const ask = useAskQuestion();

  const connected = Boolean(database.data?.connected);
  const ready = Boolean(knowledge.data?.knowledge_base_loaded);

  const submit = () => {
    const trimmed = question.trim();
    if (!trimmed) {
      setFormError("Enter a business question first.");
      return;
    }
    setFormError(null);
    ask.mutate({ question: trimmed });
  };

  const clear = () => {
    setQuestion("");
    setFormError(null);
    ask.reset();
  };

  if (health.isError) {
    return (
      <>
        <PageHeader eyebrow="Ask Your Data" title="Service unavailable" description="SQLSense cannot reach the application service right now." />
        <EmptyState title="Service unavailable" message="Check that the SQLSense service is running, then refresh this page." />
      </>
    );
  }

  if (database.isError) {
    return (
      <>
        <PageHeader eyebrow="Ask Your Data" title="Database connection lost" description="SQLSense could not confirm the database connection." />
        <EmptyState title="Database connection lost" message="Reconnect your business data before asking questions." />
        <Link to={paths.database} className="mt-4 inline-flex">
          <Button variant="primary">Go to Data Connection</Button>
        </Link>
      </>
    );
  }

  if (!connected) {
    return (
      <>
        <PageHeader
          eyebrow="Ask Your Data"
          title="Connect your business data first"
          description="Connect a database before asking questions."
        />
        <EmptyState title="Database not connected" message="Complete data connection to start asking questions." />
        <Link to={paths.database} className="mt-4 inline-flex">
          <Button variant="primary">Go to Data Connection</Button>
        </Link>
      </>
    );
  }

  if (!ready) {
    return (
      <>
        <PageHeader
          eyebrow="Ask Your Data"
          title="Your data is still being prepared"
          description="Prepare data knowledge before asking business questions."
        />
        <EmptyState title="Data setup required" message="Prepare your data knowledge first." />
        <Link to={paths.knowledgeBase} className="mt-4 inline-flex">
          <Button variant="primary">Go to Data Knowledge</Button>
        </Link>
      </>
    );
  }

  const rejection = ask.error ? businessMessage(ask.error) : null;
  const showWelcome = !ask.data && !ask.isPending && !rejection;

  if (showWelcome) {
    return (
      <section className="query-canvas query-canvas-welcome mx-auto flex min-h-[calc(100vh-11rem)] max-w-7xl items-center overflow-hidden rounded-[2rem] px-5 py-12 sm:px-10 lg:px-16">
        <div className="relative z-10 mx-auto flex w-full max-w-5xl flex-col items-center text-center">
          <div className="query-enter query-hero-stage" aria-hidden="true">
            <div className="query-hero-mark">
              <span>SQL</span>
            </div>
          </div>

          <div className="query-enter query-enter-delay-1 mt-9">
            <p className="text-xs font-bold uppercase tracking-[0.3em] text-signal-600 dark:text-signal-400">Business answers, safely delivered</p>
            <h1 className="mt-4 text-5xl font-black leading-tight tracking-normal text-slate-950 dark:text-white md:text-7xl">
              Ask Your <span className="text-signal-600 dark:text-signal-400">Data</span>
            </h1>
            <p className="mx-auto mt-6 max-w-2xl text-base leading-7 text-slate-600 dark:text-slate-300 sm:text-lg sm:leading-8">
              Turn everyday business questions into clear, validated, read-only results from the data you already trust.
            </p>
          </div>

          <div className="query-enter query-enter-delay-2 mt-10 w-full">
            <QuestionExamples onPick={setQuestion} />
          </div>

          <div className="query-enter query-enter-delay-3 mt-10 w-full">
            <QuestionComposer
              question={question}
              onQuestionChange={setQuestion}
              onSubmit={submit}
              onClear={clear}
              disabled={ask.isPending}
              error={formError}
              variant="hero"
            />
            <p className="mt-4 text-xs font-medium text-slate-500 dark:text-slate-400">
              Validated read-only access. Press Ctrl+Enter to submit.
            </p>
          </div>
        </div>
      </section>
    );
  }

  return (
    <section className="query-canvas mx-auto min-h-[calc(100vh-11rem)] max-w-7xl rounded-[2rem] p-5 sm:p-8 lg:p-10">
      <div className="query-enter mx-auto max-w-5xl text-center">
        <p className="text-xs font-bold uppercase tracking-[0.3em] text-signal-600 dark:text-signal-400">Ask Your Data</p>
        <h1 className="mt-3 text-3xl font-black tracking-normal text-slate-950 dark:text-white sm:text-4xl">Ask another question</h1>
        <p className="mx-auto mt-3 max-w-2xl text-sm leading-6 text-slate-600 dark:text-slate-300 sm:text-base">
          Your question stays editable while SQLSense presents the validated answer below.
        </p>
      </div>

      <div className="mt-8 grid gap-7">
        <div className="query-enter query-enter-delay-1 mx-auto w-full max-w-5xl">
          <QuestionComposer
            question={question}
            onQuestionChange={setQuestion}
            onSubmit={submit}
            onClear={clear}
            disabled={ask.isPending}
            error={formError}
            variant="hero"
          />
        </div>

        <ResultErrorBoundary>
          <div aria-live="polite" className="grid gap-5">
            {ask.isPending ? <QueryLoadingState /> : null}
            {rejection ? (
              <div className="query-result-enter">
                <QueryRejection
                  code={rejection.code}
                  message={rejection.message}
                  details={rejection.details}
                  onClarify={(text) => setQuestion(text)}
                />
              </div>
            ) : null}
            {ask.data ? (
              <section className="query-result-panel query-result-enter rounded-[1.75rem] border border-white/10 bg-slate-950/90 p-5 shadow-[0_28px_90px_rgba(15,23,42,0.22)] sm:p-7 lg:p-8">
                <div className="mb-5 flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
                  <div>
                    <p className="text-xs font-bold uppercase tracking-[0.24em] text-signal-400">Validated answer</p>
                    <h2 className="mt-2 text-2xl font-black tracking-normal text-white sm:text-3xl">Results</h2>
                  </div>
                  <div className="flex flex-wrap gap-3">
                    <ResultSummary result={ask.data} />
                    <ResultExportMenu result={ask.data} />
                  </div>
                </div>
                {ask.data.row_count === 0 ? (
                  <EmptyResult />
                ) : (
                  <ResultVisualization columns={ask.data.columns} rows={ask.data.rows} />
                )}
                {ask.data.sql ? (
                  <div className="mt-5">
                    <QueryDetails result={ask.data} />
                  </div>
                ) : null}
              </section>
            ) : null}
          </div>
        </ResultErrorBoundary>
      </div>
    </section>
  );
}
