"""Tests for local Ollama backend calls through the central service."""

from unittest.mock import MagicMock, patch

import pytest

from ai.sql_generator import _call_ollama


def test_call_ollama_uses_local_url_and_timeout(monkeypatch):
    monkeypatch.setenv("AI_BACKEND", "local")
    monkeypatch.setenv("LOCAL_API_URL", "http://localhost:11434")
    monkeypatch.setenv("LOCAL_MODEL", "llama3")
    monkeypatch.setenv("LOCAL_TIMEOUT", "120")
    response = MagicMock()
    response.json.return_value = {"message": {"content": "SELECT 1;"}}
    response.raise_for_status.return_value = None

    with patch("core.ai_backend_service.requests.post", return_value=response) as mock_post:
        result = _call_ollama([{"role": "user", "content": "test"}])

    assert result == "SELECT 1;"
    mock_post.assert_called_once()
    assert mock_post.call_args.kwargs["timeout"] == 120
    assert mock_post.call_args.args[0] == "http://localhost:11434/api/chat"
    assert mock_post.call_args.kwargs["json"]["options"] == {"temperature": 0, "num_predict": 300}


def test_call_ollama_passes_response_format(monkeypatch):
    monkeypatch.setenv("AI_BACKEND", "local")
    monkeypatch.setenv("LOCAL_API_URL", "http://localhost:11434")
    response = MagicMock()
    response.json.return_value = {"message": {"content": "{}"}}
    response.raise_for_status.return_value = None

    with patch("core.ai_backend_service.requests.post", return_value=response) as mock_post:
        _call_ollama([{"role": "user", "content": "test"}], response_format={"type": "object"})

    assert mock_post.call_args.kwargs["json"]["format"] == {"type": "object"}


def test_call_ollama_timeout_has_clean_message(monkeypatch):
    monkeypatch.setenv("AI_BACKEND", "local")
    monkeypatch.setenv("LOCAL_TIMEOUT", "1")

    with patch("core.ai_backend_service.requests.post", side_effect=pytest.importorskip("requests").exceptions.Timeout):
        with pytest.raises(TimeoutError, match="Local AI timed out"):
            _call_ollama([{"role": "user", "content": "test"}])
