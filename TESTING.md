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

Last refreshed: 2026-07-21.

- Architecture + QueryPipeline focused suite: `21 passed`.
- Gateway, CLI, QuestionService and Phase 9 focused suite: `96 passed, 1 failed`.
- Regression suite: `398 passed, 1 failed, 8 skipped, 678 deselected`.
- Full supported non-live/non-legacy suite: `1054 passed, 7 failed, 8 skipped, 16 deselected`.

Known supported failures are currently classified before runtime fixes as:

- API gateway / CLI display expectation drift: CLI menu text assertion expects
  the older `Backend  :` spacing.
- Semantic artifact identity mismatch: committed metadata names
  `sqlsense_realistic_business_lab` while the AI semantic lab test expects
  `sqlsense_ai_semantic_lab`.
- Query planner payload expectation drift: one test still expects the older
  filter source shape.
- Conversation rewrite/follow-up expectation drift: two tests expect older
  wording for location and sorting rewrites.
- Single-table `SELECT *` projection expectation drift: one test still expects
  explicit columns after the approved single-table full-row projection cleanup.
