"""
vector_store/persistence.py
===========================
Persistent storage for vector index documents.

The knowledge base and glossary remain the source of truth. This module only
persists the derived search layer so it can be reused across CLI sessions.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from utils.logger import get_logger
from vector_store.embedding_service import EmbeddingService

logger = get_logger()

INDEX_SCHEMA_VERSION = 1


class VectorIndexPersistence:
    """Persist and validate vector index documents on disk."""

    def __init__(self, index_dir: str | os.PathLike[str] | None = None):
        configured_dir = index_dir or os.getenv("VECTOR_INDEX_DIR") or os.path.join("vector_store", "index")
        self.index_dir = Path(configured_dir)
        self.manifest_path = self.index_dir / "manifest.json"
        self.documents_path = self.index_dir / "documents.json"

    def build_signature(
        self,
        knowledge_base: dict[str, Any] | None,
        glossary: dict[str, Any] | None,
        embedding_service: EmbeddingService,
        source_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Build a deterministic signature for the current KB, glossary, and embedding backend."""
        embedding_status = embedding_service.get_status()
        source_context = source_context or {}
        return {
            "schema_version": INDEX_SCHEMA_VERSION,
            "knowledge_base_hash": self._content_hash(knowledge_base or {}),
            "glossary_hash": self._content_hash(glossary or {}),
            "database_name": str(source_context.get("database_name", "") or ""),
            "database_type": str(source_context.get("database_type", "") or ""),
            "schema_fingerprint": str(source_context.get("schema_fingerprint", "") or ""),
            "embedding": {
                "backend": embedding_status.get("backend"),
                "model": embedding_status.get("model"),
                "dimension": embedding_status.get("dimension"),
                "configured_backend": embedding_status.get("configured_backend"),
                "configured_model": embedding_status.get("configured_model"),
            },
        }

    def inspect_index(
        self,
        knowledge_base: dict[str, Any] | None,
        glossary: dict[str, Any] | None,
        embedding_service: EmbeddingService,
        source_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Inspect whether the persisted index is fresh enough to reuse."""
        signature = self.build_signature(knowledge_base, glossary, embedding_service, source_context=source_context)
        details = self._status_template(signature)

        manifest_exists = self.manifest_path.exists()
        documents_exists = self.documents_path.exists()
        details["exists"] = manifest_exists and documents_exists

        if not manifest_exists or not documents_exists:
            missing = []
            if not manifest_exists:
                missing.append("manifest")
            if not documents_exists:
                missing.append("documents")
            details["stale_reason"] = f"missing persisted index file(s): {', '.join(missing)}"
            return details

        try:
            manifest = self._read_json(self.manifest_path)
        except Exception as exc:
            details["exists"] = True
            details["stale_reason"] = f"manifest unreadable: {exc}"
            return details

        details["manifest"] = manifest
        manifest_embedding = manifest.get("embedding", {})
        details["document_count"] = int(manifest.get("document_count", 0) or 0)

        if int(manifest.get("schema_version", 0) or 0) != INDEX_SCHEMA_VERSION:
            details["stale_reason"] = "index schema version changed"
            return details

        expected_database_name = str(signature.get("database_name", "") or "")
        manifest_database_name = str(manifest.get("database_name", "") or "")
        if expected_database_name and manifest_database_name and manifest_database_name != expected_database_name:
            details["stale_reason"] = "database name changed"
            return details

        expected_schema_fingerprint = str(signature.get("schema_fingerprint", "") or "")
        manifest_schema_fingerprint = str(manifest.get("schema_fingerprint", "") or "")
        if expected_schema_fingerprint and manifest_schema_fingerprint and manifest_schema_fingerprint != expected_schema_fingerprint:
            details["stale_reason"] = "schema fingerprint changed"
            return details

        if manifest.get("knowledge_base_hash") != signature["knowledge_base_hash"]:
            details["stale_reason"] = "knowledge base hash changed"
            return details

        if manifest.get("glossary_hash") != signature["glossary_hash"]:
            details["stale_reason"] = "business glossary hash changed"
            return details

        if manifest_embedding.get("backend") != signature["embedding"]["backend"]:
            details["stale_reason"] = "embedding backend changed"
            return details

        if manifest_embedding.get("model") != signature["embedding"]["model"]:
            details["stale_reason"] = "embedding model changed"
            return details

        if int(manifest_embedding.get("dimension", 0) or 0) != int(signature["embedding"]["dimension"] or 0):
            details["stale_reason"] = "embedding dimension changed"
            return details

        details["fresh"] = True
        details["is_fresh"] = True
        details["stale_reason"] = ""
        return details

    def load_documents(
        self,
        knowledge_base: dict[str, Any] | None,
        glossary: dict[str, Any] | None,
        embedding_service: EmbeddingService,
        source_context: dict[str, Any] | None = None,
    ) -> tuple[bool, str, list[dict[str, Any]], dict[str, Any]]:
        """Load persisted documents when the on-disk index is fresh and valid."""
        details = self.inspect_index(knowledge_base, glossary, embedding_service, source_context=source_context)
        if not details.get("fresh"):
            return False, details.get("stale_reason", "index is stale"), [], details

        try:
            raw_documents = self._read_json(self.documents_path)
            if not isinstance(raw_documents, list):
                raise ValueError("documents payload must be a list")

            expected_dimension = int(embedding_service.get_dimension() or 0)
            documents: list[dict[str, Any]] = []
            for item in raw_documents:
                if not isinstance(item, dict):
                    raise ValueError("document entry must be an object")

                text = str(item.get("text", ""))
                metadata = item.get("metadata", {})
                embedding = item.get("embedding", [])
                tokenized_text = item.get("tokenized_text", [])

                if not isinstance(metadata, dict):
                    raise ValueError("document metadata must be an object")
                if not isinstance(embedding, list):
                    raise ValueError("document embedding must be a list")
                if expected_dimension and len(embedding) != expected_dimension:
                    raise ValueError(
                        f"document embedding dimension mismatch: expected {expected_dimension}, got {len(embedding)}"
                    )
                if not isinstance(tokenized_text, list):
                    tokenized_text = embedding_service.tokenize(text)

                documents.append(
                    {
                        "text": text,
                        "metadata": metadata,
                        "embedding": embedding,
                        "tokenized_text": tokenized_text,
                    }
                )

            details["loaded_from_disk"] = True
            details["source"] = "disk"
            details["document_count"] = len(documents)
            details["persisted"] = True
            return True, f"Loaded {len(documents)} vector documents from disk", documents, details
        except Exception as exc:
            details["fresh"] = False
            details["is_fresh"] = False
            details["loaded_from_disk"] = False
            details["source"] = "rebuild_required"
            details["stale_reason"] = f"persisted index unreadable: {exc}"
            return False, details["stale_reason"], [], details

    def save_documents(
        self,
        documents: list[dict[str, Any]],
        knowledge_base: dict[str, Any] | None,
        glossary: dict[str, Any] | None,
        embedding_service: EmbeddingService,
        source_context: dict[str, Any] | None = None,
    ) -> tuple[bool, str, dict[str, Any]]:
        """Persist vector documents and the manifest to disk."""
        signature = self.build_signature(knowledge_base, glossary, embedding_service, source_context=source_context)
        details = self._status_template(signature)
        details["source"] = "rebuilt"
        details["rebuilt"] = True
        details["document_count"] = len(documents)

        manifest = {
            **signature,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "built_at": datetime.now(timezone.utc).isoformat(),
            "document_count": len(documents),
        }

        serializable_documents = []
        for document in documents:
            serializable_documents.append(
                {
                    "text": str(document.get("text", "")),
                    "metadata": document.get("metadata", {}),
                    "embedding": document.get("embedding", []),
                    "tokenized_text": document.get("tokenized_text", []),
                }
            )

        try:
            self.index_dir.mkdir(parents=True, exist_ok=True)
            self._write_json_atomic(self.documents_path, serializable_documents)
            self._write_json_atomic(self.manifest_path, manifest)
            details["fresh"] = True
            details["is_fresh"] = True
            details["persisted"] = True
            details["manifest"] = manifest
            return True, f"Saved {len(documents)} vector documents to disk", details
        except Exception as exc:
            details["persisted"] = False
            details["persistence_error"] = str(exc)
            details["stale_reason"] = f"persisted index save failed: {exc}"
            return False, details["stale_reason"], details

    def _status_template(self, signature: dict[str, Any]) -> dict[str, Any]:
        embedding_signature = signature.get("embedding", {})
        return {
            "index_dir": str(self.index_dir),
            "manifest_path": str(self.manifest_path),
            "documents_path": str(self.documents_path),
            "exists": False,
            "fresh": False,
            "is_fresh": False,
            "source": "none",
            "loaded_from_disk": False,
            "rebuilt": False,
            "persisted": False,
            "document_count": 0,
            "stale_reason": "",
            "persistence_error": "",
            "knowledge_base_hash": signature.get("knowledge_base_hash"),
            "glossary_hash": signature.get("glossary_hash"),
            "database_name": signature.get("database_name"),
            "database_type": signature.get("database_type"),
            "schema_fingerprint": signature.get("schema_fingerprint"),
            "embedding_backend": embedding_signature.get("backend"),
            "embedding_model": embedding_signature.get("model"),
            "embedding_dimension": embedding_signature.get("dimension"),
        }

    def _content_hash(self, data: Any) -> str:
        serialized = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    def _read_json(self, path: Path) -> Any:
        return json.loads(path.read_text(encoding="utf-8"))

    def _write_json_atomic(self, path: Path, data: Any) -> None:
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        tmp_path.write_text(
            json.dumps(data, indent=2, sort_keys=True, ensure_ascii=True),
            encoding="utf-8",
        )
        tmp_path.replace(path)
