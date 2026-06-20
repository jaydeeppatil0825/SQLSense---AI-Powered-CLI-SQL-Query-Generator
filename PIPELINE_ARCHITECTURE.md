# SQLSense Pipeline Architecture

SQLSense stays CLI-only and is organized around three logical pipelines.

This document describes the active runtime boundaries without doing a risky
folder migration. The goal is to keep the current codebase understandable,
dynamic, and safe while preserving Phase 7 behavior.

## Core Rule

No pipeline should add hardcoded database-specific or business-specific logic.

Allowed fixed logic:
- SELECT-only safety
- SQL validation rules
- PK/FK/_id structural handling
- date/datetime/timestamp structural handling
- boolean structural handling
- reserved SQL keyword handling
- confidence thresholds
- neutral fallback logic
- generic graph/BFS logic
- generic profiling/statistical logic

Not allowed:
- fixed database names
- fixed table names
- fixed column names
- ERP/business mappings
- glossary aliases
- semantic word buckets
- fixed formulas
- fixed SQL templates
- table-specific query rules

## Pipeline 1: KB Pipeline

Purpose:
Connect to the runtime database and build trusted dynamic evidence.

Responsibilities:
- database connection
- schema extraction
- PK/FK discovery
- profiling and sample statistics
- structural semantic facts
- AI semantic enrichment
- dynamic glossary generation
- relationship graph creation
- BFS join-path foundation
- vector index build/load
- KB/glossary/vector metadata persistence




Current files:
- `kb_pipeline/connection.py`
- `kb_pipeline/schema_reader.py`
- `kb_pipeline/data_profiler.py`
- `kb_pipeline/database_service.py`
- `kb_pipeline/knowledge_base_builder.py`
- `kb_pipeline/ai_semantic_enricher.py`
- `kb_pipeline/semantic_mapper.py`
- `kb_pipeline/business_glossary.py`
- `kb_pipeline/relationship_graph.py`
- `kb_pipeline/schema_facts.py`
- `kb_pipeline/vector/embedding_service.py`
- `kb_pipeline/vector/index_builder.py`
- `kb_pipeline/vector/retriever.py`
- `kb_pipeline/vector/persistence.py`

Compatibility wrappers kept at old paths:
- `db/connection.py`
- `db/schema_reader.py`
- `db/data_profiler.py`
- `core/database_service.py`
- `semantic/knowledge_base_builder.py`
- `semantic/ai_semantic_enricher.py`
- `semantic/semantic_mapper.py`
- `semantic/business_glossary.py`
- `semantic/relationship_graph.py`
- `semantic/erp_metadata.py`
- `vector_store/embedding_service.py`
- `vector_store/index_builder.py`
- `vector_store/retriever.py`
- `vector_store/persistence.py`

Outputs:
- `semantic/knowledge_base.json`
- `semantic/business_glossary.json`
- `semantic/knowledge_base.meta.json`
- vector index files
- relationship graph evidence

Boundary rules:
- Must not depend on question normalization, intent detection, or SQL generation.
- May expose KB/glossary/vector/relationship evidence to downstream pipelines.

## Pipeline 2: Query Planning Pipeline

Purpose:
Understand the user question and select dynamic evidence from the KB Pipeline.

Responsibilities:
- normalize the question without rewriting business meaning
- detect follow-up/action context
- build structured intent
- retrieve KB/glossary/vector evidence
- select tables and columns
- select measure/dimension/filter candidates
- retrieve `possible_join_paths`
- detect missing evidence
- produce unresolved/low-confidence planning output when needed

Current files:
- `query_pipeline/question_normalizer.py`
- `query_pipeline/intent_builder.py`
- `query_pipeline/context_retriever.py`
- `query_pipeline/query_planner.py`
- `query_pipeline/query_pipeline.py`
- `query_pipeline/conversation/action_detector.py`
- `query_pipeline/conversation/followup_detector.py`
- `query_pipeline/conversation/question_rewriter.py`
- `query_pipeline/conversation/conversation_memory.py`

Compatibility wrappers kept at old paths:
- `utils/question_normalizer.py`
- `core/intent_builder.py`
- `core/context_retriever.py`
- `core/query_planner.py`
- `core/query_pipeline.py`
- `conversation/action_detector.py`
- `conversation/followup_detector.py`
- `conversation/question_rewriter.py`
- `conversation/conversation_memory.py`

Outputs:
- `normalized_question`
- `intent`
- `retrieved_context`
- `selected_tables`
- `selected_columns`
- `measure_candidates`
- `dimension_candidates`
- `filters`
- `possible_join_paths`
- `formula_evidence`
- `evidence_sources`
- `confidence`
- unresolved reason/warnings when needed

Boundary rules:
- May read KB/glossary/vector evidence.
- Must not generate final SQL directly.
- Must not invent joins or formulas without runtime evidence.

## Pipeline 3: SQL Generation Pipeline

Purpose:
Generate, validate, and execute safe SELECT SQL only from Query Planning output.

Responsibilities:
- deterministic SQL for simple evidence-clear questions
- AI SQL generation from runtime evidence only
- strict SQL prompt construction
- SQL cleanup
- SQL validation
- safe deterministic repair only when runtime evidence supports it
- read-only SQL execution
- result/error return to the CLI

Current files:
- `sql_pipeline/sql_generator.py`
- `sql_pipeline/prompt_builder.py`
- `sql_pipeline/simple_query_generator.py`
- `sql_pipeline/erp_query_generator.py`
- `sql_pipeline/sql_validator.py`
- `sql_pipeline/query_executor.py`
- `sql_pipeline/question_service.py`
- `sql_pipeline/result_service.py`

Compatibility wrappers kept at old paths:
- `ai/sql_generator.py`
- `ai/prompt_builder.py`
- `ai/simple_query_generator.py`
- `ai/erp_query_generator.py`
- `utils/sql_validator.py`
- `db/query_executor.py`
- `core/question_service.py`
- `core/result_service.py`

Boundary rules:
- Must consume planning evidence instead of rebuilding hidden business meaning.
- Must not invent tables, columns, joins, filters, or formulas.
- Must use `possible_join_paths` for join-capable SQL generation and repair.
- Must use `formula_evidence` only when present.
- Validator is the final gate before execution.

## Current Orchestration

`core/question_service.py` remains the central orchestrator for now.

That is intentional in this phase. Phase 8A does not move the CLI flow or do a
large refactor. Instead, it enforces that `QuestionService` consumes the
planning pipeline output instead of rebuilding independent business guesses.

## Evidence Flow

Runtime flow:

1. Database connection is handled by the KB Pipeline.
2. Knowledge base, glossary, relationships, and vector state are built or loaded.
3. `core/query_pipeline.py` normalizes the question and builds:
   - intent
   - retrieved context
   - preview planning context
   - formula evidence
   - evidence sources
4. `core/question_service.py` consumes that pipeline context.
5. Rule-based or AI SQL generation uses:
   - selected tables
   - selected columns
   - measure candidates
   - dimension candidates
   - filters
   - `possible_join_paths`
   - formula evidence
   - evidence sources
6. `utils/sql_validator.py` validates the generated SQL.
7. `db/query_executor.py` executes only validated SELECT SQL.

## Join-Path Ownership

`semantic/relationship_graph.py` belongs to the KB Pipeline because it builds
generic runtime graph evidence from schema relationships.

`core/context_retriever.py` and `core/query_planner.py` consume that evidence
to produce `possible_join_paths`.

`core/question_service.py`, `ai/sql_generator.py`, and `ai/prompt_builder.py`
consume `possible_join_paths` during SQL generation and retry/repair.

## Neutral Naming Notes

- `kb_pipeline/schema_facts.py` is the primary runtime implementation for
  schema-fact enrichment and neutral metadata helpers.
- `semantic/erp_metadata.py` is still the file path for backward compatibility.
- Active runtime enrichment is schema-fact-only.
- Use `enrich_knowledge_base_schema_facts(...)` as the neutral runtime API.
- `enrich_knowledge_base_for_erp(...)` remains only as a backward-compatible
  alias and should not be the preferred runtime entry point.

## Phase 8A Non-Goals

This phase does not:
- rewrite the project
- move many files between folders
- change the CLI menu
- add frontend or API layers
- change the active SQL generation behavior
- reintroduce ERP templates or hardcoded business mappings
