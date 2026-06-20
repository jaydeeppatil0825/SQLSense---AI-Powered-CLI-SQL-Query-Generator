"""
semantic/knowledge_base_builder.py
===================================
Orchestrates the three-step knowledge base build pipeline:

  Step 1 — Schema extraction   (db/schema_reader.py)
  Step 2 — Data profiling      (db/data_profiler.py)
  Step 3 — Semantic mapping    (semantic/semantic_mapper.py)

Each step prints a progress message so the user can see what is happening.
Any exception from a step is re-raised immediately so main.py can report
which step failed without writing a partial file.
"""

from __future__ import annotations

from kb_pipeline.data_profiler import profile_database_data
from kb_pipeline.schema_reader import read_database_schema
from kb_pipeline.schema_facts import enrich_knowledge_base_schema_facts
from kb_pipeline.semantic_mapper import add_semantic_mapping


def build_knowledge_base(engine) -> dict:
    """
    Build a fully-enriched knowledge base dictionary from a live database.

    Runs three steps in sequence. Progress is printed after each step.
    Raises immediately on any failure so the caller never writes partial data.

    Args:
        engine: A connected SQLAlchemy engine (from SessionState).

    Returns:
        A dictionary keyed by table name, each containing columns
        (with type, nullable, semantic_type, profiling stats),
        primary_keys, foreign_keys, and row_count.

    Raises:
        RuntimeError: If schema reflection fails.
        Exception:    If profiling or semantic mapping fails.
    """
    # ── Step 1: Extract schema ───────────────────────────────────────────────
    schema_data = read_database_schema(engine)
    print("  [OK] Schema extracted successfully.")

    # ── Step 2: Profile data ─────────────────────────────────────────────────
    profiled_data = profile_database_data(schema_data, engine)
    print("  [OK] Data profiling completed successfully.")

    # ── Step 3: Add semantic types ───────────────────────────────────────────
    knowledge_base = add_semantic_mapping(profiled_data)
    print("  [OK] Semantic mapping completed successfully.")

    knowledge_base = enrich_knowledge_base_schema_facts(knowledge_base)
    print("  [OK] Schema fact enrichment and relationships completed successfully.")

    return knowledge_base
