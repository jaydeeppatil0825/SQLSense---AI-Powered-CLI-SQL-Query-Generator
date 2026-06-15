"""
vector_store/embedding_service.py
===============================
Embedding service for vector retrieval.

Provides text embedding using sentence-transformers or falls back
to deterministic token and character n-gram hashing when unavailable.
"""

from typing import List
import hashlib
import re
from utils.logger import get_logger

logger = get_logger()

_STOP_WORDS = {
    "a", "an", "and", "are", "as", "at", "about", "by", "for", "from", "get",
    "give", "how", "in", "is", "it", "list", "me", "of", "on", "or", "please",
    "return", "show", "tell", "the", "this", "to", "what", "with",
}


class EmbeddingService:
    """Service for generating text embeddings."""
    
    def __init__(self):
        self._model = None
        self._use_fallback = True
        self._init_model()
    
    def _init_model(self):
        """Initialize the embedding model if available."""
        try:
            from sentence_transformers import SentenceTransformer
            try:
                # Try to load a lightweight model
                self._model = SentenceTransformer('all-MiniLM-L6-v2')
                self._use_fallback = False
                logger.info("Using sentence-transformers for embeddings")
            except Exception as e:
                logger.warning(f"Failed to load sentence-transformers model: {e}, using fallback")
                self._use_fallback = True
        except ImportError:
            logger.info("sentence-transformers not available, using fallback embeddings")
            self._use_fallback = True

    def is_fallback_mode(self) -> bool:
        """Return True when deterministic fallback embeddings are active."""
        return self._use_fallback or self._model is None

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
            return [0.0] * 384  # Default dimension
        
        text = str(text).strip()
        
        if not self._use_fallback and self._model:
            try:
                embedding = self._model.encode(text, convert_to_numpy=False)
                return embedding.tolist()
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
        dimension = 384
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
        return [self.embed(text) for text in texts]
    
    def get_dimension(self) -> int:
        """Return the embedding dimension."""
        return 384 if self._use_fallback else 384  # Both use 384
