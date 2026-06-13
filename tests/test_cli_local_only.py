"""CLI tests for local-only backend workflow."""

import main


def test_main_menu_is_cli_local_only(capsys):
    state = main.SessionState()

    main.display_menu(state)

    output = capsys.readouterr().out
    assert "Ask a Question / Ask Business Question" in output
    assert "AI Backend Settings" in output
    assert "NVIDIA" not in output


def test_ai_backend_settings_hides_nvidia(monkeypatch, capsys):
    state = main.SessionState()
    monkeypatch.setattr(main, "_input", lambda prompt: "3")

    main.handle_ai_backend_settings(state)

    output = capsys.readouterr().out
    assert "Current backend: local" in output
    assert "Model: llama3" in output
    assert "URL: http://localhost:11434" in output
    assert "Timeout: 120 seconds" in output
    assert "Ollama status:" in output
    assert "NVIDIA" not in output
