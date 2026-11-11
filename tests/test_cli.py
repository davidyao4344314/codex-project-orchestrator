from pathlib import Path

import pytest
from typer.testing import CliRunner

from codex_orchestrator.cli import app
from codex_orchestrator.codex import CodexError
from codex_orchestrator.models import ProjectPlan
from codex_orchestrator.planner import template_plan

runner = CliRunner()


def test_help() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "frontend" in result.output


def test_plan_writes_valid_json_and_leaves_project_alone(tmp_path: Path) -> None:
    project = tmp_path / "app"
    project.mkdir()
    existing = project / "existing.txt"
    existing.write_text("unchanged", encoding="utf-8")
    output = tmp_path / "plans" / "demo.json"
    result = runner.invoke(app, ["plan", str(project), "Build a booking app", "-o", str(output)])
    assert result.exit_code == 0, result.output
    plan = ProjectPlan.model_validate_json(output.read_text(encoding="utf-8"))
    assert plan.project_name == "app"
    assert plan.brief == "Build a booking app"
    assert plan.planning_mode == "template"
    assert "generic" in result.output
    assert list(project.iterdir()) == [existing]
    assert existing.read_text(encoding="utf-8") == "unchanged"


def test_refuses_overwrite_unless_forced(tmp_path: Path) -> None:
    output = tmp_path / "plan.json"
    output.write_text("keep this", encoding="utf-8")
    arguments = ["plan", str(tmp_path), "Build an app", "-o", str(output)]
    assert runner.invoke(app, arguments).exit_code != 0
    assert output.read_text(encoding="utf-8") == "keep this"
    assert runner.invoke(app, [*arguments, "--force"]).exit_code == 0
    ProjectPlan.model_validate_json(output.read_text(encoding="utf-8"))


def test_brief_file_and_name(tmp_path: Path) -> None:
    brief = tmp_path / "brief.txt"
    brief.write_text("Build a café app\nInclude bookings.\n", encoding="utf-8")
    output = tmp_path / "plan.json"
    result = runner.invoke(
        app,
        ["plan", str(tmp_path), "--brief-file", str(brief), "--name", "Café", "-o", str(output)],
    )
    assert result.exit_code == 0, result.output
    plan = ProjectPlan.model_validate_json(output.read_text(encoding="utf-8"))
    assert plan.project_name == "Café"
    assert plan.brief == "Build a café app\nInclude bookings."


@pytest.mark.parametrize("brief", [None, "", "   "])
def test_reject_missing_or_blank_brief(tmp_path: Path, brief: str | None) -> None:
    output = tmp_path / "plan.json"
    args = ["plan", str(tmp_path)]
    if brief is not None:
        args.append(brief)
    result = runner.invoke(app, [*args, "-o", str(output)])
    assert result.exit_code == 2
    assert not output.exists()


def test_reject_two_briefs(tmp_path: Path) -> None:
    brief = tmp_path / "brief.txt"
    brief.write_text("A brief", encoding="utf-8")
    result = runner.invoke(
        app, ["plan", str(tmp_path), "Another brief", "--brief-file", str(brief)]
    )
    assert result.exit_code == 2


def test_reject_missing_project(tmp_path: Path) -> None:
    result = runner.invoke(app, ["plan", str(tmp_path / "missing"), "Build an app"])
    assert result.exit_code == 2


def test_codex_provider(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    def planner(*args: object, **kwargs: object):
        calls.append((args, kwargs))
        return template_plan()

    monkeypatch.setattr("codex_orchestrator.cli.ensure_codex", lambda **_: "/tools/codex")
    monkeypatch.setattr("codex_orchestrator.cli.codex_plan", planner)
    output = tmp_path / "plan.json"
    result = runner.invoke(
        app,
        [
            "plan",
            str(tmp_path),
            "Build an app",
            "--provider",
            "codex",
            "--model",
            "account-model",
            "--timeout",
            "20",
            "-o",
            str(output),
        ],
    )
    assert result.exit_code == 0, result.output
    plan = ProjectPlan.model_validate_json(output.read_text(encoding="utf-8"))
    assert plan.planning_mode == "codex"
    assert len(calls) == 1
    assert calls[0][1] == {"model": "account-model", "timeout": 20}


def test_failed_codex_keeps_previous_plan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*args: object, **kwargs: object):
        raise CodexError("Invalid planning output")

    monkeypatch.setattr("codex_orchestrator.cli.ensure_codex", lambda **_: "/tools/codex")
    monkeypatch.setattr("codex_orchestrator.cli.codex_plan", fail)
    output = tmp_path / "plan.json"
    output.write_text("previous plan", encoding="utf-8")
    result = runner.invoke(
        app,
        [
            "plan",
            str(tmp_path),
            "Build an app",
            "--provider",
            "codex",
            "--force",
            "-o",
            str(output),
        ],
    )
    assert result.exit_code == 1
    assert "Invalid planning output" in result.output
    assert output.read_text(encoding="utf-8") == "previous plan"


def test_model_requires_codex(tmp_path: Path) -> None:
    result = runner.invoke(app, ["plan", str(tmp_path), "A brief", "--model", "some-model"])
    assert result.exit_code == 2


def test_reject_nonpositive_timeout(tmp_path: Path) -> None:
    result = runner.invoke(app, ["plan", str(tmp_path), "A brief", "--timeout", "0"])
    assert result.exit_code == 2
