import { Link } from "@tanstack/react-router";
import { useState } from "react";
import { EmptyState } from "../../components/shared/EmptyState";
import { PageHeader } from "../../components/shared/PageHeader";
import { Button } from "../../components/ui/Button";
import { Card } from "../../components/ui/Card";
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
      <div className="mx-auto flex min-h-[calc(100vh-12rem)] max-w-6xl flex-col items-center justify-center px-2 py-8 text-center">
        <div className="grid h-28 w-28 place-items-center rounded-[2rem] border border-signal-300/40 bg-signal-500/10 text-6xl text-signal-500 shadow-[0_24px_70px_rgba(245,158,11,0.22)]">
          <svg aria-hidden="true" viewBox="0 0 24 24" className="h-14 w-14" fill="none" stroke="currentColor" strokeWidth="1.8">
            <path d="M12 3l1.9 6.1L20 11l-6.1 1.9L12 20l-1.9-7.1L4 11l6.1-1.9L12 3Z" />
            <path d="M19 3v4M17 5h4" />
          </svg>
        </div>
        <h1 className="mt-10 text-5xl font-black tracking-tight text-slate-950 dark:text-white md:text-7xl">
          Ask Your <span className="text-signal-500">Data</span>
        </h1>
        <p className="mt-5 max-w-3xl text-lg leading-8 text-slate-600 dark:text-slate-300">
          Ask business questions in plain English. SQLSense validates the request and returns read-only results from your connected data.
        </p>

        <div className="mt-12 w-full max-w-5xl">
          <QuestionExamples onPick={setQuestion} />
        </div>

        <div className="mt-12 w-full max-w-5xl">
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
      </div>
    );
  }

  return (
    <>
      <PageHeader
        eyebrow="Ask Your Data"
        title="Ask another question"
        description="Your question stays editable. Results appear below after SQLSense validates the request."
      />
      <div className="grid gap-6">
        <Card className="mx-auto w-full max-w-5xl">
          <QuestionComposer
            question={question}
            onQuestionChange={setQuestion}
            onSubmit={submit}
            onClear={clear}
            disabled={ask.isPending}
            error={formError}
            variant="hero"
          />
        </Card>

        <ResultErrorBoundary>
          <div className="grid gap-4">
            {ask.isPending ? <QueryLoadingState /> : null}
            {rejection ? (
              <QueryRejection
                code={rejection.code}
                message={rejection.message}
                details={rejection.details}
                onClarify={(text) => setQuestion(text)}
              />
            ) : null}
            {ask.data ? (
              <Card>
                <div className="mb-5 flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
                  <h2 className="text-2xl font-black text-white">Results</h2>
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
              </Card>
            ) : null}
          </div>
        </ResultErrorBoundary>
      </div>
    </>
  );
}
