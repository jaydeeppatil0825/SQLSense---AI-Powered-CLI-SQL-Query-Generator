"""CLI tests for backend settings workflow."""

import main


def test_main_menu_shows_backend_settings(capsys):
    state = main.SessionState()

    main.display_menu(state)

    output = capsys.readouterr().out
    assert "Ask a Question / Ask Business Question" in output
    assert "AI Backend Settings" in output
    assert "Backend  :" in output


def test_ai_backend_settings_shows_nvidia_option(monkeypatch, capsys):
    monkeypatch.setenv("AI_BACKEND", "local")
    state = main.SessionState()
    state.app_service.set_local_backend("llama3", "http://localhost:11434")
    monkeypatch.setattr(state.app_service, "test_backend_connection", lambda: (False, "Ollama is not running."))
    monkeypatch.setattr(main, "_input", lambda prompt: "5")

    main.handle_ai_backend_settings(state)

    output = capsys.readouterr().out
    assert "Current backend: local" in output
    assert "Use NVIDIA backend" in output
    assert "Test active backend" in output


def test_ai_backend_test_option_prints_backend_result(monkeypatch, capsys):
    state = main.SessionState()
    answers = iter(["3", "5"])
    monkeypatch.setattr(state.app_service, "test_backend_connection", lambda: (True, "NVIDIA backend connected. Model replied: OK"))
    monkeypatch.setattr(main, "_input", lambda prompt: next(answers))

    main.handle_ai_backend_settings(state)

    output = capsys.readouterr().out
    assert "Testing AI Backend Connection" in output
    assert "NVIDIA backend connected. Model replied: OK" in output


def test_ai_backend_settings_does_not_auto_test_nvidia_on_open(monkeypatch, capsys):
    monkeypatch.setenv("AI_BACKEND", "nvidia")
    monkeypatch.setenv("NVIDIA_API_KEY", "secret")
    monkeypatch.setenv("NVIDIA_MODEL", "nvidia/nemotron-3-ultra-550b-a55b")
    state = main.SessionState()
    monkeypatch.setattr(
        state.app_service,
        "test_backend_connection",
        lambda: (_ for _ in ()).throw(AssertionError("backend test should not run automatically")),
    )
    monkeypatch.setattr(main, "_input", lambda prompt: "5")

    main.handle_ai_backend_settings(state)

    output = capsys.readouterr().out
    assert "Current backend: nvidia" in output
    assert "Backend status: not tested" in output
