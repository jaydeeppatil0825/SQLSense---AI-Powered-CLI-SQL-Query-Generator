"""
core/ai_backend_service.py
==========================
AI Backend service for AI backend management.

This service handles CLI AI backend configuration and testing.
"""

from typing import Optional, Tuple
import os

from utils.logger import get_logger

logger = get_logger()


class AIBackendService:
    """Service for AI backend management."""
    
    def __init__(self):
        self.active_backend: str = "local"
        self.local_model: str = "llama3"
        self.local_api_url: str = "http://localhost:11434"
        self.local_timeout: int = 120
        self.nvidia_model: str = ""
        self.nvidia_api_key: str = ""
        self.nvidia_base_url: str = ""
        self.custom_api_url: str = ""
        self.custom_model: str = ""
        self.custom_auth_header: str = ""
        self.custom_auth_token: str = ""
        
        # Load from environment variables
        self.local_model = os.getenv("LOCAL_MODEL", "llama3")
        self.local_api_url = os.getenv("LOCAL_API_URL", "http://localhost:11434")
        self.local_timeout = self._read_local_timeout()

        # The active CLI workflow is local-only. Legacy NVIDIA attributes remain
        # on the class so old imports/tests do not break, but env values cannot
        # switch the running CLI away from Ollama.
        self.active_backend = "local"
        os.environ["LLM_BACKEND"] = "local"
        os.environ.setdefault("LOCAL_MODEL", self.local_model)
        os.environ.setdefault("LOCAL_API_URL", self.local_api_url)
        os.environ.setdefault("LOCAL_TIMEOUT", str(self.local_timeout))

    def _read_local_timeout(self) -> int:
        raw = os.getenv("LOCAL_TIMEOUT", "120").strip()
        try:
            timeout = int(raw)
        except ValueError:
            return 120
        return max(timeout, 1)
    
    def set_local_backend(self, model: str, api_url: str) -> None:
        """
        Set local LLM backend.
        
        Args:
            model: Model name
            api_url: API URL
        """
        self.active_backend = "local"
        self.local_model = model or "llama3"
        self.local_api_url = (api_url or "http://localhost:11434").rstrip("/")
        self.local_timeout = self._read_local_timeout()
        os.environ["LLM_BACKEND"] = "local"
        os.environ["LOCAL_MODEL"] = self.local_model
        os.environ["LOCAL_API_URL"] = self.local_api_url
        os.environ["LOCAL_TIMEOUT"] = str(self.local_timeout)
        logger.info(f"Backend switched to local: {model} at {api_url}")
    
    def set_nvidia_backend(self, model: str, api_key: str, base_url: str = "") -> None:
        """
        Set NVIDIA backend.
        
        Args:
            model: Model name
            api_key: NVIDIA API key
            base_url: NVIDIA API base URL (optional, defaults to NVIDIA's standard URL)
        """
        logger.info("NVIDIA backend configuration ignored; CLI is local-only")
    
    def set_custom_backend(
        self,
        api_url: str,
        model: str = "",
        auth_header: str = "",
        auth_token: str = "",
    ) -> None:
        """
        Set custom AI backend.
        
        Args:
            api_url: API URL
            model: Model name
            auth_header: Auth header
            auth_token: Auth token
        """
        logger.info("Custom backend configuration ignored; CLI is local-only")
    
    def get_active_backend(self) -> str:
        """Get active backend."""
        return self.active_backend
    
    def get_backend_config(self) -> dict:
        """
        Get current backend configuration.
        
        Returns:
            Dictionary with backend configuration
        """
        config = {
            "active_backend": self.active_backend,
        }
        
        if self.active_backend == "local":
            config["model"] = self.local_model
            config["api_url"] = self.local_api_url
            config["timeout"] = self.local_timeout
        
        return config
    
    def test_backend_connection(self) -> Tuple[bool, str]:
        """
        Test current AI backend connection.
        
        Returns:
            (success, message)
        """
        if self.active_backend == "local":
            try:
                import requests
                response = requests.get(f"{self.local_api_url.rstrip('/')}/api/tags", timeout=5)
                if response.status_code == 200:
                    models = response.json().get("models", [])
                    model_names = [m.get('name', 'unknown') for m in models[:5]]
                    if model_names:
                        return True, f"Ollama is running. Available models: {', '.join(model_names)}"
                    return True, "Ollama is running."
                else:
                    return False, "Ollama is not running."
            except Exception as e:
                logger.debug(f"Ollama connection test failed: {e}")
                return False, "Ollama is not running."
        
        return False, "Unknown backend"
    
    def is_local_backend(self) -> bool:
        """Check if using local backend."""
        return self.active_backend == "local"
    
    def is_nvidia_backend(self) -> bool:
        """Check if using NVIDIA backend."""
        return False
    
    def is_custom_backend(self) -> bool:
        """Check if using custom backend."""
        return False
