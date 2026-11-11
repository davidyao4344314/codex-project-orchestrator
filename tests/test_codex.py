import json
import subprocess
import sys
from pathlib import Path

import pytest

from codex_orchestrator.codex import CodexError, codex_plan
from codex_orchestrator.planner import template_plan


def test_codex_invocation_uses_read_only_and_schema(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("codex_orchestrator.codex.shutil.which", lambda _: "/tools/codex")
    content = template_plan()
    brief = "Handle spaces, café, and shell syntax: $(touch should-not-exist)"

    def run(command: list[str], **options: object) -> subprocess.CompletedProcess:
        assert command[:4] == ["/tools/codex", "--ask-for-approval", "never", "exec"]
        assert command[command.index("--sandbox") + 1] == "read-only"
        assert "--ephemeral" in command
        assert "--skip-git-repo-check" in command
        assert command[command.index("--cd") + 1] == str(tmp_path.resolve())
        assert command[command.index("--model") + 1] == "account-model"
        assert command[-1] == "-"
        assert brief not in command
        assert brief in options["input"]
        assert "shell" not in options
        assert options["timeout"] == 25
        schema = json.loads(Path(command[command.index("--output-schema") + 1]).read_text())
        assert schema["additionalProperties"] is False
        assert set(schema["required"]) == set(schema["properties"])
        task_schema = schema["$defs"]["Task"]
        assert task_schema["additionalProperties"] is False
        assert set(task_schema["required"]) == set(task_schema["properties"])
        Path(command[command.index("--output-last-message") + 1]).write_text(
            content.model_dump_json(), encoding="utf-8"
        )
        return subprocess.CompletedProcess(
            command, 0, stdout="progress is not final JSON", stderr=""
        )

    monkeypatch.setattr("codex_orchestrator.codex.subprocess.run", run)
    assert codex_plan(tmp_path, "Example", brief, model="account-model", timeout=25) == content


def test_missing_codex(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("codex_orchestrator.codex.find_codex", lambda: None)
    with pytest.raises(CodexError, match="not found"):
        codex_plan(tmp_path, "Example", "Build an app")


@pytest.mark.parametrize(
    ("failure", "message"),
    [
        ("timeout", "timed out"),
        ("os_error", "Could not start"),
        ("exit", "Please sign in"),
        ("no_output", "valid plan"),
        ("invalid_json", "valid plan"),
        ("cycle", "cycle"),
    ],
)
def test_codex_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str, message: str
):
    monkeypatch.setattr("codex_orchestrator.codex.shutil.which", lambda _: "/tools/codex")

    def run(command: list[str], **options: object) -> subprocess.CompletedProcess:
        if failure == "timeout":
            raise subprocess.TimeoutExpired(command, options["timeout"])
        if failure == "os_error":
            raise OSError("executable disappeared")
        if failure == "exit":
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="Please sign in")
        response = Path(command[command.index("--output-last-message") + 1])
        if failure == "invalid_json":
            response.write_text("not JSON", encoding="utf-8")
        elif failure == "cycle":
            payload = template_plan().model_dump(mode="json")
            payload["tasks"][0]["depends_on"] = ["verification"]
            response.write_text(json.dumps(payload), encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("codex_orchestrator.codex.subprocess.run", run)
    with pytest.raises(CodexError, match=message):
        codex_plan(tmp_path, "Example", "Build an app")


def test_real_subprocess_boundary_with_fake_cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Exercise stdin and file handoff without an account, network call, or real Codex turn."""
    fake = tmp_path / "fake codex"
    payload = template_plan().model_dump_json()
    fake.write_text(
        f"#!{sys.executable}\n"
        "import json, pathlib, sys\n"
        "args = sys.argv[1:]\n"
        "prompt = sys.stdin.read()\n"
        "assert 'Build a café app' in prompt\n"
        "schema = pathlib.Path(args[args.index('--output-schema') + 1])\n"
        "assert json.loads(schema.read_text())['type'] == 'object'\n"
        "output = pathlib.Path(args[args.index('--output-last-message') + 1])\n"
        f"output.write_text({payload!r}, encoding='utf-8')\n"
        "print('diagnostic text, not the final response')\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    monkeypatch.setattr("codex_orchestrator.codex.shutil.which", lambda _: str(fake))
    result = codex_plan(tmp_path, "Café", "Build a café app")
    assert result == template_plan()


@pytest.mark.parametrize("reported", ["served-model", None])
def test_response_model_metadata(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys, reported):
    monkeypatch.setattr("codex_orchestrator.codex.find_codex", lambda: "/tools/codex")
    seen = []

    def run(command, **kwargs):
        Path(command[command.index("--output-last-message") + 1]).write_text(
            template_plan().model_dump_json()
        )
        stderr = f"model: {reported}\n" if reported else ""
        return subprocess.CompletedProcess(command, 0, stdout="", stderr=stderr)

    monkeypatch.setattr("codex_orchestrator.codex.subprocess.run", run)
    from codex_orchestrator.codex import codex_response
    from codex_orchestrator.models import PlanContent

    codex_response(tmp_path, "Plan", PlanContent, model="requested-model", on_response=seen.append)
    assert seen == [reported]
    output = capsys.readouterr().err
    assert "Request model: requested-model" in output
    assert (f"Response model: {reported}" if reported else "unreported by CLI") in output
