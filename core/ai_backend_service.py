"""
core/ai_backend_service.py
==========================
Central AI backend service for CLI AI configuration, testing, and chat calls.
"""

from __future__ import annotations

from typing import Optional, Tuple
import os

from dotenv import load_dotenv

from utils.logger import get_logger

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None


load_dotenv()
logger = get_logger()

_DEFAULT_LOCAL_MODEL = "llama3"
_DEFAULT_LOCAL_URL = "http://localhost:11434"
_DEFAULT_NVIDIA_MODEL = "nvidia/nemotron-3-ultra-550b-a55b"
_DEFAULT_NVIDIA_URL = "https://integrate.api.nvidia.com/v1"
_BACKEND_TEST_MAX_TOKENS = 8


def _response_preview(text: str, limit: int = 80) -> str:
    preview = " ".join(str(text or "").split())
    if len(preview) <= limit:
        return preview
    return preview[: limit - 3] + "..."


def _normalize_backend_name(value: str | None) -> str:
    backend = str(value or "").strip().lower()
    if backend in {"nvidia", "local"}:
        return backend
    return "local"


def _has_explicit_nvidia_config() -> bool:
    """Return True only when NVIDIA is intentionally and sufficiently configured."""
    api_key = str(os.getenv("NVIDIA_API_KEY") or "").strip()
    model = str(os.getenv("NVIDIA_MODEL") or "").strip()
    return bool(api_key and model)


def _resolve_active_backend_from_env() -> str:
    """
    Resolve the active backend from environment variables.

    Local Ollama remains the default. NVIDIA becomes active only when
    ``AI_BACKEND=nvidia`` is explicitly set and the required NVIDIA config
    is present.
    """
    explicit_backend = _normalize_backend_name(os.getenv("AI_BACKEND"))
    if explicit_backend == "nvidia" and _has_explicit_nvidia_config():
        return "nvidia"
    if explicit_backend == "local":
        return "local"

    legacy_backend = _normalize_backend_name(os.getenv("LLM_BACKEND"))
    if legacy_backend == "local":
        return "local"

    return "local"


def _read_int_env(name: str, default: int, minimum: int = 1) -> int:
    raw = str(os.getenv(name, str(default)) or str(default)).strip()
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(value, minimum)


def _read_float_env(name: str, default: float) -> float:
    raw = str(os.getenv(name, str(default)) or str(default)).strip()
    try:
        return float(raw)
    except ValueError:
        return default


def _sync_backend_env(backend: str) -> None:
    os.environ["AI_BACKEND"] = backend
    os.environ["LLM_BACKEND"] = backend


def _normalize_model_name(value: str | None) -> str:
    model = str(value or "").strip().lower()
    if not model:
        return ""
    return model.split(":", 1)[0]


def _extract_model_names(payload: object) -> list[str]:
    if not isinstance(payload, dict):
        return []
    models = payload.get("models")
    if not isinstance(models, list):
        return []

    names: list[str] = []
    for entry in models:
        if isinstance(entry, dict):
            name = str(entry.get("name", "")).strip()
        else:
            name = str(entry or "").strip()
        if name:
            names.append(name)
    return names


def _find_matching_model(configured_model: str, available_models: list[str]) -> str | None:
    configured_full = str(configured_model or "").strip().lower()
    configured_normalized = _normalize_model_name(configured_model)
    for available_model in available_models:
        available_full = str(available_model or "").strip().lower()
        available_normalized = _normalize_model_name(available_model)
        if configured_full and available_full == configured_full:
            return available_model
        if configured_normalized and available_normalized == configured_normalized:
            return available_model
    return None


def _check_local_backend_status(
    api_url: str,
    configured_model: str,
    *,
    timeout: int = 5,
) -> tuple[bool, str]:
    if requests is None:
        return False, "The 'requests' package is required for the local backend."

    base_url = str(api_url or _DEFAULT_LOCAL_URL).strip().rstrip("/")
    try:
        response = requests.get(f"{base_url}/api/tags", timeout=timeout)
    except requests.exceptions.Timeout:
        return False, "Ollama status check timed out."
    except (requests.exceptions.ConnectionError, ConnectionError):
        return False, "Ollama is not running."
    except Exception as exc:
        logger.debug(f"Ollama status check failed: {exc}")
        return False, f"Ollama status check failed: {exc}"

    if response.status_code != 200:
        return False, f"Ollama status check failed with HTTP {response.status_code}."

    try:
        payload = response.json()
    except Exception:
        payload = {}

    available_models = _extract_model_names(payload)
    if not available_models:
        return True, "Ollama is running."

    matched_model = _find_matching_model(configured_model, available_models)
    if matched_model:
        if str(matched_model).strip().lower() == str(configured_model or "").strip().lower():
            return True, f"Ollama is running. Configured model '{configured_model}' is available."
        return True, (
            f"Ollama is running. Configured model '{configured_model}' matched available model "
            f"'{matched_model}'."
        )

    preview = ", ".join(available_models[:5])
    return True, (
        f"Ollama is running, but configured model '{configured_model}' was not found. "
        f"Available models: {preview}"
    )


class AIBackendService:
    """Service for AI backend management and shared chat completions."""

    def __init__(self):
        self._load_from_env()

    def _load_from_env(self) -> None:
        self.active_backend = _resolve_active_backend_from_env()
        self.local_model = (os.getenv("LOCAL_MODEL") or _DEFAULT_LOCAL_MODEL).strip() or _DEFAULT_LOCAL_MODEL
        self.local_api_url = (os.getenv("LOCAL_API_URL") or _DEFAULT_LOCAL_URL).strip().rstrip("/")
        self.local_timeout = _read_int_env("LOCAL_TIMEOUT", 120)

        self.nvidia_model = (os.getenv("NVIDIA_MODEL") or _DEFAULT_NVIDIA_MODEL).strip() or _DEFAULT_NVIDIA_MODEL
        self.nvidia_base_url = (os.getenv("NVIDIA_BASE_URL") or _DEFAULT_NVIDIA_URL).strip().rstrip("/")
        self.nvidia_api_key = (os.getenv("NVIDIA_API_KEY") or "").strip()
        self.nvidia_temperature = _read_float_env("NVIDIA_TEMPERATURE", 1.0)
        self.nvidia_max_tokens = _read_int_env("NVIDIA_MAX_TOKENS", 16384)
        self.nvidia_timeout = _read_int_env("NVIDIA_TIMEOUT", 60)

        _sync_backend_env(self.active_backend)
        os.environ["LOCAL_MODEL"] = self.local_model
        os.environ["LOCAL_API_URL"] = self.local_api_url
        os.environ["LOCAL_TIMEOUT"] = str(self.local_timeout)
        os.environ["NVIDIA_MODEL"] = self.nvidia_model
        os.environ["NVIDIA_BASE_URL"] = self.nvidia_base_url
        os.environ["NVIDIA_TEMPERATURE"] = str(self.nvidia_temperature)
        os.environ["NVIDIA_MAX_TOKENS"] = str(self.nvidia_max_tokens)
        os.environ["NVIDIA_TIMEOUT"] = str(self.nvidia_timeout)
        if self.nvidia_api_key:
            os.environ["NVIDIA_API_KEY"] = self.nvidia_api_key

    def refresh_from_env(self) -> None:
        """Reload configuration from environment variables."""
        self._load_from_env()

    def set_local_backend(self, model: str, api_url: str) -> None:
        """Set local Ollama backend."""
        self.active_backend = "local"
        self.local_model = (model or _DEFAULT_LOCAL_MODEL).strip() or _DEFAULT_LOCAL_MODEL
        self.local_api_url = (api_url or _DEFAULT_LOCAL_URL).strip().rstrip("/")
        self.local_timeout = _read_int_env("LOCAL_TIMEOUT", self.local_timeout)
        _sync_backend_env("local")
        os.environ["LOCAL_MODEL"] = self.local_model
        os.environ["LOCAL_API_URL"] = self.local_api_url
        os.environ["LOCAL_TIMEOUT"] = str(self.local_timeout)
        logger.info(f"Backend switched to local: {self.local_model} at {self.local_api_url}")

    def set_nvidia_backend(self, model: str, api_key: str, base_url: str = "") -> None:
        """Set NVIDIA backend using env-backed settings."""
        self.active_backend = "nvidia"
        self.nvidia_model = (model or self.nvidia_model or _DEFAULT_NVIDIA_MODEL).strip() or _DEFAULT_NVIDIA_MODEL
        self.nvidia_base_url = (base_url or self.nvidia_base_url or _DEFAULT_NVIDIA_URL).strip().rstrip("/")
        if api_key:
            self.nvidia_api_key = api_key.strip()
            os.environ["NVIDIA_API_KEY"] = self.nvidia_api_key
        _sync_backend_env("nvidia")
        os.environ["NVIDIA_MODEL"] = self.nvidia_model
        os.environ["NVIDIA_BASE_URL"] = self.nvidia_base_url
        os.environ["NVIDIA_TEMPERATURE"] = str(self.nvidia_temperature)
        os.environ["NVIDIA_MAX_TOKENS"] = str(self.nvidia_max_tokens)
        os.environ["NVIDIA_TIMEOUT"] = str(self.nvidia_timeout)
        logger.info(f"Backend switched to NVIDIA: {self.nvidia_model} at {self.nvidia_base_url}")

    def set_custom_backend(
        self, 
        api_url: str,
        model: str = "",
        auth_header: str = "",
        auth_token: str = "",
    ) -> None:
        """Legacy no-op placeholder kept for backward compatibility."""
        logger.info("Custom backend is not configured for the active CLI workflow")

    def get_active_backend(self) -> str:
        """Get active backend."""
        return self.active_backend

    def get_backend_config(self) -> dict:
        """Get current backend configuration."""
        self.refresh_from_env()
        config = {"active_backend": self.active_backend}
        if self.active_backend == "nvidia":
            config.update(
                {
                    "model": self.nvidia_model,
                    "api_url": self.nvidia_base_url,
                    "temperature": self.nvidia_temperature,
                    "max_tokens": self.nvidia_max_tokens,
                    "timeout": self.nvidia_timeout,
                }
            )
        else:
            config.update(
                {
                    "model": self.local_model,
                    "api_url": self.local_api_url,
                    "timeout": self.local_timeout,
                }
            )
        return config

    def _call_local(
        self,
        messages: list[dict],
        response_format: dict | str | None = None,
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str:
        if requests is None:
            raise RuntimeError(
                "The 'requests' package is required for the local backend. Run: pip install requests"
            )

        payload = {
            "model": self.local_model,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": 0 if temperature is None else temperature,
                "num_predict": max_tokens or 300,
            },
        }
        if response_format:
            payload["format"] = response_format

        try:
            response = requests.post(
                f"{self.local_api_url}/api/chat",
                json=payload,
                timeout=self.local_timeout,
            )
            response.raise_for_status()
            data = response.json()
            if "message" in data and isinstance(data["message"], dict):
                return data["message"].get("content", "")
            if data.get("choices"):
                return data["choices"][0].get("message", {}).get("content", "")
            raise RuntimeError("Ollama response did not contain generated content.")
        except requests.exceptions.Timeout as exc:
            raise TimeoutError("Local AI timed out.") from exc
        except requests.exceptions.ConnectionError as exc:
            raise ConnectionError("Ollama is not running.") from exc
        except Exception as exc:
            raise RuntimeError("Local AI failed. Using rule-based fallback where possible.") from exc

    def _call_nvidia(
        self,
        messages: list[dict],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str:
        if requests is None:
            raise RuntimeError(
                "The 'requests' package is required for the NVIDIA backend. Run: pip install requests"
            )
        if not self.nvidia_api_key:
            raise ValueError("NVIDIA_API_KEY is required for NVIDIA backend")

        payload = {
            "model": self.nvidia_model,
            "messages": messages,
            "temperature": self.nvidia_temperature if temperature is None else temperature,
            "max_tokens": self.nvidia_max_tokens if max_tokens is None else max_tokens,
        }
        headers = {
            "Authorization": f"Bearer {self.nvidia_api_key}",
            "Content-Type": "application/json",
        }

        try:
            response = requests.post(
                f"{self.nvidia_base_url}/chat/completions",
                json=payload,
                headers=headers,
                timeout=self.nvidia_timeout,
            )
            response.raise_for_status()
            data = response.json()
            if data.get("choices"):
                return data["choices"][0].get("message", {}).get("content", "")
            raise RuntimeError("NVIDIA API response did not contain generated content.")
        except requests.exceptions.ConnectionError as exc:
            raise ConnectionError(
                "NVIDIA API is unreachable. Please check your network connection and NVIDIA_BASE_URL."
            ) from exc
        except requests.exceptions.HTTPError as exc:
            status_code = getattr(getattr(exc, "response", None), "status_code", None)
            if status_code == 401:
                raise ValueError("Invalid NVIDIA_API_KEY") from exc
            raise RuntimeError(f"NVIDIA API returned error: {exc}") from exc
        except Exception as exc:
            raise RuntimeError(f"NVIDIA backend failed: {exc}") from exc

    def call_backend(
        self,
        messages: list[dict],
        backend: str | None = None,
        response_format: dict | str | None = None,
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str:
        """Dispatch chat completions to the selected backend."""
        selected_backend = _normalize_backend_name(backend or self.active_backend)
        self.refresh_from_env()
        if backend:
            selected_backend = _normalize_backend_name(backend)

        if selected_backend == "nvidia":
            return self._call_nvidia(messages, temperature=temperature, max_tokens=max_tokens)
        return self._call_local(
            messages,
            response_format=response_format,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    def test_backend_connection(self, backend: str | None = None) -> Tuple[bool, str]:
        """Test the selected AI backend connection."""
        self.refresh_from_env()
        selected_backend = _normalize_backend_name(backend or self.active_backend)

        if selected_backend == "nvidia":
            try:
                content = self.call_backend(
                    [
                        {"role": "system", "content": "You are a connection health check. Reply with exactly OK and no other text."},
                        {"role": "user", "content": "OK"},
                    ],
                    backend="nvidia",
                    temperature=0,
                    max_tokens=_BACKEND_TEST_MAX_TOKENS,
                ).strip()
                if not content:
                    return False, "NVIDIA backend returned an empty response."
                preview = _response_preview(content)
                if content == "OK":
                    return True, f"NVIDIA backend connected. Response preview: {preview}"
                return True, f"NVIDIA backend connected. Response preview: {preview} (health check did not return exact OK)"
            except Exception as exc:
                logger.debug(f"NVIDIA connection test failed: {exc}")
                return False, str(exc)

        try:
            return _check_local_backend_status(
                self.local_api_url,
                self.local_model,
                timeout=5,
            )
        except Exception as exc:
            logger.debug(f"Ollama connection test failed: {exc}")
            return False, f"Ollama status check failed: {exc}"

    def is_local_backend(self) -> bool:
        """Check if using local backend."""
        return self.active_backend == "local"

    def is_nvidia_backend(self) -> bool:
        """Check if using NVIDIA backend."""
        return self.active_backend == "nvidia"

    def is_custom_backend(self) -> bool:
        """Check if using custom backend."""
        return False


_SHARED_BACKEND_SERVICE: AIBackendService | None = None


def get_ai_backend_service() -> AIBackendService:
    """Return the shared AI backend service instance."""
    global _SHARED_BACKEND_SERVICE
    if _SHARED_BACKEND_SERVICE is None:
        _SHARED_BACKEND_SERVICE = AIBackendService()
    return _SHARED_BACKEND_SERVICE


def call_ai_backend(
    messages: list[dict],
    backend: str | None = None,
    response_format: dict | str | None = None,
    *,
    temperature: float | None = None,
    max_tokens: int | None = None,
) -> str:
    """Shared module-level helper for AI calls across the CLI project."""
    return get_ai_backend_service().call_backend(
        messages,
        backend=backend,
        response_format=response_format,
        temperature=temperature,
        max_tokens=max_tokens,
    )


def check_ollama_status(api_url: str | None = None, timeout: int = 5) -> tuple[bool, str]:
    """Return whether the local Ollama server is reachable."""
    base_url = (api_url or os.getenv("LOCAL_API_URL") or _DEFAULT_LOCAL_URL).strip().rstrip("/")
    configured_model = (os.getenv("LOCAL_MODEL") or _DEFAULT_LOCAL_MODEL).strip() or _DEFAULT_LOCAL_MODEL
    return _check_local_backend_status(base_url, configured_model, timeout=timeout)
