import { EmptyState } from "../../components/shared/EmptyState";
import { PageHeader } from "../../components/shared/PageHeader";
import { Card } from "../../components/ui/Card";

export function ResultsPage() {
  return (
    <>
      <PageHeader
        eyebrow="Home"
        title="Understand your business data"
        description="Connect company data, prepare it for questions, and give teams a safer way to find reliable answers."
      />
      <div className="grid gap-4 lg:grid-cols-3">
        <Card>
          <h2 className="text-lg font-bold text-white">Ask Your Data</h2>
          <p className="mt-2 text-sm leading-6 text-slate-300">
            Ask business questions in everyday language once setup is complete.
          </p>
        </Card>
        <Card>
          <h2 className="text-lg font-bold text-white">Reliable Results</h2>
          <p className="mt-2 text-sm leading-6 text-slate-300">
            Answers come from validated read-only database queries.
          </p>
        </Card>
        <Card>
          <h2 className="text-lg font-bold text-white">Secure Connection</h2>
          <p className="mt-2 text-sm leading-6 text-slate-300">
            Database sessions are held server-side and passwords are not stored in the browser.
          </p>
        </Card>
      </div>
      <div className="mt-4">
        <EmptyState title="Ready to explore your data" message="Connect a database and prepare data knowledge to begin." />
      </div>
    </>
  );
}
