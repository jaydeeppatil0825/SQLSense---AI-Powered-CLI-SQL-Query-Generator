import { createFileRoute, Link } from "@tanstack/react-router";
import { useEffect, useRef, useState, type ReactNode } from "react";
import {
  ArrowRight,
  Check,
  ChevronDown,
  Database,
  Shield,
  GitBranch,
  Lock,
  Zap,
  Eye,
  Sparkles,
  Network,
  FileCheck2,
  AlertTriangle,
  Factory,
  LineChart,
  Wallet,
  Users,
  Sun,
  Moon,
  Github,
  Twitter,
  Linkedin,
  Play,
} from "lucide-react";
import { useAppStore } from "@/stores/app-store";

export const Route = createFileRoute("/")({
  head: () => ({
    meta: [
      { title: "SQLSense — Ask Your Database. Get Trusted Answers." },
      {
        name: "description",
        content:
          "SQLSense is a deterministic natural-language-to-SQL platform. Graph-authorized joins, validated read-only SQL, and fail-closed safety — no runtime AI SQL generation.",
      },
      { property: "og:title", content: "SQLSense — Ask Your Database. Get Trusted Answers." },
      {
        property: "og:description",
        content:
          "Deterministic NL→SQL for the enterprise. Read-only, validated, graph-authorized queries you can trust.",
      },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary_large_image" },
    ],
  }),
  component: Landing,
});

function Landing() {
  const connected = useAppStore((s) => s.connected);
  const setTheme = useAppStore((s) => s.setTheme);
  const theme = useAppStore((s) => s.theme);
  useEffect(() => {
    document.documentElement.classList.add("dark");
  }, []);

  const launchHref = connected ? "/app/ask" : "/connect";

  return (
    <div className="min-h-screen bg-[#05060d] text-slate-100 antialiased selection:bg-indigo-500/40 selection:text-white overflow-x-hidden">
      <BackgroundFX />
      <Nav launchHref={launchHref} theme={theme} setTheme={setTheme} />
      <main className="relative">
        <Hero launchHref={launchHref} />
        <LogoStrip />
        <ProblemSolution />
        <InteractiveDemo />
        <Features />
        <HowItWorks />
        <SecuritySection />
        <UseCases />
        <SchemaPreview />
        <Comparison />
        <FAQ />
        <FinalCTA launchHref={launchHref} />
      </main>
      <Footer />
    </div>
  );
}

function BackgroundFX() {
  return (
    <div aria-hidden className="pointer-events-none fixed inset-0 z-0 overflow-hidden">
      <div className="absolute -top-40 left-1/2 h-[700px] w-[1100px] -translate-x-1/2 rounded-full bg-[radial-gradient(closest-side,rgba(99,102,241,0.35),transparent_70%)] blur-2xl" />
      <div className="absolute top-[40%] -left-40 h-[500px] w-[500px] rounded-full bg-[radial-gradient(closest-side,rgba(34,211,238,0.25),transparent_70%)] blur-3xl" />
      <div className="absolute top-[70%] right-[-10%] h-[600px] w-[600px] rounded-full bg-[radial-gradient(closest-side,rgba(139,92,246,0.28),transparent_70%)] blur-3xl" />
      <div className="absolute inset-0 bg-[linear-gradient(to_bottom,rgba(5,6,13,0)_0%,rgba(5,6,13,0.6)_60%,#05060d_100%)]" />
      <svg className="absolute inset-0 h-full w-full opacity-[0.07]" xmlns="http://www.w3.org/2000/svg">
        <defs>
          <pattern id="grid" width="48" height="48" patternUnits="userSpaceOnUse">
            <path d="M 48 0 L 0 0 0 48" fill="none" stroke="white" strokeWidth="0.5" />
          </pattern>
        </defs>
        <rect width="100%" height="100%" fill="url(#grid)" />
      </svg>
    </div>
  );
}

function Nav({
  launchHref,
  theme,
  setTheme,
}: {
  launchHref: string;
  theme: string;
  setTheme: (t: "dark" | "light" | "system") => void;
}) {
  const [scrolled, setScrolled] = useState(false);
  const [open, setOpen] = useState(false);
  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 8);
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  const links = [
    { href: "#product", label: "Product" },
    { href: "#features", label: "Features" },
    { href: "#security", label: "Security" },
    { href: "#use-cases", label: "Use Cases" },
    { href: "#faq", label: "FAQ" },
  ];

  return (
    <header
      className={
        "fixed top-0 inset-x-0 z-50 transition-all duration-300 " +
        (scrolled ? "backdrop-blur-xl bg-[#05060d]/70 border-b border-white/10" : "bg-transparent")
      }
    >
      <div className="mx-auto max-w-7xl px-4 sm:px-6 lg:px-8">
        <div className="flex h-16 items-center justify-between">
          <a href="#top" className="flex items-center gap-2 group">
            <span className="relative grid h-8 w-8 place-items-center rounded-lg bg-gradient-to-br from-indigo-500 via-blue-500 to-cyan-400 shadow-lg shadow-indigo-500/30">
              <Database className="h-4 w-4 text-white" />
            </span>
            <span className="font-semibold tracking-tight text-white">SQLSense</span>
          </a>
          <nav className="hidden md:flex items-center gap-1">
            {links.map((l) => (
              <a
                key={l.href}
                href={l.href}
                className="rounded-md px-3 py-1.5 text-sm text-slate-300 hover:text-white hover:bg-white/5 transition-colors"
              >
                {l.label}
              </a>
            ))}
          </nav>
          <div className="flex items-center gap-2">
            <button
              onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
              className="hidden sm:grid h-9 w-9 place-items-center rounded-md border border-white/10 bg-white/5 text-slate-300 hover:text-white hover:bg-white/10 transition"
              aria-label="Toggle theme"
            >
              {theme === "dark" ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
            </button>
            <Link
              to={launchHref}
              className="hidden sm:inline-flex h-9 items-center rounded-md px-3 text-sm text-slate-200 hover:text-white hover:bg-white/5 transition"
            >
              Sign In
            </Link>
            <Link
              to={launchHref}
              className="group relative inline-flex h-9 items-center gap-1.5 rounded-md bg-gradient-to-r from-indigo-500 to-cyan-400 px-3.5 text-sm font-medium text-white shadow-lg shadow-indigo-500/20 hover:shadow-indigo-500/40 transition-all"
            >
              Get Started
              <ArrowRight className="h-4 w-4 transition-transform group-hover:translate-x-0.5" />
            </Link>
            <button
              className="md:hidden grid h-9 w-9 place-items-center rounded-md border border-white/10 bg-white/5"
              onClick={() => setOpen((v) => !v)}
              aria-label="Menu"
            >
              <ChevronDown className={"h-4 w-4 transition-transform " + (open ? "rotate-180" : "")} />
            </button>
          </div>
        </div>
        {open && (
          <div className="md:hidden pb-3 grid gap-1">
            {links.map((l) => (
              <a
                key={l.href}
                href={l.href}
                onClick={() => setOpen(false)}
                className="rounded-md px-3 py-2 text-sm text-slate-300 hover:text-white hover:bg-white/5"
              >
                {l.label}
              </a>
            ))}
          </div>
        )}
      </div>
    </header>
  );
}

function Hero({ launchHref }: { launchHref: string }) {
  return (
    <section id="top" className="relative pt-32 pb-20 sm:pt-40 sm:pb-28">
      <div className="mx-auto max-w-7xl px-4 sm:px-6 lg:px-8">
        <div className="mx-auto max-w-3xl text-center">
          <div className="inline-flex items-center gap-2 rounded-full border border-white/10 bg-white/5 px-3 py-1 text-xs text-slate-300 backdrop-blur">
            <Sparkles className="h-3.5 w-3.5 text-cyan-300" />
            Deterministic NL→SQL · Enterprise-ready
          </div>
          <h1 className="mt-6 text-4xl sm:text-6xl lg:text-7xl font-bold tracking-tight text-white">
            Ask Your Database.{" "}
            <span className="bg-gradient-to-r from-indigo-300 via-cyan-300 to-violet-300 bg-clip-text text-transparent">
              Get Trusted Answers.
            </span>
          </h1>
          <p className="mt-6 text-lg text-slate-300 leading-relaxed">
            SQLSense turns plain English into safe, validated, read-only SQL — using a graph-authorized query planner. No hallucinated joins. No runtime AI SQL. Just answers you can defend in an audit.
          </p>
          <div className="mt-8 flex flex-wrap items-center justify-center gap-3">
            <Link
              to={launchHref}
              className="group inline-flex h-11 items-center gap-2 rounded-lg bg-gradient-to-r from-indigo-500 to-cyan-400 px-5 text-sm font-semibold text-white shadow-xl shadow-indigo-500/25 hover:shadow-indigo-500/50 transition-all hover:-translate-y-0.5"
            >
              Launch App
              <ArrowRight className="h-4 w-4 transition-transform group-hover:translate-x-0.5" />
            </Link>
            <a
              href="#demo"
              className="inline-flex h-11 items-center gap-2 rounded-lg border border-white/15 bg-white/5 px-5 text-sm font-medium text-slate-200 hover:bg-white/10 backdrop-blur transition"
            >
              <Play className="h-4 w-4" /> Watch Demo
            </a>
          </div>
          <div className="mt-6 flex flex-wrap items-center justify-center gap-x-6 gap-y-2 text-xs text-slate-400">
            {["Read-only by design", "Graph-authorized joins", "Mandatory validation", "Fail-closed safety"].map((t) => (
              <span key={t} className="inline-flex items-center gap-1.5">
                <Check className="h-3.5 w-3.5 text-cyan-300" /> {t}
              </span>
            ))}
          </div>
        </div>
        <HeroVisual />
      </div>
    </section>
  );
}

function HeroVisual() {
  return (
    <div className="relative mt-16 mx-auto max-w-5xl">
      <div className="absolute -inset-6 bg-gradient-to-r from-indigo-500/20 via-cyan-400/20 to-violet-500/20 blur-3xl rounded-3xl" />
      <div className="relative rounded-2xl border border-white/10 bg-gradient-to-b from-white/[0.06] to-white/[0.02] p-2 shadow-2xl backdrop-blur-xl">
        <div className="rounded-xl border border-white/10 bg-[#0a0b16]/90 overflow-hidden">
          <div className="flex items-center gap-2 border-b border-white/5 px-4 py-2.5">
            <div className="flex gap-1.5">
              <span className="h-2.5 w-2.5 rounded-full bg-red-400/60" />
              <span className="h-2.5 w-2.5 rounded-full bg-yellow-400/60" />
              <span className="h-2.5 w-2.5 rounded-full bg-green-400/60" />
            </div>
            <div className="ml-3 text-xs text-slate-500">sqlsense — ask</div>
          </div>
          <div className="grid md:grid-cols-[1fr_1.2fr]">
            <div className="p-6 border-b md:border-b-0 md:border-r border-white/5">
              <div className="text-xs uppercase tracking-wider text-slate-500">Question</div>
              <div className="mt-2 text-slate-100 text-lg leading-relaxed">
                "Top 5 customers by revenue last quarter, with region."
              </div>
              <div className="mt-6 space-y-2">
                {["Understanding question", "Resolving schema graph", "Planning deterministic SQL", "Validating read-only", "Executing safely"].map((s, i) => (
                  <div
                    key={s}
                    className="flex items-center gap-2 text-xs text-slate-300 rounded-md border border-white/5 bg-white/[0.03] px-2.5 py-1.5 animate-fade-in"
                    style={{ animationDelay: `${i * 100}ms` }}
                  >
                    <span className="grid h-4 w-4 place-items-center rounded-full bg-cyan-400/20 text-cyan-300">
                      <Check className="h-2.5 w-2.5" />
                    </span>
                    {s}
                  </div>
                ))}
              </div>
            </div>
            <div className="p-6 bg-[#070812]">
              <div className="text-xs uppercase tracking-wider text-slate-500 mb-2 flex items-center justify-between">
                <span>Generated SQL · validated</span>
                <span className="rounded-full border border-emerald-400/30 bg-emerald-400/10 px-2 py-0.5 text-[10px] text-emerald-300">
                  READ-ONLY
                </span>
              </div>
              <pre className="text-[12px] leading-relaxed text-slate-200 font-mono overflow-x-auto">{`SELECT c.name, c.region,
       SUM(o.total) AS revenue
FROM   customers c
JOIN   orders o ON o.customer_id = c.id
WHERE  o.placed_at >= '2025-04-01'
  AND  o.placed_at <  '2025-07-01'
GROUP  BY c.id
ORDER  BY revenue DESC
LIMIT  5;`}</pre>
              <div className="mt-4 rounded-lg border border-white/10 overflow-hidden">
                <table className="w-full text-xs">
                  <thead className="bg-white/[0.04] text-slate-400">
                    <tr>
                      <th className="text-left px-3 py-2 font-medium">Customer</th>
                      <th className="text-left px-3 py-2 font-medium">Region</th>
                      <th className="text-right px-3 py-2 font-medium">Revenue</th>
                    </tr>
                  </thead>
                  <tbody className="text-slate-200">
                    {[
                      ["Northwind Corp", "EMEA", "$482,300"],
                      ["Acme Industrial", "NA", "$391,120"],
                      ["Kaizen Robotics", "APAC", "$318,450"],
                      ["Helio Systems", "NA", "$276,900"],
                      ["Meridian Labs", "EMEA", "$241,050"],
                    ].map((r, i) => (
                      <tr key={i} className="border-t border-white/5 animate-fade-in" style={{ animationDelay: `${400 + i * 80}ms` }}>
                        <td className="px-3 py-2">{r[0]}</td>
                        <td className="px-3 py-2 text-slate-400">{r[1]}</td>
                        <td className="px-3 py-2 text-right font-mono tabular-nums text-cyan-300">{r[2]}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function LogoStrip() {
  const logos = ["MYSQL", "POSTGRES", "SNOWFLAKE", "BIGQUERY", "REDSHIFT", "DATABRICKS"];
  return (
    <section className="relative py-10 border-y border-white/5 bg-white/[0.015]">
      <div className="mx-auto max-w-7xl px-4 sm:px-6 lg:px-8">
        <div className="text-center text-xs uppercase tracking-widest text-slate-500 mb-6">
          Speaks the dialects your teams already use
        </div>
        <div className="flex flex-wrap items-center justify-center gap-x-10 gap-y-4 opacity-70">
          {logos.map((l) => (
            <span key={l} className="text-slate-400 font-mono text-sm tracking-widest">{l}</span>
          ))}
        </div>
      </div>
    </section>
  );
}

function ProblemSolution() {
  return (
    <Section id="product" eyebrow="The Problem" title="Generic AI writes SQL. That's the problem.">
      <div className="grid gap-6 md:grid-cols-2">
        <Card tone="danger">
          <div className="flex items-center gap-2 text-rose-300">
            <AlertTriangle className="h-4 w-4" />
            <span className="text-xs uppercase tracking-widest">Without SQLSense</span>
          </div>
          <h3 className="mt-3 text-xl font-semibold text-white">
            Hallucinated joins. Silent data corruption. Zero accountability.
          </h3>
          <ul className="mt-4 space-y-2 text-sm text-slate-300">
            {["LLMs invent columns and joins that don't exist", "Write access risks corrupting production", "No guarantees the query matches the question", "Impossible to defend the answer in an audit"].map((t) => (
              <li key={t} className="flex gap-2"><span className="mt-1.5 h-1 w-1 rounded-full bg-rose-400" />{t}</li>
            ))}
          </ul>
        </Card>
        <Card tone="success">
          <div className="flex items-center gap-2 text-cyan-300">
            <Shield className="h-4 w-4" />
            <span className="text-xs uppercase tracking-widest">With SQLSense</span>
          </div>
          <h3 className="mt-3 text-xl font-semibold text-white">
            Deterministic planning. Validated SQL. Fail-closed by default.
          </h3>
          <ul className="mt-4 space-y-2 text-sm text-slate-300">
            {["Queries follow a graph-authorized join path — never invented", "Every statement is read-only and mandatorily validated", "Executor revalidates before touching your database", "Ambiguity fails safely — nothing runs unless it's trusted"].map((t) => (
              <li key={t} className="flex gap-2"><Check className="mt-0.5 h-4 w-4 text-cyan-300 shrink-0" />{t}</li>
            ))}
          </ul>
        </Card>
      </div>
    </Section>
  );
}

const DEMO_QUESTIONS = [
  "Show revenue by region for Q2",
  "Which SKUs shipped late last week?",
  "Top 10 accounts with overdue invoices",
];
function InteractiveDemo() {
  const [q, setQ] = useState(DEMO_QUESTIONS[0]);
  const [step, setStep] = useState(0);
  const [running, setRunning] = useState(false);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);
  const stages = [
    "Understanding question",
    "Resolving schema graph",
    "Planning deterministic SQL",
    "Validating read-only",
    "Executor revalidation",
    "Returning trusted result",
  ];
  function run() {
    setRunning(true);
    setStep(0);
    if (timer.current) clearInterval(timer.current);
    timer.current = setInterval(() => {
      setStep((s) => {
        if (s >= stages.length - 1) {
          if (timer.current) clearInterval(timer.current);
          setRunning(false);
          return stages.length - 1;
        }
        return s + 1;
      });
    }, 550);
  }
  useEffect(() => () => { if (timer.current) clearInterval(timer.current); }, []);

  return (
    <Section id="demo" eyebrow="Interactive Demo" title="Try a deterministic query.">
      <div className="rounded-2xl border border-white/10 bg-gradient-to-b from-white/[0.05] to-white/[0.02] p-4 sm:p-6 backdrop-blur-xl">
        <div className="flex flex-wrap gap-2">
          {DEMO_QUESTIONS.map((d) => (
            <button
              key={d}
              onClick={() => { setQ(d); setStep(0); }}
              className={"rounded-full border px-3 py-1.5 text-xs transition " + (q === d ? "border-cyan-300/40 bg-cyan-400/10 text-cyan-200" : "border-white/10 bg-white/5 text-slate-300 hover:bg-white/10")}
            >
              {d}
            </button>
          ))}
        </div>
        <div className="mt-4 flex flex-col sm:flex-row gap-3">
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            className="flex-1 h-11 rounded-lg border border-white/10 bg-[#0a0b16] px-4 text-sm text-slate-100 placeholder:text-slate-500 focus:border-cyan-300/40 focus:outline-none"
            placeholder="Ask your database…"
          />
          <button
            onClick={run}
            disabled={running}
            className="inline-flex h-11 items-center justify-center gap-2 rounded-lg bg-gradient-to-r from-indigo-500 to-cyan-400 px-5 text-sm font-semibold text-white shadow-lg shadow-indigo-500/25 hover:shadow-indigo-500/50 disabled:opacity-60"
          >
            {running ? "Running…" : "Run query"}
            <ArrowRight className="h-4 w-4" />
          </button>
        </div>
        <div className="mt-6 grid gap-4 md:grid-cols-2">
          <div className="rounded-xl border border-white/10 bg-[#0a0b16]/80 p-4">
            <div className="text-xs uppercase tracking-widest text-slate-500 mb-3">Pipeline</div>
            <div className="space-y-2">
              {stages.map((s, i) => {
                const done = i <= step;
                const active = i === step && running;
                return (
                  <div key={s} className={"flex items-center gap-2 rounded-md border px-3 py-2 text-xs transition-all " + (done ? "border-cyan-300/30 bg-cyan-400/10 text-cyan-100" : "border-white/5 bg-white/[0.02] text-slate-400")}>
                    <span className={"grid h-4 w-4 place-items-center rounded-full " + (done ? "bg-cyan-400/30 text-cyan-200" : "bg-white/10 text-slate-500")}>
                      {done ? <Check className="h-2.5 w-2.5" /> : i + 1}
                    </span>
                    <span className="flex-1">{s}</span>
                    {active && <span className="h-1.5 w-1.5 rounded-full bg-cyan-300 animate-pulse" />}
                  </div>
                );
              })}
            </div>
          </div>
          <div className="rounded-xl border border-white/10 bg-[#0a0b16]/80 p-4">
            <div className="flex items-center justify-between text-xs uppercase tracking-widest text-slate-500 mb-3">
              <span>Generated SQL</span>
              <span className="rounded-full border border-emerald-400/30 bg-emerald-400/10 px-2 py-0.5 text-[10px] text-emerald-300 normal-case tracking-normal">
                VALIDATED · READ-ONLY
              </span>
            </div>
            <pre className="text-[12px] font-mono leading-relaxed text-slate-200 overflow-x-auto">{`-- ${q}
SELECT   region, SUM(amount) AS revenue
FROM     orders
WHERE    placed_at BETWEEN :q_start AND :q_end
GROUP BY region
ORDER BY revenue DESC;`}</pre>
          </div>
        </div>
      </div>
    </Section>
  );
}

function Features() {
  const items = [
    { icon: Zap, title: "Deterministic Runtime", desc: "The planner produces the same SQL for the same question. Every time." },
    { icon: Lock, title: "Read-Only SQL", desc: "SELECT-only, enforced at the parser. No writes ever leave the sandbox." },
    { icon: Sparkles, title: "No Runtime AI SQL", desc: "AI shapes intent — the query is composed from your schema, not by an LLM." },
    { icon: Network, title: "Graph-Authorized Joins", desc: "Joins traverse an explicit relationship graph you approve — no guessing." },
    { icon: FileCheck2, title: "Mandatory SQL Validation", desc: "Static analysis runs on every plan before execution is even considered." },
    { icon: Shield, title: "Executor Revalidation", desc: "The runtime re-checks safety inside the connection, not just at plan time." },
    { icon: AlertTriangle, title: "Fail-Closed Ambiguity", desc: "When intent is unclear, SQLSense refuses to answer instead of guessing." },
    { icon: Eye, title: "Full Query Transparency", desc: "Every answer ships with the exact SQL, plan trace, and row-count evidence." },
  ];
  return (
    <Section id="features" eyebrow="Features" title="Everything a data team actually needs.">
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {items.map((f) => (
          <div key={f.title} className="group relative rounded-xl border border-white/10 bg-gradient-to-b from-white/[0.05] to-white/[0.02] p-5 backdrop-blur transition-all hover:-translate-y-1 hover:border-cyan-300/30 hover:shadow-xl hover:shadow-cyan-400/10">
            <div className="absolute inset-x-0 -top-px h-px bg-gradient-to-r from-transparent via-cyan-300/50 to-transparent opacity-0 group-hover:opacity-100 transition-opacity" />
            <div className="grid h-10 w-10 place-items-center rounded-lg bg-gradient-to-br from-indigo-500/30 to-cyan-400/20 border border-white/10">
              <f.icon className="h-5 w-5 text-cyan-200" />
            </div>
            <div className="mt-4 text-white font-semibold">{f.title}</div>
            <div className="mt-1 text-sm text-slate-400 leading-relaxed">{f.desc}</div>
          </div>
        ))}
      </div>
    </Section>
  );
}

function HowItWorks() {
  const steps = [
    { n: "01", title: "You ask in plain English", desc: "Type a question the way you'd ask a data analyst." },
    { n: "02", title: "SQLSense plans deterministically", desc: "Intent is resolved against your relationship graph — not a language model." },
    { n: "03", title: "SQL is validated & executed", desc: "Read-only, revalidated inside the executor, then run against your database." },
    { n: "04", title: "You get an auditable answer", desc: "Result table, exact SQL, and full evidence — ready to defend." },
  ];
  return (
    <Section eyebrow="How it works" title="From question to trusted answer in four steps.">
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {steps.map((s, i) => (
          <div key={s.n} className="relative rounded-xl border border-white/10 bg-white/[0.03] p-6 backdrop-blur">
            <div className="text-xs font-mono text-cyan-300/70">{s.n}</div>
            <div className="mt-2 text-white font-semibold text-lg">{s.title}</div>
            <div className="mt-2 text-sm text-slate-400">{s.desc}</div>
            {i < steps.length - 1 && <div className="hidden lg:block absolute top-1/2 -right-3 h-px w-6 bg-gradient-to-r from-cyan-300/50 to-transparent" />}
          </div>
        ))}
      </div>
    </Section>
  );
}

function SecuritySection() {
  const items = [
    { icon: Lock, title: "Read-only enforcement", desc: "Parser + executor guarantee." },
    { icon: Shield, title: "No credential persistence", desc: "Passwords never stored on disk." },
    { icon: GitBranch, title: "Approved join graph", desc: "You control what can be joined." },
    { icon: FileCheck2, title: "Full audit trail", desc: "Every query is logged with evidence." },
  ];
  return (
    <Section id="security" eyebrow="Security" title="Enterprise-grade safety, baked into the runtime.">
      <div className="grid gap-6 lg:grid-cols-[1.1fr_1fr] items-center">
        <div className="rounded-2xl border border-white/10 bg-gradient-to-b from-white/[0.06] to-white/[0.02] p-8 backdrop-blur">
          <div className="flex items-center gap-3">
            <span className="grid h-12 w-12 place-items-center rounded-xl bg-gradient-to-br from-indigo-500/30 to-cyan-400/20 border border-white/10">
              <Shield className="h-6 w-6 text-cyan-200" />
            </span>
            <div>
              <div className="text-white font-semibold">Fail-closed by default</div>
              <div className="text-sm text-slate-400">Ambiguity, unauthorized joins, or unsafe SQL — the query never runs.</div>
            </div>
          </div>
          <div className="mt-6 grid gap-3 sm:grid-cols-2">
            {items.map((it) => (
              <div key={it.title} className="flex items-start gap-3 rounded-lg border border-white/10 bg-white/[0.03] p-4">
                <span className="grid h-8 w-8 place-items-center rounded-md bg-white/5">
                  <it.icon className="h-4 w-4 text-cyan-200" />
                </span>
                <div>
                  <div className="text-sm font-semibold text-white">{it.title}</div>
                  <div className="text-xs text-slate-400">{it.desc}</div>
                </div>
              </div>
            ))}
          </div>
        </div>
        <div className="rounded-2xl border border-white/10 bg-[#0a0b16]/80 p-6 font-mono text-xs text-slate-300 backdrop-blur">
          <div className="text-slate-500"># SQLSense guardrails</div>
          <div className="mt-2"><span className="text-cyan-300">assert</span> statement.<span className="text-violet-300">is_read_only</span>()</div>
          <div><span className="text-cyan-300">assert</span> plan.<span className="text-violet-300">joins</span> ⊆ graph.<span className="text-violet-300">authorized</span></div>
          <div><span className="text-cyan-300">assert</span> validator.<span className="text-violet-300">passes</span>(sql)</div>
          <div><span className="text-cyan-300">assert</span> executor.<span className="text-violet-300">revalidate</span>(sql)</div>
          <div className="mt-3 text-emerald-300">→ query executed safely ✓</div>
          <div className="mt-3 text-slate-500"># otherwise</div>
          <div className="text-rose-300">→ raise SafetyError("fail-closed")</div>
        </div>
      </div>
    </Section>
  );
}

function UseCases() {
  const cases = [
    { icon: LineChart, title: "Sales", points: ["Pipeline health at a glance", "Win-rate by segment", "Rep leaderboard queries"] },
    { icon: Wallet, title: "Finance", points: ["Revenue reconciliation", "Overdue AR reports", "Budget vs actuals"] },
    { icon: Users, title: "Operations", points: ["SLA compliance tracking", "Ticket volume trends", "Vendor performance"] },
    { icon: Factory, title: "Manufacturing", points: ["Line yield analytics", "Downtime root causes", "Inventory turnover"] },
  ];
  return (
    <Section id="use-cases" eyebrow="Use Cases" title="Built for every team that owns a KPI.">
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {cases.map((c) => (
          <div key={c.title} className="rounded-xl border border-white/10 bg-gradient-to-b from-white/[0.05] to-white/[0.02] p-5 backdrop-blur hover:border-cyan-300/30 transition">
            <div className="grid h-10 w-10 place-items-center rounded-lg bg-gradient-to-br from-indigo-500/30 to-cyan-400/20 border border-white/10">
              <c.icon className="h-5 w-5 text-cyan-200" />
            </div>
            <div className="mt-4 text-white font-semibold">{c.title}</div>
            <ul className="mt-3 space-y-1.5 text-sm text-slate-400">
              {c.points.map((p) => (
                <li key={p} className="flex gap-2"><Check className="h-4 w-4 text-cyan-300 shrink-0 mt-0.5" />{p}</li>
              ))}
            </ul>
          </div>
        ))}
      </div>
    </Section>
  );
}

function SchemaPreview() {
  const tables = [
    { name: "customers", cols: ["id", "name", "region"] },
    { name: "orders", cols: ["id", "customer_id", "total", "placed_at"] },
    { name: "products", cols: ["id", "sku", "name"] },
    { name: "line_items", cols: ["order_id", "product_id", "qty"] },
  ];
  return (
    <Section eyebrow="Schema Explorer" title="Your schema, visualized as a trust boundary.">
      <div className="rounded-2xl border border-white/10 bg-gradient-to-b from-white/[0.05] to-white/[0.02] p-6 backdrop-blur">
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {tables.map((t) => (
            <div key={t.name} className="rounded-lg border border-white/10 bg-[#0a0b16]/70 overflow-hidden">
              <div className="flex items-center gap-2 border-b border-white/5 px-3 py-2 text-xs text-slate-300">
                <Database className="h-3.5 w-3.5 text-cyan-300" />
                {t.name}
              </div>
              <ul className="divide-y divide-white/5 text-xs">
                {t.cols.map((c) => (
                  <li key={c} className="flex items-center justify-between px-3 py-1.5 font-mono text-slate-300">
                    <span>{c}</span>
                    <span className="text-slate-500">·</span>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
        <div className="mt-4 text-xs text-slate-500">
          Only joins on the approved relationship graph are ever executed.
        </div>
      </div>
    </Section>
  );
}

function Comparison() {
  const rows = [
    { label: "Deterministic output", sqlsense: "Always", generic: "Never" },
    { label: "Runtime AI SQL generation", sqlsense: "Prohibited", generic: "Core mechanic" },
    { label: "Read-only enforcement", sqlsense: "Parser + executor", generic: "Prompt-based" },
    { label: "Graph-authorized joins", sqlsense: "Enforced", generic: "Guessed" },
    { label: "Mandatory validation", sqlsense: "Yes", generic: "Optional" },
    { label: "Fail-closed on ambiguity", sqlsense: "Yes", generic: "Answers anyway" },
    { label: "Auditable evidence", sqlsense: "SQL + plan trace", generic: "Chat log" },
  ];
  return (
    <Section eyebrow="Comparison" title="SQLSense vs. generic AI SQL tools.">
      <div className="overflow-hidden rounded-2xl border border-white/10 bg-gradient-to-b from-white/[0.05] to-white/[0.02] backdrop-blur">
        <div className="grid grid-cols-[1.4fr_1fr_1fr] text-xs sm:text-sm">
          <div className="p-4 text-slate-400 font-medium">Capability</div>
          <div className="p-4 text-white font-semibold bg-gradient-to-r from-indigo-500/10 to-cyan-400/10 border-l border-white/5">SQLSense</div>
          <div className="p-4 text-slate-400 font-medium border-l border-white/5">Generic AI SQL</div>
          {rows.map((r) => (
            <RowGroup key={r.label} row={r} />
          ))}
        </div>
      </div>
    </Section>
  );
}
function RowGroup({ row }: { row: { label: string; sqlsense: string; generic: string } }) {
  return (
    <>
      <div className="p-4 border-t border-white/5 text-slate-300">{row.label}</div>
      <div className="p-4 border-t border-l border-white/5 bg-gradient-to-r from-indigo-500/5 to-cyan-400/5 text-cyan-200 font-medium flex items-center gap-2">
        <Check className="h-4 w-4 text-cyan-300" /> {row.sqlsense}
      </div>
      <div className="p-4 border-t border-l border-white/5 text-slate-400">{row.generic}</div>
    </>
  );
}

function FAQ() {
  const items = [
    { q: "Does SQLSense ever write to my database?", a: "No. SQLSense only issues SELECT statements. Writes are rejected at both plan time and inside the executor, so a bug in one layer can't bypass the other." },
    { q: "How is this different from an LLM writing SQL?", a: "LLMs invent SQL from natural language, which means they can hallucinate columns, joins, or filters. SQLSense uses AI to understand intent, then composes the query deterministically from your approved schema graph." },
    { q: "What happens when a question is ambiguous?", a: "SQLSense fails closed — it refuses to answer instead of guessing. You'll see a clear explanation of what was ambiguous and how to disambiguate." },
    { q: "Which databases are supported?", a: "MySQL and PostgreSQL today, with Snowflake, BigQuery, Redshift and Databricks on the roadmap." },
    { q: "Do you store our credentials?", a: "No. Passwords are used to open a session and never persisted. Connection metadata (host, database name) can be optionally saved for convenience." },
  ];
  return (
    <Section id="faq" eyebrow="FAQ" title="Answers to the questions data leaders ask first.">
      <div className="mx-auto max-w-3xl divide-y divide-white/10 rounded-2xl border border-white/10 bg-white/[0.03] backdrop-blur">
        {items.map((i, idx) => (
          <FAQItem key={idx} q={i.q} a={i.a} defaultOpen={idx === 0} />
        ))}
      </div>
    </Section>
  );
}
function FAQItem({ q, a, defaultOpen }: { q: string; a: string; defaultOpen?: boolean }) {
  const [open, setOpen] = useState(!!defaultOpen);
  return (
    <div>
      <button onClick={() => setOpen((v) => !v)} className="flex w-full items-center justify-between gap-4 px-5 py-4 text-left">
        <span className="text-sm sm:text-base font-medium text-white">{q}</span>
        <ChevronDown className={"h-4 w-4 text-slate-400 transition-transform " + (open ? "rotate-180" : "")} />
      </button>
      <div className={"grid overflow-hidden transition-all duration-300 " + (open ? "grid-rows-[1fr] opacity-100" : "grid-rows-[0fr] opacity-0")}>
        <div className="min-h-0">
          <div className="px-5 pb-4 text-sm text-slate-400 leading-relaxed">{a}</div>
        </div>
      </div>
    </div>
  );
}

function FinalCTA({ launchHref }: { launchHref: string }) {
  return (
    <section className="relative py-24">
      <div className="mx-auto max-w-5xl px-4 sm:px-6 lg:px-8">
        <div className="relative overflow-hidden rounded-3xl border border-white/10 bg-gradient-to-br from-indigo-600/30 via-blue-500/20 to-cyan-400/20 p-10 sm:p-16 text-center backdrop-blur-xl">
          <div className="absolute -inset-1 bg-gradient-to-r from-indigo-500/30 via-cyan-400/30 to-violet-500/30 opacity-40 blur-3xl" />
          <div className="relative">
            <h2 className="text-3xl sm:text-5xl font-bold tracking-tight text-white">
              Stop trusting SQL you can't verify.
            </h2>
            <p className="mt-4 text-slate-200/90 max-w-xl mx-auto">
              Launch SQLSense and ask your database a question you actually care about. Every answer ships with the SQL and the evidence.
            </p>
            <div className="mt-8 flex flex-wrap items-center justify-center gap-3">
              <Link to={launchHref} className="group inline-flex h-12 items-center gap-2 rounded-lg bg-white px-6 text-sm font-semibold text-slate-900 shadow-xl hover:-translate-y-0.5 transition-all">
                Launch App
                <ArrowRight className="h-4 w-4 transition-transform group-hover:translate-x-0.5" />
              </Link>
              <a href="#demo" className="inline-flex h-12 items-center rounded-lg border border-white/20 bg-white/5 px-6 text-sm font-medium text-white hover:bg-white/10 transition">
                Try the demo
              </a>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}

function Footer() {
  const cols = [
    { title: "Product", links: ["Features", "Security", "Use Cases", "Changelog"] },
    { title: "Company", links: ["About", "Careers", "Contact", "Press"] },
    { title: "Resources", links: ["Docs", "Guides", "Status", "Support"] },
    { title: "Legal", links: ["Privacy", "Terms", "DPA", "Subprocessors"] },
  ];
  return (
    <footer className="relative border-t border-white/10 bg-[#04050b]">
      <div className="mx-auto max-w-7xl px-4 sm:px-6 lg:px-8 py-14">
        <div className="grid gap-10 lg:grid-cols-[1.4fr_repeat(4,1fr)]">
          <div>
            <div className="flex items-center gap-2">
              <span className="grid h-8 w-8 place-items-center rounded-lg bg-gradient-to-br from-indigo-500 via-blue-500 to-cyan-400">
                <Database className="h-4 w-4 text-white" />
              </span>
              <span className="font-semibold text-white">SQLSense</span>
            </div>
            <p className="mt-4 text-sm text-slate-400 max-w-xs">
              Deterministic natural-language-to-SQL for teams that can't afford a wrong answer.
            </p>
            <div className="mt-4 flex gap-2">
              {[Github, Twitter, Linkedin].map((I, i) => (
                <a key={i} href="#" className="grid h-9 w-9 place-items-center rounded-md border border-white/10 bg-white/5 text-slate-400 hover:text-white hover:bg-white/10 transition">
                  <I className="h-4 w-4" />
                </a>
              ))}
            </div>
          </div>
          {cols.map((c) => (
            <div key={c.title}>
              <div className="text-xs uppercase tracking-widest text-slate-500">{c.title}</div>
              <ul className="mt-4 space-y-2 text-sm">
                {c.links.map((l) => (
                  <li key={l}><a href="#" className="text-slate-400 hover:text-white transition">{l}</a></li>
                ))}
              </ul>
            </div>
          ))}
        </div>
        <div className="mt-12 flex flex-wrap items-center justify-between gap-4 border-t border-white/5 pt-6 text-xs text-slate-500">
          <div>© {new Date().getFullYear()} SQLSense. All rights reserved.</div>
          <div>Built for enterprise data teams.</div>
        </div>
      </div>
    </footer>
  );
}

function Section({ id, eyebrow, title, children }: { id?: string; eyebrow: string; title: string; children: ReactNode }) {
  return (
    <section id={id} className="relative py-20 sm:py-28">
      <div className="mx-auto max-w-7xl px-4 sm:px-6 lg:px-8">
        <div className="mb-10 max-w-2xl">
          <div className="text-xs uppercase tracking-widest text-cyan-300/80">{eyebrow}</div>
          <h2 className="mt-3 text-3xl sm:text-4xl font-bold tracking-tight text-white">{title}</h2>
        </div>
        {children}
      </div>
    </section>
  );
}

function Card({ tone = "default", children }: { tone?: "default" | "danger" | "success"; children: ReactNode }) {
  const ring =
    tone === "danger"
      ? "border-rose-400/20 from-rose-500/10 to-transparent"
      : tone === "success"
      ? "border-cyan-400/20 from-cyan-400/10 to-transparent"
      : "border-white/10 from-white/[0.05] to-white/[0.02]";
  return (
    <div className={`rounded-2xl border bg-gradient-to-b ${ring} p-6 sm:p-8 backdrop-blur transition-all`}>
      {children}
    </div>
  );
}
