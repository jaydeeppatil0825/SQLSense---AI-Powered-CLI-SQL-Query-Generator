"""
vector_store/persistence.py
===========================
Persistent storage for vector index documents.

The knowledge base and glossary remain the source of truth. This module only
persists the derived search layer so it can be reused across CLI sessions.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from utils.logger import get_logger
from kb_pipeline.vector.embedding_service import EmbeddingService

logger = get_logger()

INDEX_SCHEMA_VERSION = 2


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
        db_engine = str(source_context.get("db_engine", source_context.get("database_type", "")) or "")
        db_name = str(source_context.get("db_name", source_context.get("database_name", "")) or "")
        schema_hash = str(source_context.get("schema_hash", source_context.get("schema_fingerprint", "")) or "")
        return {
            "schema_version": INDEX_SCHEMA_VERSION,
            "vector_index_version": INDEX_SCHEMA_VERSION,
            "knowledge_base_hash": self._content_hash(knowledge_base or {}),
            "glossary_hash": self._content_hash(glossary or {}),
            "db_engine": db_engine,
            "db_host": str(source_context.get("db_host", "") or ""),
            "db_port": str(source_context.get("db_port", "") or ""),
            "db_name": db_name,
            "schema_hash": schema_hash,
            "database_name": db_name,
            "database_type": db_engine,
            "schema_fingerprint": schema_hash,
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

        manifest_version = int(
            manifest.get("vector_index_version", manifest.get("schema_version", 0)) or 0
        )
        if manifest_version != INDEX_SCHEMA_VERSION:
            details["stale_reason"] = "index schema version changed"
            return details

        for key, alias, reason in (
            ("db_engine", "database_type", "database engine changed"),
            ("db_host", "", "database host changed"),
            ("db_port", "", "database port changed"),
            ("db_name", "database_name", "database name changed"),
            ("schema_hash", "schema_fingerprint", "schema hash changed"),
        ):
            expected_value = str(signature.get(key, "") or "")
            manifest_value = str(manifest.get(key, manifest.get(alias, "")) or "")
            if expected_value and manifest_value and manifest_value != expected_value:
                details["stale_reason"] = reason
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
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "built_at": datetime.now(timezone.utc).isoformat(),
            "document_count": len(documents),
            "storage_backend": "json_files",
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
            "db_engine": signature.get("db_engine"),
            "db_host": signature.get("db_host"),
            "db_port": signature.get("db_port"),
            "db_name": signature.get("db_name"),
            "schema_hash": signature.get("schema_hash"),
            "database_name": signature.get("database_name"),
            "database_type": signature.get("database_type"),
            "schema_fingerprint": signature.get("schema_fingerprint"),
            "vector_index_version": signature.get("vector_index_version"),
            "embedding_backend": embedding_signature.get("backend"),
            "embedding_model": embedding_signature.get("model"),
            "embedding_dimension": embedding_signature.get("dimension"),
        }

    def _content_hash(self, data: Any) -> str:
        serialized = json.dumps(
            self._json_safe(data),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    def _read_json(self, path: Path) -> Any:
        return json.loads(path.read_text(encoding="utf-8"))

    def _write_json_atomic(self, path: Path, data: Any) -> None:
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        tmp_path.write_text(
            json.dumps(self._json_safe(data), indent=2, sort_keys=True, ensure_ascii=True),
            encoding="utf-8",
        )
        tmp_path.replace(path)

    def _json_safe(self, value: Any) -> Any:
        """Recursively convert runtime metadata into JSON-safe structures."""
        if value is None or isinstance(value, (str, int, float, bool)):
            return value

        if isinstance(value, (date, datetime)):
            return value.isoformat()

        if isinstance(value, Decimal):
            return str(value)

        if isinstance(value, bytes):
            try:
                return value.decode("utf-8")
            except UnicodeDecodeError:
                return value.hex()

        if isinstance(value, dict):
            return {
                str(key): self._json_safe(item)
                for key, item in value.items()
            }

        if isinstance(value, (list, tuple)):
            return [self._json_safe(item) for item in value]

        if isinstance(value, set):
            sanitized_items = [self._json_safe(item) for item in value]
            return sorted(
                sanitized_items,
                key=lambda item: json.dumps(item, sort_keys=True, ensure_ascii=True),
            )

        return str(value)
