"""
Persistent ChromaDB semantic retrieval backend for KB-derived vector documents.

ChromaDB is used only as a retrieval layer over the dynamic knowledge base and
business glossary. It never invents tables, columns, or business meaning, and
it never calls AI at runtime.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import os
from pathlib import Path
import re
from typing import Any, Optional

from kb_pipeline.vector.embedding_service import EmbeddingService
from kb_pipeline.vector.retriever import VectorRetriever
from utils.logger import get_logger

logger = get_logger()

_JSON_METADATA_FIELDS = {
    "table_names",
    "mapped_columns",
    "business_terms",
    "primary_terms",
    "related_terms",
    "example_questions",
    "column_names",
    "sample_values",
    "profile_facts",
    "planner_roles",
    "sources",
    "evidence",
    "evidence_reasons",
}


def _safe_text(value: Any) -> str:
    return str(value or "").strip()


def _safe_collection_fragment(value: str, fallback: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "_", _safe_text(value).lower()).strip("_")
    if not cleaned:
        return fallback
    return cleaned[:24]


def _score_from_distance(distance: Any) -> float:
    try:
        numeric = float(distance)
    except (TypeError, ValueError):
        return 0.0
    if 0.0 <= numeric <= 1.0:
        return round(max(0.0, 1.0 - numeric), 4)
    return round(1.0 / (1.0 + max(numeric, 0.0)), 4)


class ChromaStore:
    """Persistent ChromaDB store for KB/glossary semantic documents."""

    def __init__(
        self,
        embedding_service: Optional[EmbeddingService] = None,
        persist_dir: str | os.PathLike[str] | None = None,
    ):
        self.embedding_service = embedding_service or EmbeddingService()
        configured_dir = persist_dir or os.getenv("CHROMA_INDEX_DIR") or os.path.join("vector_store", "chroma")
        self.persist_dir = Path(configured_dir)
        self._module = None
        self._client = None
        self._collection = None
        self._collection_name = ""
        self._available = False
        self._ready = False
        self._init_error = ""
        self._last_search_info: dict[str, Any] = {}
        self._document_count = 0
        self._source_context: dict[str, Any] = {}
        self._init_client()

    def _init_client(self) -> None:
        try:
            # Chroma telemetry is non-essential for SQLSense and older Chroma
            # releases are incompatible with newer PostHog capture signatures.
            os.environ["ANONYMIZED_TELEMETRY"] = "False"
            module = importlib.import_module("chromadb")
            persistent_client = getattr(module, "PersistentClient", None)
            if persistent_client is None:
                raise ImportError("chromadb.PersistentClient is unavailable")
            self.persist_dir.mkdir(parents=True, exist_ok=True)
            self._module = module
            client_kwargs: dict[str, Any] = {"path": str(self.persist_dir)}
            try:
                config_module = importlib.import_module("chromadb.config")
                settings_type = getattr(config_module, "Settings", None)
                if settings_type is not None:
                    client_kwargs["settings"] = settings_type(anonymized_telemetry=False)
            except (ImportError, AttributeError, TypeError, ValueError) as exc:
                logger.debug(f"Chroma telemetry settings are unavailable; using environment configuration: {exc}")

            try:
                self._client = persistent_client(**client_kwargs)
            except TypeError as exc:
                if "settings" not in client_kwargs or "settings" not in str(exc).lower():
                    raise
                self._client = persistent_client(path=str(self.persist_dir))
            self._available = True
            self._init_error = ""
        except Exception as exc:
            self._module = None
            self._client = None
            self._available = False
            self._ready = False
            self._init_error = str(exc)
            logger.info(f"ChromaDB unavailable, using fallback vector retriever: {exc}")

    def is_available(self) -> bool:
        return self._available and self._client is not None

    def is_ready(self) -> bool:
        return self.is_available() and self._ready and self._collection is not None

    def _current_collection_name(self, source_context: dict[str, Any]) -> str:
        db_engine = _safe_collection_fragment(source_context.get("db_engine", source_context.get("database_type", "")), "db")
        db_name = _safe_collection_fragment(source_context.get("db_name", source_context.get("database_name", "")), "database")
        host = _safe_text(source_context.get("db_host", ""))
        port = _safe_text(source_context.get("db_port", ""))
        schema_hash = _safe_text(source_context.get("schema_hash", source_context.get("schema_fingerprint", "")))
        schema_fragment = (schema_hash or "schema")[:12].lower()
        identity_hash = hashlib.sha1(
            json.dumps(
                {
                    "db_engine": _safe_text(source_context.get("db_engine", source_context.get("database_type", ""))),
                    "db_host": host,
                    "db_port": port,
                    "db_name": _safe_text(source_context.get("db_name", source_context.get("database_name", ""))),
                    "schema_hash": schema_hash,
                },
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()[:8]
        return f"sqlsense_{db_engine}_{db_name}_{schema_fragment}_{identity_hash}"[:63]

    def _collection_exists(self, collection_name: str) -> bool:
        if not self.is_available():
            return False
        try:
            collections = self._client.list_collections()
        except Exception:
            return False
        for collection in collections:
            name = getattr(collection, "name", None)
            if name == collection_name:
                return True
        return False

    def _sanitize_metadata(self, metadata: dict[str, Any]) -> dict[str, Any]:
        sanitized: dict[str, Any] = {}
        for key, value in dict(metadata or {}).items():
            if value is None:
                continue
            if isinstance(value, bool):
                sanitized[str(key)] = value
                continue
            if isinstance(value, (int, float, str)):
                sanitized[str(key)] = value
                continue
            if str(key) in _JSON_METADATA_FIELDS:
                sanitized[str(key)] = json.dumps(value, ensure_ascii=True, sort_keys=True)
                continue
            sanitized[str(key)] = _safe_text(value)
        sanitized["doc_type"] = _safe_text(metadata.get("type") or metadata.get("source_type"))
        return sanitized

    def _restore_metadata(self, metadata: dict[str, Any]) -> dict[str, Any]:
        restored = dict(metadata or {})
        for key in _JSON_METADATA_FIELDS:
            raw = restored.get(key)
            if isinstance(raw, str):
                try:
                    restored[key] = json.loads(raw)
                except Exception:
                    continue
        if "doc_type" in restored and "type" not in restored:
            restored["type"] = restored["doc_type"]
        return restored

    def _document_id(self, document: dict[str, Any]) -> str:
        metadata = dict(document.get("metadata", {}))
        identity = {
            "doc_type": metadata.get("type"),
            "table_name": metadata.get("table_name"),
            "column_name": metadata.get("column_name"),
            "term": metadata.get("term"),
            "from_table": metadata.get("from_table"),
            "to_table": metadata.get("to_table"),
            "text": document.get("text", ""),
        }
        return hashlib.sha1(json.dumps(identity, sort_keys=True, ensure_ascii=True).encode("utf-8")).hexdigest()

    def load_current_collection(
        self,
        source_context: dict[str, Any],
    ) -> tuple[bool, str, dict[str, Any]]:
        """Bind the current db/schema collection if it already exists."""
        details = self.get_status()
        details.update(
            {
                "collection_name": "",
                "loaded_from_disk": False,
                "source": "none",
                "db_engine": _safe_text(source_context.get("db_engine", source_context.get("database_type", ""))),
                "db_host": _safe_text(source_context.get("db_host", "")),
                "db_port": _safe_text(source_context.get("db_port", "")),
                "db_name": _safe_text(source_context.get("db_name", source_context.get("database_name", ""))),
                "schema_hash": _safe_text(source_context.get("schema_hash", source_context.get("schema_fingerprint", ""))),
            }
        )
        if not self.is_available():
            details["error"] = self._init_error or "chromadb unavailable"
            return False, details["error"], details

        collection_name = self._current_collection_name(source_context)
        details["collection_name"] = collection_name
        if not self._collection_exists(collection_name):
            details["error"] = "chroma collection not found"
            return False, details["error"], details

        try:
            self._collection = self._client.get_collection(name=collection_name)
            self._collection_name = collection_name
            self._ready = True
            self._source_context = dict(source_context or {})
            self._document_count = int(self._collection.count())
            details["ready"] = True
            details["loaded_from_disk"] = True
            details["source"] = "chroma"
            details["document_count"] = self._document_count
            return True, f"Loaded Chroma collection '{collection_name}'", details
        except Exception as exc:
            self._ready = False
            self._collection = None
            details["error"] = str(exc)
            return False, details["error"], details

    def build_or_refresh_chroma_index(
        self,
        documents: list[dict[str, Any]],
        source_context: dict[str, Any],
    ) -> tuple[bool, str, dict[str, Any]]:
        """Replace the current db/schema collection with fresh KB-derived documents."""
        details = self.get_status()
        details.update(
            {
                "source": "chroma",
                "loaded_from_disk": False,
                "db_engine": _safe_text(source_context.get("db_engine", source_context.get("database_type", ""))),
                "db_host": _safe_text(source_context.get("db_host", "")),
                "db_port": _safe_text(source_context.get("db_port", "")),
                "db_name": _safe_text(source_context.get("db_name", source_context.get("database_name", ""))),
                "schema_hash": _safe_text(source_context.get("schema_hash", source_context.get("schema_fingerprint", ""))),
            }
        )
        if not self.is_available():
            details["error"] = self._init_error or "chromadb unavailable"
            return False, details["error"], details

        collection_name = self._current_collection_name(source_context)
        details["collection_name"] = collection_name
        try:
            if self._collection_exists(collection_name):
                self._client.delete_collection(name=collection_name)

            collection = self._client.get_or_create_collection(
                name=collection_name,
                metadata={
                    "db_engine": details["db_engine"],
                    "db_host": details["db_host"],
                    "db_port": details["db_port"],
                    "db_name": details["db_name"],
                    "schema_hash": details["schema_hash"],
                },
            )
            if documents:
                collection.upsert(
                    ids=[self._document_id(document) for document in documents],
                    documents=[str(document.get("text", "")) for document in documents],
                    metadatas=[self._sanitize_metadata(document.get("metadata", {})) for document in documents],
                    embeddings=[list(document.get("embedding", [])) for document in documents],
                )

            self._collection = collection
            self._collection_name = collection_name
            self._document_count = len(documents)
            self._source_context = dict(source_context or {})
            self._ready = True
            details["ready"] = True
            details["document_count"] = len(documents)
            return True, f"Built Chroma collection '{collection_name}' with {len(documents)} documents", details
        except Exception as exc:
            self._collection = None
            self._collection_name = ""
            self._document_count = 0
            self._ready = False
            details["error"] = str(exc)
            return False, str(exc), details

    def _query(
        self,
        query: str,
        *,
        top_k: int = 10,
        doc_type: str | None = None,
    ) -> list[dict[str, Any]]:
        if not self.is_ready():
            return []

        query_embedding = self.embedding_service.embed(query)
        where = {"doc_type": doc_type} if doc_type else None
        results = self._collection.query(
            query_embeddings=[query_embedding],
            n_results=max(int(top_k or 0), 1),
            where=where,
        )
        documents = list((results.get("documents") or [[]])[0] or [])
        metadatas = list((results.get("metadatas") or [[]])[0] or [])
        distances = list((results.get("distances") or [[]])[0] or [])

        response: list[dict[str, Any]] = []
        for index, text in enumerate(documents):
            metadata = self._restore_metadata(metadatas[index] if index < len(metadatas) else {})
            distance = distances[index] if index < len(distances) else None
            response.append(
                {
                    "document": {"text": text, "metadata": metadata},
                    "score": _score_from_distance(distance),
                    "metadata": metadata,
                    "text": text,
                }
            )

        self._last_search_info = {
            "query": query,
            "doc_type": doc_type or "all",
            "result_count": len(response),
            "backend": "chroma",
            "model": self.embedding_service.get_model_name(),
            "fallback_used": self.embedding_service.is_fallback_mode(),
            "collection_name": self._collection_name,
        }
        return response

    def search(self, query: str, top_k: int = 10, doc_type: str | None = None) -> list[dict[str, Any]]:
        """Search Chroma documents for the current db/schema collection."""
        return self._query(query, top_k=top_k, doc_type=doc_type)

    def search_tables(self, query: str, top_k: int = 5) -> list[str]:
        """Search table and glossary docs only for simple browse/count table candidates."""
        results = self._query(query, top_k=max(top_k, 6), doc_type="table")
        results.extend(self._query(query, top_k=max(top_k, 6), doc_type="glossary"))
        ranked: list[tuple[str, float]] = []
        seen: set[str] = set()
        for result in results:
            if float(result.get("score") or 0.0) < 0.35:
                continue
            metadata = result.get("metadata", {})
            doc_type = metadata.get("type")
            candidate_tables: list[str] = []
            if doc_type == "table" and metadata.get("table_name"):
                candidate_tables.append(metadata["table_name"])
            elif doc_type == "glossary":
                for table_name in metadata.get("table_names", []) or []:
                    if table_name:
                        candidate_tables.append(table_name)
            for table_name in candidate_tables:
                if table_name in seen:
                    continue
                seen.add(table_name)
                ranked.append((table_name, float(result.get("score") or 0.0)))
                if len(ranked) >= top_k:
                    return [name for name, _ in ranked]
        return [name for name, _ in ranked]

    def search_columns(self, query: str, top_k: int = 10) -> list[dict[str, Any]]:
        return [self._result_entry(result) for result in self._query(query, top_k=top_k, doc_type="column")]

    def search_glossary(self, query: str, top_k: int = 10) -> list[dict[str, Any]]:
        return [self._result_entry(result) for result in self._query(query, top_k=top_k, doc_type="glossary")]

    def search_relationships(self, query: str, top_k: int = 10) -> list[dict[str, Any]]:
        return [self._result_entry(result) for result in self._query(query, top_k=top_k, doc_type="relationship")]

    def _result_entry(self, result: dict[str, Any]) -> dict[str, Any]:
        metadata = dict(result.get("metadata", {}))
        return {
            **metadata,
            "score": result.get("score", 0.0),
            "text": result.get("text", ""),
        }

    def get_relevant_tables(self, query: str, top_k: int = 5) -> list[str]:
        return self.search_tables(query, top_k=top_k)

    def get_relevant_table_details(self, query: str, top_k: int = 5) -> list[dict[str, Any]]:
        table_results = [self._result_entry(result) for result in self._query(query, top_k=top_k, doc_type="table")]
        seen: set[str] = set()
        details: list[dict[str, Any]] = []
        for entry in table_results:
            table_name = entry.get("table_name")
            if not table_name or table_name in seen:
                continue
            seen.add(table_name)
            details.append(entry)
        return details

    def get_relevant_columns(self, query: str, top_k: int = 10) -> list[dict[str, Any]]:
        seen: set[tuple[str, str]] = set()
        columns: list[dict[str, Any]] = []
        for entry in self.search_columns(query, top_k=top_k):
            key = (_safe_text(entry.get("table_name")), _safe_text(entry.get("column_name")))
            if not all(key) or key in seen:
                continue
            seen.add(key)
            columns.append(entry)
        return columns

    def get_relevant_metrics(self, query: str, top_k: int = 8) -> list[dict[str, Any]]:
        seen: set[tuple[str, str]] = set()
        metrics: list[dict[str, Any]] = []
        for entry in [self._result_entry(result) for result in self._query(query, top_k=top_k, doc_type="measure")]:
            key = (_safe_text(entry.get("table_name")), _safe_text(entry.get("column_name")))
            if not all(key) or key in seen:
                continue
            seen.add(key)
            metrics.append(entry)
        return metrics

    def get_relevant_dimensions(self, query: str, top_k: int = 8) -> list[dict[str, Any]]:
        seen: set[tuple[str, str]] = set()
        dimensions: list[dict[str, Any]] = []
        for entry in [self._result_entry(result) for result in self._query(query, top_k=top_k, doc_type="dimension")]:
            key = (_safe_text(entry.get("table_name")), _safe_text(entry.get("column_name")))
            if not all(key) or key in seen:
                continue
            seen.add(key)
            dimensions.append(entry)
        return dimensions

    def get_relevant_dates(self, query: str, top_k: int = 8) -> list[dict[str, Any]]:
        seen: set[tuple[str, str]] = set()
        dates: list[dict[str, Any]] = []
        for entry in [self._result_entry(result) for result in self._query(query, top_k=top_k, doc_type="date")]:
            key = (_safe_text(entry.get("table_name")), _safe_text(entry.get("column_name")))
            if not all(key) or key in seen:
                continue
            seen.add(key)
            dates.append(entry)
        return dates

    def get_relevant_glossary_terms(self, query: str, top_k: int = 10) -> list[dict[str, Any]]:
        seen: set[str] = set()
        terms: list[dict[str, Any]] = []
        for entry in self.search_glossary(query, top_k=top_k):
            term = _safe_text(entry.get("term"))
            if not term or term in seen:
                continue
            seen.add(term)
            terms.append(entry)
        return terms

    def get_relevant_relationships(self, query: str, top_k: int = 10) -> list[dict[str, Any]]:
        seen: set[tuple[str, str, str, str]] = set()
        relationships: list[dict[str, Any]] = []
        for entry in self.search_relationships(query, top_k=top_k):
            if entry.get("safe_for_planner") is False:
                continue
            signature = (
                _safe_text(entry.get("from_table")),
                _safe_text(entry.get("from_column")),
                _safe_text(entry.get("to_table")),
                _safe_text(entry.get("to_column")),
            )
            if not all(signature) or signature in seen:
                continue
            seen.add(signature)
            relationships.append(entry)
        return relationships

    def get_relevant_semantic_descriptions(self, query: str, top_k: int = 8) -> list[dict[str, Any]]:
        descriptions: list[dict[str, Any]] = []
        for result in self._query(query, top_k=top_k):
            metadata = result.get("metadata", {})
            description = metadata.get("description") or metadata.get("business_purpose") or result.get("text", "")
            if not description:
                continue
            descriptions.append(
                {
                    "type": metadata.get("type"),
                    "table_name": metadata.get("table_name"),
                    "column_name": metadata.get("column_name"),
                    "term": metadata.get("term"),
                    "description": description,
                    "score": result.get("score", 0.0),
                }
            )
        return descriptions

    def get_relevant_profiling_hints(self, query: str, top_k: int = 8) -> list[dict[str, Any]]:
        hints: list[dict[str, Any]] = []
        for result in self._query(query, top_k=top_k):
            metadata = result.get("metadata", {})
            hint = {
                "type": metadata.get("type"),
                "table_name": metadata.get("table_name"),
                "column_name": metadata.get("column_name"),
                "row_count": metadata.get("row_count"),
                "sample_values": metadata.get("sample_values", []),
                "nullable": metadata.get("nullable"),
                "column_type": metadata.get("column_type"),
                "score": result.get("score", 0.0),
            }
            if not any(hint.get(key) not in (None, "", [], {}) for key in ("row_count", "sample_values", "nullable", "column_type")):
                continue
            hints.append(hint)
        return hints

    def get_normalized_evidence_package(self, query: str, top_k: int = 8) -> dict[str, Any]:
        candidate_tables = self.get_relevant_table_details(query, top_k=max(top_k, 6))
        candidate_columns = self.get_relevant_columns(query, top_k=max(top_k + 2, 10))
        candidate_metrics = self.get_relevant_metrics(query, top_k=max(top_k, 6))
        candidate_dimensions = self.get_relevant_dimensions(query, top_k=max(top_k, 6))
        candidate_dates = self.get_relevant_dates(query, top_k=max(top_k, 4))
        relationships = self.get_relevant_relationships(query, top_k=max(top_k, 6))
        glossary_matches = self.get_relevant_glossary_terms(query, top_k=max(top_k, 6))

        def _top_score(entries: list[dict[str, Any]]) -> float:
            return round(max((float(entry.get("score") or 0.0) for entry in entries), default=0.0), 4)

        def _close_candidates(entries: list[dict[str, Any]], keys: tuple[str, ...]) -> list[dict[str, Any]]:
            if len(entries) < 2:
                return []
            top_score = float(entries[0].get("score") or 0.0)
            close = []
            for entry in entries:
                if top_score - float(entry.get("score") or 0.0) > 0.08:
                    continue
                close.append({key: entry.get(key) for key in keys if entry.get(key)})
            return close[1:] if len(close) > 1 else []

        evidence_scores = {
            "tables": _top_score(candidate_tables),
            "columns": _top_score(candidate_columns),
            "metrics": _top_score(candidate_metrics),
            "dimensions": _top_score(candidate_dimensions),
            "dates": _top_score(candidate_dates),
            "relationships": _top_score(relationships),
            "glossary": _top_score(glossary_matches),
        }
        evidence_scores["overall"] = round(max(evidence_scores.values()) if evidence_scores else 0.0, 4)

        return {
            "candidate_tables": candidate_tables,
            "candidate_columns": candidate_columns,
            "candidate_metrics": candidate_metrics,
            "candidate_dimensions": candidate_dimensions,
            "candidate_dates": candidate_dates,
            "relationships": relationships,
            "glossary_matches": glossary_matches,
            "evidence_scores": evidence_scores,
            "source_metadata": {
                **self.get_status(),
                "query": query,
            },
            "ambiguity_candidates": {
                "tables": _close_candidates(candidate_tables, ("table_name",)),
                "metrics": _close_candidates(candidate_metrics, ("table_name", "column_name")),
                "dimensions": _close_candidates(candidate_dimensions, ("table_name", "column_name")),
            },
            "missing_evidence_indicators": {
                "tables_missing": not candidate_tables,
                "columns_missing": not candidate_columns,
                "metrics_missing": not candidate_metrics,
                "dimensions_missing": not candidate_dimensions,
                "dates_missing": not candidate_dates,
                "relationships_missing": not relationships,
                "glossary_missing": not glossary_matches,
            },
            "retrieval_sources": ["vector"] if any(
                [
                    candidate_tables,
                    candidate_columns,
                    candidate_metrics,
                    candidate_dimensions,
                    candidate_dates,
                    relationships,
                    glossary_matches,
                ]
            ) else [],
        }

    def get_status(self) -> dict[str, Any]:
        return {
            "backend": "chroma" if self.is_ready() else "chroma_unavailable",
            "ready": self.is_ready(),
            "available": self.is_available(),
            "persist_dir": str(self.persist_dir),
            "collection_name": self._collection_name,
            "document_count": self._document_count,
            "embedding": self.embedding_service.get_status(),
            "last_search": dict(self._last_search_info),
            "init_error": self._init_error,
            "db_name": _safe_text(self._source_context.get("db_name", self._source_context.get("database_name", ""))),
            "schema_hash": _safe_text(self._source_context.get("schema_hash", self._source_context.get("schema_fingerprint", ""))),
        }


class HybridVectorRetriever:
    """Use Chroma as primary retrieval and the existing vector retriever as fallback."""

    def __init__(
        self,
        chroma_store: Optional[ChromaStore] = None,
        fallback_retriever: Optional[VectorRetriever] = None,
    ):
        self.chroma_store = chroma_store
        self.fallback_retriever = fallback_retriever

    def _dispatch(self, method_name: str, *args: Any, **kwargs: Any) -> Any:
        if self.chroma_store and self.chroma_store.is_ready():
            try:
                return getattr(self.chroma_store, method_name)(*args, **kwargs)
            except Exception as exc:
                logger.warning(f"Chroma retrieval failed for {method_name}; using fallback retriever: {exc}")
        if self.fallback_retriever and hasattr(self.fallback_retriever, method_name):
            return getattr(self.fallback_retriever, method_name)(*args, **kwargs)
        if method_name == "get_status":
            return {}
        return []

    def search(self, query: str, top_k: int = 10, doc_type: str | None = None) -> list[dict[str, Any]]:
        return self._dispatch("search", query, top_k=top_k, doc_type=doc_type)

    def get_relevant_tables(self, query: str, top_k: int = 5) -> list[str]:
        return self._dispatch("get_relevant_tables", query, top_k=top_k)

    def get_relevant_table_details(self, query: str, top_k: int = 5) -> list[dict[str, Any]]:
        return self._dispatch("get_relevant_table_details", query, top_k=top_k)

    def get_relevant_columns(self, query: str, top_k: int = 10) -> list[dict[str, Any]]:
        return self._dispatch("get_relevant_columns", query, top_k=top_k)

    def get_relevant_metrics(self, query: str, top_k: int = 8) -> list[dict[str, Any]]:
        return self._dispatch("get_relevant_metrics", query, top_k=top_k)

    def get_relevant_dimensions(self, query: str, top_k: int = 8) -> list[dict[str, Any]]:
        return self._dispatch("get_relevant_dimensions", query, top_k=top_k)

    def get_relevant_dates(self, query: str, top_k: int = 8) -> list[dict[str, Any]]:
        return self._dispatch("get_relevant_dates", query, top_k=top_k)

    def get_relevant_glossary_terms(self, query: str, top_k: int = 10) -> list[dict[str, Any]]:
        return self._dispatch("get_relevant_glossary_terms", query, top_k=top_k)

    def get_relevant_relationships(self, query: str, top_k: int = 10) -> list[dict[str, Any]]:
        return self._dispatch("get_relevant_relationships", query, top_k=top_k)

    def get_relevant_semantic_descriptions(self, query: str, top_k: int = 8) -> list[dict[str, Any]]:
        return self._dispatch("get_relevant_semantic_descriptions", query, top_k=top_k)

    def get_relevant_profiling_hints(self, query: str, top_k: int = 8) -> list[dict[str, Any]]:
        return self._dispatch("get_relevant_profiling_hints", query, top_k=top_k)

    def get_normalized_evidence_package(self, query: str, top_k: int = 8) -> dict[str, Any]:
        return self._dispatch("get_normalized_evidence_package", query, top_k=top_k)

    def get_status(self) -> dict[str, Any]:
        primary_status = self.chroma_store.get_status() if self.chroma_store else {}
        fallback_status = self.fallback_retriever.get_status() if self.fallback_retriever else {}
        active_backend = "chroma" if primary_status.get("ready") else "fallback"
        return {
            "active_backend": active_backend,
            "index_built": bool(primary_status.get("ready") or fallback_status.get("index_built")),
            "document_count": int(primary_status.get("document_count") or fallback_status.get("document_count") or 0),
            "embedding": primary_status.get("embedding") or fallback_status.get("embedding") or {},
            "last_search": primary_status.get("last_search") or fallback_status.get("last_search") or {},
            "chroma": primary_status,
            "fallback": fallback_status,
        }
