"""Tests for centralized AI backend service behavior."""

from unittest.mock import MagicMock, patch

from core.ai_backend_service import AIBackendService


def test_backend_uses_nvidia_when_env_requests_it(monkeypatch):
    monkeypatch.setenv("AI_BACKEND", "nvidia")
    monkeypatch.setenv("NVIDIA_MODEL", "nvidia/nemotron-3-ultra-550b-a55b")
    monkeypatch.setenv("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1")
    monkeypatch.setenv("NVIDIA_TEMPERATURE", "1")
    monkeypatch.setenv("NVIDIA_MAX_TOKENS", "16384")

    service = AIBackendService()

    assert service.get_active_backend() == "nvidia"
    assert service.get_backend_config() == {
        "active_backend": "nvidia",
        "model": "nvidia/nemotron-3-ultra-550b-a55b",
        "api_url": "https://integrate.api.nvidia.com/v1",
        "temperature": 1.0,
        "max_tokens": 16384,
        "timeout": 60,
    }


def test_local_backend_connection_status_running(monkeypatch):
    monkeypatch.setenv("AI_BACKEND", "local")
    service = AIBackendService()
    response = MagicMock(status_code=200)
    response.json.return_value = {"models": [{"name": "llama3"}]}

    with patch("core.ai_backend_service.requests.get", return_value=response) as mock_get:
        ok, message = service.test_backend_connection()

    assert ok is True
    assert "Ollama is running" in message
    mock_get.assert_called_once_with("http://localhost:11434/api/tags", timeout=5)


def test_local_backend_connection_status_not_running(monkeypatch):
    monkeypatch.setenv("AI_BACKEND", "local")
    service = AIBackendService()

    with patch("core.ai_backend_service.requests.get", side_effect=ConnectionError("raw connection text")):
        ok, message = service.test_backend_connection()

    assert ok is False
    assert message == "Ollama is not running."


def test_nvidia_backend_connection_uses_ok_probe(monkeypatch):
    monkeypatch.setenv("AI_BACKEND", "nvidia")
    monkeypatch.setenv("NVIDIA_API_KEY", "secret")
    service = AIBackendService()

    response = MagicMock()
    response.raise_for_status.return_value = None
    response.json.return_value = {"choices": [{"message": {"content": "OK"}}]}

    with patch("core.ai_backend_service.requests.post", return_value=response) as mock_post:
        ok, message = service.test_backend_connection()

    assert ok is True
    assert "NVIDIA backend connected" in message
    request_json = mock_post.call_args.kwargs["json"]
    assert request_json["messages"][1]["content"] == "Reply with only: OK"
    assert request_json["temperature"] == 0
    assert request_json["max_tokens"] == 16
