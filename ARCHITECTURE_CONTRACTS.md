# SQLSense Public Architecture Contracts

This document defines the supported Python boundaries for the architecture in
`SQLSense_SYSTEM_DESIGN.md` and `PIPELINE_ARCHITECTURE.md`.

## Dependency Direction

```text
api_gateway / main
        |
        v
core.app_service
        |
        +--> kb_pipeline
        +--> query_pipeline
        +--> sql_pipeline
        +--> infrastructure
```

`kb_pipeline` must not import question planning or SQL execution.
`query_pipeline` may consume KB evidence but must not import SQL generation or
execution. `sql_pipeline` may consume the immutable planner handoff but must not
authorize joins or recompute planner decisions. `infrastructure` must not import
application pipelines.

## Public Entry Points

| Boundary | Entry point | Contract |
|---|---|---|
| Application | `core.app_service.AppService` | Owns service lifecycle and coordinates planning, generation, validation and execution. |
| Database | `kb_pipeline.database_service.DatabaseService` | Owns connection and KB, glossary, graph and vector lifecycle. |
| Planning | `query_pipeline.query_pipeline.QueryPipeline.run` | Returns `QueryPipelineResult`; never generates or executes SQL. |
| Retrieval | `query_pipeline.query_pipeline.retrieve_context` | Cache-aware retrieval entry returning the same evidence shape on hit, miss or disabled cache. |
| Planner | `query_pipeline.query_planner.build_query_context` | Builds the deterministic planner context from normalized question, intent and evidence. |
| SQL generation | `sql_pipeline.question_service.QuestionService.generate_from_pipeline` | Consumes a `QueryPipelineResult.to_pipeline_context()` handoff and selects deterministic generators. |
| SQL validation | `sql_pipeline.sql_validator.validate_sql` | Enforces read-only SQL safety; structural validation remains mandatory for executable SQL. |
| Execution | `sql_pipeline.query_executor.execute_query` | Revalidates and executes approved SQL against the active engine. |
| Gateway | `api_gateway.app.gateway` | Exposes versioned gateway actions and safe response envelopes. |

The machine-readable registry is `infrastructure.contracts`. Contract versions
are included in `QueryPipelineResult` and cache identities where applicable.

## QueryPipeline Handoff

`QueryPipelineResult` contains the normalized question, intent, retrieved
evidence, planner context, route, query shape, clause plan, ambiguity state and
contract versions. Its SQL fields remain `None`; SQL generation belongs to
`sql_pipeline`.

The active handoff is:

```text
QueryPipeline.run(...)
-> QueryPipelineResult.to_pipeline_context()
-> QuestionService.generate_from_pipeline(...)
```

`QuestionService.process_question(...)` remains a compatibility entry point for
existing direct callers. New runtime orchestration must use the pipeline handoff.

## Compatibility Imports

`core.context_retriever`, `core.database_service`, `core.query_planner`,
`core.question_service`, `db`, `semantic`, `vector_store`,
`utils.question_normalizer` and `utils.sql_validator` preserve supported legacy
imports. They contain no new implementation and delegate to the active modules.

Import-direction tests reject compatibility imports from active production
packages. Compatibility modules may be removed only after their external callers
have migrated.

## Authority Rules

- Runtime is deterministic; AI is restricted to optional KB enrichment.
- Relationship Graph is the only join authority.
- Cache evidence cannot authorize a route, join, SQL or execution.
- QueryPipeline does not generate SQL.
- SQL generation does not create planner authority.
- SQL validation and executor revalidation remain mandatory.
