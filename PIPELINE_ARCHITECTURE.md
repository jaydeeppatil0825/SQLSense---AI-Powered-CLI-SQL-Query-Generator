# SQLSense Pipeline Architecture

SQLSense is a full-stack application organized around three logical pipelines with multiple interface layers (CLI, Web UI, API Gateway).

This document describes the active runtime boundaries and architecture of the system. The goal is to keep the codebase understandable, dynamic, and safe while maintaining the current behavior.

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
- Database connection management
- Schema extraction using SQLAlchemy reflection
- PK/FK discovery and relationship detection
- Data profiling and sample statistics
- Structural semantic facts extraction
- AI semantic enrichment (KB build only)
- Dynamic business glossary generation
- Relationship graph creation for join paths
- Vector index build/load with ChromaDB
- KB/glossary/vector metadata persistence
- Chart generation logic
- Insight generation (rule-based + AI)

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
- `kb_pipeline/insights/insight_generator.py`
- `kb_pipeline/charts/chart_generator.py`
- `kb_pipeline/vector/chroma_store.py`
- `kb_pipeline/vector/chroma_telemetry.py`
- `kb_pipeline/vector/embedding_service.py`
- `kb_pipeline/vector/index_builder.py`
- `kb_pipeline/vector/retriever.py`
- `kb_pipeline/vector/persistence.py`

Outputs:
- `semantic/knowledge_base.json`
- `semantic/business_glossary.json`
- `semantic/knowledge_base.meta.json`
- Vector index files (ChromaDB)
- Relationship graph evidence
- Generated charts
- Generated insights

Boundary rules:
- Must not depend on question normalization, intent detection, or SQL generation.
- May expose KB/glossary/vector/relationship evidence to downstream pipelines.
- AI is used only during KB build for semantic enrichment, not at runtime.

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
- `sql_pipeline/deterministic_sql_generator.py`
- `sql_pipeline/sql_validator.py`
- `sql_pipeline/query_executor.py`
- `sql_pipeline/question_service.py`
- `sql_pipeline/result_service.py`

Boundary rules:
- Must consume planning evidence instead of rebuilding hidden business meaning.
- Must not invent tables, columns, joins, filters, or formulas.
- Must use `possible_join_paths` for join-capable SQL generation and repair.
- Must use `formula_evidence` only when present.
- Validator is the final gate before execution.

## Current Orchestration

`core/app_service.py` is the main application service orchestrator that coordinates all lower-level services behind the CLI and API Gateway.

The orchestration flow:
1. **CLI Mode**: `main.py` handles CLI menu display and user input
2. **Web/API Mode**: `api_gateway/app.py` handles HTTP requests and session management
3. `utils/config_manager.py` handles local connection configuration persistence
4. `core/app_service.py` coordinates business logic across all pipelines
5. `kb_pipeline/database_service.py` handles database connection and KB build
6. `query_pipeline/query_pipeline.py` handles question processing and planning
7. `sql_pipeline/question_service.py` handles SQL generation and execution
8. `core/chart_service.py` handles chart generation
9. `core/insight_service.py` handles insight generation (runtime disabled)

## Evidence Flow

Runtime flow:

1. Database connection is handled by the KB Pipeline (`kb_pipeline/database_service.py`).
2. Knowledge base, glossary, relationships, and vector state are built or loaded.
3. `query_pipeline/query_pipeline.py` normalizes the question and builds:
   - intent
   - retrieved context
   - preview planning context
   - formula evidence
   - evidence sources
4. `sql_pipeline/question_service.py` consumes that pipeline context.
5. Deterministic SQL generation uses:
   - selected tables
   - selected columns
   - measure candidates
   - dimension candidates
   - filters
   - `possible_join_paths`
   - formula evidence
   - evidence sources
6. `sql_pipeline/sql_validator.py` validates the generated SQL.
7. `sql_pipeline/query_executor.py` executes only validated SELECT SQL.

## Join-Path Ownership

`kb_pipeline/relationship_graph.py` belongs to the KB Pipeline because it builds
generic runtime graph evidence from schema relationships.

`query_pipeline/context_retriever.py` and `query_pipeline/query_planner.py` consume that evidence
to produce `possible_join_paths`.

`sql_pipeline/question_service.py` and `sql_pipeline/deterministic_sql_generator.py`
consume `possible_join_paths` during SQL generation and retry/repair.

## Neutral Naming Notes

- `kb_pipeline/schema_facts.py` is the primary runtime implementation for
  schema-fact enrichment and neutral metadata helpers.
- Active runtime enrichment is schema-fact-only.
- Use `enrich_knowledge_base_schema_facts(...)` as the neutral runtime API.

## Core Services

The `core/` directory contains the main application services that orchestrate the pipelines:

- `core/app_service.py`: Main application service orchestrator that coordinates all lower-level services
- `core/ai_backend_service.py`: AI backend service for KB build only (local Ollama)
- `core/chart_service.py`: Chart generation service
- `core/insight_service.py`: Insight generation service (runtime disabled - AI restricted to KB enrichment)
- `core/database_service.py`: Compatibility alias for `kb_pipeline.database_service`
- `core/query_planner.py`: Compatibility shim for `query_pipeline.query_planner`
- `core/question_service.py`: Compatibility alias for `sql_pipeline.question_service`

## Interface Layers

SQLSense provides multiple interface layers to access the same core pipeline architecture:

### CLI Interface
- **Entry Point**: `main.py`
- **Features**: 
  - Interactive menu system
  - Connection reuse with password protection
  - Auto-execute validated SQL
  - Real-time status indicators
  - Conversation memory and follow-up questions

### API Gateway
- **Entry Point**: `api_gateway/app.py`
- **Technology**: FastAPI
- **Features**:
  - REST API for programmatic access
  - Session-based authentication
  - CORS support for web UI
  - Structured JSON responses
  - Error handling and validation
- **Endpoints**:
  - `POST /api/action` - Main action endpoint
  - `GET /api/health` - Health check
  - `GET /api/system/status` - System status

### Web UI
- **Technology**: React 19 + TypeScript + Vite
- **Features**:
  - Modern responsive interface
  - Real-time database connection status
  - Interactive question composer
  - Visual query results with charts
  - Query history and conversation management
  - Knowledge base status dashboard

## Legacy Compatibility

The following directories and files are maintained for backward compatibility:

- `db/` - Legacy database utilities (connection, data_profiler, query_executor, schema_reader)
- `vector_store/` - Legacy vector store implementation
- `core/database_service.py` - Compatibility alias for `kb_pipeline.database_service`
- `core/query_planner.py` - Compatibility shim for `query_pipeline.query_planner`
- `core/question_service.py` - Compatibility alias for `sql_pipeline.question_service`

These are gradually being phased out in favor of the pipeline-based architecture.

## Utility Services

The `utils/` directory contains utility services used across the application:

- `utils/config_manager.py`: Local connection configuration manager (stores last-used connection details in `.sqlsense/local_connection.json`)
- `utils/file_utils.py`: JSON file utilities for save/load operations
- `utils/logger.py`: Centralized logging configuration

## CLI Features

The CLI (`main.py`) provides a professional user experience with:

- **Connection Reuse**: Remembers last database connection and offers to reuse it (passwords not stored)
- **Auto-Execute**: Automatically executes validated SQL after question processing
- **Status Indicators**: Shows real-time status for AI backend, database connection, and KB load status
- **Clean Menu Design**: Uses ASCII box-drawing characters for a modern, clean interface

## Design Principles

1. **Deterministic Runtime**: SQL generation at runtime is entirely deterministic without AI/LLM
2. **AI for KB Build Only**: AI/LLM is used only during knowledge base build for semantic enrichment
3. **Schema-Agnostic**: Intent detection and query planning are schema-agnostic to preserve business meaning
4. **Evidence-Based**: All SQL generation must be backed by runtime evidence from the KB
5. **Safety First**: SQL validation is the final gate before execution
6. **Pipeline Boundaries**: Clear separation between KB, Query Planning, and SQL Generation pipelines
