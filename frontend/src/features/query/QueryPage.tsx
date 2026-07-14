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

  return (
    <>
      <PageHeader
        eyebrow="Ask Your Data"
        title="What would you like to know about your business?"
        description="Ask in everyday language. SQLSense validates the request and returns read-only results."
      />
      <div className="grid gap-6 xl:grid-cols-[0.9fr_1.1fr]">
        <Card>
          <QuestionComposer
            question={question}
            onQuestionChange={setQuestion}
            onSubmit={submit}
            onClear={clear}
            disabled={ask.isPending}
            error={formError}
          />
          <div className="mt-6">
            <h2 className="mb-3 text-sm font-bold text-slate-200">Try an example</h2>
            <QuestionExamples onPick={setQuestion} />
          </div>
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
