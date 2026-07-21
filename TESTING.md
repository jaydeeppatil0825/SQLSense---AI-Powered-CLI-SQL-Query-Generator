# SQLSense Test Baseline

SQLSense test runs are split between supported deterministic suites and legacy
diagnostics for removed architectures. Runtime SQL behavior should not be
changed only to satisfy legacy expectations.

Architecture and public-boundary checks:

```powershell
pytest tests/test_architecture_boundaries.py
```

These checks enforce the dependency direction and compatibility-module rules
documented in `ARCHITECTURE_CONTRACTS.md`.

## Dependencies

Install development test dependencies with:

```powershell
pip install -r requirements-dev.txt
```

`requirements-dev.txt` includes the runtime requirements plus test-only tools
such as `pytest` and `hypothesis`.

## Authoritative Commands

Fast authoritative suite:

```powershell
pytest -m "not live and not legacy"
```

Regression suite:

```powershell
pytest -m regression
```

Live suite:

```powershell
pytest -m live
```

Legacy diagnostic suite:

```powershell
pytest -m legacy
```

Full supported suite:

```powershell
pytest -m "not legacy"
```

## Markers

- `unit`: default in-process tests with no external service requirement.
- `integration`: multi-component tests that still run locally by default.
- `regression`: supported behavior locks and fixed-bug coverage.
- `live`: tests requiring external services, real databases, or manual setup.
- `legacy`: obsolete diagnostics for removed architecture; not authoritative.
- `mysql`: tests that use or model MySQL-specific behavior.
- `frontend_contract`: backend gateway contract used by the frontend.

## Live And MySQL Setup

The default fast suite is intended to run without a live database. Tests marked
`live` or manual verifier scripts may require a running MySQL instance and the
expected SQLSense lab databases, such as `sqlsense_full_small_lab`.

Do not create, drop, or reseed live databases from generic pytest commands.
Use explicit setup flags on scripts that provide them.

## Supported Versus Legacy

Supported tests cover the current deterministic SQLSense architecture:
intent extraction, planner safety, Relationship Graph joins, grain analysis,
SQL validation/execution boundaries, Phase 9 caches, CLI, and API gateway
contracts.

Legacy tests are kept as diagnostics only when they depend on removed runtime-AI
modules, obsolete wrapper paths, old ERP AI-first routing, or deprecated
conversation actions. They are marked `legacy` and isolated from supported
commands instead of being deleted.

## Compatibility Import Shims

These shims exist only for still-supported interfaces that moved:

- `semantic.erp_metadata` maps to `kb_pipeline.schema_facts`.
- `semantic.semantic_mapper` maps to `kb_pipeline.semantic_mapper`.

Removed `ai.*` runtime-AI modules are not shimmed.

## Current Baseline

Last refreshed: 2026-07-17.

- Collection: `1029 tests collected`, no collection/import errors.
- Fast authoritative suite: `973 passed, 40 failed, 8 skipped, 16 deselected`.
- Regression suite: `365 passed, 29 failed, 8 skipped, 635 deselected`.
- Live suite: `4 passed, 8 skipped, 1025 deselected`.
- Legacy suite: pending rerun after local Python approval.
- Full supported suite: pending rerun after local Python approval.

Known supported failures are currently classified before runtime fixes as:

- Full-small-lab regression tests: current semantic artifact / planner evidence
  mismatch; verify against the intended lab artifacts before changing runtime.
- Phase 3 grouped/HAVING and Phase 4 ranking failures: current regression
  candidates around aggregate intent, filter extraction, projection shape, and
  ranking ambiguity.
- Phase 6/8 joined aggregate failures: current regression candidates around
  source-scope filters, metric resolution, safe fail-closed behavior, and grain
  planner context.
- Query pipeline monkeypatch failures: infrastructure compatibility issue caused
  by the current `cached_retrieve_context` boundary replacing the older
  `retrieve_context` symbol.
- Conversation rewrite/follow-up failures: obsolete expected-output candidates
  for current deterministic conversation behavior.
- Single-table `SELECT *` projection failure: obsolete expected-output candidate
  after the approved projection-format cleanup.
