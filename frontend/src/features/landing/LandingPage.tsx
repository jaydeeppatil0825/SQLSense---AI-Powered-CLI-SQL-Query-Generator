import { Link } from "@tanstack/react-router";
import { ApplicationLogo } from "../../components/shared/ApplicationLogo";
import { Badge } from "../../components/ui/Badge";
import { Button } from "../../components/ui/Button";
import { Card } from "../../components/ui/Card";
import { paths } from "../../app/router/paths";

const benefits = [
  "Ask everyday business questions",
  "Receive validated read-only answers",
  "Keep database sessions server-side",
];

const steps = [
  ["Connect", "Securely connect your company database."],
  ["Prepare", "SQLSense maps schema, glossary, and safe relationships."],
  ["Ask", "Business teams ask questions without writing SQL."],
];

const capabilities = [
  "Single-table questions",
  "Filtered business lists",
  "Aggregates and rankings",
  "Validated relationship-based joins",
  "Clear tabular results",
  "Safe failure for ambiguous requests",
];

export function LandingPage() {
  return (
    <main className="min-h-screen text-white">
      <header className="mx-auto flex max-w-7xl items-center justify-between px-6 py-6">
        <ApplicationLogo />
        <nav aria-label="Landing navigation" className="hidden items-center gap-6 text-sm text-slate-300 md:flex">
          <a href="#how-it-works" className="hover:text-white">
            How it works
          </a>
          <a href="#security" className="hover:text-white">
            Security
          </a>
          <Link to={paths.app} className="font-semibold text-signal-400 hover:text-signal-300">
            Open SQLSense
          </Link>
        </nav>
      </header>

      <section className="mx-auto grid max-w-7xl gap-10 px-6 py-16 lg:grid-cols-[1.1fr_0.9fr] lg:items-center">
        <div>
          <Badge tone="good">Validated business answers from company data</Badge>
          <h1 className="mt-6 max-w-4xl text-5xl font-black tracking-tight md:text-7xl">
            Ask questions. Understand your business data.
          </h1>
          <p className="mt-6 max-w-2xl text-lg leading-8 text-slate-300">
            Connect your company database and retrieve reliable business information using everyday language
            without manually writing SQL.
          </p>
          <div className="mt-8 flex flex-col gap-3 sm:flex-row">
            <Link to={paths.app}>
              <Button variant="primary" className="w-full sm:w-auto">
                Open SQLSense
              </Button>
            </Link>
            <a href="#how-it-works">
              <Button variant="secondary" className="w-full sm:w-auto">
                See How It Works
              </Button>
            </a>
          </div>
        </div>
        <Card className="bg-slate-950/70">
          <div className="rounded-2xl border border-signal-400/20 bg-signal-400/10 p-4 text-sm text-signal-100">
            Data ready
          </div>
          <div className="mt-5 space-y-3">
            {benefits.map((item) => (
              <div key={item} className="rounded-2xl border border-white/10 bg-white/5 p-4 text-slate-200">
                {item}
              </div>
            ))}
          </div>
        </Card>
      </section>

      <section className="mx-auto grid max-w-7xl gap-4 px-6 py-10 md:grid-cols-3">
        {benefits.map((benefit) => (
          <Card key={benefit}>
            <h2 className="text-xl font-bold">{benefit}</h2>
            <p className="mt-3 text-sm leading-6 text-slate-300">
              Designed for teams that need dependable answers from operational data.
            </p>
          </Card>
        ))}
      </section>

      <section id="how-it-works" className="mx-auto max-w-7xl px-6 py-14">
        <h2 className="text-3xl font-black">How it works</h2>
        <div className="mt-6 grid gap-4 md:grid-cols-3">
          {steps.map(([title, body]) => (
            <Card key={title}>
              <h3 className="text-lg font-bold">{title}</h3>
              <p className="mt-3 text-sm leading-6 text-slate-300">{body}</p>
            </Card>
          ))}
        </div>
      </section>

      <section className="mx-auto max-w-7xl px-6 py-14">
        <h2 className="text-3xl font-black">Supported capabilities</h2>
        <div className="mt-6 grid gap-3 md:grid-cols-3">
          {capabilities.map((item) => (
            <div key={item} className="rounded-2xl border border-white/10 bg-white/5 p-4 text-sm text-slate-200">
              {item}
            </div>
          ))}
        </div>
      </section>

      <section id="security" className="mx-auto max-w-7xl px-6 py-14">
        <Card>
          <h2 className="text-3xl font-black">Security and trust</h2>
          <p className="mt-4 max-w-3xl text-sm leading-7 text-slate-300">
            SQLSense uses server-side sessions, read-only validated SQL, and safe rejection for unsupported or
            ambiguous requests. Database passwords are not stored in browser storage.
          </p>
        </Card>
      </section>

      <section className="mx-auto max-w-7xl px-6 py-16">
        <div className="rounded-[2rem] border border-signal-400/20 bg-signal-500/10 p-8 text-center">
          <h2 className="text-3xl font-black">Ready to explore your data?</h2>
          <p className="mx-auto mt-3 max-w-2xl text-sm leading-6 text-slate-300">
            Open SQLSense and connect your database to begin guided setup.
          </p>
          <Link to={paths.app} className="mt-6 inline-flex">
            <Button variant="primary">Open SQLSense</Button>
          </Link>
        </div>
      </section>

      <footer className="border-t border-white/10 px-6 py-8 text-center text-sm text-slate-500">
        SQLSense helps teams ask safer questions of business data.
      </footer>
    </main>
  );
}
