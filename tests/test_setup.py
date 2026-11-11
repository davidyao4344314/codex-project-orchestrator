import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from codex_orchestrator.cli import app
from codex_orchestrator.codex import CodexError, find_codex
from codex_orchestrator.session import PlanningSession

runner = CliRunner()


def test_finds_app_cli_when_missing_from_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    executable = tmp_path / "app" / "codex"
    executable.parent.mkdir()
    executable.write_text("#!/bin/sh\nexit 0\n")
    executable.chmod(0o755)
    monkeypatch.delenv("CODEX_EXECUTABLE", raising=False)
    monkeypatch.setattr("codex_orchestrator.codex.shutil.which", lambda _: None)
    monkeypatch.setattr("codex_orchestrator.codex.codex_candidates", lambda: [executable])
    assert find_codex() == str(executable)


def test_invalid_executable_override_is_actionable(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CODEX_EXECUTABLE", "/nonexistent/codex")
    with pytest.raises(CodexError, match="CODEX_EXECUTABLE"):
        find_codex()


@pytest.mark.parametrize("logged_in", [True, False])
def test_setup_checks_auth_and_opens_login_when_needed(
    monkeypatch: pytest.MonkeyPatch, logged_in: bool
):
    monkeypatch.setattr("codex_orchestrator.setup.find_codex", lambda: "/tools/codex")
    calls = []

    def run(command, **options):
        calls.append(command)
        if command[-2:] == ["login", "status"]:
            return subprocess.CompletedProcess(command, 0 if logged_in or len(calls) > 1 else 1)
        assert command == ["/tools/codex", "login"]
        assert "capture_output" not in options
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr("codex_orchestrator.setup.subprocess.run", run)
    result = runner.invoke(app, ["setup"], input="y\n")
    assert result.exit_code == 0, result.output
    assert "Codex is ready" in result.output
    assert len(calls) == (1 if logged_in else 3)


def test_missing_cli_install_and_login(monkeypatch: pytest.MonkeyPatch):
    found = iter([None, "/tools/codex"])
    monkeypatch.setattr("codex_orchestrator.setup.find_codex", lambda: next(found))
    monkeypatch.setattr("codex_orchestrator.setup.shutil.which", lambda _: "/tools/npm")
    calls = []

    def run(command, **options):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr("codex_orchestrator.setup.subprocess.run", run)
    result = runner.invoke(app, ["setup"], input="y\n")
    assert result.exit_code == 0, result.output
    assert calls == [
        ["/tools/npm", "install", "--global", "@openai/codex"],
        ["/tools/codex", "login", "status"],
    ]


def test_missing_cli_without_npm_shows_guide(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("codex_orchestrator.setup.find_codex", lambda: None)
    monkeypatch.setattr("codex_orchestrator.setup.shutil.which", lambda _: None)
    result = runner.invoke(app, ["setup"], input="\n")
    assert result.exit_code == 1
    assert "Installation guide" in result.output
    assert "Setup postponed" in result.output


@pytest.mark.parametrize("failure", ["login", "verify", "timeout"])
def test_failed_sign_in_has_retry_instructions(monkeypatch: pytest.MonkeyPatch, failure: str):
    monkeypatch.setattr("codex_orchestrator.setup.find_codex", lambda: "/tools/codex")
    calls = []

    def run(command, **options):
        calls.append(command)
        if failure == "timeout":
            raise subprocess.TimeoutExpired(command, 20)
        code = 1
        if command[-1] == "login":
            code = 1 if failure == "login" else 0
        return subprocess.CompletedProcess(command, code)

    monkeypatch.setattr("codex_orchestrator.setup.subprocess.run", run)
    result = runner.invoke(app, ["setup"], input="y\n")
    assert result.exit_code == 1
    assert "orchestrator setup" in result.output


def test_postponed_login_preserves_idea_then_resume_continues(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr("codex_orchestrator.setup.find_codex", lambda: "/tools/codex")
    monkeypatch.setattr(
        "codex_orchestrator.setup.subprocess.run",
        lambda command, **_: subprocess.CompletedProcess(command, 1),
    )
    path = tmp_path / "session.json"
    result = runner.invoke(
        app,
        ["plan", str(tmp_path), "--interactive", "-o", str(path)],
        input="A recursion counter\nn\n",
    )
    assert result.exit_code == 1, result.output
    session = PlanningSession.load(path)
    assert session.answers[0].response == "A recursion counter"
    assert session.phase == "intake"

    monkeypatch.setattr(
        "codex_orchestrator.setup.subprocess.run",
        lambda command, **_: subprocess.CompletedProcess(command, 0),
    )

    def collect(session, output):
        session.pending_question = "What should be counted?"
        session.save(output)

    monkeypatch.setattr("codex_orchestrator.interactive.collect_brief", collect)
    result = runner.invoke(app, ["resume", str(path)], input=":save\n")
    assert result.exit_code == 0, result.output
    assert "What should be counted?" in result.output
    assert PlanningSession.load(path).answers == session.answers
