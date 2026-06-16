"""
vector_store/embedding_service.py
=================================
Embedding service for vector retrieval.

Supports a real local sentence-transformers backend when available and falls
back to deterministic token and character n-gram hashing when unavailable.
"""

from typing import List, Any
import hashlib
import os
import re
import sys
from utils.logger import get_logger

logger = get_logger()

_STOP_WORDS = {
    "a", "an", "and", "are", "as", "at", "about", "by", "for", "from", "get",
    "give", "how", "in", "is", "it", "list", "me", "of", "on", "or", "please",
    "return", "show", "tell", "the", "this", "to", "what", "with",
}


class EmbeddingService:
    """Service for generating text embeddings."""
    _STATE_CACHE: dict[tuple[str, str, int | None], dict[str, Any]] = {}
    
    def __init__(self):
        self._model = None
        self._use_fallback = True
        self._backend = (os.getenv("EMBEDDING_BACKEND") or "local").strip().lower() or "local"
        self._configured_model_name = (
            os.getenv("EMBEDDING_MODEL") or "sentence-transformers/all-MiniLM-L6-v2"
        ).strip() or "sentence-transformers/all-MiniLM-L6-v2"
        self._active_backend = "fallback"
        self._active_model_name = "deterministic-hash"
        self._last_init_error = ""
        self._dimension = 384
        self._cache_key = self._build_cache_key()
        cached_state = self._STATE_CACHE.get(self._cache_key)
        if cached_state is not None:
            self._load_cached_state(cached_state)
        else:
            self._init_model()
            self._STATE_CACHE[self._cache_key] = self._snapshot_state()

    def _build_cache_key(self) -> tuple[str, str, int | None]:
        module_obj = sys.modules.get("sentence_transformers")
        module_marker = id(module_obj) if module_obj is not None else None
        return (self._backend, self._configured_model_name, module_marker)

    def _snapshot_state(self) -> dict[str, Any]:
        return {
            "model": self._model,
            "use_fallback": self._use_fallback,
            "active_backend": self._active_backend,
            "active_model_name": self._active_model_name,
            "last_init_error": self._last_init_error,
            "dimension": self._dimension,
        }

    def _load_cached_state(self, state: dict[str, Any]) -> None:
        self._model = state.get("model")
        self._use_fallback = bool(state.get("use_fallback", True))
        self._active_backend = str(state.get("active_backend", "fallback"))
        self._active_model_name = str(state.get("active_model_name", "deterministic-hash"))
        self._last_init_error = str(state.get("last_init_error", ""))
        self._dimension = int(state.get("dimension", 384) or 384)
    
    def _init_model(self):
        """Initialize the embedding model if available."""
        if self._backend != "local":
            self._activate_fallback(f"Unsupported embedding backend '{self._backend}'")
            return

        try:
            from sentence_transformers import SentenceTransformer
        except ImportError:
            self._activate_fallback("sentence-transformers not available")
            return

        try:
            model_name = self._resolved_model_name()
            try:
                self._model = SentenceTransformer(model_name, local_files_only=True)
            except TypeError:
                self._model = SentenceTransformer(model_name)
            self._use_fallback = False
            self._active_backend = "local"
            self._active_model_name = self._configured_model_name
            model_dimension = getattr(self._model, "get_embedding_dimension", None)
            if not callable(model_dimension):
                model_dimension = getattr(self._model, "get_sentence_embedding_dimension", None)
            if callable(model_dimension):
                self._dimension = int(model_dimension() or 384)
            logger.info(
                f"Using local embedding backend '{self._active_backend}' with model '{self._active_model_name}'"
            )
        except Exception as e:
            self._activate_fallback(f"Failed to load embedding model: {e}")

    def _activate_fallback(self, reason: str) -> None:
        self._model = None
        self._use_fallback = True
        self._active_backend = "fallback"
        self._active_model_name = "deterministic-hash"
        self._last_init_error = str(reason or "")
        logger.info(f"{self._last_init_error}, using fallback embeddings")

    def _resolved_model_name(self) -> str:
        if self._configured_model_name.startswith("sentence-transformers/"):
            return self._configured_model_name.split("/", 1)[1]
        return self._configured_model_name

    def is_fallback_mode(self) -> bool:
        """Return True when deterministic fallback embeddings are active."""
        return self._use_fallback or self._model is None

    def get_backend_name(self) -> str:
        """Return the active embedding backend name."""
        return self._active_backend

    def get_model_name(self) -> str:
        """Return the active embedding model name."""
        return self._active_model_name

    def get_configured_backend(self) -> str:
        """Return the configured embedding backend name."""
        return self._backend

    def get_configured_model_name(self) -> str:
        """Return the configured embedding model name."""
        return self._configured_model_name

    def get_last_init_error(self) -> str:
        """Return the latest embedding initialization or fallback reason."""
        return self._last_init_error

    def get_status(self) -> dict[str, Any]:
        """Return embedding backend status for CLI/debug reporting."""
        return {
            "configured_backend": self.get_configured_backend(),
            "configured_model": self.get_configured_model_name(),
            "backend": self.get_backend_name(),
            "model": self.get_model_name(),
            "fallback_used": self.is_fallback_mode(),
            "dimension": self.get_dimension(),
            "init_error": self.get_last_init_error(),
        }

    def tokenize(self, text: str) -> List[str]:
        """Normalize text into deterministic retrieval tokens."""
        normalized = str(text or "").lower().replace("_", " ")
        raw_tokens = re.findall(r"[a-z0-9]+", normalized)
        tokens: list[str] = []
        seen: set[str] = set()

        for token in raw_tokens:
            if token in _STOP_WORDS:
                continue

            normalized_token = self._normalize_token(token)
            if not normalized_token or normalized_token in _STOP_WORDS:
                continue

            for variant in {token, normalized_token}:
                if variant and variant not in _STOP_WORDS and variant not in seen:
                    tokens.append(variant)
                    seen.add(variant)

        return tokens

    def _normalize_token(self, token: str) -> str:
        """Normalize common plural and gerund forms for lexical retrieval."""
        token = str(token or "").strip().lower()
        if len(token) <= 3:
            return token
        if token.endswith("ies") and len(token) > 4:
            return token[:-3] + "y"
        if token.endswith("ing") and len(token) > 5:
            return token[:-3]
        if token.endswith(("sses", "shes", "ches", "xes", "zes")) and len(token) > 5:
            return token[:-2]
        if token.endswith("es") and len(token) > 4:
            return token[:-1]
        if token.endswith("s") and not token.endswith("ss") and len(token) > 4:
            return token[:-1]
        return token

    def _stable_index(self, feature: str, dimension: int) -> int:
        digest = hashlib.sha256(feature.encode("utf-8")).digest()
        return int.from_bytes(digest[:8], "big") % dimension

    def _fallback_features(self, text: str) -> List[str]:
        tokens = self.tokenize(text)
        features = [f"tok:{token}" for token in tokens]
        compact_text = "".join(tokens)
        for idx in range(max(len(compact_text) - 2, 0)):
            features.append(f"tri:{compact_text[idx:idx + 3]}")
        return features
    
    def embed(self, text: str) -> List[float]:
        """
        Generate embedding for a text string.
        
        Args:
            text: Text to embed
            
        Returns:
            List of float values representing the embedding
        """
        if not text:
            return [0.0] * self.get_dimension()
        
        text = str(text).strip()
        
        if not self._use_fallback and self._model:
            try:
                embedding = self._model.encode(text, convert_to_numpy=False, normalize_embeddings=True)
                return embedding.tolist() if hasattr(embedding, "tolist") else list(embedding)
            except Exception as e:
                logger.warning(f"Model embedding failed: {e}, using fallback")
                return self._fallback_embed(text)
        
        return self._fallback_embed(text)
    
    def _fallback_embed(self, text: str) -> List[float]:
        """
        Fallback embedding using deterministic feature hashing.
        
        This keeps retrieval usable when sentence-transformers is not installed
        and avoids Python's process-randomized hash().
        """
        dimension = self.get_dimension()
        embedding = [0.0] * dimension

        for feature in self._fallback_features(text):
            idx = self._stable_index(feature, dimension)
            embedding[idx] += 1.0

        norm = sum(x * x for x in embedding) ** 0.5
        if norm > 0:
            embedding = [x / norm for x in embedding]
        
        return embedding
    
    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """
        Generate embeddings for multiple texts.
        
        Args:
            texts: List of texts to embed
            
        Returns:
            List of embedding vectors
        """
        if not texts:
            return []

        if not self._use_fallback and self._model:
            try:
                embeddings = self._model.encode(texts, convert_to_numpy=False, normalize_embeddings=True)
                return [
                    embedding.tolist() if hasattr(embedding, "tolist") else list(embedding)
                    for embedding in embeddings
                ]
            except Exception as e:
                logger.warning(f"Batch model embedding failed: {e}, using fallback")

        return [self.embed(text) for text in texts]
    
    def get_dimension(self) -> int:
        """Return the embedding dimension."""
        return self._dimension
