"""Tests for local-only AI backend service behavior."""

from unittest.mock import MagicMock, patch

from core.ai_backend_service import AIBackendService


def test_backend_defaults_to_local_even_when_env_requests_nvidia(monkeypatch):
    monkeypatch.setenv("LLM_BACKEND", "nvidia")
    monkeypatch.setenv("LOCAL_MODEL", "llama3")
    monkeypatch.setenv("LOCAL_API_URL", "http://localhost:11434")
    monkeypatch.setenv("LOCAL_TIMEOUT", "120")

    service = AIBackendService()

    assert service.get_active_backend() == "local"
    assert service.get_backend_config() == {
        "active_backend": "local",
        "model": "llama3",
        "api_url": "http://localhost:11434",
        "timeout": 120,
    }


def test_local_backend_connection_status_running(monkeypatch):
    monkeypatch.setenv("LLM_BACKEND", "local")
    service = AIBackendService()
    response = MagicMock(status_code=200)
    response.json.return_value = {"models": [{"name": "llama3"}]}

    with patch("requests.get", return_value=response) as mock_get:
        ok, message = service.test_backend_connection()

    assert ok is True
    assert "Ollama is running" in message
    mock_get.assert_called_once_with("http://localhost:11434/api/tags", timeout=5)


def test_local_backend_connection_status_not_running(monkeypatch):
    monkeypatch.setenv("LLM_BACKEND", "local")
    service = AIBackendService()

    with patch("requests.get", side_effect=ConnectionError("raw connection text")):
        ok, message = service.test_backend_connection()

    assert ok is False
    assert message == "Ollama is not running."
